"""Cross-REPL session + admin slash commands.

/reset, /sync, /cost, /budget, /yolo, /safe, /iterations — available in both the code
and chat REPLs. Owns `_print_pricing`, whose only caller is /cost.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.session_state import TIER_FREEBALL, TIER_SAFE, TIER_YOLO
from xlii.shellgate import MODIFIES_PROJECT, NETWORK, READ_ONLY, SEVERITY
from xlii.sync import provision_collection, sync_project
from xlii.tool_context import GATED_INTENTS
from xlii.ui import console

_APPROVE_ALIASES: dict[str, str] = {
    "read-only": READ_ONLY,
    "readonly": READ_ONLY,
    "ro": READ_ONLY,
    "modifies-project": MODIFIES_PROJECT,
    "project": MODIFIES_PROJECT,
    "proj": MODIFIES_PROJECT,
    "network": NETWORK,
    "net": NETWORK,
}

_APPROVE_HELP = "read-only, network, modifies-project"


def _format_auto_approve(cats: set[str]) -> str:
    # Derived from GATED_INTENTS rather than hard-coded, and honest about
    # grants that don't gate anyway (read-only / modifies-project never prompt,
    # so granting them is a no-op — the gate only consults grants for gated
    # intents, and modifies-system can never be granted).
    if not cats:
        gated = sorted(GATED_INTENTS, key=lambda c: SEVERITY.get(c, 99))
        return f"(none — {' and '.join(gated)} will prompt)"
    order = sorted(cats, key=lambda c: SEVERITY.get(c, 99))
    return ", ".join(
        c if c in GATED_INTENTS else f"{c} (never prompts anyway)" for c in order
    )


def _parse_approve_categories(tokens: list[str]) -> tuple[set[str], Optional[str]]:
    cats: set[str] = set()
    for raw in tokens:
        if raw.startswith("--"):
            continue
        key = raw.lower()
        if key in ("modifies-system", "system", "sys"):
            return set(), "modifies-system cannot be auto-approved (session-only gate always applies)"
        if key not in _APPROVE_ALIASES:
            return set(), f"unknown category {raw!r} — use {_APPROVE_HELP}"
        cats.add(_APPROVE_ALIASES[key])
    return cats, None

if TYPE_CHECKING:
    from xlii.config import GlobalConfig


def _print_pricing(cfg: "GlobalConfig", console=console) -> None:
    """Render the configured pricing table + coverage of active models.

    `console` defaults to the module global for legacy callers; the registry
    handler passes the session console so the table reaches the TUI transcript
    (where the global console is buried under the Textual screen)."""
    orch = cfg.get_model_for_role("orchestrator")
    worker = cfg.get_model_for_role("worker")
    chat = cfg.get_model_for_role("chat")
    help_m = cfg.get_model_for_role("help")
    if not cfg.pricing:
        console.print(
            "[yellow]no pricing configured[/yellow] — add a `pricing` map to your "
            "config.json to enable cost estimates."
        )
        return
    console.print("[bold]pricing[/bold] (USD per million tokens)")
    for model, rates in cfg.pricing.items():
        in_r = rates.get("input_per_million", 0)
        out_r = rates.get("output_per_million", 0)
        marks = []
        if model == orch:
            marks.append("[cyan]orch[/cyan]")
        if model == worker:
            marks.append("[cyan]worker[/cyan]")
        if model == chat:
            marks.append("[cyan]chat[/cyan]")
        if model == help_m:
            marks.append("[cyan]help[/cyan]")
        tag = "  ←  " + " + ".join(marks) if marks else ""
        console.print(f"  · {model:<32}  in ${in_r:>6.2f}  out ${out_r:>6.2f}{tag}")
    if orch not in cfg.pricing:
        console.print(f"  [yellow]· orchestrator model {orch!r} has no pricing[/yellow]")
    if worker != orch and worker not in cfg.pricing:
        console.print(f"  [yellow]· worker model {worker!r} has no pricing[/yellow]")
    if chat not in (orch, worker) and chat not in cfg.pricing:
        console.print(f"  [yellow]· chat model {chat!r} has no pricing[/yellow]")
    if help_m not in (orch, worker, chat) and help_m not in cfg.pricing:
        console.print(f"  [yellow]· help model {help_m!r} has no pricing[/yellow]")


def _live_turns_dir(state) -> Optional[Any]:
    """This stream's on-disk tape (code ``.xlii/turns`` or the persona store)."""
    from pathlib import Path

    profile = getattr(state, "profile", None)
    mem = getattr(profile, "memory", None) if profile is not None else None
    turns = getattr(mem, "turns_dir", None)
    if turns is not None:
        return Path(turns)
    persona = getattr(state, "persona", None)
    pdir = getattr(persona, "turns_dir", None)
    if pdir is not None:
        return Path(pdir)
    xli = getattr(getattr(state, "project", None), "xli_dir", None)
    if xli is not None:
        return Path(xli) / "turns"
    return None


def _forget_parked_tape(state) -> None:
    """Drop the parked copy so a reload / switch-back cannot resurrect this chat.

    Stream **is** this project's history. RAM-only ``/reset`` left ``.xlii/turns``
    and ``_history_stash`` intact — Face ``stream_sync`` and the next launch
    replayed the tape and reset looked like a no-op.
    """
    if state is None:
        return
    from xlii.transcript import clear_turns

    turns = _live_turns_dir(state)
    if turns is not None:
        try:
            clear_turns(turns)
        except Exception:
            # Best-effort wipe — /reset still clears the RAM state.
            pass
    stash = getattr(state, "_history_stash", None)
    if not isinstance(stash, dict):
        return
    try:
        from xlii.repl_cmds.switch import _surface_key

        profile = getattr(state, "profile", None)
        if profile is not None:
            stash.pop(_surface_key(profile, getattr(state, "persona", None)), None)
    except Exception:
        # Best-effort — the name-based pops below still run.
        pass
    name = (getattr(getattr(state, "project", None), "name", "") or "").strip()
    if name:
        stash.pop(f"code:{name}", None)
        stash.pop(f"chat:{name}", None)


def h_reset(line: str, ctx: dict[str, Any]) -> bool:
    agent = ctx["agent"]
    agent.history = agent.history[:1]
    agent.plan_mode = False
    # A wiped session's first receipt must not inherit a pre-reset approval's
    # plan_source stash (plan-write-domain P1 review).
    try:
        agent.session.pending_plan_source = None
    except Exception:
        # A session object without the attribute has no stale plan_source to clear.
        pass
    # A cleared history means a new task — restart the rail at stage 0
    # (but leave it enabled if the user opted into it).
    if agent.rail is not None:
        agent.rail.reset()
    # Same for debug: a new bug must re-hypothesize, not inherit the prior phase's
    # tool gate/directive (the sibling staged coding mode was previously left stale).
    if agent.debug is not None:
        agent.debug.reset()
    _forget_parked_tape(ctx.get("state"))
    hook = getattr(ctx.get("state"), "on_stream_reset", None)
    if callable(hook):
        try:
            hook()
        except Exception:
            # Best-effort hook — the reset itself already happened.
            pass
    ctx["console"].print("[dim]forgot this chat[/dim]")
    return True


def h_clear_screen(line: str, ctx: dict[str, Any]) -> bool:
    """Wipe the glass — not the chat. Same as Xlii → Clear transcript.

    The TUI and Face intercept ``/clear`` / ``/cls`` / ``/clear-screen`` and
    empty the transcript widget. Inline REPL clears the real terminal. Memory
    stays; use ``/reset`` to forget the talk.
    """
    hook = getattr(ctx.get("state"), "on_clear_screen", None)
    if callable(hook):
        try:
            hook()
            return True
        except Exception:
            # The surface hook failed -- fall through to clearing the console directly.
            pass
    console = ctx.get("console")
    clear = getattr(console, "clear", None) if console is not None else None
    if callable(clear):
        try:
            clear()
            return True
        except Exception:
            # Neither path could clear -- fall through to printing the cleared notice below.
            pass
    if console is not None:
        console.print("[dim]cleared[/dim]")
    return True


def h_yolo(line: str, ctx: dict[str, Any]) -> bool:
    """/yolo [--freeball [<task>]] — drop the bash confirmation gate.

    One escalating dial, three forms (no fourth top-level command, no /freeball
    alias — the-fold Vector C):
      /yolo                    bash gate OFF for the session (unchanged).
      /yolo --freeball         trusted-run tier: gate off AND spend + per-action
                               prompts auto-yes, until /safe. Rails still hold.
      /yolo --freeball <task>  one-shot: run <task> gates-down, then the tier
                               restores itself automatically after that turn.
    """
    agent = ctx["agent"]
    console = ctx["console"]

    parts = line.split(maxsplit=1)
    rest = parts[1].strip() if len(parts) > 1 else ""
    tokens = rest.split()

    if "--freeball" not in tokens:
        # Plain /yolo — bash gate off (behavior unchanged; extra args ignored as before).
        agent.yolo = True
        console.print(
            "[red]YOLO mode ON[/red] — bash confirmation gate is OFF. "
            "modifies-system and network commands will run without prompting. "
            "/safe to turn back on."
        )
        return True

    # Everything after the --freeball flag is the one-shot task (empty → toggle).
    task = rest[rest.index("--freeball") + len("--freeball"):].strip()

    if task:
        # One-shot: stash the CURRENT tier so run_turn's finally restores it, drop
        # gates for exactly this turn, and hand <task> to the loop as an agent turn
        # via the rewrite marker (process_repl_input consumes it; the TUI shares
        # that path). Restore fires even on interrupt/error — gates never stick down.
        # (Read via the yolo/freeball views, not TrustState.tier, so the handler
        # also works against the plain-attribute agent doubles in tests.)
        agent.session.freeball_restore = (
            TIER_FREEBALL if agent.freeball else (TIER_YOLO if agent.yolo else TIER_SAFE)
        )
        agent.yolo = True
        agent.freeball = True
        console.print(
            "[bold white on red] FREEBALL · one-shot [/bold white on red] — running this "
            "turn gates-down, then the tier restores itself. Rails still hold "
            "[dim](sync delete-guard, /admin elevation, /budget cap)[/dim]."
        )
        ctx["_freeball_rewritten"] = task
        return True

    # Session toggle: the trusted-run tier stays on until /safe (or session end).
    # Session-scoped, never persisted — a fresh session always starts safe.
    agent.yolo = True
    agent.freeball = True
    console.print(
        "[bold white on red] FREEBALL ON [/bold white on red] — trusted-run tier above "
        "yolo: bash gate off, spend + per-action prompts auto-yes. "
        "[bold]Rails still hold[/bold] [dim](sync delete-guard, /admin elevation, the "
        "debug-marker contract, /budget cap)[/dim]. Session-only, never persisted. "
        "/safe to stand down."
    )
    return True


def h_safe(line: str, ctx: dict[str, Any]) -> bool:
    agent = ctx["agent"]
    agent.yolo = False
    # Stand the trusted-run tier down with yolo (freeball is a superset). Clearing
    # the one-shot ticket too so a pending restore can't resurrect it next turn.
    agent.freeball = False
    agent.session.freeball_restore = None
    agent.auto_approve = set()
    ctx["console"].print(
        "[green]safe mode ON[/green] — bash gate active for network and "
        "modifies-system intents. Freeball stood down; auto-approve cleared; "
        "/approve to pre-authorize categories."
    )
    return True


def h_approve(line: str, ctx: dict[str, Any]) -> bool:
    """/approve [category…] | --none — pre-authorize bash intent categories."""
    agent = ctx["agent"]
    console = ctx["console"]
    parts = line.split()

    if len(parts) == 1:
        console.print(
            f"[bold]auto-approve[/bold]  {_format_auto_approve(agent.auto_approve)}"
        )
        if agent.freeball:
            console.print(
                "[dim]freeball is ON — the trusted-run tier bypasses every gate "
                "(rails still hold)[/dim]"
            )
        elif agent.yolo:
            console.print("[dim]yolo is ON — all categories bypass the gate[/dim]")
        else:
            console.print(
                "[dim]usage: /approve read-only network  ·  /approve --none  ·  "
                f"categories: {_APPROVE_HELP} (modifies-system always prompts)[/dim]"
            )
        return True

    if "--none" in parts[1:]:
        agent.auto_approve = set()
        console.print("[green]auto-approve cleared[/green] — gated categories will prompt.")
        return True

    cats, err = _parse_approve_categories(parts[1:])
    if err:
        console.print(f"[red]{err}[/red]")
        return True

    agent.auto_approve = cats
    console.print(f"[green]auto-approve[/green]  {_format_auto_approve(cats)}")
    return True


def h_hook_control(line: str, ctx: dict[str, Any]) -> bool:
    """/hook-control [on|off|auto|status] — opt into B1 policy (control) hooks.

    On: an executable in .xlii/hooks/on-turn-stop/ may return {"followup": ...}
    to drive capped follow-up turns. Off: such hooks run as observers only. Auto:
    defer to project.json `hooks.control`."""
    agent = ctx["agent"]
    console = ctx["console"]
    parts = line.split(maxsplit=1)
    sub = parts[1].strip().lower() if len(parts) > 1 else "status"

    if sub in ("on", "enable", "true"):
        agent.session.hook_control = True
        console.print(
            "[yellow]control hooks ON[/yellow] — an executable in "
            "[bold].xlii/hooks/on-turn-stop/[/bold] may now drive up to 5 follow-up "
            'turns by printing {"followup": "..."}. /hook-control off to stop.'
        )
    elif sub in ("off", "disable", "false"):
        agent.session.hook_control = False
        console.print("[green]control hooks OFF[/green] — on-turn-stop hooks run "
                      "as observers only (any followup is ignored).")
    elif sub in ("auto", "default", "project"):
        agent.session.hook_control = None
        console.print("[dim]control hooks: deferring to project.json hooks.control[/dim]")
    elif sub in ("status", "show", "?"):
        override = agent.session.hook_control
        state = ctx.get("state")
        if state is not None:
            from xlii.repl import _control_hooks_enabled
            eff = _control_hooks_enabled(state)
        else:
            eff = bool(override)
        src = "session override" if override is not None else "project.json default"
        console.print(f"  control hooks: {'[yellow]ON[/yellow]' if eff else 'off'}  [dim]({src})[/dim]")
    else:
        console.print(f"[yellow]unknown /hook-control arg: {sub}[/yellow] — "
                      "use on | off | auto | status")
    return True


def h_cost(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    agent = ctx["agent"]
    parts = line.split()
    session_only = "--session" in parts[1:]
    pricing_only = "--pricing" in parts[1:]

    if session_only:
        from xlii.session_meter import format_session_cost_line

        console.print(f"[bold]{format_session_cost_line(agent.session)}[/bold]")
        return True

    if not pricing_only:
        from xlii.session_meter import format_session_cost_line, format_turn_cost_line

        last = agent.session.last_turn_stats
        if last is not None:
            console.print(f"[bold]{format_turn_cost_line(last)}[/bold]")
        console.print(format_session_cost_line(agent.session))
        if last is not None:
            console.print("[dim]use /cost --pricing for the rate table[/dim]")
            return True

    _print_pricing(ctx["cfg"], console)
    return True


def h_budget(line: str, ctx: dict[str, Any]) -> bool:
    """/budget [<usd>] | --clear — soft session spend cap (also XLII_BUDGET env)."""
    from xlii.cost import format_cost
    from xlii.session_meter import format_session_cost_line

    agent = ctx["agent"]
    console = ctx["console"]
    parts = line.split()

    if len(parts) == 1:
        limit = agent.session.budget_usd
        if limit is None:
            console.print(
                "[dim]no budget set[/dim] — usage: /budget <usd>  ·  "
                "env XLII_BUDGET  ·  /budget --clear"
            )
        else:
            console.print(f"[bold]budget[/bold]  {format_cost(limit)}")
        console.print(format_session_cost_line(agent.session))
        return True

    if parts[1] in ("--clear", "--none", "clear", "none"):
        agent.session.budget_usd = None
        agent.session.budget_env_cleared = True
        console.print("[green]budget cleared[/green]")
        return True

    try:
        limit = float(parts[1])
    except ValueError:
        console.print(f"[red]invalid budget: {parts[1]!r}[/red] — use a USD amount")
        return True
    if limit <= 0:
        console.print("[red]budget must be > 0[/red]")
        return True

    agent.session.budget_usd = limit
    agent.session.budget_env_cleared = False
    console.print(f"[green]budget[/green]  {format_cost(limit)}  [dim](soft cap — warns before turns)[/dim]")
    return True


def h_sync(line: str, ctx: dict[str, Any]) -> bool:
    project = ctx["project"]
    pool = ctx["pool"]
    cfg = ctx["cfg"]
    from xlii.desk_files import files_root_of

    if project.local_only:
        mgmt = bool(getattr(cfg, "management_api_key", None) or "")
        if not (mgmt and files_root_of(project)):
            ctx["console"].print(
                "[dim]/sync: local-only project — nothing to upload[/dim]"
            )
            return True
        # Throne opt-in: attach searchable memory, then upload the remote tree.
        try:
            cid = provision_collection(pool.primary(), project)
        except Exception as e:  # noqa: BLE001
            ctx["console"].print(f"[red]/sync: could not attach collection: {e}[/red]")
            return True
        ctx["console"].print(
            f"[dim]/sync: attached collection {cid} — uploading from "
            f"{files_root_of(project)}[/dim]"
        )
    with ctx["console"].status("[cyan]syncing…[/cyan]"):
        stats = sync_project(pool.primary(), project, cfg)
    ctx["console"].print(f"[dim]sync: {stats.summary()}[/dim]")
    return True


def _session_chatish(ctx: dict[str, Any]) -> bool:
    """Whether the next turn uses the conversational tool-iteration cap."""
    agent = ctx.get("agent")
    if agent is not None and getattr(agent, "cfg", None) is not None:
        return agent._model_role() in ("chat", "help")
    session = getattr(agent, "session", None) if agent is not None else None
    if session is not None:
        return bool(
            getattr(session, "conversational", False)
            or getattr(session, "howto_mode", False)
        )
    return ctx.get("persona") is not None


def _iterations_handler(line: str, ctx: dict[str, Any]) -> bool:
    """Adjust the active tool-iteration cap for the rest of this session.

    run_turn reads the cap fresh each turn, so mutating the in-memory config
    takes effect on the very next turn. Session-only by design — config.json
    on disk is untouched; edit it there to change the default for future
    sessions. Conversational turns use max_chat_tool_iterations when present.
    """
    from xlii.agent import set_tool_iteration_cap, tool_iteration_cap

    cfg = ctx["cfg"]
    chatish = _session_chatish(ctx)
    knob, cap = tool_iteration_cap(cfg, chatish=chatish)
    parts = line.split(maxsplit=1)
    if len(parts) != 2:
        ctx["console"].print(
            f"[dim]{knob}: {cap} per turn  "
            "— usage: /iterations <1..100> (this session only)[/dim]"
        )
        return True
    try:
        n = int(parts[1])
    except ValueError:
        ctx["console"].print(f"[red]invalid iteration count: {parts[1]!r}[/red]")
        return True
    if not 1 <= n <= 100:
        ctx["console"].print("[red]iterations must be 1..100[/red]")
        return True
    knob, old = set_tool_iteration_cap(cfg, chatish=chatish, n=n)
    ctx["console"].print(
        f"[yellow]{knob}: {old} → {n}[/yellow] "
        "[dim](applies from the next turn; this session only — "
        "edit config.json to change the default)[/dim]"
    )
    return True


def h_inspect(line: str, ctx: dict[str, Any]) -> bool:
    """Dump live session + agent internals for debugging."""
    c = ctx.get("console")
    st = ctx.get("state")
    ag = ctx.get("agent")
    proj = ctx.get("project")
    persona = ctx.get("persona")

    c.print("[bold cyan]xlii /inspect[/bold cyan]")
    c.print(f"  repl: {'chat' if persona else 'code'}")
    if proj:
        c.print(f"  project: {proj.name}  (root={proj.project_root})")
    if st:
        c.print(f"  workspace: {st.current_workspace}")
        c.print(f"  yolo: {st.yolo}   freeball: {st.freeball}   plan_mode: {st.plan_mode}")
        c.print(f"  auto_approve: {_format_auto_approve(st.auto_approve)}")
        from xlii.session_meter import format_session_cost_line

        c.print(f"  {format_session_cost_line(st.agent.session)}")
        c.print(f"  attached_refs: {len(st.attached_refs)}  attached_docs: {len(st.attached_docs)}")
        if st.attached_refs:
            c.print("    refs: " + ", ".join(n for n, _ in st.attached_refs))
        if st.attached_docs:
            c.print("    docs: " + ", ".join(n for n, _ in st.attached_docs))
    if ag:
        hist = getattr(ag, "history", [])
        c.print(f"  history_turns: {len(hist)}")
        from xlii.agent import tool_iteration_cap

        chatish = _session_chatish(ctx)
        knob, cap = tool_iteration_cap(ctx.get("cfg"), chatish=chatish)
        c.print(f"  {knob}: {cap}")
        rail = getattr(ag, "rail", None)
        if rail:
            c.print(f"  rail.enabled: {getattr(rail, 'enabled', False)}  stage: {getattr(rail, 'stage', None)}")
        # Rough tool call stats from last turn if present
        if hasattr(ag, "_last_tool_count"):
            c.print(f"  last_turn_tools: {getattr(ag, '_last_tool_count', 0)}")
    # Registry snapshot
    from xlii.commands import _REPL_COMMANDS
    project_cmds = [cmd for cmd in _REPL_COMMANDS if getattr(cmd, "source", None) == "project"]
    c.print(f"  registered_slash: {len(_REPL_COMMANDS)}  (project: {len(project_cmds)})")
    c.print("[dim]use /describe <cmd> for details on any[/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="reset",
            handler=h_reset,
            description="Forget this chat (working talk only — journal, wiki, typed lines stay)",
            category="session",
        )
    )
    register_repl_command(
        REPLCommand(
            name="clear",
            handler=h_clear_screen,
            aliases=["cls", "clear-screen"],
            description="Clear the transcript (pixels only — talk stays; /reset forgets)",
            usage="/clear  |  /cls  |  /clear-screen",
            category="session",
        )
    )
    register_repl_command(
        REPLCommand(
            name="yolo",
            handler=h_yolo,
            aliases=["yolo!"],
            description="Drop the bash confirmation gate; --freeball adds the trusted-run tier",
            usage="/yolo  |  /yolo --freeball [<task>]",
            category="mode",
        )
    )
    register_repl_command(
        REPLCommand(
            name="safe",
            handler=h_safe,
            description="Re-enable bash confirmation gate and clear auto-approve",
            category="mode",
        )
    )
    register_repl_command(
        REPLCommand(
            name="approve",
            handler=h_approve,
            description="Pre-authorize bash intent categories (between /safe and /yolo)",
            usage="/approve <category…>  |  /approve --none",
            category="mode",
        )
    )
    register_repl_command(
        REPLCommand(
            name="hook-control",
            handler=h_hook_control,
            usage="/hook-control [on|off|auto|status]",
            description="Opt into B1 policy hooks (on-turn-stop may drive capped follow-up turns)",
            category="mode",
            repls=["code"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="cost",
            handler=h_cost,
            description="Show turn/session cost (use --pricing for the rate table)",
            usage="/cost [--session] [--pricing]",
            category="admin",
        )
    )
    register_repl_command(
        REPLCommand(
            name="budget",
            handler=h_budget,
            description="Set or show the soft session spend cap (XLII_BUDGET env)",
            usage="/budget [<usd>]  |  /budget --clear",
            category="admin",
        )
    )
    register_repl_command(
        REPLCommand(
            name="sync",
            handler=h_sync,
            description="Force a full sync of the project to the collection now",
            category="session",
        )
    )
    register_repl_command(
        REPLCommand(
            name="iterations",
            handler=_iterations_handler,
            aliases=["iter"],
            description="Set max tool iterations per turn for this session (no arg = show)",
            usage="/iterations <1..100>",
            category="mode",
        )
    )
    register_repl_command(
        REPLCommand(
            name="inspect",
            handler=h_inspect,
            description="Dump live REPLState, agent internals, attachments and registry stats",
            category="admin",
        )
    )
