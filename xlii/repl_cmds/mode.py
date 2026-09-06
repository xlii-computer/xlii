"""Plan-mode and Coding Rail slash commands.

/plan, /execute, /cancel, /rail and the rail-display helpers. `_rail_brief` is
also imported by the code-REPL session runner to print the stage banner at
launch, so it lives here (the lower layer) and is pulled up, never the reverse.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.debug_mode import DebugController, find_debug_markers
from xlii.mode_controller import DiscoveryController, OpsController, PlanController
from xlii.rail import RailController


def _rail_header_line(console, rail) -> None:
    """One-line stage header + read-only/writes gate (used on advance/launch)."""
    gate = "read-only" if rail.is_read_only_stage else "writes unlocked"
    console.print(f"[magenta]{rail.get_status_header()}[/magenta] [dim]({gate})[/dim]")


def _rail_brief(console, rail) -> None:
    """Stage header plus the full enforcement instruction for the stage."""
    from xlii.rail import RAIL_STAGE_PROMPTS
    _rail_header_line(console, rail)
    console.print(f"[dim]{RAIL_STAGE_PROMPTS[rail.current_stage]}[/dim]")



def _last_assistant_text(agent) -> Optional[str]:
    """The most recent assistant message with text — i.e. the plan the model just
    produced. Shared by /loop --from-plan and /plan save."""
    for msg in reversed(agent.history):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None


def _current_plan_text(ctx: dict[str, Any], started_at: Optional[float]) -> Optional[str]:
    """The planner's working file (`<plans>/current.md`), when it is FRESH.

    plan-write-domain P0 made planning file-first: the model writes the plan
    to current.md and only *summarizes* in chat, so the file — not the last
    chat message — is the plan. ``started_at`` (PlanController.started_at)
    fences off a stale current.md left by an earlier session: an mtime older
    than the live plan session ⇒ not this plan. None = no fence (prefer the
    file whenever it exists)."""
    d = _plans_dir(ctx)
    if d is None:
        return None
    f = d / "current.md"
    try:
        if not f.is_file():
            return None
        if started_at is not None and f.stat().st_mtime < started_at:
            return None
        text = f.read_text(errors="replace").strip()
    except OSError:
        return None
    return text or None


def _save_plan_last(ctx: dict[str, Any], started_at: Optional[float] = None) -> Optional[str]:
    """Persist the plan of record for /loop --from-plan (L3): the fresh working
    file (`plans/current.md`) when the model reconciled one, else the last
    assistant text (legacy — the model narrated the plan instead of writing it).

    Returns which source fed plan-last.md — ``"plan-file"`` | ``"chat-snapshot"``
    — or None when nothing was saved, so approval can announce it (P1 D1b) and
    the turn receipt can record it (D1c). D4: /loop --from-plan keeps reading
    plan-last.md ONLY — it is the *approval* artifact; approval is not
    skippable, so there is no fallback to current.md over there."""
    from xlii.atomicio import write_text_atomic
    from xlii.loop import PLAN_LAST_FILE as LOOP_PLAN

    project = ctx.get("project")
    xli_dir = getattr(project, "xli_dir", None) if project is not None else None
    if xli_dir is None:
        return None
    plan = _current_plan_text(ctx, started_at)
    source = "plan-file" if plan is not None else None
    if plan is None:
        plan = _last_assistant_text(ctx["agent"])
        source = "chat-snapshot" if plan is not None else None
    if plan is None:
        return None
    write_text_atomic(Path(xli_dir) / LOOP_PLAN, plan + "\n")
    return source


def _announce_plan_source(ctx: dict[str, Any], source: Optional[str]) -> None:
    """Approval-time honesty (P1 D1b): say where the plan of record came from,
    and warn VISIBLY when it's only a chat snapshot — that silence is exactly
    how the mcTesty '/plan save captured the workflow note' incident hid."""
    console = ctx["console"]
    if source == "plan-file":
        console.print("[dim]plan → plan-last.md (from plans/current.md)[/dim]")
    elif source == "chat-snapshot":
        console.print(
            "[yellow]⚠ no reconciled plans/current.md — approval snapshotted the "
            "last chat message into plan-last.md. The plan file is the plan; have "
            "the planner keep .xlii/plans/current.md updated.[/yellow]"
        )


def _stash_plan_source(
    agent: Any, source: Optional[str], expected_opening: Optional[str]
) -> None:
    """One-shot stash for the receipt builder (P1 D1c): a ``(source,
    expected_opening)`` pair, where expected_opening is the exact rewritten
    text the approval handler plants as the follow-up turn's user message.
    build_receipt stamps plan_source ONLY onto a turn whose opening carries
    that text — an aborted or dropped approval turn (Ctrl-C, run_turn error,
    TUI busy-gate drop) must never stamp a later unrelated receipt; an audit
    field that lies is worse than none. Always overwrites: a later approval
    replaces a stale pair, and a save-less approval clears it. Best-effort and
    memory-only — fakes without a session just skip it."""
    session = getattr(agent, "session", None)
    if session is None:
        return
    try:
        if source is None or expected_opening is None:
            session.pending_plan_source = None
        else:
            session.pending_plan_source = (source, expected_opening)
    except (AttributeError, TypeError):
        # Best-effort metadata only: some fake/limited session objects may
        # not support this attribute assignment, and that's safe to ignore.
        pass


# --------------------------------------------------------------------------- #
#  Saved plans (.xlii/plans/<name>.md) — carry a plan across sessions. Re-enter
#  /plan and it offers to continue the last one (attach as a /doc refresher) or
#  start fresh, since tinkering between sessions may have changed the plan.
# --------------------------------------------------------------------------- #

def _plans_dir(ctx: dict[str, Any]) -> Optional[Path]:
    project = ctx.get("project")
    xli_dir = getattr(project, "xli_dir", None) if project is not None else None
    return (Path(xli_dir) / "plans") if xli_dir is not None else None


def _slug_plan(name: str) -> str:
    import re
    name = name.strip()
    # Typing the extension means the name WITHOUT it: '/plan save spec.md' must
    # not create spec.md.md, and 'CURRENT.MD' must hit the 'current' guard —
    # strip ONE trailing .md (case-insensitive) before slugging.
    if name.casefold().endswith(".md"):
        name = name[:-3]
    out = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-.")
    return out or "plan"


def _ago(mtime: float) -> str:
    import time
    secs = max(0.0, time.time() - mtime)
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{int(round(secs / 60))} min ago"
    if secs < 129600:  # 36h
        return f"{int(round(secs / 3600))} h ago"
    return f"{int(secs // 86400)} d ago"


def _list_saved_plans(ctx: dict[str, Any]) -> list[tuple[str, Path, float]]:
    """(name, path, mtime) for saved plans, most-recent first."""
    d = _plans_dir(ctx)
    if d is None or not d.is_dir():
        return []
    out: list[tuple[str, Path, float]] = []
    for p in d.glob("*.md"):
        if p.stem.casefold() == "current":
            # The working file, not a christened save — a cancelled plan must
            # not resurface as "saved plan 'current'" (any casing; the save
            # guard casefolds too, so the pair can't drift). The file stays.
            continue
        try:
            out.append((p.stem, p, p.stat().st_mtime))
        except OSError:
            continue
    out.sort(key=lambda t: t[2], reverse=True)
    return out


def _ask_yes(prompt: str) -> bool:
    """y/N prompt that works inline (input) AND in the TUI (the launch()-installed
    modal-backed _confirm). Defaults to No when there's no console to answer."""
    from xlii.tools import _confirm
    try:
        return _confirm(prompt).strip().lower() == "y"
    except (EOFError, KeyboardInterrupt, OSError):
        return False


def _resume_saved_plan(ctx: dict[str, Any], name: str, path: Path, mtime: float) -> None:
    """Queue a saved plan as a ONE-SHOT refresher: it's folded into the next user
    turn once (run_turn consumes session.pending_plan_refresher), then it's just
    part of the conversation — not re-inlined every turn like a /doc."""
    agent = ctx["agent"]
    console = ctx["console"]
    session = getattr(agent, "session", None)
    if session is None:
        console.print("[dim](can't resume here — saved plan left on disk)[/dim]")
        return
    try:
        content = path.read_text(errors="replace")
    except OSError as e:
        console.print(f"[yellow]couldn't read saved plan '{name}': {e}[/yellow]")
        return
    session.pending_plan_refresher = content
    verb = "updated" if name == "current" else "saved"
    console.print(
        f"[green]continuing plan '{name}'[/green] [dim]({verb} {_ago(mtime)}) — it'll be folded "
        f"into your next message as a refresher, then it's just part of the conversation.[/dim]"
    )


def _plan_save(ctx: dict[str, Any], args: list[str]) -> bool:
    """/plan save <name> — christening (P1 D2): promote the reconciled working
    file to a named plan. Persistence is continuous (current.md); save gives
    the state a NAME. Chat text stays the legacy fallback, said out loud."""
    console = ctx["console"]
    d = _plans_dir(ctx)
    if d is None:
        console.print("[dim]/plan save: no project context[/dim]")
        return True
    started = getattr(ctx["agent"].active_mode, "started_at", None)
    current = _current_plan_text(ctx, started)
    plan = current or _last_assistant_text(ctx["agent"])
    if not plan:
        console.print("[yellow]/plan save: nothing to save yet[/yellow] — produce a plan first, then save it.")
        return True
    name = _slug_plan(args[0]) if args else "plan"
    if name.casefold() in ("current", "current.md"):
        console.print(
            "[yellow]/plan save: 'current' is the working file, not a christening "
            "target[/yellow] — it is already saved continuously; pick a real name, "
            "e.g. /plan save my-feature."
        )
        return True
    from xlii.atomicio import write_text_atomic
    d.mkdir(parents=True, exist_ok=True)
    write_text_atomic(d / f"{name}.md", plan + "\n")
    if current is not None:
        # Show the working file's age — promoting outside plan mode (no
        # started_at fence) can christen an old current.md; say how old.
        try:
            age = f" (updated {_ago((d / 'current.md').stat().st_mtime)})"
        except OSError:
            age = ""
        console.print(
            f"[green]promoted current.md{age} → {name}.md[/green] [dim](.xlii/plans/"
            f"{name}.md — /plan continue {name} to resume it later).[/dim]"
        )
    else:
        console.print(
            f"[green]saved plan '{name}'[/green] [dim]→ .xlii/plans/{name}.md — "
            f"saved from chat text (no reconciled current.md); re-enter /plan "
            f"later to continue it.[/dim]"
        )
    return True


def _working_plan_match(ctx: dict[str, Any]) -> Optional[tuple[str, Path, float]]:
    """The working file as a continue target — only when it has CONTENT. An
    empty/whitespace current.md must not shadow real named plans (its
    'folded into your next message' promise would silently no-op)."""
    d = _plans_dir(ctx)
    if d is None:
        return None
    cur = d / "current.md"
    try:
        if not cur.is_file() or not cur.read_text(errors="replace").strip():
            return None
        mtime = cur.stat().st_mtime
    except OSError:
        return None
    return ("current", cur, mtime)


def _plan_continue(ctx: dict[str, Any], args: list[str]) -> bool:
    agent = ctx["agent"]
    console = ctx["console"]
    plans = _list_saved_plans(ctx)
    working = _working_plan_match(ctx)
    if args and _slug_plan(args[0]).casefold() in ("current", "current.md"):
        # The bare form announces the plan as 'current' — the explicit form of
        # that same name must not be a dead end; route it to the working file.
        if working is None:
            console.print(
                "[dim]/plan continue: no working plan (plans/current.md is empty "
                "or missing) — /plan list shows the named plans.[/dim]"
            )
            return True
        match = working
    elif args:
        if not plans:
            console.print("[dim]/plan continue: no saved plans yet (/plan save [name] to create one)[/dim]")
            return True
        name = _slug_plan(args[0])
        match = next((p for p in plans if p[0] == name), None)
        if match is None:
            console.print(f"[yellow]no saved plan '{name}'[/yellow] — /plan list to see them.")
            return True
    else:
        # Bare continue prefers the WORKING file (P1 D3): asking to continue is
        # the freshness signal, so any mtime counts — unlike the passive entry
        # offer, which stays named-plans-only.
        if working is not None:
            match = working
        elif plans:
            match = plans[0]
        else:
            console.print("[dim]/plan continue: no saved plans yet (/plan save [name] to create one)[/dim]")
            return True
    if not agent.plan_mode:
        agent.set_mode(PlanController())
    if match is working and working is not None:
        # Backdate the freshness fence to the file's mtime: resuming the
        # working file declares it THIS session's plan, so the session
        # semantically began when the file was last written — an immediate
        # /execute must treat it as fresh, not fence it as a stale leftover.
        # min() so an already-running plan session's earlier fence never moves
        # FORWARD (the fence exists for abandoned leftovers, not this file).
        ctrl = agent.active_mode
        started = getattr(ctrl, "started_at", None)
        if started is not None:
            ctrl.started_at = min(started, working[2])
    _resume_saved_plan(ctx, *match)
    return True


def _plan_list(ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    plans = _list_saved_plans(ctx)
    if not plans:
        console.print("[dim](no saved plans — /plan save [name] to create one)[/dim]")
        return True
    console.print("[bold]saved plans[/bold] [dim](/plan continue <name> to resume)[/dim]")
    for name, _path, mtime in plans:
        console.print(f"  [cyan]{name}[/cyan]  [dim]saved {_ago(mtime)}[/dim]")
    return True


def _plan_check(ctx: dict[str, Any], args: list[str]) -> bool:
    """/plan check <id> [--receipt <ref>] [--plan <name>] — the human mirror of
    the plan_check agent tool (plan-write-domain P2): flip a checkbox, annotate
    evidence. Same core op, same rules (no unchecking, no text edits)."""
    console = ctx["console"]
    d = _plans_dir(ctx)
    if d is None:
        console.print("[dim]/plan check: no project context[/dim]")
        return True
    pos: list[str] = []
    receipt: Optional[str] = None
    plan: Optional[str] = None
    i = 0
    while i < len(args):
        a = args[i]
        if a in ("--receipt", "-r") and i + 1 < len(args):
            receipt = args[i + 1]
            i += 2
        elif a == "--plan" and i + 1 < len(args):
            plan = args[i + 1]
            i += 2
        else:
            pos.append(a)
            i += 1
    if not pos:
        console.print(
            "[dim]usage: /plan check <item-id> [--receipt <ref>] [--plan <name>][/dim]"
        )
        return True
    from xlii.plan_ops import PlanOpError, check_plan_item

    try:
        res = check_plan_item(Path(d).resolve(), pos[0], evidence=receipt, plan=plan)
    except PlanOpError as e:
        console.print(f"[yellow]/plan check: {e}[/yellow]")
        return True
    if not res.changed:
        console.print(f"[dim]{{#{res.item_id}}}: {res.note}[/dim]")
        return True
    from rich.markup import escape

    color = "green" if res.new_state == "x" else "yellow"
    console.print(f"[{color}]✓ {res.note}[/{color}] [dim]({res.file.name})[/dim]")
    console.print(f"  [{color}]{escape(res.line.strip())}[/{color}]")
    return True


def _plan_show(ctx: dict[str, Any], args: list[str]) -> bool:
    """/plan show [name] — render a plan with checkbox states styled:
    [x] green (receipted), [x?] yellow (unreceipted), [ ] dim (open)."""
    console = ctx["console"]
    d = _plans_dir(ctx)
    if d is None:
        console.print("[dim]/plan show: no project context[/dim]")
        return True
    from xlii.plan_ops import PlanOpError, render_plan_lines, resolve_plan_file

    try:
        f = resolve_plan_file(Path(d).resolve(), args[0] if args else None)
    except PlanOpError as e:
        console.print(f"[yellow]/plan show: {e}[/yellow]")
        return True
    try:
        text = f.read_text(errors="replace")
    except OSError as e:
        console.print(f"[yellow]/plan show: couldn't read {f.name}: {e}[/yellow]")
        return True
    console.print(f"[bold]{f.stem}[/bold] [dim](.xlii/plans/{f.name})[/dim]")
    for line in render_plan_lines(text):
        console.print(line)
    return True


def _plan_amend(ctx: dict[str, Any], args: list[str]) -> bool:
    """/plan amend [--re <item-id>] [--plan <name>] <text> — the human mirror
    of the plan_amend agent tool (P3): queue a proposal into the plan's
    `## Amendments` section. The planner resolves it; nobody edits by proxy."""
    console = ctx["console"]
    d = _plans_dir(ctx)
    if d is None:
        console.print("[dim]/plan amend: no project context[/dim]")
        return True
    pos: list[str] = []
    item_id: Optional[str] = None
    plan: Optional[str] = None
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--re" and i + 1 < len(args):
            item_id = args[i + 1]
            i += 2
        elif a == "--plan" and i + 1 < len(args):
            plan = args[i + 1]
            i += 2
        else:
            pos.append(a)
            i += 1
    text = " ".join(pos).strip()
    if not text:
        console.print(
            "[dim]usage: /plan amend [--re <item-id>] [--plan <name>] <text>[/dim]"
        )
        return True
    from xlii.plan_ops import PlanOpError, amend_plan

    try:
        res = amend_plan(Path(d).resolve(), text, item_id=item_id, plan=plan)
    except PlanOpError as e:
        console.print(f"[yellow]/plan amend: {e}[/yellow]")
        return True
    from rich.markup import escape

    console.print(
        f"[magenta]✎ queued amendment {{#{res.amend_id}}}[/magenta] "
        f"[dim]({res.file.name} — the planner resolves it on the next reconcile).[/dim]"
    )
    console.print(f"  [magenta]{escape(res.line.strip())}[/magenta]")
    return True


def _pending_amendment_note(ctx: dict[str, Any]) -> None:
    """One line at /plan entry when queued amendments await the planner — on
    the file amend_plan actually TARGETS by default (current.md, else the
    newest named plan: the same resolve_plan_file(None) fallback the op uses),
    so a note is never missed just because current.md is absent. Silent when
    no plans exist. The passive entry offer stays named-plans-only; this is a
    note, not a prompt."""
    d = _plans_dir(ctx)
    if d is None:
        return
    from xlii.plan_ops import PlanOpError, pending_amendments, resolve_plan_file

    try:
        f = resolve_plan_file(Path(d).resolve(), None)
        pend = pending_amendments(f.read_text(errors="replace"))
    except (PlanOpError, OSError):
        return
    if pend:
        where = "the working plan" if f.stem == "current" else f"plan '{f.stem}'"
        ctx["console"].print(
            f"[magenta]{len(pend)} pending amendment(s) in {where}[/magenta] "
            "[dim]— /plan continue to load it.[/dim]"
        )


def _offer_saved_plan_on_entry(ctx: dict[str, Any]) -> None:
    """On a bare /plan, if a prior plan was saved, ask whether to continue from it
    (attach as a refresher) or start fresh — tinkering since may have changed it."""
    agent = ctx["agent"]
    console = ctx["console"]
    plans = _list_saved_plans(ctx)
    if not plans:
        return
    # Already resumed this turn (a refresher is queued but not yet consumed) — the
    # plan is coming on the next turn, so don't re-ask.
    if getattr(getattr(agent, "session", None), "pending_plan_refresher", None):
        return
    name, path, mtime = plans[0]
    extra = f"  (+{len(plans) - 1} more — /plan list)" if len(plans) > 1 else ""
    if _ask_yes(f"saved plan '{name}' from {_ago(mtime)}{extra} — continue from it? [y/N] "):
        _resume_saved_plan(ctx, name, path, mtime)
    else:
        console.print(
            f"[dim]starting fresh — '{name}' left untouched "
            f"(/plan continue {name} to bring it in later).[/dim]"
        )


def _seed_rail_from_plan(ctx: dict[str, Any]) -> None:
    """Approve the pending plan and start the rail seeded from it (stage 0).

    Shared by `/rail` (when a plan is pending) and `/execute rail`. Sets the
    rewrite that runs the first stage immediately. IMPORTANT: clears plan_mode
    on the REPLState too, not just the Agent — the loop re-syncs state→agent
    before the turn, so leaving state.plan_mode=True would clobber the switch.
    """
    agent = ctx["agent"]
    console = ctx["console"]

    # Capture the plan session's start BEFORE the mode swap clears it — it
    # fences _save_plan_last's preference for a *fresh* plans/current.md.
    started = getattr(agent.active_mode, "started_at", None)
    agent.set_mode(RailController(seeded_from_plan=True))
    source = _save_plan_last(ctx, started)
    _announce_plan_source(ctx, source)
    rewritten = (
        "The plan above is approved. Proceed on the Coding Rail starting at "
        "Stage 0 (Requirements Lock): restate and lock each requirement, confirming "
        "it against the approved plan rather than deriving from scratch."
    )
    # The stash is bound to the SAME rewritten text planted below — the receipt
    # stamps only the turn that actually opens with it.
    _stash_plan_source(agent, source, rewritten)

    try:
        from xlii.hooks import run_hooks
        run_hooks(ctx["project"].xli_dir, "on-plan-approved",
                  {"mode": "rail"}, console=console)
    except Exception:
        # Rail seed: the plan is already approved, so a failing project hook must not undo it.
        pass
    console.print("[magenta]plan approved → rail ON[/magenta] — carrying the approved plan "
                  "through all 6 stages, starting at Requirements Lock. /rail next to advance.")
    _rail_header_line(console, agent.rail)
    ctx["_rail_rewritten"] = rewritten


def _plan_args(line: str) -> list[str]:
    """Tokenize a /plan subcommand's argument tail with shlex, so a QUOTED
    plan name ('my plan' — writable by the planner's write_file, which has no
    name grammar) survives as one token for --plan/save/show/continue.
    Unbalanced quotes (an apostrophe in amend prose) fall back to a plain
    whitespace split — teaching text must never crash the parser."""
    import shlex

    head = line.split(None, 2)
    rest = head[2] if len(head) > 2 else ""
    try:
        return shlex.split(rest)
    except ValueError:
        return rest.split()


def _notify_plan_surfaces() -> None:
    """Repaint the plan strip/pane after a file-level plan op (save/continue) —
    those move whole files, so they don't ride plan_ops' own write hooks."""
    try:
        from xlii.plan_ops import notify_plan_changed

        notify_plan_changed()
    except Exception:
        # Non-critical UI refresh; must not interrupt /plan command handling.
        pass


# Opt-in roll of recent Mojo talk into the planner. Bare /plan stays cold —
# talk and lab are different tapes; this flag is the deliberate handoff.
_FROM_MOJO_FLAGS = frozenset({"--from-mojo", "--from-talk", "--add-context"})
_FROM_TALK_MARK = "[from talk · mojo]"
_FROM_MOJO_TURNS = 8
_FROM_MOJO_CHARS = 12_000
_FROM_MOJO_PER = 1_800


def _plan_enter_flags(toks: list[str]) -> tuple[bool, Optional[str], list[str]]:
    """``(from_mojo, with_provider, rest)``. ``with_provider=""`` means
    ``--with`` with no name (caller errors). ``None`` means no ``--with``."""
    from_mojo = False
    with_name: Optional[str] = None
    rest: list[str] = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in _FROM_MOJO_FLAGS:
            from_mojo = True
            i += 1
            continue
        if t == "--with":
            if i + 1 >= len(toks):
                return from_mojo, "", rest
            with_name = toks[i + 1]
            i += 2
            continue
        rest.append(t)
        i += 1
    return from_mojo, with_name, rest


def _talk_turns_dir(state: Any, agent: Any) -> tuple[Optional[Path], str]:
    """Persona turn store for the live talk mouth, plus a short label."""
    from xlii.cmds.sessions.resolve import _lookup_persona
    from xlii.persona import talk_persona_id

    project = getattr(state, "project", None) if state is not None else getattr(
        agent, "project", None)
    cfg = (getattr(state, "cfg", None) if state is not None else None) or getattr(
        agent, "cfg", None)
    pid = talk_persona_id(state=state, project=project, cfg=cfg)
    persona = _lookup_persona(pid)
    if persona is None:
        return None, pid or "mojo"
    who = (getattr(persona, "name", None) or pid or "mojo").strip() or "mojo"
    td = getattr(persona, "turns_dir", None)
    return (Path(td) if td is not None else None), who


def _format_talk_briefing(turns: list, *, who: str) -> str:
    lines = [
        _FROM_TALK_MARK,
        f"Recent {who} talk that led to this plan. Task context only — "
        "not already-approved work.",
        "",
    ]
    budget = _FROM_MOJO_CHARS
    for t in turns:
        u = (getattr(t, "user", None) or "").strip()
        a = (getattr(t, "assistant", None) or "").strip()
        if len(u) > _FROM_MOJO_PER:
            u = u[:_FROM_MOJO_PER] + "…"
        if len(a) > _FROM_MOJO_PER:
            a = a[:_FROM_MOJO_PER] + "…"
        block = f"User:\n{u}\n\n{who}:\n{a}\n"
        if budget < 80:
            break
        if len(block) > budget:
            block = block[:budget] + "…"
        lines.append(block)
        budget -= len(block)
    return "\n".join(lines).strip()


def _roll_talk_into_plan(ctx: dict[str, Any]) -> tuple[int, str]:
    """Inject recent persona turns into the live lab history. Session-only.

    Returns ``(n, who)``: n>0 rolled, 0 nothing to roll, -1 already carrying.
    Does not write ``.xlii/turns`` — this sitting's handoff, not a lab memory.
    """
    agent = ctx["agent"]
    hist = getattr(agent, "history", None)
    if not isinstance(hist, list):
        return 0, "mojo"
    for msg in hist:
        if isinstance(msg, dict) and _FROM_TALK_MARK in str(msg.get("content") or ""):
            return -1, "mojo"
    td, who = _talk_turns_dir(ctx.get("state"), agent)
    if td is None:
        return 0, who
    from xlii.transcript import load_recent_turns

    turns = load_recent_turns(td, _FROM_MOJO_TURNS)
    if not turns:
        return 0, who
    hist.append({"role": "user", "content": _format_talk_briefing(turns, who=who)})
    return len(turns), who


def h_plan(line: str, ctx: dict[str, Any]) -> bool:
    agent = ctx["agent"]
    state = ctx.get("state")

    parts = line.split()
    sub = parts[1].lower() if len(parts) > 1 else ""
    if sub == "save":
        out = _plan_save(ctx, _plan_args(line))
        _notify_plan_surfaces()
        return out
    if sub in ("continue", "load", "resume"):
        out = _plan_continue(ctx, _plan_args(line))
        _notify_plan_surfaces()
        return out
    if sub in ("list", "saved", "ls"):
        return _plan_list(ctx)
    if sub == "panel":
        # The plan surface's panel door (T1) — routed through file_tab's shared
        # opener (the one command home for the panel-host seam).
        from xlii.repl_cmds.file_tab import open_door

        open_door(ctx["console"], "plan",
                  hint="Inline, /plan show renders the same items.")
        return True
    if sub == "check":
        return _plan_check(ctx, _plan_args(line))
    if sub == "amend":
        return _plan_amend(ctx, _plan_args(line))
    if sub in ("show", "view", "cat"):
        return _plan_show(ctx, _plan_args(line))

    # Plan-surface T2: /plan --with <provider> hires a gig brain as THIS plan
    # session's planner. Resolved BEFORE the mode flips, so a bad provider or
    # missing key refuses here (message is the fix) instead of failing the
    # first plan turn. The backend lives on the controller: /execute and
    # /cancel end the hire by construction; execution always runs home.
    # --from-mojo / --from-talk / --add-context: opt-in roll of recent talk.
    backend = None
    from_mojo, with_name, _rest = _plan_enter_flags(line.split()[1:])
    if with_name == "":
        ctx["console"].print(
            "[red]/plan --with: pass a provider name (see /gigwork ls)[/red]"
        )
        return True
    if with_name:
        from xlii.chat_backend import GigError, resolve_gig_backend

        try:
            backend = resolve_gig_backend(agent.cfg, with_name)
        except GigError as e:
            ctx["console"].print(f"[red]/plan --with: {e}[/red]")
            return True
    if from_mojo:
        n, who = _roll_talk_into_plan(ctx)
        if n > 0:
            ctx["console"].print(
                f"[dim]from talk: {n} recent {who} turn(s) in the planner's "
                "context[/dim]"
            )
        elif n == 0:
            ctx["console"].print(
                "[yellow]/plan --from-mojo: no recent talk to roll[/yellow]"
            )
        else:
            ctx["console"].print("[dim]from talk: already carrying[/dim]")

    if state is not None and getattr(state, "loop", None) is not None:
        ctx["console"].print("[yellow]/plan: loop is active — /loop off first[/yellow]")
        return True
    if agent.rail is not None or agent.debug is not None:
        if agent.rail is not None:
            ctx["console"].print("[dim]rail OFF (plan mode takes over)[/dim]")
        if agent.debug is not None:
            ctx["console"].print("[dim]debug OFF (plan mode takes over)[/dim]")
    agent.set_mode(PlanController(chat_backend=backend))
    if backend is not None:
        ctx["console"].print(
            f"[dim]planner: gigwork[{backend.label}] · {backend.model} — plan "
            "turns ride this brain; /execute runs on the home plane[/dim]"
        )
    ctx["console"].print(
        "[yellow]plan mode ON[/yellow] — next turn will investigate read-only and produce a plan. "
        "Then: [bold]/execute[/bold] to run it, [bold]/rail[/bold] (or /execute rail) to carry it "
        "out stage-by-stage on the coding rail, or /cancel to drop. "
        "[dim]Save it with /plan save [name].[/dim]"
    )
    # The implementer may have queued amendments in the working plan — the
    # planner must see them at entry (one line; the offer stays named-only).
    _pending_amendment_note(ctx)
    # If a plan was saved before, offer to continue it (or start fresh).
    _offer_saved_plan_on_entry(ctx)
    return True


def h_execute(line: str, ctx: dict[str, Any]) -> bool:
    agent = ctx["agent"]
    if not agent.plan_mode:
        ctx["console"].print("[dim](not in plan mode — nothing to execute)[/dim]")
        return True
    # `/execute rail` (or `--rail`): carry the approved plan onto the rail
    # instead of a free-for-all execution.
    want_rail = any(tok in ("rail", "--rail") for tok in line.split()[1:])
    if want_rail:
        _seed_rail_from_plan(ctx)
        return False
    # Capture the plan session's start BEFORE clearing the mode — it fences
    # _save_plan_last's preference for a *fresh* plans/current.md.
    started = getattr(agent.active_mode, "started_at", None)
    agent.set_mode(None)
    source = _save_plan_last(ctx, started)
    ctx["console"].print("[green]plan approved — executing[/green]")
    _announce_plan_source(ctx, source)
    rewritten = "Approved. Execute the plan above using all available tools."
    # The stash is bound to the SAME rewritten text planted below — the receipt
    # stamps only the turn that actually opens with it.
    _stash_plan_source(agent, source, rewritten)
    try:
        from xlii.hooks import run_hooks
        run_hooks(ctx["project"].xli_dir, "on-plan-approved",
                  {"mode": "execute"}, console=ctx["console"])
    except Exception:
        # Execute path, same contract -- a failing project hook must not undo the approval.
        pass
    # Rewrite so the next agent.run_turn gets the execution instruction
    ctx["_execute_rewritten"] = rewritten
    return False  # fall through with rewritten input


def h_cancel(line: str, ctx: dict[str, Any]) -> bool:
    agent = ctx["agent"]
    if agent.plan_mode:
        agent.plan_mode = False
        ctx["console"].print("[dim]plan mode off[/dim]")
    else:
        ctx["console"].print("[dim](not in plan mode)[/dim]")
    return True


def h_rail(line: str, ctx: dict[str, Any]) -> bool:
    """/rail [start|next|back|status|off] — drive the Coding Rail."""
    agent = ctx["agent"]
    console = ctx["console"]
    state = ctx.get("state")
    if state is not None and getattr(state, "loop", None) is not None:
        console.print("[yellow]/rail: loop is active — /loop off first[/yellow]")
        return True
    parts = line.split(maxsplit=1)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if sub in ("", "start", "on"):
        # If a /plan is pending, "approve the plan and start the rail" —
        # carry the approved plan onto the rail and run stage 0 now.
        if agent.plan_mode:
            _seed_rail_from_plan(ctx)
            return False  # fall through and run the seeded stage-0 turn
        if agent.debug is not None:
            agent.set_mode(None)
            console.print("[dim]debug OFF (rail takes over)[/dim]")
        if agent.rail is None:
            agent.set_mode(RailController())
        else:
            agent.rail.reset()
        console.print("[magenta]rail ON[/magenta] — gating each turn through 6 stages. "
                      "Type your task to begin stage 0; /rail next to advance, /rail off to exit.")
        _rail_brief(console, agent.rail)
        return True

    if agent.rail is None:
        console.print("[dim](rail is off — /rail to start)[/dim]")
        return True

    if sub in ("next", "advance", "n"):
        if agent.rail.advance():
            _rail_header_line(console, agent.rail)
            # Carry the same task forward so the user doesn't have to retype.
            # (They can still type a normal message to refine within a stage
            # instead of advancing.) Only auto-run once a task is underway.
            if any(m.get("role") == "user" for m in agent.history):
                ctx["_rail_rewritten"] = (
                    "Proceed with this stage of the rail for the same task."
                )
                return False  # fall through and run the stage turn now
        else:
            console.print("[green]rail complete[/green] — stage 5 (Self-Review) is the last stage. "
                          "/rail off to exit, or /rail start to run a new task on the rail.")
        return True

    if sub in ("back", "prev", "b"):
        if agent.rail.back():
            _rail_brief(console, agent.rail)
        else:
            console.print("[dim](already at stage 0 — Requirements Lock)[/dim]")
        return True

    if sub in ("status", "show", "?"):
        _rail_brief(console, agent.rail)
        return True

    if sub in ("off", "stop", "exit"):
        agent.set_mode(None)
        console.print("[dim]rail OFF[/dim]")
        return True

    console.print(f"[yellow]unknown /rail subcommand: {sub}[/yellow] — "
                  "use start | next | back | status | off")
    return True


# --------------------------------------------------------------------------- #
#  Debug mode (cursor-workflows.md B0) — sibling to plan/rail
# --------------------------------------------------------------------------- #

def _debug_header(console, dbg) -> None:
    """One-line phase header + the active tool gate."""
    gate = {
        "read_only": "read-only",
        "instrument": "marked writes (edit_file only)",
        "reproduce": "bash repro",
        "write": "writes unlocked",
    }[dbg.tool_mode]
    console.print(f"[cyan]{dbg.get_status_header()}[/cyan] [dim]({gate})[/dim]")


def _debug_save_artifact(ctx: dict[str, Any], dbg, status: str = "active") -> None:
    """Persist a minimal debug-session artifact (.xlii/debug/session.json)."""
    project = ctx.get("project")
    xli_dir = getattr(project, "xli_dir", None) if project is not None else None
    if xli_dir is None:
        return
    import json

    from xlii.atomicio import write_text_atomic

    path = Path(xli_dir) / "debug" / "session.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "phase": dbg.current_phase.name,
        "phase_index": dbg.current_phase.value,
        "status": status,
    }
    write_text_atomic(path, json.dumps(data, indent=2) + "\n")


def h_debug(line: str, ctx: dict[str, Any]) -> bool:
    """/debug [next|back|status|exit] — drive a staged bug hunt."""
    agent = ctx["agent"]
    console = ctx["console"]
    state = ctx.get("state")
    if state is not None and getattr(state, "loop", None) is not None:
        console.print("[yellow]/debug: loop is active — /loop off first[/yellow]")
        return True
    parts = line.split(maxsplit=1)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if sub in ("", "start", "on"):
        if agent.plan_mode:
            console.print("[dim]plan mode OFF (debug takes over)[/dim]")
        if agent.rail is not None:
            console.print("[dim]rail OFF (debug takes over)[/dim]")
        agent.set_mode(DebugController())
        _debug_save_artifact(ctx, agent.debug)
        console.print(
            "[cyan]debug mode ON[/cyan] — staged bug hunt: hypothesize → instrument → "
            "reproduce → analyze → fix → verify. Describe the bug to begin; /debug next "
            "to advance, /debug exit once instrumentation is clean."
        )
        _debug_header(console, agent.debug)
        return True

    if sub == "config":
        return _debug_config(ctx)

    if sub.startswith("consult"):
        return _debug_consult(sub, ctx)

    if agent.debug is None:
        console.print("[dim](debug is off — /debug to start)[/dim]")
        return True

    if sub in ("status", "show", "?"):
        _debug_header(console, agent.debug)
        return True

    if sub in ("next", "advance", "n"):
        if agent.debug.advance():
            _debug_header(console, agent.debug)
            _debug_save_artifact(ctx, agent.debug)
            # Carry the same bug forward so the user doesn't retype it — only once
            # a task is underway (mirrors /rail next).
            if any(m.get("role") == "user" for m in agent.history):
                ctx["_debug_rewritten"] = (
                    "Proceed with this phase of debug mode for the same bug."
                )
                return False  # fall through and run the phase turn now
        else:
            console.print("[green]debug: final phase (Verify)[/green] — remove the "
                          "instrumentation, then /debug exit.")
        return True

    if sub in ("back", "prev", "b"):
        if agent.debug.back():
            _debug_header(console, agent.debug)
            _debug_save_artifact(ctx, agent.debug)
        else:
            console.print("[dim](already at phase 0 — Hypothesize)[/dim]")
        return True

    if sub in ("exit", "off", "stop", "done"):
        project = ctx.get("project")
        root = getattr(project, "project_root", None) if project is not None else None
        markers = []
        if root is not None:
            markers = find_debug_markers(root, getattr(project, "extra_ignores", None))
        if markers:
            console.print(
                f"[red]debug exit blocked[/red] — {len(markers)} instrumentation "
                "marker(s) remain (remove them or fold them into the fix):"
            )
            for rel, lineno, text in markers[:10]:
                console.print(f"  [dim]{rel}:{lineno}[/dim] {text.strip()[:70]}")
            if len(markers) > 10:
                console.print(f"  [dim]… and {len(markers) - 10} more[/dim]")
            return True
        closing = agent.debug
        agent.set_mode(None)
        _debug_save_artifact(ctx, closing, status="closed")
        console.print("[green]debug mode OFF[/green] — instrumentation clean.")
        return True

    console.print(f"[yellow]unknown /debug subcommand: {sub}[/yellow] — "
                  "use next | back | status | consult | config | exit")
    return True


def _debug_consult(sub: str, ctx: dict[str, Any]) -> bool:
    """/debug consult [harness] <question> — harness second opinion with debug artifacts."""
    console = ctx["console"]
    project = ctx.get("project")
    if project is None or getattr(project, "xli_dir", None) is None:
        console.print("[yellow]/debug consult: no project context[/yellow]")
        return True

    from xlii.harness import run_ask
    from xlii.harness.brief import HarnessBrief
    from xlii.harness.debug_harness import collect_debug_context, load_debug_harness_config
    from xlii.harness.detect import harness_meta, list_harness_names, load_local_harnesses

    load_local_harnesses(project.xli_dir)

    tail = sub[len("consult"):].strip()
    via: str | None = None
    model: str | None = None
    if tail:
        first = tail.split(None, 1)[0].lower()
        if first in list_harness_names():
            via = first
            tail = tail.split(None, 1)[1].strip() if len(tail.split(None, 1)) > 1 else ""

    if not tail:
        console.print("[dim]usage: /debug consult [cursor|claude|codex|grok] <question>[/dim]")
        return True

    cfg = load_debug_harness_config(project.xli_dir)
    analyze = cfg.get("analyze") or {}
    if via is None:
        via = str(analyze.get("via", "claude"))
    if model is None:
        model = analyze.get("model")

    known_harnesses = set(list_harness_names())
    if via not in known_harnesses:
        console.print(
            f"[yellow]/debug consult unavailable:[/yellow] unknown harness {via!r} "
            f"(choose: {', '.join(sorted(known_harnesses))})"
        )
        return True

    debug_ctx = collect_debug_context(project.xli_dir)
    blocks = []
    if debug_ctx.strip():
        blocks.append(f"[debug artifacts]\n{debug_ctx}")
    meta = harness_meta(via)
    result = run_ask(
        via,
        HarnessBrief(
            kind="debug_consult",
            tier=str(meta["tier"]),
            question=tail,
            context_blocks=blocks,
            project_root=Path(project.project_root) if getattr(project, "project_root", None) else None,
        ),
        model=model,
    )
    if result.error:
        console.print(f"[yellow]/debug consult unavailable:[/yellow] {result.error}")
        return True
    console.print(f"[bold magenta][debug consult · {via}/{result.model} · {result.tier}][/bold magenta]")
    console.print(result.text)
    return True


def _debug_config(ctx: dict[str, Any]) -> bool:
    """Show per-phase harness map (.xlii/debug/harness.json)."""
    import json

    from xlii.harness.debug_harness import DEFAULT_DEBUG_HARNESS, load_debug_harness_config

    console = ctx["console"]
    project = ctx.get("project")
    if project is None or getattr(project, "xli_dir", None) is None:
        console.print("[dim]debug harness defaults:[/dim]")
        console.print(json.dumps(DEFAULT_DEBUG_HARNESS, indent=2))
        return True
    cfg = load_debug_harness_config(project.xli_dir)
    console.print("[cyan]debug harness map[/cyan] (.xlii/debug/harness.json):")
    console.print(json.dumps(cfg, indent=2))
    return True


# --------------------------------------------------------------------------- #
#  Discovery mode — read-only "just talk about the code" gate. Lighter than plan
#  mode: same read-only tools, but no plan deliverable and no /execute step.
# --------------------------------------------------------------------------- #

def h_discovery(line: str, ctx: dict[str, Any]) -> bool:
    """/discovery [on|off|status] (alias /research) — toggle read-only discussion.

    Bare /discovery toggles. While active, the agent investigates read-only and
    talks — it cannot edit, write, or run anything until you turn it off.
    """
    agent = ctx["agent"]
    console = ctx["console"]
    state = ctx.get("state")
    parts = line.split(maxsplit=1)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if sub in ("off", "stop", "exit"):
        if agent.discovery_mode:
            agent.set_mode(None)
            console.print("[dim]discovery mode OFF[/dim] — writes unlocked again.")
        else:
            console.print("[dim](not in discovery mode)[/dim]")
        return True

    if sub in ("status", "show", "?"):
        on = "[green]ON[/green]" if agent.discovery_mode else "off"
        console.print(f"discovery mode: {on}")
        return True

    # Bare /discovery (or 'on') toggles into the mode — and out of it if already
    # on, so the same keystroke is the on/off switch.
    if agent.discovery_mode and sub != "on":
        agent.set_mode(None)
        console.print("[dim]discovery mode OFF[/dim] — writes unlocked again.")
        return True

    if state is not None and getattr(state, "loop", None) is not None:
        console.print("[yellow]/discovery: loop is active — /loop off first[/yellow]")
        return True
    # Mutual exclusion is enforced by set_mode; announce what it displaces.
    if agent.plan_mode:
        console.print("[dim]plan mode OFF (discovery takes over)[/dim]")
    if agent.rail is not None:
        console.print("[dim]rail OFF (discovery takes over)[/dim]")
    if agent.debug is not None:
        console.print("[dim]debug OFF (discovery takes over)[/dim]")
    if agent.ops_mode:
        console.print("[dim]ops OFF (discovery takes over)[/dim]")

    agent.set_mode(DiscoveryController())
    console.print(
        "[cyan]discovery mode ON[/cyan] — read-only discussion & research. The agent "
        "will read, grep, and explain but [bold]won't change any code[/bold]. "
        "[dim]/discovery off to unlock writes; /plan when you're ready to act.[/dim]"
    )
    return True


# --------------------------------------------------------------------------- #
#  Ops mode — OS diagnostics / workflow on the host (terminal-native Phase 8)
# --------------------------------------------------------------------------- #

def h_ops(line: str, ctx: dict[str, Any]) -> bool:
    """/ops [on|off|status] — toggle OS diagnostics mode.

    Bare /ops toggles. While active, the agent runs platform-correct shell probes
    (read-only first) and explains results — no code edits.
    """
    agent = ctx["agent"]
    console = ctx["console"]
    state = ctx.get("state")
    parts = line.split(maxsplit=1)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if sub in ("off", "stop", "exit"):
        if agent.ops_mode:
            agent.set_mode(None)
            console.print("[dim]ops mode OFF[/dim] — back to normal coding tools.")
        else:
            console.print("[dim](not in ops mode)[/dim]")
        return True

    if sub in ("status", "show", "?"):
        on = "[green]ON[/green]" if agent.ops_mode else "off"
        console.print(f"ops mode: {on}")
        return True

    if agent.ops_mode and sub != "on":
        agent.set_mode(None)
        console.print("[dim]ops mode OFF[/dim] — back to normal coding tools.")
        return True

    if state is not None and getattr(state, "loop", None) is not None:
        console.print("[yellow]/ops: loop is active — /loop off first[/yellow]")
        return True
    if agent.plan_mode:
        console.print("[dim]plan mode OFF (ops takes over)[/dim]")
    if agent.discovery_mode:
        console.print("[dim]discovery OFF (ops takes over)[/dim]")
    if agent.rail is not None:
        console.print("[dim]rail OFF (ops takes over)[/dim]")
    if agent.debug is not None:
        console.print("[dim]debug OFF (ops takes over)[/dim]")

    agent.set_mode(OpsController())
    console.print(
        "[green]ops mode ON[/green] — OS diagnostics & workflow. The agent will run "
        "platform-correct shell probes (read-only first) and explain what it finds. "
        "[dim]/ops off to return to coding; destructive commands still gate.[/dim]"
    )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="plan",
            handler=h_plan,
            usage="/plan [--from-mojo] [--with <provider>] | [save|continue|list|show|check|amend|panel] [args]",
            description=(
                "Plan mode — the plan lives in .xlii/plans/current.md; "
                "/plan --from-mojo adds recent talk as context (opt-in); "
                "/plan save <name> promotes it to a named plan, bare "
                "/plan continue resumes it; check items done with receipts "
                "(/plan check <id> --receipt <ref>); propose changes "
                "(/plan amend [--re <id>] <text>)"
            ),
            category="mode",
        )
    )
    register_repl_command(
        REPLCommand(
            name="execute",
            handler=h_execute,
            usage="/execute [rail]",
            description="Approve the plan and execute it (add 'rail' to run it stage-by-stage)",
            category="mode",
        )
    )
    register_repl_command(
        REPLCommand(
            name="cancel",
            handler=h_cancel,
            description="Exit plan mode without executing",
            category="mode",
        )
    )
    register_repl_command(
        REPLCommand(
            name="discovery",
            handler=h_discovery,
            usage="/discovery [on|off|status]",
            description="Discovery mode: read-only discussion/research — the agent reads & explains but won't change code",
            category="mode",
            aliases=["research"],
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="ops",
            handler=h_ops,
            usage="/ops [on|off|status]",
            description="Ops mode: OS diagnostics & workflow — platform-correct shell probes, read-only first",
            category="mode",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="rail",
            handler=h_rail,
            usage="/rail [next|back|status|off]",
            description="Coding Rail: stage-gated coding (req→arch→edge→pseudo→impl→review)",
            category="mode",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="debug",
            handler=h_debug,
            usage="/debug [next|back|status|consult|config|exit]",
            description="Debug mode: staged bug hunt (hypothesize→instrument→reproduce→analyze→fix→verify)",
            category="mode",
            repls=["code"],
        )
    )
