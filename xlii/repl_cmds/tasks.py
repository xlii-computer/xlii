"""``/tasks`` — the horizontal pipe primitive (REPL surface).

Engine lives in :mod:`xlii.tasks`; this module is only argument parsing + I/O.

    /tasks run <inline-pipe | saved-name> [--dry-run] [--yes] [--keep-going] [--from FILE]
    /tasks list
    /tasks show <name>
    /tasks new  <name> [--from "<description>"]
    /tasks edit <name>
    /tasks resume
    /tasks status
    /tasks cancel
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any, Optional

from xlii import tasks as T
from xlii.commands import REPLCommand, register_repl_command


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #

def _xli_dir(ctx: dict[str, Any]) -> Optional[Path]:
    state = ctx.get("state")
    project = getattr(state, "project", None) or ctx.get("project")
    d = getattr(project, "xli_dir", None) if project is not None else None
    return Path(d) if d else None


def _short(text: str, limit: int = 100) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= limit else one[: limit - 1] + "…"


def _make_emitter(console: Any):
    def emit(event: str, **d: Any) -> None:
        if console is None:
            return
        if event == "step_begin":
            console.print(
                f"[dim][tasks {d['index']}/{d['total']} · {d['kind']}][/dim] ",
                end="",
            )
            console.print(_short(d["resolved"]), markup=False)
        elif event == "step_end":
            tag = "[green]ok[/green]" if d["ok"] else "[red]fail[/red]"
            extra = f" [dim]· {d['detail']}[/dim]" if d.get("detail") else ""
            console.print(f"[dim]  → {d['carry_len']} chars[/dim] {tag}{extra}")
        elif event == "blocked":
            console.print(f"[yellow]  ⨯ blocked — {d['reason']}[/yellow]")

    return emit


def _saved_task_exists(xli_dir: Path, name: str) -> bool:
    """True when ``name`` is a saved task (project *or* bundled stock)."""
    try:
        T.pipeline_origin(xli_dir, name)
        return True
    except T.TaskNotFound:
        return False


def _resolve_target_and_args(
    ctx: dict[str, Any], text: str, xli_dir: Optional[Path]
) -> "tuple[T.Pipeline, dict[str, str]]":
    """Return ``(pipeline, bound_args)``. A saved-task name may be followed by
    param args (``name value`` / ``name key=val``); inline pipes and bare shell
    commands take none. Binding fails closed (raises ``TaskParseError``)."""
    text = (text or "").strip()
    if T.PIPE_SEP in text:
        return T.parse_inline(text), {}
    first, _, rest = text.partition(" ")
    if first and xli_dir is not None and _saved_task_exists(xli_dir, first):
        pipeline = T.load_pipeline(xli_dir, first)
        tokens = shlex.split(rest) if rest.strip() else []
        return pipeline, T.bind_task_args(pipeline.params, tokens)
    # not a saved task → the whole text is an inline pipe / bare command
    return T.parse_inline(text), {}


def _print_plan(console: Any, pipeline: T.Pipeline) -> None:
    for ln in T.render_plan(pipeline):
        console.print(ln, markup=False)


# --------------------------------------------------------------------------- #
#  run / dry-run / resume
# --------------------------------------------------------------------------- #

# Vector J (interaction-III): `--background`/`--bg` is appended to the flag set so
# a long pipe runs as a non-blocking job (xlii.jobs) instead of holding the REPL.
# It's the only `/tasks` change J makes — the parser + _do_run branch below; F's
# spine (xlii.tasks.run_pipeline + TaskRun persistence) is reused untouched.
_FLAG_RE = re.compile(r"(--dry-run|--yes|--keep-going|--background|--bg)(\s+|$)")
_FROM_RE = re.compile(r"--from\s+(\S+)(\s+|$)")


def _parse_run_flags(rest: str) -> tuple[dict[str, Any], str]:
    opts = {"dry_run": False, "yes": False, "keep_going": False, "background": False, "from_file": None}

    def _record_flag(flag: str) -> None:
        opts["dry_run"] |= flag == "--dry-run"
        opts["yes"] |= flag == "--yes"
        opts["keep_going"] |= flag == "--keep-going"
        opts["background"] |= flag in ("--background", "--bg")

    def _eat_flags(s: str) -> str:
        """Consume run-flags from the FRONT of ``s``; return the remainder."""
        while True:
            m = _FLAG_RE.match(s)
            if m:
                _record_flag(m.group(1))
                s = s[m.end():]
                continue
            m = _FROM_RE.match(s)
            if m:
                opts["from_file"] = m.group(1)
                s = s[m.end():]
                continue
            return s.strip()

    def _eat_trailing_flags(s: str) -> str:
        """Consume run-flags from the END of ``s`` so `<name> <args> --dry-run`
        works, not only leading `--dry-run <name> <args>` (task args sit between)."""
        while True:
            m = re.search(r"(?:^|\s)(--dry-run|--yes|--keep-going|--background|--bg)$", s)
            if m:
                _record_flag(m.group(1))
                s = s[:m.start()].rstrip()
                continue
            m = re.search(r"(?:^|\s)--from\s+(\S+)$", s)
            if m:
                opts["from_file"] = m.group(1)
                s = s[:m.start()].rstrip()
                continue
            return s.strip()

    s = _eat_flags((rest or "").strip())
    # A quoted pipeline: extract it BETWEEN the matching outer quotes and parse any
    # flags that TRAIL the closing quote (so `'<pipe>' --keep-going` works — flags
    # are not leading-only). This keeps the wrapper quote out of the first/last step,
    # which otherwise glued a stray `'` on and made a slash step parse as shell with
    # an unterminated quote (and absorbed a trailing flag into the last step).
    if s and s[0] in "'\"":
        close = s.find(s[0], 1)
        if close != -1:
            target = s[1:close]
            tail = _eat_flags(s[close + 1:].strip())  # flags after the closing quote
            if tail:
                target = f"{target} {tail}"
            return opts, target.strip()
        s = s[1:]  # unmatched-leading fallback: drop opening wrapper quote
    # Unquoted inline pipelines own their last step's tokens. Stripping a
    # trailing "--yes" here would turn a step-local flag into a run-level
    # confirmation bypass for every earlier step; quote the whole pipe (or use
    # leading flags) when the flag is intended for /tasks itself.
    if T.PIPE_SEP in s:
        return opts, s.strip()
    return opts, _eat_trailing_flags(s.strip())


def _seed_carry(console: Any, from_file: Optional[str]) -> Optional[str]:
    if not from_file:
        return ""
    p = Path(from_file).expanduser()
    if not p.exists():
        console.print(f"[red]--from: no such file {from_file}[/red]")
        return None
    try:
        return p.read_text()
    except OSError as e:
        console.print(f"[red]--from: {e}[/red]")
        return None


def _session_freeball(ctx: dict[str, Any]) -> bool:
    """True when the session is in the freeball tier (``/yolo --freeball``).

    Freeball's contract is *friction drops, rails never* — its "remaining
    per-action prompts skipped" promise covers the /tasks per-step confirmation,
    which is a friction gate. So freeball auto-``yes``es that gate. It does NOT
    touch ``confirm_shell``: the untrusted-carry gate (a shell/slash step fed by
    an agent step's output) is a security rail and still fires even under yes.
    Keyed on ``freeball`` specifically — plain /yolo leaves the step gate as-is.
    """
    return bool(getattr(ctx.get("state"), "freeball", False))


def _do_run(rest: str, ctx: dict[str, Any]) -> None:
    console = ctx["console"]
    xli_dir = _xli_dir(ctx)
    opts, target = _parse_run_flags(rest)
    if not target:
        console.print("[dim]usage:[/dim] [cyan]/tasks run '<cmd> |> ?prompt |> /slash' "
                      "[--dry-run] [--yes] [--keep-going] [--background] [--from FILE][/cyan]")
        return
    if _session_freeball(ctx):
        opts["yes"] = True   # freeball auto-yes's the per-step friction gate (rail stays via confirm_shell)

    try:
        pipeline, args = _resolve_target_and_args(ctx, target, xli_dir)
    except T.TaskError as e:
        console.print(f"[red]/tasks: {e}[/red]")
        return

    errs = T.validate_pipeline(pipeline, ctx)
    if errs:
        for e in errs:
            console.print(f"[red]/tasks: {e}[/red]")
        return

    if opts["dry_run"]:
        _print_plan(console, pipeline)
        if args:
            call = " ".join(f"{k}={v}" for k, v in args.items())
            console.print(f"[dim]call: {pipeline.name} {call}[/dim]")
        console.print("[dim](dry run — nothing executed)[/dim]")
        return

    carry0 = _seed_carry(console, opts["from_file"])
    if carry0 is None:
        return

    defaults = T.resolve_tasks_defaults(ctx)
    keep_going = opts["keep_going"] or bool(defaults.get("keep_going"))
    _warn_chat_shell(ctx, pipeline, opts["yes"])

    # Vector J: `--background` wraps the SAME run_pipeline call in a session-owned
    # job (xlii.jobs) so a long pipe doesn't hold the REPL hostage; the on-disk
    # TaskRun is still the per-job durable state (/tasks status|resume read it).
    if opts["background"]:
        bg_run = (
            T.new_run(pipeline, carry0=carry0, keep_going=keep_going, args=args)
            if xli_dir is not None else None
        )
        _dispatch_background(
            ctx, pipeline,
            carry0=carry0, keep_going=keep_going, yes=opts["yes"],
            defaults=defaults, run=bg_run, xli_dir=xli_dir, args=args,
        )
        return

    run = None
    if xli_dir is not None:
        run = T.new_run(pipeline, carry0=carry0, keep_going=keep_going, args=args)

    outcome = T.run_pipeline(
        pipeline, ctx,
        carry0=carry0,
        keep_going=keep_going,
        yes=opts["yes"],
        confirm_shell=bool(defaults.get("confirm_shell", True)),
        carry_max_chars=defaults.get("carry_max_chars", T.DEFAULT_CARRY_MAX_CHARS),
        emit=_make_emitter(console),
        run=run,
        xli_dir=xli_dir,
        args=args,
    )
    _report_result(console, outcome)
    if xli_dir is not None:
        T.prune_runs(xli_dir)


def _dispatch_background(
    ctx: dict[str, Any],
    pipeline: T.Pipeline,
    *,
    carry0: str,
    keep_going: bool,
    yes: bool,
    defaults: dict[str, Any],
    run: Optional["T.TaskRun"],
    xli_dir: Optional[Path],
    args: Optional[dict[str, str]] = None,
) -> None:
    """Run the pipeline as a non-blocking job on the session JobRegistry (Vector
    J). The job thread runs run_pipeline non-interactively: shell/slash gates that
    would prompt fail closed (the job stops at that step; the on-disk TaskRun lets
    you `/tasks resume` it in the foreground to approve) — a detached run never
    blocks on input. Pass --yes to pre-approve ordinary shell steps so an
    unattended pipe runs straight through. Notify-on-completion + live progress
    surface via /jobs and the profile-bar segment.

    NOTE: an *agent* step in a background pipe shares the session agent/history;
    the isolated-session model for concurrent agent work is III's Fleet, not this
    wrapper — background pipes are intended for shell-heavy "build/test" work."""
    from xlii.jobs import KIND_TASK, get_registry

    console = ctx["console"]
    reg = get_registry(ctx.get("state"))
    if reg is None:
        console.print("[yellow]/tasks --background needs a live session[/yellow] "
                      "[dim](run inside xlii code/chat)[/dim]")
        return

    confirm_shell = bool(defaults.get("confirm_shell", True))
    carry_max = defaults.get("carry_max_chars", T.DEFAULT_CARRY_MAX_CHARS)

    def _job() -> "T.PipelineOutcome":
        return T.run_pipeline(
            pipeline, ctx,
            carry0=carry0,
            keep_going=keep_going,
            yes=yes,
            confirm_shell=confirm_shell,
            carry_max_chars=carry_max,
            emit=None,        # no live per-step console spam from a detached job
            run=run,
            xli_dir=xli_dir,
            args=args,
        )

    job_id = reg.dispatch(KIND_TASK, pipeline.name or "inline", _job)
    console.print(
        f"[green]▶ background job {job_id}[/green] "
        f"[dim]— {pipeline.name or 'inline'} · /jobs show {job_id} · /tasks status[/dim]"
    )


def spawn_saved_task(state: Any, name: str, *, yes: bool = False) -> Optional[str]:
    """Load a saved pipeline and dispatch it as a background job (JobSink seam).

    Same engine as ``/tasks run --background <name>``: session JobRegistry,
    non-interactive confirms, on-disk TaskRun when a project is present. Returns
    the job id, or ``None`` when the session/project/pipeline isn't available.
    The pane layer's :data:`~xlii.panes.SPAWN_JOB` / ``AppJobSink`` is the first
    client — it never runs the pipeline itself.
    """
    from xlii.jobs import KIND_TASK, get_registry

    if state is None or not name:
        return None
    project = getattr(state, "project", None)
    xli_dir = getattr(project, "xli_dir", None) if project is not None else None
    if xli_dir is None:
        return None
    xli_dir = Path(xli_dir)
    try:
        pipeline = T.load_pipeline(xli_dir, name)
    except T.TaskError:
        return None
    if not hasattr(state, "as_context_dict"):
        return None
    console = getattr(state, "console", None)
    ctx: dict[str, Any] = {**state.as_context_dict(), "console": console, "state": state}
    errs = T.validate_pipeline(pipeline, ctx)
    if errs:
        return None
    reg = get_registry(state)
    if reg is None:
        return None
    defaults = T.resolve_tasks_defaults(ctx)
    keep_going = bool(defaults.get("keep_going"))
    if _session_freeball(ctx):
        yes = True
    run = T.new_run(pipeline, carry0="", keep_going=keep_going)
    confirm_shell = bool(defaults.get("confirm_shell", True))
    carry_max = defaults.get("carry_max_chars", T.DEFAULT_CARRY_MAX_CHARS)

    def _job() -> "T.PipelineOutcome":
        return T.run_pipeline(
            pipeline, ctx,
            carry0="",
            keep_going=keep_going,
            yes=yes,
            confirm_shell=confirm_shell,
            carry_max_chars=carry_max,
            emit=None,
            run=run,
            xli_dir=xli_dir,
        )

    return reg.dispatch(KIND_TASK, pipeline.name or name, _job)


def _do_resume(ctx: dict[str, Any]) -> None:
    console = ctx["console"]
    xli_dir = _xli_dir(ctx)
    if xli_dir is None:
        console.print("[dim](no project — nothing to resume)[/dim]")
        return
    run = T.latest_run(xli_dir, statuses={"failed", "active"})
    if run is None:
        console.print("[dim](no interrupted pipeline to resume)[/dim]")
        return
    pipeline = run.pipeline()
    console.print(f"[cyan][tasks] resuming {run.name} at step {run.cursor + 1}/{len(pipeline.steps)}[/cyan]")
    defaults = T.resolve_tasks_defaults(ctx)
    outcome = T.run_pipeline(
        pipeline, ctx,
        carry0=run.carry,
        start_index=run.cursor,
        keep_going=run.keep_going or bool(defaults.get("keep_going")),
        yes=_session_freeball(ctx),   # freeball resumes gates-down too (rail via confirm_shell)
        confirm_shell=bool(defaults.get("confirm_shell", True)),
        carry_max_chars=defaults.get("carry_max_chars", T.DEFAULT_CARRY_MAX_CHARS),
        emit=_make_emitter(console),
        run=run,
        xli_dir=xli_dir,
    )
    _report_result(console, outcome)


def _report_result(console: Any, outcome: T.PipelineOutcome) -> None:
    if outcome.ok and not outcome.had_errors:
        console.print("[green]✓ pipeline complete[/green]")
    elif outcome.ok and outcome.had_errors:
        console.print("[yellow]pipeline completed with errors (--keep-going)[/yellow]")
    else:
        where = "" if outcome.failed_index is None else f" at step {outcome.failed_index + 1}"
        console.print(f"[red]✗ pipeline stopped{where}[/red] [dim](/tasks resume to continue)[/dim]")
    console.print("[dim]result carry:[/dim]")
    console.print(outcome.carry or "[dim](empty)[/dim]", markup=not outcome.carry)


def _warn_chat_shell(ctx: dict[str, Any], pipeline: T.Pipeline, yes: bool) -> None:
    if yes:
        return
    state = ctx.get("state")
    scope = ctx.get("command_scope") or getattr(state, "command_scope", None)
    if scope == "chat" and any(s.kind == T.KIND_SHELL for s in pipeline.steps):
        ctx["console"].print(
            "[yellow]heads-up:[/yellow] shell steps run with confirmation in the chat REPL "
            "[dim](pass --yes to skip the per-step prompt)[/dim]"
        )


# --------------------------------------------------------------------------- #
#  list / show / status / cancel
# --------------------------------------------------------------------------- #

def _do_list(ctx: dict[str, Any]) -> None:
    console = ctx["console"]
    xli_dir = _xli_dir(ctx)
    names = T.list_pipelines(xli_dir) if xli_dir is not None else []
    if not names:
        console.print("[dim]no saved pipelines — /tasks new <name> to scaffold one[/dim]")
        return
    console.print("[bold]saved pipelines[/bold]")
    bound = ""
    try:
        from xlii.session_boot import bound_startup_task

        state = ctx.get("state")
        root = getattr(getattr(state, "project", None), "project_root", None) if state is not None else None
        bound = bound_startup_task(root)
    except Exception:
        bound = ""
    for name, origin in T.list_pipeline_entries(xli_dir):
        klass = T.peek_task_class(T.pipeline_file(xli_dir, name))
        tag = T.listing_badge(origin, klass)
        bits = [t for t in (tag, "startup" if bound and name == bound else "") if t]
        badge = f" [dim]{' · '.join(bits)}[/dim]" if bits else ""
        console.print(f"  · [cyan]{name}[/cyan]{badge}")


def _do_show(name: str, ctx: dict[str, Any]) -> None:
    console = ctx["console"]
    xli_dir = _xli_dir(ctx)
    if not name:
        console.print("[dim]usage:[/dim] [cyan]/tasks show <name>[/cyan]")
        return
    if xli_dir is None:
        console.print("[dim](no project)[/dim]")
        return
    try:
        pipeline = T.load_pipeline(xli_dir, name)
    except T.TaskError as e:
        console.print(f"[red]/tasks: {e}[/red]")
        return
    _print_plan(console, pipeline)
    last = T.latest_run(xli_dir)
    if last is not None and last.name == pipeline.name:
        console.print(f"[dim]last run: {last.status} · step {last.cursor}/{len(last.steps)} "
                      f"· {last.updated_at}[/dim]")


def _do_status(ctx: dict[str, Any]) -> None:
    console = ctx["console"]
    xli_dir = _xli_dir(ctx)
    run = T.latest_run(xli_dir) if xli_dir is not None else None
    if run is None:
        console.print("[dim](no pipeline runs yet)[/dim]")
        return
    console.print(f"[bold]{run.name}[/bold] — {run.status} · step {run.cursor}/{len(run.steps)}")
    if run.last_error:
        console.print(f"  [red]{_short(run.last_error)}[/red]")
    if run.carry:
        console.print(f"  [dim]carry: {_short(run.carry)}[/dim]")


def _do_cancel(ctx: dict[str, Any]) -> None:
    console = ctx["console"]
    xli_dir = _xli_dir(ctx)
    run = T.latest_run(xli_dir, statuses={"failed", "active"}) if xli_dir is not None else None
    if run is None:
        console.print("[dim](no active pipeline to cancel)[/dim]")
        return
    run.status = "cancelled"
    T.save_run(xli_dir, run)
    console.print(f"[dim][tasks] cancelled {run.name}[/dim]")


# --------------------------------------------------------------------------- #
#  new / edit
# --------------------------------------------------------------------------- #

def _eat_flag(s: str, flag: str) -> tuple[str, Optional[str]]:
    """Pull ``--flag WORD`` out of *s*. Returns (remainder, value|None)."""
    m = re.search(rf"--{re.escape(flag)}\s+(\S+)", s)
    if not m:
        return s, None
    return (s[: m.start()] + s[m.end():]).strip(), m.group(1)


def _parse_new(rest: str) -> dict[str, Any]:
    """``<name> [--from "…"] [--shape …] [--clone …]`` plus shape extras."""
    description = None
    s = rest
    m = re.search(r"--from\s+(.*)$", s, re.DOTALL)
    if m:
        description = m.group(1).strip()
        if len(description) >= 2 and description[0] == description[-1] and description[0] in "'\"":
            description = description[1:-1]
        s = s[: m.start()].strip()
    s, shape = _eat_flag(s, "shape")
    s, clone = _eat_flag(s, "clone")
    s, param = _eat_flag(s, "param")
    s, branches = _eat_flag(s, "branches")
    s, arms = _eat_flag(s, "arms")
    s, policy = _eat_flag(s, "policy")
    name = s.strip().split()[0] if s.strip() else ""
    return {
        "name": name,
        "description": description,
        "shape": shape,
        "clone": clone,
        "param": param,
        "branches": branches,
        "arms": arms,
        "policy": policy,
    }


def _do_new(rest: str, ctx: dict[str, Any]) -> None:
    console = ctx["console"]
    xli_dir = _xli_dir(ctx)
    if xli_dir is None:
        console.print("[dim](no project — run inside an xlii project)[/dim]")
        return
    spec = _parse_new(rest)
    name = spec["name"]
    if not name:
        console.print(
            "[dim]usage:[/dim] [cyan]/tasks new <name>[/cyan] "
            "[dim][--from \"…\" | --shape linear|params|verdict|rc|split | --clone <stock>][/dim]"
        )
        return

    modes = [k for k in ("description", "shape", "clone") if spec.get(k)]
    if len(modes) > 1:
        console.print("[red]/tasks new: use only one of --from, --shape, --clone[/red]")
        return

    if spec["description"]:
        _new_from_description(name, spec["description"], ctx, xli_dir)
        return

    if spec["clone"]:
        from xlii.task_shapes import clone_stock

        try:
            path = clone_stock(xli_dir, name, spec["clone"])
        except T.TaskError as e:
            console.print(f"[yellow]{e}[/yellow]")
            return
        console.print(
            f"[green]✓ cloned[/green] {spec['clone']} → {path} "
            f"[dim]— /tasks show {name}[/dim]"
        )
        return

    if spec["shape"]:
        from xlii.task_shapes import write_shaped

        opts = {}
        if spec["param"]:
            opts["param"] = spec["param"]
        if spec["branches"]:
            opts["branches"] = spec["branches"]
        if spec["arms"]:
            opts["arms"] = spec["arms"]
        if spec["policy"]:
            opts["policy"] = spec["policy"]
        try:
            path = write_shaped(xli_dir, name, spec["shape"], **opts)
            T.load_pipeline(xli_dir, name)
        except T.TaskError as e:
            console.print(f"[yellow]{e}[/yellow]")
            return
        console.print(
            f"[green]✓ scaffolded[/green] {path} "
            f"[dim]shape {spec['shape']} — /tasks show {name} · /tasks edit {name}[/dim]"
        )
        return

    try:
        path = T.scaffold_pipeline(xli_dir, name)
    except T.TaskError as e:
        console.print(f"[yellow]{e}[/yellow]")
        return
    console.print(f"[green]✓ scaffolded[/green] {path} [dim]— /tasks edit {name} to fill it in[/dim]")


def _new_from_description(name: str, description: str, ctx: dict[str, Any], xli_dir: Path) -> None:
    """Agent-drafted authoring (tasks proposal Mode 3): one structured turn drafts
    the ``.toml``, which is then opened for review — it never auto-runs.

    NOTE (interaction-layer-II Phase 5): the live-editor surface is Vector A2's
    seam (#2); until that lands we open ``$EDITOR`` exactly as the base proposal
    specifies. The review-before-run guarantee is identical either way.
    """
    console = ctx["console"]
    agent = ctx.get("agent") or getattr(ctx.get("state"), "agent", None)
    if agent is None:
        console.print("[red]/tasks new --from needs a live agent session[/red]")
        return
    console.print(f"[dim]drafting pipeline '{name}' from your description…[/dim]")
    try:
        toml_text = _draft_pipeline_toml(agent, name, description)
    except T.TaskError as e:
        console.print(f"[red]/tasks: drafting failed: {e}[/red]")
        return
    path = T.write_pipeline_toml(xli_dir, name, toml_text)
    # Validate the draft; warn (don't fail) so the user can fix it in the editor.
    try:
        T.load_pipeline(xli_dir, name)
    except T.TaskError as e:
        console.print(f"[yellow]draft needs a fix before it will run: {e}[/yellow]")
    console.print(f"[green]✓ drafted[/green] {path} [dim]— opening for review (never auto-runs)[/dim]")
    _open_editor(console, path)


_DRAFT_SYSTEM = (
    "You translate a task description into an xlii /tasks pipeline TOML file. "
    "Output ONLY the TOML, no prose, no code fences.\n"
    "\n"
    "BASE SCHEMA — a top-level `name` and `description`, optional `class` "
    "(`system` is the product/ops class — clone those to learn the shape), then one or more [[step]] "
    "tables. Each [[step]] has EXACTLY ONE of: `run` (a shell command), `ask` (an "
    "agent prompt), or `slash` (a /command line). Each step's text output — the "
    "carry — flows into the next; reference it with {{prev}} (shell-quoted in shell "
    "steps) or {{prev:raw}} (verbatim). A linear pipe needs nothing more; only add "
    "the features below when the task actually branches, takes input, or fans out.\n"
    "\n"
    "PARAMETERS — declare inputs with `[params.<name>]` tables (fields: `required` "
    "= true, `default`, `enum = [...]`, `help`) and reference them anywhere as "
    "{{<name>}}. They bind from argv (positional, or name=/--name=). `prev` is "
    "reserved. Example:\n"
    "  [params.base]\n"
    "  default = \"HEAD~1\"\n"
    "  [[step]]\n"
    "  run = \"git diff {{base}}\"\n"
    "\n"
    "ROUTING needs step ids — give any step you route to (or from) an `id`.\n"
    "\n"
    "SHELL-RC BRANCH (by exit code) — on a `run` step, `on_success = \"<id>\"` and/or "
    "`on_failure = \"<id>\"` jump by exit status. NOTE: this is a one-sided forward "
    "skip — after the jump the runner keeps going linearly, so it expresses "
    "'on failure, ALSO do X' cleanly but NOT a clean either/or. For a real "
    "either/or, use a verdict.\n"
    "\n"
    "AGENT VERDICT BRANCH (clean either/or) — an `ask` step ends its reply with a "
    "FINAL line that is exactly `TASK+ {\"branch\": \"<label>\"}`, then [[edge]] "
    "tables route it: `from = \"<ask-id>\"`, `when = { branch = \"<label>\" }`, "
    "`to = \"<id>\"`. Cover EVERY label you tell the agent to emit with an edge — an "
    "unmatched or missing verdict fails the run closed. After a routed arm runs the "
    "pipe stops. Example:\n"
    "  [[step]]\n"
    "  id = \"classify\"\n"
    "  ask = \"\"\"…decide. FINAL line exactly one of:\n"
    "  TASK+ {\"branch\": \"clean\"}\n"
    "  TASK+ {\"branch\": \"dirty\"}\"\"\"\n"
    "  [[edge]]\n"
    "  from = \"classify\"\n"
    "  when = { branch = \"clean\" }\n"
    "  to = \"clean_step\"\n"
    "\n"
    "PARALLEL FAN-OUT (split/join) — a step with `id`, `split = [\"a\",\"b\"]`, "
    "`join = \"<id>\"`, and `policy = \"all\"|\"any\"|\"first_ok\"` runs branch ids "
    "CONCURRENTLY then continues at the join. Branch ids MUST be shell (`run`) "
    "steps, defined as their own [[step]] tables, and must not be reached any other "
    "way. The join step sees each branch's output as {{prev}} (with "
    "`### arm <id> (<status>)` headers) and individually as {{arm.<id>}}.\n"
    "\n"
    "Prefer the simplest form that fits. Output the TOML now."
)


def _fallback_last_assistant_text(agent: Any) -> str:
    # Best-effort local fallback to avoid depending on private APIs in xlii.tasks.
    # Prefer explicit accessor methods if present.
    getter = getattr(agent, "last_assistant_text", None)
    if callable(getter):
        try:
            text = getter()
        except Exception:  # noqa: BLE001
            text = ""
        return text or ""

    # Common in chat-like agents: a `messages` list of role/content objects.
    messages = getattr(agent, "messages", None)
    if isinstance(messages, list):
        for msg in reversed(messages):
            role = None
            content = ""
            if isinstance(msg, dict):
                role = msg.get("role")
                content = msg.get("content") or ""
            else:
                role = getattr(msg, "role", None)
                content = getattr(msg, "content", "") or ""
            if role == "assistant" and isinstance(content, str) and content.strip():
                return content

    return ""


def _draft_pipeline_toml(agent: Any, name: str, description: str) -> str:
    user_prompt = (
        f"Name the pipeline `{name}`.\n"
        f"Task description: {description}\n\nReturn the TOML now."
    )
    text = ""
    _dirty = False
    _stats = None
    try:
        text, _dirty, _stats = agent.run_turn(user_prompt, system=_DRAFT_SYSTEM)
    except TypeError:
        try:
            text, _dirty, _stats = agent.run_turn(user_prompt, _DRAFT_SYSTEM)
        except TypeError:
            prompt = f"{_DRAFT_SYSTEM}\n\n{user_prompt}"
            text, _dirty, _stats = agent.run_turn(prompt)
    if not text:
        text = _fallback_last_assistant_text(agent)
    return _strip_code_fence(text or "").strip() + "\n"


def _strip_code_fence(text: str) -> str:
    m = re.match(r"^\s*```(?:toml)?\s*\n(.*?)\n```\s*$", text, re.DOTALL)
    return m.group(1) if m else text


def _do_edit(name: str, ctx: dict[str, Any]) -> None:
    console = ctx["console"]
    xli_dir = _xli_dir(ctx)
    if not name:
        console.print("[dim]usage:[/dim] [cyan]/tasks edit <name>[/cyan]")
        return
    if xli_dir is None:
        console.print("[dim](no project)[/dim]")
        return
    path = T.pipeline_path(xli_dir, name)
    if not path.exists():
        console.print(f"[yellow]no pipeline {name} — /tasks new {name} to scaffold it first[/yellow]")
        return
    _open_editor(console, path)


def _open_editor(console: Any, path: Path) -> None:
    from xlii.editor import open_for_edit

    try:
        open_for_edit(path)
    except (OSError, RuntimeError) as e:
        console.print(f"[red]could not open editor: {e}[/red] [dim]({path})[/dim]")


# --------------------------------------------------------------------------- #
#  dispatch + registration
# --------------------------------------------------------------------------- #

_USAGE = (
    "[dim]usage:[/dim] [cyan]/tasks run <pipe|name> [args][/cyan] · "
    "[cyan]list[/cyan] · [cyan]show <name>[/cyan] · "
    "[cyan]new <name> [--shape|--clone|--from][/cyan] · "
    "[cyan]edit <name>[/cyan] · [cyan]resume[/cyan] · [cyan]status[/cyan] · [cyan]cancel[/cyan]"
)


def _tasks_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx.get("console")
    if console is None:
        return True
    parts = line.split(maxsplit=2)
    _s1 = line.split(maxsplit=1)
    tail = _s1[1] if len(_s1) > 1 else ""
    sub = parts[1].strip().lower() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""

    if sub in ("", "help", "-h", "--help"):
        console.print(_USAGE)
        return True
    if sub == "run":
        _do_run(rest, ctx)
    elif sub == "list":
        _do_list(ctx)
    elif sub == "show":
        _do_show(rest, ctx)
    elif sub == "status":
        _do_status(ctx)
    elif sub == "resume":
        _do_resume(ctx)
    elif sub == "cancel":
        _do_cancel(ctx)
    elif sub == "new":
        _do_new(rest, ctx)
    elif sub == "edit":
        _do_edit(rest, ctx)
    else:
        # Bare `/tasks 'a |> b'` (no explicit `run`) is the common case — treat the
        # whole remainder as a run target so the throwaway form needs no verb.
        if T.PIPE_SEP in tail:
            _do_run(tail, ctx)
        else:
            console.print(f"[yellow]unknown /tasks subcommand: {sub}[/yellow]")
            console.print(_USAGE)
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="tasks",
            aliases=["task"],  # singular routes to the same place (like exit/quit)
            handler=_tasks_handler,
            description="Run a pipe of steps (shell · ?agent · /slash), carry flows step→step.",
            usage="/tasks run '<cmd> |> ?prompt |> /slash' [--dry-run] [--yes] [--keep-going] [--background]",
            category="general",
            repls=["code", "chat"],
        )
    )
