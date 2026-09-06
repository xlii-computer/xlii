"""Tool-call execution — the dispatch half of the orchestrator turn.

`Agent.run_turn` owns the model loop and history; everything about *running* the
tool calls it comes back with — argument parsing, the parallel-safe/mutator-barrier
segmentation, worker sub-agent dispatch, and emitting each result to the console —
lives here as `_ToolDispatchMixin`, which `Agent` inherits. Pure code motion out of
agent.py: these stay instance methods (`self` is the Agent), so behavior and the
call sites in run_turn are unchanged. `_renderer()` deliberately stays on Agent —
the streaming path uses it too.

Also hosts `run_headless_turn` (godzilla-mothra B5): THE headless body
primitive — one `ask --session`-style turn (seed → run → recover → persist)
with the typed event stream mirrored to a caller-supplied sink and no tty
assumptions. The WS head (`xlii.ws_server`) and any future body (the node
daemon) call it instead of shelling out to `xlii ask`.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Optional

from xlii.agent_render import RendererTap, _format_tool_preview, _tool_arg_preview
from xlii.agent_stats import CallStats, TurnStats
from xlii.pool import is_auth_failure
from xlii.tools import PARALLEL_SAFE, ToolContext, auto_deny, confirm_override
from xlii.turn_events import MetaMessage, ToolFinished
from xlii.shell_run import styled_enabled, styled_events
from xlii.worker_agent import WorkerAgent


def _last_user_text(history: list[dict[str, Any]]) -> str:
    """The most recent user message's text (handles multimodal parts arrays)."""
    for msg in reversed(history or []):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts = [
                p.get("text", "") for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            return " ".join(t for t in parts if t).strip()
    return ""


def _format_findings_digest(res: Any) -> str:
    """Render a DeepSearchResult's findings for the turn model to synthesize."""
    lines: list[str] = [f"Deep-search findings for: {res.question}"]
    if res.partial:
        lines.append("(note: some sub-queries failed — coverage is partial)")
    if res.cancelled:
        lines.append("(note: search was cancelled early — partial results)")
    sources: list[str] = list(res.citations)
    src_index = {u: i + 1 for i, u in enumerate(sources)}
    for f in res.findings:
        if not f.ok:
            lines.append(f"\n### {f.subquery.query}\n(failed: {f.error})")
            continue
        tags = "".join(f"[{src_index[c]}]" for c in f.citations if c in src_index)
        lines.append(f"\n### {f.subquery.query} {tags}\n{f.text}")
    if sources:
        lines.append("\nSources:")
        lines.extend(f"[{i + 1}] {u}" for i, u in enumerate(sources))
    lines.append(
        "\nWrite the user a clear answer to their question from these findings, "
        "citing inline as [n]. Flag conflicts and thin coverage; don't invent sources."
    )
    return "\n".join(lines)


class _ToolDispatchMixin:
    """The tool-call execution + result-emission methods of `Agent`.

    Mixed into `Agent`; every `self.*` here resolves against the Agent instance
    (history, console, pool, cfg, project, _renderer)."""

    def _execute_tool_batch(
        self,
        tool_calls,
        ctx: ToolContext,
        stats: TurnStats,
        allowed_tools: Optional[set[str]] = None,
    ) -> None:
        """Run a batch of tool calls.

        Strategy: the batch executes in declaration order, segment by segment.
        Runs of consecutive parallel-safe tools (reads, greps, searches,
        dispatch_subagent) execute concurrently within their segment; any
        mutating tool (write, edit, bash) is its own sequential segment and
        acts as a barrier. A read declared AFTER a write therefore always
        observes the write — the old pre-execute-all-reads strategy ran reads
        before same-batch mutations. Results append to history in the
        original tool_calls order so the model sees a stable transcript.

        ``allowed_tools`` (plan-write-domain P0) is the set of tool names
        advertised to the model this turn; a call outside it is refused at
        dispatch instead of executed, mirroring WorkerAgent's role boundary.
        None = no enforcement (legacy call sites / direct test drivers).
        """
        # Parse args once. Bad JSON resolves to a recorded error result.
        parsed: list[tuple[str, dict | None, str | None]] = []
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
                parsed.append((tc.function.name, args, None))
            except json.JSONDecodeError as e:
                parsed.append((tc.function.name, None, f"invalid JSON arguments: {e}"))

        results: dict[int, str] = {}
        errors: dict[int, bool] = {}
        announced_parallel: set[int] = set()
        refused_unadvertised: set[int] = set()

        def _run_parallel_segment(indices: list[int]) -> None:
            worker_count = sum(1 for i in indices if parsed[i][0] == "dispatch_subagent")
            if len(indices) > 1:
                tag = f"{len(indices)} tool(s) in parallel"
                if worker_count:
                    tag += f" ({worker_count} worker(s))"
                if styled_enabled():
                    self._renderer().emit(MetaMessage(tag))
                else:
                    self.console.print(f"  [magenta]⇉[/magenta] {tag}")
                announced_parallel.update(indices)
            max_workers = min(self.cfg.max_parallel_workers, len(indices))
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {
                    ex.submit(self._run_one_safe_tool, parsed[i][0], parsed[i][1], ctx): i
                    for i in indices
                }
                for fut in as_completed(futures):
                    i = futures[fut]
                    try:
                        text, is_err, wcall = fut.result()
                    except Exception as e:
                        text, is_err, wcall = (
                            f"tool raised: {type(e).__name__}: {e}", True, None,
                        )
                    results[i] = text
                    errors[i] = is_err
                    if wcall is not None:
                        stats.workers.absorb(wcall)
            stats.workers_dispatched += worker_count

        def _run_sequential(i: int) -> None:
            name, args, _ = parsed[i]
            from xlii.tools import get_tool_fn
            fn = get_tool_fn(name)
            if fn is None:
                results[i], errors[i] = f"unknown tool: {name}", True
                self.console.print(f"  [red]✗[/red] {name}: unknown")
                return
            self._announce_tool(name, args)
            try:
                r = fn(ctx, args)
                results[i], errors[i] = r.content, r.is_error
                shell_ev = getattr(r, "shell", None)
                if shell_ev is not None:
                    self.session.last_shell = shell_ev
                    if styled_enabled():
                        # One bash presentation: the same ShellBlock a human sees.
                        self._renderer().emit(shell_ev)
                else:
                    self._emit_tool(name, args, results[i], errors[i])
            except Exception as e:
                results[i], errors[i] = f"tool raised: {type(e).__name__}: {e}", True
                self.console.print(f"  [red]✗[/red] {name}: {e}")

        # Execute in order: batch up consecutive parallel-safe calls, flush
        # the batch before any mutator runs.
        pending_parallel: list[int] = []
        for i, (name, args, parse_err) in enumerate(parsed):
            if parse_err is not None:
                continue  # recorded below
            if allowed_tools is not None and name not in allowed_tools:
                # The model called a tool that was never advertised this turn
                # (mode-gated palette). Refuse at dispatch — never execute.
                results[i] = (
                    f"tool '{name}' was not in this turn's advertised palette "
                    "(the active mode gates the toolset) — refused, not run"
                )
                errors[i] = True
                refused_unadvertised.add(i)
                continue
            if name in PARALLEL_SAFE:
                pending_parallel.append(i)
                continue
            if pending_parallel:
                _run_parallel_segment(pending_parallel)
                pending_parallel = []
            _run_sequential(i)
        if pending_parallel:
            _run_parallel_segment(pending_parallel)

        # Append results to history in original order.
        for i, tc in enumerate(tool_calls):
            stats.tool_calls += 1
            name, args, parse_err = parsed[i]

            if parse_err is not None:
                result_text, is_err = parse_err, True
                self.console.print(f"  [red]✗[/red] {name}: bad JSON")
            else:
                result_text = results.get(i, f"tool did not run: {name}")
                is_err = errors.get(i, True)
                if i in announced_parallel:
                    suffix = " (worker)" if name == "dispatch_subagent" else " (parallel)"
                    self._emit_tool(name, args, result_text, is_err, suffix=suffix)
                elif name in PARALLEL_SAFE or i in refused_unadvertised:
                    self._emit_tool(name, args, result_text, is_err)

            self.history.append(
                {"role": "tool", "tool_call_id": tc.id, "content": result_text}
            )
            try:
                from xlii.hooks import run_hooks
                run_hooks(self.project.xli_dir, "post-tool",
                          {"tool": name, "is_error": is_err}, console=self.console)
            except Exception:
                # The tool result is already appended; an observer hook must not rewrite the batch.
                pass

    def _run_one_safe_tool(
        self, name: str, args: dict, ctx: ToolContext
    ) -> tuple[str, bool, Optional[CallStats]]:
        """Execute one parallel-safe tool. Returns (content, is_error, worker_call).

        worker_call is non-None only for dispatch_subagent; the caller absorbs
        it into the main turn's worker stats.
        """
        if name == "dispatch_subagent":
            text, wcall = self._run_worker(args)
            return (text, False, wcall)
        if name == "request_deep_search":
            return (self._run_deep_search(args), False, None)
        from xlii.tools import get_tool_fn
        fn = get_tool_fn(name)
        if fn is None:
            return (f"unknown tool: {name}", True, None)
        try:
            r = fn(ctx, args)
            return (r.content, r.is_error, None)
        except Exception as e:
            return (f"tool raised: {type(e).__name__}: {e}", True, None)

    def _run_worker(self, args: dict[str, Any]) -> tuple[str, CallStats]:
        task = args.get("task", "").strip()
        if not task:
            return ("dispatch_subagent: 'task' is required", CallStats())
        where = str(args.get("where") or "").strip()
        if where:
            from xlii.farm_post import LOCAL_WHERE, JobError as FarmJobError
            from xlii.farm_post import resolve_where

            if where.lower() not in LOCAL_WHERE:
                try:
                    pool = resolve_where(self.cfg, where)
                except FarmJobError as e:
                    return (f"dispatch_subagent: {e}", CallStats())
                if pool:
                    return self._post_farm_ad(args, pool)
        context = args.get("context")
        from xlii.tools import WORKER_ROLES
        # A3: explore is the default — most dispatches are investigation, and it
        # keeps shell surface/noise out unless the orchestrator asks for it.
        role = (args.get("role") or "explore").strip().lower()
        worker_writes = role == "lab"
        if role not in WORKER_ROLES:
            role = "explore"
            worker_writes = False
        lab_project = (
            getattr(self, "lab_project", None)
            or getattr(self.session, "lab_project", None)
        )
        if worker_writes:
            from xlii.project_paths import is_home_desk_project

            hire = getattr(self.session, "hire", "none")
            if (
                lab_project is None
                or is_home_desk_project(lab_project)
                or hire != "write"
            ):
                return (
                    "dispatch_subagent: no desk to write to — "
                    "switch into a folder first.",
                    CallStats(),
                )
        from xlii.plugin import live_subscriptions

        # Gigwork (G2): `gig=` pins a configured foreign brain for this pass.
        # G3: `gaggle=` runs a named preset (home + gig, merge). The
        # orchestrator may only hire names in gigwork.defaults.allow —
        # errors come back as tool text (the model can rephrase/re-dispatch),
        # and there is NO silent home fallback pretending to be the gig.
        gig = (args.get("gig") or "").strip()
        gaggle = (args.get("gaggle") or "").strip()
        if gig and gaggle:
            return (
                "dispatch_subagent: pass gig= or gaggle=, not both",
                CallStats(),
            )
        if gaggle:
            return self._run_gaggle(gaggle, task, context)
        gig_backend = None
        if gig:
            from xlii.chat_backend import GigError, gig_allowlist, resolve_gig_backend
            allow = gig_allowlist(self.cfg)
            if gig not in allow:
                allowed = ", ".join(sorted(allow)) or "(none — gigwork.defaults.allow is empty)"
                return (
                    f"dispatch_subagent: gig {gig!r} is not in gigwork.defaults.allow "
                    f"— hireable: {allowed}",
                    CallStats(),
                )
            try:
                gig_backend = resolve_gig_backend(self.cfg, gig)
            except GigError as e:
                return (f"dispatch_subagent: {e}", CallStats())

        if gig_backend is not None:
            # Foreign brain: no pool draw (the chat spend isn't an xAI key),
            # and foreign failures never feed the xAI key-quarantine map.
            worker_clients = self.clients
        else:
            worker_clients = self.pool.acquire()
        # K2: a writer never falls through to the persona's memory project.
        project = lab_project if worker_writes else self.project
        worker = WorkerAgent(
            clients=worker_clients,
            project=project,
            cfg=self.cfg,
            subscribed_plugins=live_subscriptions(project, self.session),
            role=role,
            worker_writes=worker_writes,
            chat_backend=gig_backend,
        )
        try:
            text, wcall = worker.run(task, context=context)
            if gig_backend is None:
                self.pool.report_success(worker_clients)
        except Exception as e:
            if gig_backend is not None:
                return (
                    f"gigwork[{gig_backend.label}] failed: {type(e).__name__}: {e}",
                    CallStats(),
                )
            if is_auth_failure(e):
                self.pool.report_auth_failure(worker_clients)
            raise
        from xlii.cost import format_cost, format_tokens
        cost_part = (
            f" · {format_cost(wcall.cost_usd)}" if wcall.cost_usd is not None else ""
        )
        who = (
            f"gigwork[{gig_backend.label}]" if gig_backend is not None
            else f"worker[{worker_clients.label}]"
        )
        header = (
            f"--- {who} · {wcall.model} · "
            f"{wcall.iterations} iter · {format_tokens(wcall.total_tokens)}{cost_part} ---"
        )
        return (f"{header}\n{text}", wcall)

    def _post_farm_ad(self, args: dict[str, Any], pool: str) -> tuple[str, CallStats]:
        """Hire by posting an ad to a pool instead of running a local worker."""
        from xlii.farm import Budget, JobError as FarmJobError
        from xlii.farm_post import post_to_pool

        task = str(args.get("task") or "").strip()
        job = str(args.get("job") or "explore").strip() or "explore"
        usd = args.get("budget")
        try:
            max_usd = float(usd) if usd is not None and usd != "" else None
        except (TypeError, ValueError):
            max_usd = None
        budget = Budget(max_usd=max_usd)
        try:
            ticket = post_to_pool(
                self.cfg,
                job=job,
                task=task,
                pool=pool,
                budget=budget,
                context=str(args.get("context") or ""),
            )
        except FarmJobError as e:
            return (f"dispatch_subagent: {e}", CallStats())
        usd_s = "none" if budget.max_usd is None else f"{budget.max_usd:g}"
        return (
            f"posted ad {ticket.id} to pool {pool} "
            f"(job={ticket.job} budget max_usd={usd_s} "
            f"max_iters={budget.max_iters})",
            CallStats(),
        )

    def _run_gaggle(
        self, name: str, task: str, context: Optional[str]
    ) -> tuple[str, CallStats]:
        """G3: run a named gaggle and return the merged answers.

        Foreign members must be on gigwork.defaults.allow (same gate as gig=).
        write: false is locked in the spec; a failed resolve returns tool text.
        """
        from xlii.chat_backend import GigError
        from xlii.jam import orchestrator_may_run_gaggle, run_jam
        from xlii.plugin import live_subscriptions
        from xlii.cost import format_tokens

        try:
            spec = orchestrator_may_run_gaggle(self.cfg, name)
        except GigError as e:
            return (f"dispatch_subagent: {e}", CallStats())
        try:
            result = run_jam(
                name,
                task,
                cfg=self.cfg,
                project=self.project,
                clients=self.clients,
                pool=self.pool,
                context=context,
                subscribed_plugins=live_subscriptions(self.project, self.session),
            )
        except GigError as e:
            return (f"gaggle[{name}] failed: {e}", CallStats())
        stats = CallStats()
        for r in result.results:
            stats.absorb(CallStats(
                model=r.model,
                iterations=r.iterations,
                prompt_tokens=r.prompt_tokens,
                completion_tokens=r.completion_tokens,
            ))
        if result.synth_model:
            stats.model = result.synth_model
        members = " + ".join(m.label for m in spec.members)
        merge_note = (
            f"synth via {result.synth_model}" if result.synth_model else spec.merge
        )
        header = (
            f"--- gaggle[{result.spec.name}] · {members} · {merge_note} · "
            f"{format_tokens(stats.total_tokens)} ---"
        )
        return (f"{header}\n{result.merged}", stats)

    def _run_deep_search(
        self, args: dict[str, Any], *, question: Optional[str] = None
    ) -> str:
        """Execute a coordinated deep search — the heavy tier / escalation hop.

        The turn's own model synthesizes the final answer from the returned
        findings (run with ``synthesize=False`` so there's no second coordinator
        synth). The question is the user's current turn; any ``subqueries``
        warm-start the first wave (the :func:`xlii.chat_router.maybe_escalate`
        hand-off)."""
        from xlii.chat_backend import GigError
        from xlii.deep_search import resolve_investigate_hire, run_deep_search

        subs = args.get("subqueries") or []
        if not isinstance(subs, list):
            subs = []
        subs = [str(s).strip() for s in subs if str(s).strip()]
        reason = str(args.get("reason") or "").strip()
        question = (
            (question or "").strip()
            or str(getattr(self, "_current_turn_question", "") or "").strip()
            or _last_user_text(self.history)
            or reason
            or "(no question)"
        )
        self.console.print(
            "  [bold cyan]⇉ deep search[/bold cyan]"
            + (f" [dim]{reason[:70]}[/dim]" if reason else "")
        )
        gig = str(args.get("gig") or "").strip() or None
        gaggle = str(args.get("gaggle") or "").strip() or None
        try:
            resolve_investigate_hire(self.cfg, gig=gig, gaggle=gaggle)
        except GigError as e:
            return f"request_deep_search: {e}"
        res = run_deep_search(
            question,
            clients=self.clients,
            cfg=self.cfg,
            project=self.project,
            pool=self.pool,
            warm_start=subs or None,
            synthesize=False,
            gig=gig,
            gaggle=gaggle,
            on_log=lambda m: self.console.print(f"  [dim]{m}[/dim]"),
            cancelled=lambda: getattr(self, "_cancel_requested", False),
        )
        return _format_findings_digest(res)

    def _emit_tool_result(
        self, name: str, content: str, is_error: bool, *, suffix: str = ""
    ) -> None:
        """Print the result badge plus a short preview of what came back.

        The preview is purely cosmetic — the full content still flows to the
        model. The shape per tool lives in _format_tool_preview.
        """
        badge = "[red]✗[/red]" if is_error else "[green]✓[/green]"
        self.console.print(f"  {badge} {name}{suffix}")
        for line in _format_tool_preview(name, content, is_error):
            self.console.print(line)

    def _announce_tool(self, name: str, args: dict[str, Any]) -> None:
        if styled_enabled():
            return  # styled mode shows one block per tool on completion
        preview = _tool_arg_preview(name, args)
        self.console.print(f"  [dim]→[/dim] [cyan]{name}[/cyan] {preview}")

    def _emit_tool(
        self,
        name: str,
        args: dict[str, Any] | None,
        content: str,
        is_error: bool,
        *,
        suffix: str = "",
    ) -> None:
        """One tool's result. Styled → a ToolBlock in the unified grammar;
        otherwise the classic `✓/✗ name` + dim `⎿` preview."""
        if styled_enabled():
            self._renderer().emit(
                ToolFinished(
                    name=name,
                    args_preview=_tool_arg_preview(name, args or {}),
                    content=content or "",
                    is_error=is_error,
                )
            )
        else:
            self._emit_tool_result(name, content, is_error, suffix=suffix)


# --------------------------------------------------------------------------- #
#  The headless turn engine (godzilla-mothra B5)
# --------------------------------------------------------------------------- #


def run_headless_turn(
    *,
    project,
    session_id: str,
    prompt: str,
    sink: Callable[[dict[str, Any]], None],
    yolo: bool = False,
    pool=None,
    cfg=None,
) -> int:
    """One ``ask --session`` turn with events mirrored to ``sink``. Returns exit code.

    THE headless body primitive: seed the session's recent turns, run the agent
    turn, mirror the typed event stream (serialized via ``xlii.ws_protocol`` —
    the documented W2 vocabulary: ``user_turn``, renderer events,
    ``assistant_chunk``, ``error``, ``turn_done``) to the body's sink, recover
    the streamed reply, persist the turn. No tty assumptions — the live UI goes
    to a stderr Console exactly like ``xlii ask``; nothing reads stdin.

    Non-yolo behavior: a gated (network/system) tool intent has no approval
    channel on a headless body, so for the duration of the turn the injectable
    ``xlii.tools._confirm`` is held at ``tools.auto_deny`` via
    ``tools.confirm_override`` — gated intents are refused cleanly instead of
    blocking on the body's stdin or leaking the y/N prompt to its stdout. Pass
    ``yolo=True`` to pre-approve them (trusted loopback only).
    """
    from rich.console import Console

    from xlii.agent import Agent, SessionState
    from xlii.client import MissingCredentials
    from xlii.config import GlobalConfig
    from xlii.pool import ClientPool
    from xlii.turn_events import UserTurn
    from xlii.turn_store import (
        SESSION_SEED_TURNS,
        ask_session_turns_dir,
        final_reply_from_history,
        persist_turn,
        seed_history,
    )
    from xlii.ws_protocol import serialize_event

    # A long-lived body (the WS server) builds ONE pool per connection and passes
    # it (+ cfg) so it isn't rebuilt — leaking an httpx client set — every turn.
    # A direct/one-shot caller (tests, `xlii ask`-style) passes neither.
    if cfg is None:
        cfg = GlobalConfig.load()
    if pool is None:
        try:
            pool = ClientPool.from_config(cfg)
        except MissingCredentials as e:
            sink({"type": "error", "message": str(e)})
            sink({"type": "turn_done", "ok": False, "exit_code": 1})
            return 1

    ui = Console(stderr=True)
    agent = Agent(
        pool=pool,
        project=project,
        cfg=cfg,
        console=ui,
        session=SessionState.from_flat(yolo=yolo),
    )
    turns_dir = ask_session_turns_dir(project, session_id)
    seed_history(agent, turns_dir, SESSION_SEED_TURNS)

    sink(serialize_event(UserTurn(prompt)))

    def _on_chunk(text: str) -> None:
        sink({"type": "assistant_chunk", "text": text})

    ui.on_content_chunk = _on_chunk
    tap = RendererTap(agent._renderer(), lambda event: sink(serialize_event(event)))
    agent._renderer_cache = tap

    # styled_events(): the renderer only emits the typed tool/shell/answer
    # events when styled — a headless body mirrors that stream to its sink, so
    # without this the client gets only chunks + done (the tap is installed but
    # every emit is suppressed). Gated intents on a non-yolo turn must not fall
    # through to input() on the body's stdin/stdout — auto-deny them. (The swap
    # is process-global + lock-guarded today, so concurrent connections
    # serialize on the confirm hook; yolo sessions never reach it.)
    try:
        with styled_events(), confirm_override(auto_deny):
            text, _dirty, _stats = agent.run_turn(prompt)
    except Exception as e:
        sink({"type": "error", "message": f"{type(e).__name__}: {e}"})
        sink({"type": "turn_done", "ok": False, "exit_code": 1})
        return 1

    if not text:
        text = final_reply_from_history(agent.history)
    persist_turn(turns_dir, prompt, text or "")
    sink({"type": "turn_done", "ok": True, "exit_code": 0})
    return 0
