"""/delegate — drive external agent harnesses (Cursor, Claude, Codex, Grok).

Unified entry point for harness delegation. Cursor uses a full ACP session;
Claude, Codex, and Grok use headless one-shots. ``/cursor`` is a permanent alias
for ``/delegate cursor``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from rich import box
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from xlii.agent_render import _streaming_tail
from xlii.commands import REPLCommand, register_repl_command
from xlii.harness.delegate import run_delegate
from xlii.harness.acp_session import acp_harness_names
from xlii.harness.detect import harness_label, harness_meta, list_harness_names
from xlii.harness.mcp_context import build_handoff_context
from xlii.harness.session import (
    capture_harness_output,
    get_registry,
    register_session_seams,
    run_session_turn,
)

_USAGE = (
    "[dim]usage: /delegate <harness> [--plan|--ask|--agent] [--model <name>] "
    "[--context] [--reject] <task>[/dim]\n"
    "[dim]  harness: cursor | claude | codex | grok[/dim]\n"
    "[dim]  cursor, grok, claude → ACP session; codex → headless exec[/dim]\n"
    "[dim]  --context forwards xlii's manifest (DeepContext MCP for cursor, grok)[/dim]\n"
    "[dim]  sessions: <harness> new <name> · @<name> [--bg] <task> · ls · on|off · close <name>[/dim]\n"
    "[dim]  --bg runs the turn as a background job (keep working; /jobs to watch)[/dim]"
)

_MODES = {"agent", "plan", "ask"}

# `--bg`/`--background` on a `@name <task>` turn runs it as a session-owned job
# (Vector J) instead of blocking the REPL — strip it from the task wherever it sits.
_BG_RE = re.compile(r"(?:^|\s)(--background|--bg)(?=\s|$)")


def _strip_flag_prefix(tail: str, flag: str) -> str | None:
    """Match ``flag`` exactly or ``flag`` followed by whitespace; not longer tokens."""
    if tail == flag:
        return ""
    if tail.startswith(flag + " "):
        return tail[len(flag):].lstrip()
    return None


def _parse(rest: str) -> tuple[str, str, str | None, str, bool, str, str | None]:
    """Return (harness, mode, model, permission, with_context, task, error)."""
    rest = rest.strip()
    if not rest:
        return ("", "agent", None, "allow", False, "", "harness name required")

    parts = rest.split(None, 1)
    harness = parts[0].lower()
    tail = parts[1].strip() if len(parts) > 1 else ""

    if harness not in list_harness_names():
        return ("", "agent", None, "allow", False, "", f"unknown harness {harness!r}")

    mode = "agent"
    model: str | None = None
    permission = "allow"
    with_context = False

    while True:
        tail = tail.lstrip()
        if tail.startswith("--mode ") or tail.startswith("--model "):
            flag, _, after = tail.partition(" ")
            bits = after.strip().split(None, 1)
            if not bits:
                break
            value, tail = bits[0], (bits[1] if len(bits) > 1 else "")
            if flag == "--mode":
                mode = value
            else:
                model = value
        elif (rest := _strip_flag_prefix(tail, "--plan")) is not None:
            mode, tail = "plan", rest
        elif (rest := _strip_flag_prefix(tail, "--ask")) is not None:
            mode, tail = "ask", rest
        elif (rest := _strip_flag_prefix(tail, "--agent")) is not None:
            mode, tail = "agent", rest
        elif (rest := _strip_flag_prefix(tail, "--context")) is not None:
            with_context, tail = True, rest
        elif (rest := _strip_flag_prefix(tail, "--reject")) is not None:
            permission, tail = "reject", rest
        else:
            break

    task = tail.strip()
    if not task:
        return (harness, mode, model, permission, with_context, "", None)
    if mode not in _MODES:
        return (harness, mode, model, permission, with_context, task, f"unknown mode {mode!r}")
    return (harness, mode, model, permission, with_context, task, None)


def _harness_badge(
    harness: str,
    model: str | None = None,
    session: Any | None = None,
) -> str:
    """Human title for a framed harness answer (`cursor · composer-2.5`, etc.)."""
    if session is not None:
        return _mode_badge(harness, session)
    label = harness_label(harness)
    from xlii.harness.detect import harness_model_selectable

    if model and harness_model_selectable(harness):
        return f"{label} · {model}"
    return label


def emit_harness_answer(console: Any, markdown: str, badge: str) -> None:
    """Framed harness prose — same Panel/Markdown grammar as native answers."""
    from xlii.shell_run import styled_enabled
    from xlii.theme import THEME

    if not markdown.strip():
        return
    if not styled_enabled():
        console.print(markdown, markup=False, highlight=False, soft_wrap=True)
        return
    title = Text()
    parts = badge.split(" · ", 1)
    title.append(parts[0], style=f"{THEME.assistant} bold")
    if len(parts) > 1:
        title.append(f" · {parts[1]}", style=THEME.dim)
    console.print(
        Panel(
            Markdown(markdown),
            title=title,
            title_align="left",
            border_style=THEME.assistant,
            box=box.ROUNDED,
        )
    )


class HarnessStreamRelay:
    """Segment-flush framing for harness ACP event streams (Vector F)."""

    def __init__(
        self,
        console: Any,
        *,
        harness: str,
        model: str | None = None,
        session: Any | None = None,
    ) -> None:
        self._console = console
        self._harness = harness
        self._model = model
        self._session = session
        self._parts: list[str] = []
        self._live: Live | None = None
        self.streamed = False

    def _badge(self) -> str:
        return _harness_badge(self._harness, self._model, self._session)

    def on_event(self, kind: str, update: dict[str, Any]) -> None:
        if kind == "agent_message_chunk":
            content = update.get("content") or {}
            if isinstance(content, dict) and content.get("type") == "text":
                chunk = content.get("text", "")
                if chunk:
                    self._parts.append(chunk)
                    self._update_live()
        elif kind == "tool_call":
            self.flush_segment()
            title = update.get("title") or "tool"
            self._console.print(
                f"\n[dim]· {title} ({update.get('kind', '')})…[/dim]"
            )

    def flush_segment(self) -> None:
        if not self._parts:
            self._stop_live()
            return
        text = "".join(self._parts)
        self._parts.clear()
        self._stop_live()
        if not text.strip():
            return
        self.streamed = True
        emit_harness_answer(self._console, text, self._badge())

    def finish(self) -> None:
        self.flush_segment()

    def _update_live(self) -> None:
        if not getattr(self._console, "supports_live", True):
            return
        if not hasattr(self._console, "clear_live"):
            return
        text = "".join(self._parts)
        if self._live is None:
            self._console.print()
            self._live = Live(
                Text(""),
                console=self._console,
                refresh_per_second=10,
                vertical_overflow="crop",
                transient=True,
            )
            self._live.start()
        self._live.update(_streaming_tail(text, self._console))

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None


def _print_delegate_result(
    console,
    result,
    *,
    project_root: Path,
    harness: str | None = None,
    model: str | None = None,
    acp_streamed: bool = False,
) -> None:
    if result.error:
        console.print(f"\n[yellow]/delegate unavailable:[/yellow] {result.error}")
        return
    for note in result.notes:
        if note.strip():
            console.print(f"[dim]note: {note}[/dim]")
    if result.text and not acp_streamed:
        badge = _harness_badge(harness or result.harness, model)
        emit_harness_answer(console, result.text, badge)
    if result.files_touched:
        console.print("[bold]files changed (uncommitted):[/bold]")
        for f in result.files_touched:
            try:
                rel = Path(f).resolve().relative_to(project_root.resolve())
            except ValueError:
                rel = Path(f)
            console.print(f"  [green]{rel}[/green]")
    if result.stop_reason and result.stop_reason != "end_turn":
        console.print(f"[dim]stop reason: {result.stop_reason}[/dim]")


# Tier 1 / 1.5 session sub-verbs — recognised *before* the one-shot task parse so
# `/cursor new build`, `/cursor @build <task>`, `/cursor on` etc. orchestrate the
# persistent-session registry instead of firing a one-shot delegate.
_SESSION_VERBS = frozenset({"new", "ls", "list", "sessions", "close", "kill", "on", "off", "switch", "use"})


def _parse_session_flags(tokens: list[str]) -> tuple[str, str | None, str, bool, list[str]]:
    """Pull `--plan|--ask|--agent`, `--model <m>`, `--context`, `--reject` out of a
    session verb's token list. Returns (mode, model, permission, with_context, rest)."""
    mode, model, permission, with_context = "agent", None, "allow", False
    rest: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--plan":
            mode = "plan"
        elif tok == "--ask":
            mode = "ask"
        elif tok == "--agent":
            mode = "agent"
        elif tok == "--context":
            with_context = True
        elif tok == "--reject":
            permission = "reject"
        elif tok in ("--model", "--mode") and i + 1 < len(tokens):
            val = tokens[i + 1]
            i += 1
            if tok == "--model":
                model = val
            else:
                mode = val
        else:
            rest.append(tok)
        i += 1
    return mode, model, permission, with_context, rest


def _mode_badge(harness: str, session) -> str:
    """`<harness> · <model>` for the confirmation line — model only for the host
    harness (seam #5: model is cursor's axis alone)."""
    label = harness_label(harness)
    from xlii.harness.detect import harness_model_selectable

    if harness_model_selectable(harness) and (session.model or session.model_id):
        return f"{label} · {session.model or session.model_id}"
    return label


def _handle_swarm(ctx: dict[str, Any], harness: str, tokens: list[str], cmd_token: str, root: Path) -> bool:
    """`<harness> swarm <plan.md> [--vectors a1,c] [--go] [--keep]` (Tier 4).

    Without ``--go`` this is a dry run: parse + preview the vectors. With ``--go``
    it spawns one harness session per vector in its own git worktree, collects
    the results, and prints a summary."""
    console = ctx["console"]
    args = tokens[1:]
    go = "--go" in args
    keep = "--keep" in args
    vectors: list[str] | None = None
    plan_arg: str | None = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--vectors" and i + 1 < len(args):
            vectors = [v for v in args[i + 1].replace(",", " ").split() if v]
            i += 1
        elif a.startswith("--"):
            pass
        elif plan_arg is None:
            plan_arg = a
        i += 1

    if not plan_arg:
        console.print(f"[dim]usage:[/dim] [cyan]{cmd_token} swarm <plan.md> "
                      "[--vectors a1,c] [--go] [--keep][/cyan]")
        return True
    plan_path = Path(plan_arg)
    if not plan_path.is_absolute():
        plan_path = root / plan_arg
    if not plan_path.exists():
        console.print(f"[red]{cmd_token} swarm: plan not found: {plan_arg}[/red]")
        return True

    from xlii.harness.swarm import parse_build_plan, run_build_swarm

    text = plan_path.read_text(errors="replace")
    specs = parse_build_plan(text)
    if vectors:
        want = {v.lower() for v in vectors}
        specs = [s for s in specs if s.name.lower() in want]
    if not specs:
        console.print(f"[yellow]no vectors found in {plan_arg}[/yellow] "
                      "[dim](expected '## Vector <name> — …' headers)[/dim]")
        return True

    if not go:
        n = len(specs)
        console.print(f"[bold]swarm plan:[/bold] {plan_path.name} "
                      f"[dim]({n} vector{'s' if n != 1 else ''} · harness {harness_label(harness)})[/dim]")
        for s in specs:
            owns = (f"  [dim]owns {len(s.owns)} file{'s' if len(s.owns) != 1 else ''}[/dim]"
                    if s.owns else "")
            console.print(f"  [cyan]{s.name}[/cyan] [dim]{s.title}[/dim]{owns}")
        console.print(f"[dim]dry run — pass [/dim][cyan]--go[/cyan][dim] to spawn one "
                      f"{harness_label(harness)} session per vector in its own worktree[/dim]")
        return True

    n = len(specs)
    console.print(f"[bold cyan]swarm:[/bold cyan] {n} vector{'s' if n != 1 else ''} → "
                  f"{harness_label(harness)} sessions in worktrees…")

    def on_progress(phase: str, name: str, outcome) -> None:
        if phase == "start":
            console.print(f"[dim]· {name} starting…[/dim]")
        elif outcome is not None and outcome.error:
            console.print(f"  [red]{name}: {outcome.error}[/red]")
        elif outcome is not None:
            files = len(outcome.files_touched)
            verdict = f" · {outcome.verdict.splitlines()[0]}" if outcome.verdict else ""
            console.print(f"  [green]{name}[/green] [dim]→ {outcome.branch or 'in-place'} "
                          f"({files} file{'s' if files != 1 else ''}){verdict}[/dim]")

    report = run_build_swarm(
        text, project_root=root, harness=harness, vectors=vectors,
        cleanup=not keep, on_progress=on_progress,
    )
    ok = sum(1 for o in report.outcomes if o.error is None)
    console.print(f"[bold]swarm done:[/bold] {ok}/{len(report.outcomes)} vectors ran "
                  f"[dim](id {report.swarm_id})[/dim]")
    if keep:
        console.print("[dim]worktrees kept — `git worktree list` to inspect[/dim]")
    return True


def _dispatch_session_job(
    state: Any, console: Any, harness: str, name: str, session: Any, task: str
) -> bool:
    """Run a `@name <task>` turn as a non-blocking session-owned job (Vector J)
    instead of holding the REPL. The turn runs un-streamed — the full reply prints
    as one block on completion — on the JobRegistry pool; notify + live progress
    via /jobs and the profile-bar segment. Falls back to a foreground turn when
    there's no live job registry (headless / test ctx), so the work never drops."""
    from xlii.jobs import KIND_HARNESS, get_registry as get_job_registry

    reg = get_job_registry(state)
    if reg is None:
        run_session_turn(state, session, task)
        return True
    job_id = reg.dispatch(
        KIND_HARNESS,
        f"{harness} @{name}",
        lambda: run_session_turn(state, session, task, stream=False),
    )
    console.print(
        f"[green]▶ background job {job_id}[/green] "
        f"[dim]— {harness_label(harness)} @{name} · keep working · "
        f"/jobs show {job_id} · /jobs to watch[/dim]"
    )
    return True


def _handle_session_verb(ctx: dict[str, Any], harness: str, tail: str, cmd_token: str, *, project) -> bool:
    """Handle a Tier 1/1.5 session sub-verb. Returns True if one matched (and was
    handled), False to fall through to the one-shot delegate path."""
    console = ctx["console"]
    state = ctx.get("state")
    reg = get_registry(state)
    if reg is None:
        return False  # no stateful registry here (headless ctx) → one-shot path

    tokens = tail.split()
    if not tokens:
        return False
    verb = tokens[0]

    if project is None or getattr(project, "project_root", None) is None:
        # Session verbs need a project root; only intercept if this really is one.
        if verb.lower() in _SESSION_VERBS or verb.startswith("@"):
            console.print(f"[red]{cmd_token}: no project root in this session[/red]")
            return True
        return False
    root = Path(project.project_root)

    # `@name [task]` — run a turn on (or switch to) a named session.
    if verb.startswith("@"):
        name = verb[1:]
        if not name:
            console.print(f"[red]{cmd_token}: name required (e.g. {cmd_token} @build <task>)[/red]")
            return True
        task = tail.split(maxsplit=1)[1].strip() if len(tail.split(maxsplit=1)) > 1 else ""
        background = bool(_BG_RE.search(task))
        if background:
            task = _BG_RE.sub(" ", task).strip()
        session = reg.get(name)
        if session is None or session.harness != harness:
            session = reg.open(harness, name, project_root=root)
            console.print(f"[dim]+ opened {harness_label(harness)} session [cyan]{name}[/cyan][/dim]")
        reg.touch(name)
        if not task:
            console.print(f"[green]✓[/green] active session → [cyan]{name}[/cyan]")
            return True
        if background:
            return _dispatch_session_job(state, console, harness, name, session, task)
        run_session_turn(state, session, task)
        return True

    low = verb.lower()
    if low == "swarm":
        return _handle_swarm(ctx, harness, tokens, cmd_token, root)
    # In foreground mode for this harness, a prefixed `/cursor <task>` (typed out
    # of habit) continues the LIVE session — same coherence as bare input in mode —
    # rather than spawning a fresh, contextless one-shot. Real session verbs
    # (on/off/ls/new/close) still dispatch below; only a non-verb task is routed.
    if low not in _SESSION_VERBS and reg.foreground == harness:
        session = reg.foreground_session()
        if session is not None:
            reg.touch(session.name)
            run_session_turn(state, session, tail.strip())
            return True
    if low not in _SESSION_VERBS:
        return False  # a real one-shot task (no foreground mode for this harness)

    if low == "new":
        rest_tokens = tokens[1:]
        mode, model, permission, with_context, leftover = _parse_session_flags(rest_tokens)
        if not leftover:
            console.print(f"[dim]usage:[/dim] [cyan]{cmd_token} new <name> "
                          "[--plan|--ask] [--model <m>] [--context][/cyan]")
            return True
        name = leftover[0]
        session = reg.open(
            harness, name, project_root=root, mode=mode, model=model,
            permission=permission, with_context=with_context,
        )
        if with_context and harness in ("cursor", "grok"):
            from xlii.harness.mcp_context import ensure_deep_context

            action, _approved = ensure_deep_context(root, harness)
            if action == "added":
                console.print(f"[dim]+ registered xlii-deep-contexts in .{harness}/mcp.json[/dim]")
        ctxbit = "  [dim]+xlii context[/dim]" if with_context else ""
        console.print(
            f"[green]✓[/green] [bold cyan]{_mode_badge(harness, session)} · {mode}[/bold cyan] "
            f"session [cyan]{name}[/cyan] ready{ctxbit} "
            f"[dim]— {cmd_token} @{name} <task> · {cmd_token} on[/dim]"
        )
        if len(leftover) > 1:
            console.print(f"[dim]note: `new` only opens a session — use {cmd_token} @{name} "
                          "<task> to run one[/dim]")
        return True

    if low in ("ls", "list", "sessions"):
        rows = reg.for_harness(harness)
        if not rows:
            console.print(f"[dim](no {harness_label(harness)} sessions — {cmd_token} new <name>)[/dim]")
            return True
        console.print(f"[bold]{harness_label(harness)} sessions:[/bold]")
        for s in rows:
            marks = []
            if reg.foreground == harness and reg.active_name == s.name:
                marks.append("foreground")
            elif reg.active_name == s.name:
                marks.append("active")
            tag = f"  [dim]({', '.join(marks)})[/dim]" if marks else ""
            console.print(f"  [cyan]{s.name}[/cyan] [dim]· {s.status_line()}[/dim]{tag}")
        return True

    if low in ("close", "kill"):
        if len(tokens) < 2:
            console.print(f"[dim]usage:[/dim] [cyan]{cmd_token} close <name>[/cyan]")
            return True
        name = tokens[1]
        if reg.close(name):
            console.print(f"[green]✓[/green] closed session [cyan]{name}[/cyan]")
        else:
            console.print(f"[dim](no session named {name!r})[/dim]")
        return True

    if low in ("switch", "use"):
        if len(tokens) < 2:
            console.print(f"[dim]usage:[/dim] [cyan]{cmd_token} switch <name>[/cyan]")
            return True
        name = tokens[1]
        if reg.get(name) is None:
            console.print(f"[dim](no session named {name!r})[/dim]")
            return True
        reg.touch(name)
        console.print(f"[green]✓[/green] active session → [cyan]{name}[/cyan]")
        return True

    if low == "on":
        mode, model, permission, with_context, leftover = _parse_session_flags(tokens[1:])
        name = leftover[0] if leftover else None
        session = reg.enter_mode(
            harness, name, project_root=root, mode=mode, model=model,
            permission=permission, with_context=with_context,
        )
        console.print(
            f"[green]✓[/green] [bold cyan]{_mode_badge(harness, session)}[/bold cyan] mode — "
            f"bare input drives [cyan]{session.name}[/cyan]; "
            f"[cyan]?[/cyan] still asks xlii · [cyan]{cmd_token} off[/cyan] to leave"
        )
        return True

    if low == "off":
        left = reg.leave_mode()
        if left:
            console.print(f"[green]✓[/green] left [bold cyan]{harness_label(left)}[/bold cyan] mode")
        else:
            console.print("[dim](not in a harness mode)[/dim]")
        return True

    return False


def run_delegate_command(line: str, ctx: dict[str, Any], *, default_harness: str | None = None) -> bool:
    """Shared handler for /delegate and /cursor."""
    console = ctx["console"]
    state = ctx.get("state")
    agent = ctx.get("agent") or getattr(state, "agent", None)
    project = state.project if state else ctx.get("project")

    if project is not None and getattr(project, "xli_dir", None) is not None:
        from xlii.harness.detect import load_local_harnesses

        load_local_harnesses(project.xli_dir)

    parts = line.split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""

    # Session orchestration (Tier 1/1.5) is checked before the one-shot parse.
    cmd_token = line.split()[0] if line.split() else "/delegate"
    if default_harness:
        s_harness, s_tail = default_harness, rest
    else:
        _bits = rest.split(maxsplit=1)
        s_harness = _bits[0].lower() if _bits else ""
        s_tail = _bits[1].strip() if len(_bits) > 1 else ""
    if s_harness in list_harness_names() and _handle_session_verb(
        ctx, s_harness, s_tail, cmd_token, project=project
    ):
        return True

    if default_harness:
        rest = f"{default_harness} {rest}".strip()

    harness, mode, model, permission, with_context, task, err = _parse(rest)
    if err:
        console.print(f"[red]{line.split()[0]}: {err}[/red]")
        return True
    if not task:
        console.print(_USAGE)
        return True
    if project is None or getattr(project, "project_root", None) is None:
        console.print(f"[red]{line.split()[0]}: no project root in this session[/red]")
        return True

    read_only_palette = bool(getattr(agent, "read_only_tool_palette", False))
    if read_only_palette and mode == "agent":
        console.print(
            f"[red]{line.split()[0]}: agent mode is not available in read-only mode; "
            "use --ask or --plan[/red]"
        )
        return True
    if read_only_palette and with_context:
        console.print(
            f"[red]{line.split()[0]}: --context writes harness MCP config and is not "
            "available in read-only mode[/red]"
        )
        return True

    root = Path(project.project_root)
    model_label = model or str(harness_meta(harness)["default_model"])

    tier = str(harness_meta(harness)["tier"])
    read_only = mode in ("plan", "ask")
    context_harnesses = ("cursor", "grok")
    # Tier 2 handoff: --context now forwards the live xlii tab manifest (role ·
    # docs · refs · conversation) to ANY harness as a prompt preamble, on top of
    # the MCP registration that stays cursor/grok-only.
    handoff = build_handoff_context(state) if with_context else ""
    if handoff:
        task = f"{handoff}\n\n---\n\n{task}"
    console.print(
        f"[bold cyan][{harness} · {model_label} · {mode} · {tier}][/bold cyan]"
        + ("  [dim](read-only)[/dim]" if read_only else "  [yellow](may edit files)[/yellow]")
        + ("  [dim]+xlii context[/dim]" if with_context else "")
    )

    relay = HarnessStreamRelay(console, harness=harness, model=model_label)
    if with_context and harness in context_harnesses:
        from xlii.harness.mcp_context import ensure_deep_context

        action, approved = ensure_deep_context(root, harness)
        if action == "added":
            console.print(f"[dim]+ registered xlii-deep-contexts in .{harness}/mcp.json[/dim]")
        if harness == "cursor" and not approved:
            console.print(
                "[yellow]note: run `cursor-agent mcp enable xlii-deep-contexts`[/yellow]"
            )

    result = run_delegate(
        harness,
        task,
        mode=mode,
        model=model,
        permission=permission,
        with_context=with_context,
        project_root=root,
        xli_dir=project.xli_dir,
        on_event=relay.on_event if harness in acp_harness_names() else None,
    )
    if harness in acp_harness_names():
        relay.finish()
    if harness in acp_harness_names() and not result.error:
        console.print()
    _print_delegate_result(
        console,
        result,
        project_root=root,
        harness=harness,
        model=model_label,
        acp_streamed=relay.streamed,
    )
    # Tier 3: capture the exchange into B's buffer + history (default-on for
    # /cursor·/delegate) so `/replay` and `?what did we do?` can reach it. A
    # silent no-op until B's seam #3 lands.
    if state is not None and not result.error and result.text:
        capture_harness_output(state, f"harness:{harness}", result.text, into_history=True)
    return True


def _delegate_handler(line: str, ctx: dict[str, Any]) -> bool:
    return run_delegate_command(line, ctx)


def register() -> None:
    # Seam #5: teach the input box each harness's foreground-mode hint, and (when
    # A1/A2 land) surface a live session as a context tab + preview. Both are
    # best-effort no-ops until those seams merge, so safe to call on day 1.
    from xlii.harness.detect import register_harness_hints

    register_harness_hints()
    register_session_seams()
    register_repl_command(
        REPLCommand(
            name="delegate",
            handler=_delegate_handler,
            description="Drive or orchestrate an external agent harness (one-shot or persistent sessions)",
            usage="/delegate <harness> [new <name>|@<name> <task>|ls|on|off|close <name>] | [--plan|--ask] [--model <m>] [--context] <task>",
            category="knowledge",
            repls=["code", "chat"],
            conversational=True,
        )
    )
