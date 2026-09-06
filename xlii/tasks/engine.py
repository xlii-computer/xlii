"""Task pipeline runner and step executors."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

from .model import (
    DEFAULT_CARRY_MAX_CHARS,
    KIND_AGENT,
    KIND_SHELL,
    KIND_SLASH,
    PREV_TOKEN,
    RAW_TOKEN,
    Emitter,
    Pipeline,
    PipelineOutcome,
    Step,
    StepOutcome,
    TaskParseError,
    TaskRun,
    _CHECKPOINT_UNSET,
    _noop,
)
from .parse import (
    edges_by_from,
    references_prev,
    split_branch_ids,
    split_taskplus_trailer,
    step_index_maps,
    substitute,
    truncate_for_inject,
)
from .store import save_run

# Reentrancy stamp for startup-task (and any other "don't fire while a pipe
# is running" guard). Thread-local so a background job doesn't poison the
# foreground REPL, and nested run_pipeline calls nest correctly.
_PIPELINE_DEPTH = threading.local()


def pipeline_in_flight() -> bool:
    """True while ``run_pipeline`` is executing on this thread."""
    return getattr(_PIPELINE_DEPTH, "n", 0) > 0


# --------------------------------------------------------------------------- #
#  Step executors
# --------------------------------------------------------------------------- #

def _last_assistant_text(agent: Any) -> str:
    """The most recent assistant message content (run_turn returns "" when the
    text was streamed live, but the real content still lands in history)."""
    for entry in reversed(getattr(agent, "history", None) or []):
        if entry.get("role") == "assistant" and entry.get("content"):
            return str(entry["content"])
    return ""


def _run_shell_step(cmd: str, stdin_carry: str, cwd: Optional[str]) -> tuple[str, int, str]:
    """Run a shell command, piping the carry to stdin. Returns (stdout, rc, stderr)."""
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd,
            input=stdin_carry,
            capture_output=True,
            text=True,
            errors="replace",  # a branch/step emitting non-UTF-8 bytes must not raise
        )
    except OSError as e:
        return "", 1, f"shell error: {e}"
    return (proc.stdout or ""), proc.returncode, (proc.stderr or "")


def _record_shell_capture(state: Any, cmd: str, stdout: str, stderr: str,
                          rc: int, cwd: Optional[str]) -> None:
    """Feed a pipeline shell step into the session's shell-capture seam — the same
    ``record_last_shell`` the interactive ``!cmd`` path uses.

    Without this, ``state.last_shell`` still held the last *interactive* command,
    so ``ls -al |> /shum`` summarized the PREVIOUS shell output (an off-by-one
    lag), and post-pipeline ``/shum``·``/shplain``·``/replay``·``?>`` were stale
    too. Recording here makes a pipeline shell step "the last shell command",
    exactly as if it had been typed with ``!``. Best-effort: capture must never
    fail a step.
    """
    if state is None:
        return
    try:
        from xlii.shell_toolkit import record_last_shell
        from xlii.turn_events import ShellRan

        record_last_shell(state, ShellRan(
            command=cmd,
            cwd=Path(cwd) if cwd else Path.cwd(),
            stdout=stdout,
            stderr=stderr,
            returncode=rc,
            source="user_bang",   # a task step is user-authored, like a ! escape
        ))
    except Exception:
        # Recording the step's shell event is bookkeeping; the step itself already ran.
        pass


def _agent_prompt(body: str, carry: str, args: Optional[dict[str, str]] = None) -> str:
    """Build the agent turn text: bind ``{{prev}}`` + any declared ``{{param}}``;
    when the author did not place ``{{prev}}`` explicitly, the carry is appended
    under a fence so the common case needs no placeholder."""
    text = substitute(body, {"prev": carry, **(args or {})}, kind=KIND_AGENT)
    if not references_prev(body) and carry:
        return f"{text}\n\n--- piped input ---\n{carry}"
    return text


def _run_agent_step(agent: Any, prompt: str) -> tuple[str, bool, str]:
    if agent is None:
        return "", False, "agent steps need a live session"
    try:
        text, _dirty, _stats = agent.run_turn(prompt)
    except Exception as e:  # noqa: BLE001 — surface any turn failure as a step error
        return "", False, f"agent error: {e}"
    if not text:
        text = _last_assistant_text(agent)
    return (text or ""), True, ""


def _scrape_slash_output(state: Any, slash_line: str) -> tuple[str, bool]:
    """Dispatch a slash command and capture its text output (the carry).

    ⮕ Seam #3 (interaction-layer-II merge contract): this is THE single capture
    chokepoint that *consumes* Vector B's capturing-console primitive. B owns the
    long-lived implementation (``shell_toolkit.run_capturing`` / ``capture_output``
    + the typed ``last_output`` buffer); at integration this body becomes a call
    to that seam so there is exactly one capturing console.
    """
    from xlii.commands import dispatch_repl_command
    from xlii.shell_toolkit import capturing_console

    con = capturing_console()
    ctx = {**state.as_context_dict(), "console": con}
    handled = bool(dispatch_repl_command(slash_line, ctx))
    return con.export_text().strip("\n"), handled


# --------------------------------------------------------------------------- #
#  Security gate
# --------------------------------------------------------------------------- #

def _confirm_step(console: Any, prompt: str) -> bool:
    """Confirm-before-run gate. Fails closed (no/EOF → don't run)."""
    from xlii.tools import _confirm

    try:
        if console is not None:
            console.print(prompt, markup=False)
            answer = _confirm("run? (y/N): ")
        else:
            answer = _confirm(prompt + "\nrun? (y/N): ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() in ("y", "yes")


# --------------------------------------------------------------------------- #
#  The runner
# --------------------------------------------------------------------------- #

def validate_pipeline(pipeline: Pipeline, ctx: dict[str, Any]) -> list[str]:
    """Fail-fast checks before anything runs: unknown slash commands, bad slash
    bodies. Shell/agent steps are always well-formed once parsed."""
    from xlii.commands import find_repl_command

    repl = _repl_scope(ctx)
    active_role = ctx.get("active_role") or getattr(ctx.get("state"), "active_role", None)
    errs: list[str] = []
    try:
        step_index_maps(pipeline)
    except TaskParseError as e:
        errs.append(str(e))
    for i, step in enumerate(pipeline.steps, 1):
        if step.kind != KIND_SLASH:
            continue
        if not step.body.lstrip().startswith("/"):
            errs.append(f"step {i}: slash step must start with '/' (got {step.body!r})")
            continue
        # Strip the carry token before lookup so the first word resolves cleanly.
        probe = step.body.replace(PREV_TOKEN, "").replace(RAW_TOKEN, "")
        if find_repl_command(probe, repl=repl, active_role=active_role) is None:
            token = probe.split(maxsplit=1)[0] if probe.split() else step.body
            errs.append(f"step {i}: unknown slash command {token}")
    return errs


def _repl_scope(ctx: dict[str, Any]) -> str:
    state = ctx.get("state")
    scope = ctx.get("command_scope") or getattr(state, "command_scope", None)
    if scope:
        return scope
    return "chat" if ctx.get("persona") or getattr(state, "persona", None) else "code"


def _resolve_cwd(ctx: dict[str, Any]) -> Optional[str]:
    state = ctx.get("state")
    cwd = getattr(state, "shell_cwd", None) if state is not None else None
    if not cwd:
        project = getattr(state, "project", None) or ctx.get("project")
        cwd = getattr(project, "project_root", None) if project is not None else None
    return str(cwd) if cwd else None


def _taskplus_branch_error(
    trailer: Optional[dict[str, Any]],
    outs: dict[str, str],
) -> str:
    """Return a detail string when verdict routing fails; \"\" when ok."""
    if not trailer:
        return "missing TASK+ trailer (branch required)"
    branch = trailer.get("branch")
    if not isinstance(branch, str) or not branch.strip():
        return "TASK+ trailer missing branch"
    key = branch.strip()
    if key not in outs:
        return f"TASK+ branch {key!r} unmatched"
    return ""


def _resolve_next_index(
    i: int,
    step: Step,
    ok: bool,
    *,
    id_to_index: dict[str, int],
    n_steps: int,
    keep_going: bool,
) -> Optional[int]:
    """0-based index of the next step, or ``None`` to stop the run."""
    if step.kind == KIND_SHELL:
        if ok and step.on_success:
            return id_to_index[step.on_success]
        if not ok and step.on_failure:
            return id_to_index[step.on_failure]
    if ok or keep_going or step.continue_on_error:
        nxt = i + 1
        return nxt if nxt < n_steps else n_steps
    return None


def _arm_bindings(carries: dict[str, str]) -> dict[str, str]:
    return {f"arm.{bid}": carry for bid, carry in carries.items()}


def _join_digest(order: list[str], carries: dict[str, str], oks: dict[str, bool]) -> str:
    parts: list[str] = []
    for bid in order:
        status = "ok" if oks.get(bid) else "fail"
        parts.append(f"### arm {bid} ({status})\n{carries.get(bid, '')}".rstrip())
    return "\n\n".join(parts)


def _resolve_split_branches(
    step: Step, steps: list[Step], id_to_index: dict[str, int],
    inject: str, task_args: dict[str, str],
) -> list[tuple[str, str]]:
    """Resolve each branch's shell command ONCE (with the carry substituted), so the
    gate shows exactly what will run and there is no resolve-after-confirm gap."""
    return [
        (bid, substitute(steps[id_to_index[bid]].body,
                         {"prev": inject, **task_args}, kind=KIND_SHELL))
        for bid in step.split
    ]


def _run_split_shell(
    bid: str, cmd: str, inject: str, cwd: Optional[str], *,
    cancel_event: Optional[threading.Event] = None,
    procs: Optional[dict[str, subprocess.Popen]] = None,
    procs_lock: Optional[threading.Lock] = None,
) -> tuple[str, str, int, str]:
    if cancel_event is not None and cancel_event.is_set():
        return bid, "", 1, "cancelled"
    try:
        proc = subprocess.Popen(
            cmd, shell=True, cwd=cwd,
            stdin=subprocess.PIPE if inject else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace",
        )
    except OSError as e:
        return bid, "", 1, f"shell error: {e}"
    if procs is not None and procs_lock is not None:
        with procs_lock:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                proc.communicate()
                return bid, "", 1, "cancelled"
            procs[bid] = proc
    out, err = proc.communicate(input=inject or None)
    return bid, out or "", proc.returncode, err or ""


def _run_split(
    step: Step,
    resolved_branches: list[tuple[str, str]],
    id_to_index: dict[str, int],
    inject: str,
    cwd: Optional[str],
    cap: int,
) -> tuple[bool, str, dict[str, str], list[StepOutcome], str]:
    import concurrent.futures

    branches = [bid for bid, _cmd in resolved_branches]
    cmd_of = dict(resolved_branches)
    workers = max(1, min(cap, len(branches)))
    cancel_event = threading.Event() if step.policy == "first_ok" else None
    procs: dict[str, subprocess.Popen] = {}
    procs_lock = threading.Lock()
    winner: Optional[str] = None
    winner_lock = threading.Lock()

    def _one(bid: str) -> tuple[str, str, int, str]:
        return _run_split_shell(
            bid, cmd_of[bid], inject, cwd,
            cancel_event=cancel_event, procs=procs, procs_lock=procs_lock,
        )

    raw: dict[str, tuple[str, int, str]] = {}
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=workers)
    try:
        futs = [ex.submit(_one, b) for b in branches]
        for fut in concurrent.futures.as_completed(futs):
            bid, out, rc, err = fut.result()
            raw[bid] = (out, rc, err)
            if step.policy == "first_ok" and rc == 0:
                with winner_lock:
                    if winner is None:
                        winner = bid
                        if cancel_event is not None:
                            cancel_event.set()
                            with procs_lock:
                                for other, proc in list(procs.items()):
                                    if other != bid:
                                        try:
                                            proc.kill()
                                        except OSError:
                                            # The sibling already exited -- the split is being torn down anyway.
                                            pass
                break
    finally:
        ex.shutdown(wait=(step.policy != "first_ok"), cancel_futures=(step.policy == "first_ok"))

    carries: dict[str, str] = {}
    oks: dict[str, bool] = {}
    outcomes: list[StepOutcome] = []
    for bid in branches:
        out, rc, err = raw.get(bid, ("", 1, "cancelled"))
        ok = rc == 0
        carry = out
        detail = "cancelled" if err == "cancelled" else ("" if ok else f"exit {rc}")
        if not ok and err and err != "cancelled":
            carry = (out + ("\n" if out else "") + "[stderr] " + err.strip()).strip()
        carries[bid] = carry
        oks[bid] = ok
        outcomes.append(StepOutcome(
            id_to_index[bid] + 1, KIND_SHELL, cmd_of[bid], carry, ok, detail=detail,
        ))

    split_ok = all(oks.values()) if step.policy == "all" else any(oks.values())
    if step.policy == "first_ok" and split_ok and winner is not None:
        merged = carries[winner]
    else:
        merged = _join_digest(branches, carries, oks)
    detail = "" if split_ok else f"split policy '{step.policy}' not met"
    return split_ok, merged, carries, outcomes, detail


def run_pipeline(
    pipeline: Pipeline,
    ctx: dict[str, Any],
    *,
    carry0: str = "",
    start_index: int = 0,
    keep_going: bool = False,
    yes: bool = False,
    confirm_shell: bool = True,
    carry_max_chars: Optional[int] = DEFAULT_CARRY_MAX_CHARS,
    emit: Optional[Emitter] = None,
    run: Optional[TaskRun] = None,
    xli_dir: Optional[Path] = None,
    args: Optional[dict[str, str]] = None,
) -> PipelineOutcome:
    """Execute a pipeline sequentially, threading the carry.

    Security: a carry that has passed through an **agent** step is treated as
    untrusted; the first shell/slash step downstream then prompts even under
    ``yes`` — unless ``confirm_shell`` is False (the explicit opt-out).

    Outcome semantics:
      - ``PipelineOutcome.ok`` indicates the run was not stopped by a
        non-continue failure/block.
      - ``PipelineOutcome.had_errors`` indicates at least one step failed.
      - With ``keep_going=True`` or ``continue_on_error=True``, it is valid to
        return ``ok=True`` and ``had_errors=True`` in the same outcome.
    """
    depth = getattr(_PIPELINE_DEPTH, "n", 0)
    _PIPELINE_DEPTH.n = depth + 1
    try:
        return _run_pipeline_body(
            pipeline, ctx,
            carry0=carry0, start_index=start_index, keep_going=keep_going,
            yes=yes, confirm_shell=confirm_shell, carry_max_chars=carry_max_chars,
            emit=emit, run=run, xli_dir=xli_dir, args=args,
        )
    finally:
        _PIPELINE_DEPTH.n = depth


def _run_pipeline_body(
    pipeline: Pipeline,
    ctx: dict[str, Any],
    *,
    carry0: str = "",
    start_index: int = 0,
    keep_going: bool = False,
    yes: bool = False,
    confirm_shell: bool = True,
    carry_max_chars: Optional[int] = DEFAULT_CARRY_MAX_CHARS,
    emit: Optional[Emitter] = None,
    run: Optional[TaskRun] = None,
    xli_dir: Optional[Path] = None,
    args: Optional[dict[str, str]] = None,
) -> PipelineOutcome:
    emit = emit or _noop
    console = ctx.get("console")
    agent = ctx.get("agent") or getattr(ctx.get("state"), "agent", None)
    cwd = _resolve_cwd(ctx)

    carry = carry0
    outcomes: list[StepOutcome] = []
    # Restore the untrusted-carry flag on resume: if the original run blocked a
    # routed shell/slash arm because the carry had passed through an agent step,
    # a fresh run must re-assert that gate rather than silently execute the arm.
    untrusted_pending = bool(run.untrusted_pending) if run is not None else False
    ok_all = True
    had_errors = False
    failed_index: Optional[int] = None
    # Every step index we have entered this run — the loop bound. A back-edge
    # (on_failure/on_success or a verdict edge) that revisits an already-run step
    # is an unbounded cycle: no bounded-retry feature exists, so we fail closed on
    # the first revisit (proposals/task+plus.md — "caps are mandatory").
    visited: set[int] = set()

    steps = pipeline.steps
    id_to_index, step_ids = step_index_maps(pipeline)
    if run is not None and run.cursor_id:
        cid = run.cursor_id.strip()
        if cid in id_to_index:
            start_index = id_to_index[cid]
    branch_edges = edges_by_from(pipeline)
    verdict_routed_to = run.verdict_routed_to if run is not None else None
    # Declared param bindings (arg #1..n; {{prev}} stays the carry). Explicit args
    # win; else restore from the saved run so a resumed pipe still resolves {{name}}.
    task_args = dict(args) if args is not None else (dict(run.args) if run is not None else {})
    split_carries = dict(run.split_carries) if run is not None else {}
    split_env = _arm_bindings(split_carries)
    branch_indices = {id_to_index[b] for b in split_branch_ids(pipeline)}
    _cfg = ctx.get("cfg") or getattr(ctx.get("state"), "cfg", None)
    _max_workers = max(1, int(getattr(_cfg, "max_parallel_workers", None) or 4))

    def _skip_branches(n: Optional[int]) -> Optional[int]:
        while n is not None and n < len(steps) and n in branch_indices:
            n += 1
        return n

    i = _skip_branches(start_index)  # never begin *on* a branch step
    while i is not None and i < len(steps):
        step = steps[i]
        visited.add(i)
        idx = i + 1
        incoming = carry  # the carry that FEEDS this step (preserved on failure)
        inject = truncate_for_inject(incoming, carry_max_chars)

        # ------------------------------------------------------------------ #
        #  T+P3 — split node: fan out to shell branches, join to one carry.
        # ------------------------------------------------------------------ #
        if step.is_split():
            # Resolve the branch commands ONCE so the gate shows exactly what runs
            # (an untrusted-carry review must see the real command, not "split[…]").
            resolved_branches = _resolve_split_branches(step, steps, id_to_index, inject, task_args)
            resolved = "split:\n" + "\n".join(f"  [{bid}] {cmd}" for bid, cmd in resolved_branches)
            emit("step_begin", index=idx, total=len(steps), kind="split", resolved=resolved)
            # A split is a shell action — gate it once (untrusted carry, or non-yes).
            if confirm_shell:
                reason = ""
                if untrusted_pending:
                    reason = "carry passed through an agent step (untrusted)"
                elif not yes:
                    reason = "split step"
                if reason and not _confirm_step(console, _gate_prompt("split", resolved, reason)):
                    outcomes.append(StepOutcome(idx, "split", resolved, incoming, False,
                                                detail=f"blocked: {reason}", blocked=True))
                    emit("blocked", index=idx, reason=reason)
                    ok_all = False
                    had_errors = True
                    failed_index = i
                    _checkpoint(run, xli_dir, cursor=i, status="failed", carry=incoming,
                                error=f"step {idx} blocked: {reason}",
                                untrusted_pending=untrusted_pending, split_carries=split_carries)
                    break
            untrusted_pending = False  # the gate (if any) has been cleared
            _split_cap = min(_max_workers, len(step.split))
            ok, step_carry, branch_carries, branch_outs, detail = _run_split(
                step, resolved_branches, id_to_index, inject, cwd, _split_cap)
            outcomes.extend(branch_outs)
            outcomes.append(StepOutcome(idx, "split", resolved, step_carry, ok, detail=detail))
            emit("step_end", index=idx, kind="split", ok=ok, carry_len=len(step_carry), detail=detail)
            split_carries.update(branch_carries)
            split_env.update(_arm_bindings(branch_carries))
            # Proceed to the join, or fail closed (like a shell step) if the policy
            # failed — set continue_on_error / --keep-going to triage on failure.
            next_i = id_to_index[step.join] if (ok or keep_going or step.continue_on_error) else None
        else:
            branch_jump: Optional[int] = None
            branch_required_failed = False  # a required verdict could not be resolved
            bindings = {"prev": inject, **task_args, **split_env}
            if step.kind == KIND_SHELL:
                resolved = substitute(step.body, bindings, kind=KIND_SHELL)
            elif step.kind == KIND_SLASH:
                resolved = substitute(step.body, bindings, kind=KIND_SLASH)
            else:
                resolved = _agent_prompt(step.body, inject, {**task_args, **split_env})

            emit("step_begin", index=idx, total=len(steps), kind=step.kind, resolved=resolved)

            # --- security gate (shell/slash only) ---
            # `yes` skips the ordinary "this is a shell step" nag, not
            # network/system classification — those always confirm (or refuse
            # when confirm_shell is off).
            if step.kind in (KIND_SHELL, KIND_SLASH):
                reason = ""
                if step.kind == KIND_SHELL:
                    try:
                        from xlii.shellgate import NETWORK, MODIFIES_SYSTEM, classify_command

                        cls = classify_command(resolved, Path(cwd) if cwd else None)
                        if cls in (NETWORK, MODIFIES_SYSTEM):
                            reason = f"classified as {cls}"
                    except Exception:
                        reason = "could not classify shell command"
                if not reason and confirm_shell:
                    if untrusted_pending:
                        reason = "carry passed through an agent step (untrusted)"
                    elif not yes:
                        reason = f"{step.kind} step"
                if reason:
                    allowed = False
                    if confirm_shell and _confirm_step(
                        console, _gate_prompt(step.kind, resolved, reason)
                    ):
                        allowed = True
                    if not allowed:
                        outcomes.append(StepOutcome(idx, step.kind, resolved, incoming, False,
                                                    detail=f"blocked: {reason}", blocked=True))
                        emit("blocked", index=idx, reason=reason)
                        ok_all = False
                        had_errors = True
                        failed_index = i
                        _checkpoint(run, xli_dir, cursor=i, status="failed", carry=incoming,
                                    error=f"step {idx} blocked: {reason}",
                                    untrusted_pending=untrusted_pending, split_carries=split_carries)
                        break

            if untrusted_pending and step.kind in (KIND_SHELL, KIND_SLASH):
                untrusted_pending = False  # the gate (if any) has been cleared

            # --- execute ---
            if step.kind == KIND_SHELL:
                out, rc, err = _run_shell_step(resolved, inject, cwd)
                # The step is now "the last shell command" — /shum, /sh --transform,
                # /replay and ?> must read THIS output, not a previous command's.
                _record_shell_capture(ctx.get("state"), resolved, out, err, rc, cwd)
                ok = rc == 0
                step_carry = out
                detail = "" if ok else f"exit {rc}"
                if not ok and err:
                    detail = f"exit {rc}: {err.strip()[:300]}"
                    # --keep-going carries the (stderr-tagged) output onward.
                    if keep_going or step.continue_on_error:
                        step_carry = (out + ("\n" if out else "") + "[stderr] " + err.strip()).strip()
            elif step.kind == KIND_AGENT:
                step_carry, ok, detail = _run_agent_step(agent, resolved)
                if ok:
                    untrusted_pending = True
                    sid = step_ids[i]
                    outs = branch_edges.get(sid)
                    if outs:
                        step_carry, trailer = split_taskplus_trailer(step_carry)
                        route_err = _taskplus_branch_error(trailer, outs)
                        if route_err:
                            ok = False
                            detail = route_err
                            branch_required_failed = True
                        elif trailer.get("continue") is False:
                            branch_jump = len(steps)
                        else:
                            branch_jump = id_to_index[outs[str(trailer["branch"]).strip()]]
                            verdict_routed_to = branch_jump
            else:
                step_carry, ok, detail = _scrape_slash_output_step(resolved, ctx)

            outcomes.append(StepOutcome(idx, step.kind, resolved, step_carry, ok, detail=detail))
            emit("step_end", index=idx, kind=step.kind, ok=ok, carry_len=len(step_carry), detail=detail)

            stop_after_current = verdict_routed_to == i
            if branch_required_failed:
                # A required verdict could not be resolved (missing/unmatched TASK+
                # trailer). This is a ROUTING failure, not an ordinary step error, so
                # it must fail closed even under --keep-going / continue_on_error —
                # otherwise control falls through linearly and runs EVERY branch arm.
                # (Guarantee: this module's docstring + proposals/task+plus.md —
                # "fail closed when the trailer is missing or unmatched … don't guess".)
                next_i = None
            elif stop_after_current and (ok or keep_going or step.continue_on_error):
                next_i = len(steps)
                verdict_routed_to = None
            elif branch_jump is not None:
                next_i = branch_jump
            else:
                next_i = _resolve_next_index(
                    i, step, ok, id_to_index=id_to_index, n_steps=len(steps), keep_going=keep_going,
                )
                if stop_after_current and next_i is not None:
                    verdict_routed_to = None

        # ------------------------------------------------------------------ #
        #  Shared tail — stop / loop-guard / checkpoint / advance.
        # ------------------------------------------------------------------ #
        if next_i is None:
            had_errors = True
            ok_all = False
            failed_index = i
            _checkpoint(run, xli_dir, cursor=i, status="failed", carry=incoming, error=detail,
                        untrusted_pending=untrusted_pending, split_carries=split_carries)
            break

        next_i = _skip_branches(next_i)  # a branch step runs only via its split

        if next_i in visited:
            # A back-edge to an already-run step — an unbounded cycle (a self-loop
            # ``next_i == i`` is just its length-1 case). Fail closed rather than
            # spin: there is no bounded-retry primitive yet (that stays ``/loop``).
            had_errors = True
            ok_all = False
            failed_index = i
            detail = f"branch loop: step {idx} routes back to an already-run step"
            _checkpoint(run, xli_dir, cursor=i, status="failed", carry=incoming, error=detail,
                        untrusted_pending=untrusted_pending, split_carries=split_carries)
            break

        if not ok:
            had_errors = True

        carry = step_carry
        _checkpoint(run, xli_dir, cursor=next_i, status="active", carry=carry,
                    error=("" if ok else detail), verdict_routed_to=verdict_routed_to,
                    untrusted_pending=untrusted_pending, split_carries=split_carries)
        if next_i >= len(steps):
            break
        i = next_i

    if failed_index is None:
        _checkpoint(run, xli_dir, cursor=len(steps), status="done", carry=carry, error="",
                    verdict_routed_to=None)

    emit("done", ok=ok_all, carry=carry, had_errors=had_errors)
    return PipelineOutcome(steps=outcomes, carry=carry, ok=ok_all,
                           had_errors=had_errors, failed_index=failed_index)


def _scrape_slash_output_step(slash_line: str, ctx: dict[str, Any]) -> tuple[str, bool, str]:
    state = ctx.get("state")
    if state is None or not hasattr(state, "as_context_dict"):
        return "", False, "slash steps need a live session"
    text, handled = _scrape_slash_output(state, slash_line)
    return text, handled, ("" if handled else "slash command not handled")


def _gate_prompt(kind: str, resolved: str, reason: str) -> str:
    return f"[tasks] {reason} — about to run this {kind} step:\n  {resolved}"


def _checkpoint(
    run: Optional[TaskRun],
    xli_dir: Optional[Path],
    *,
    cursor: int,
    status: str,
    carry: str,
    error: str,
    verdict_routed_to: Any = _CHECKPOINT_UNSET,
    untrusted_pending: Any = _CHECKPOINT_UNSET,
    split_carries: Any = _CHECKPOINT_UNSET,
) -> None:
    if run is None or xli_dir is None:
        return
    run.cursor = cursor
    if run.steps and 0 <= cursor < len(run.steps):
        sid = str(run.steps[cursor].get("id", "") or "").strip()
        run.cursor_id = sid or str(cursor + 1)
    elif cursor >= len(run.steps):
        run.cursor_id = ""
    run.status = status
    run.carry = carry
    run.last_error = error
    if verdict_routed_to is not _CHECKPOINT_UNSET:
        run.verdict_routed_to = verdict_routed_to
    if untrusted_pending is not _CHECKPOINT_UNSET:
        run.untrusted_pending = untrusted_pending
    if split_carries is not _CHECKPOINT_UNSET:
        run.split_carries = dict(split_carries)
    save_run(xli_dir, run)


