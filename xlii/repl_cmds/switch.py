"""Live `/code` <-> `/chat` in-session profile switch (RP2, detach in RP7).

Swaps the ACTIVE Profile on the live agent+state IN PLACE — same process, same
Agent — rather than relaunching. This is the `/tui` in-place pattern
(repl_cmds/code.py), NOT the old `/persona` `pending_persona_switch` restart.

**Detachment (RP7).** A switch does NOT carry the conversation across surfaces.
Each surface owns its own context: entering one parks the leaving surface's
conversation and restores (or freshly seeds, from that surface's OWN turn store)
the target's. This is the whole design — a chat persona must stay *oblivious* to
the code (and to other personas): the only way code content reaches a chat, or a
chat idea reaches code, is the deliberate one-way `/recall <persona>:<mark>`
(RP6). Carrying the live history was the RP2 footgun that let the codebase bleed
into chat.

What a switch mutates (see `_live_switch`): the agent's project + base system
prompt + per-surface conversation, the live `state.profile`/`persona`/
`command_scope`, the memory TurnStore, the loadout, the **project journal**
(rebuilt so per-project ``code_auto`` takes effect — was missing and left the
old scratch journal dead after ``/project switch``), scratch/no_sync flags when
leaving scratch into a real code project, and it clears mode-incompatible state
(rail/plan) on entering a surface that lacks those affordances.

Scope notes:
- Swapping `agent.project` IS intended: a chat persona is a real project-backed
  surface (its own Collection/turns). The first post-swap turn is a prompt-cache
  miss (new conversation_id) — expected.
- Attachments follow the target project's session.json (reloaded on switch, RP5);
  the conversation follows the per-surface stash below.
"""

from __future__ import annotations

from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command


def _stash_displaced(state) -> None:
    """Remember the Profile we're leaving, keyed by its mode, so the reverse
    command can restore it. CLI args that resolved the code project are consumed
    at launch — nothing else remembers the prior surface."""
    prior = getattr(state, "profile", None)
    if prior is None:
        return
    if getattr(state, "_profile_stash", None) is None:
        state._profile_stash = {}
    state._profile_stash[prior.mode] = prior


def _surface_key(profile, persona) -> str:
    """Stable per-surface key for the conversation stash. Each chat persona and
    each code project is its own thread — switching never crosses them."""
    if getattr(profile, "mode", None) == "chat":
        name = (getattr(persona, "name", None)
                or getattr(getattr(profile, "identity", None), "name", None) or "?")
        return f"chat:{name}"
    return f"code:{getattr(getattr(profile, 'project', None), 'name', '?')}"


def _stash_history(state, key: str, conversation: list) -> None:
    stash = getattr(state, "_history_stash", None)
    if stash is None:
        stash = {}
        state._history_stash = stash
    stash[key] = conversation


def _stash_pending_input(state, key: str) -> None:
    stash = getattr(state, "_pending_input_stash", None)
    if stash is None:
        stash = {}
        state._pending_input_stash = stash
    stash[key] = getattr(state, "pending_input", "") or ""
    state.pending_input = ""


def _restore_pending_input(state, key: str) -> None:
    parked = (getattr(state, "_pending_input_stash", None) or {}).get(key)
    state.pending_input = parked if parked is not None else ""


def _restore_or_seed(state, profile, persona) -> list:
    """The target surface's conversation: its parked thread if we've been here
    this session, else a fresh seed from its OWN turn store (chat → the persona's
    turns; code → the project's). NEVER the surface we're leaving."""
    key = _surface_key(profile, persona)
    parked = (getattr(state, "_history_stash", None) or {}).get(key)
    if parked is not None:
        return parked
    from xlii.transcript import turns_to_history
    return turns_to_history(profile.memory.recent_turns())


def _pause_loop_for_switch(state, new_profile) -> None:
    """Keep an active code loop pinned to its original project when switching away."""
    ctrl = getattr(state, "loop", None)
    if ctrl is None or not getattr(ctrl, "is_active", False):
        return
    if getattr(ctrl, "xli_dir", None) == getattr(new_profile.project, "xli_dir", None):
        return
    ctrl.pause()
    state.loop = None
    console = getattr(state, "console", None)
    if console is not None:
        console.print("[dim][loop] paused for surface switch — /code then /loop resume to continue[/dim]")


def _flush_journal_best_effort(state) -> None:
    """Archive any buffered journal entries for the surface we're leaving."""
    jrnl = getattr(state, "journal", None)
    if jrnl is None:
        return
    try:
        if getattr(jrnl, "is_recording", lambda: False)() or getattr(jrnl, "buffer", None):
            jrnl.flush()
    except Exception as exc:
        # Best-effort by design: journal flush failures must not block profile switching.
        console = getattr(state, "console", None)
        if console is not None:
            console.print(f"[dim][journal] flush skipped during switch: {exc}[/dim]")


def _rebind_journal_for_surface(state, new_profile) -> None:
    """Rebuild or clear ``state.journal`` for the target surface.

    ``cmd_code`` only calls ``build_project_journal`` at process launch. Live
    ``/project switch`` (and scratch → real project) used to keep the *old*
    ProjectJournal object — still pointed at the previous root, still
    ``code_on=False`` from scratch — so a project with ``code_auto: true`` never
    started recording after an in-session open. Chat has no project journal.
    """
    _flush_journal_best_effort(state)
    if getattr(new_profile, "mode", None) != "code":
        state.journal = None
        return
    try:
        from xlii.journal import build_project_journal, dispatch_catchup
        state.journal = build_project_journal(state)
        # A live switch is a session open for the target project: heal any tail
        # a previous fast exit deferred there, as a background job.
        dispatch_catchup(state)
    except Exception:
        state.journal = None


def _leave_scratch_if_entering_real_code(state, new_profile) -> None:
    """Scratch is a base surface. Teleporting into a registered code project must
    drop the scratch/no-sync overlay so the target project's normal session
    contract (sync + journal auto) applies — unless the target is still a
    local-only scratch-named project."""
    if getattr(new_profile, "mode", None) != "code":
        return
    if not getattr(state, "scratch", False) and not getattr(state, "no_sync", False):
        return
    project = getattr(new_profile, "project", None)
    if project is None:
        return
    name = (getattr(project, "name", "") or "").lower()
    # Named/home scratch stores use scratch/…; local_only alone is not enough
    # (real projects can be local-only too) — only keep scratch chrome when the
    # name still says scratch OR we never left a scratch-flagged session into
    # something that still looks like one.
    still_scratch = name.startswith("scratch/") or name == "scratch"
    if still_scratch:
        return
    state.scratch = False
    # Only clear no_sync that was implied by scratch; explicit --no-sync on the
    # switch command is re-applied by the caller after switch_to_code_project.
    state.no_sync = False


def _live_switch(ctx: dict[str, Any], new_profile, *, persona) -> bool:
    """Swap the whole Profile on the live agent+state in place, DETACHING the
    conversation: park the leaving surface's thread and restore/seed the target's
    own (RP7). NEVER carries one surface's conversation into another; NEVER sets
    pending_persona_switch."""
    state = ctx.get("state")
    if state is None:
        ctx["console"].print("[red]/code and /chat need an interactive session[/red]")
        return True
    agent = state.agent

    # Park the conversation we're leaving under its surface key (history[1:] — the
    # system prompt at [0] is rebuilt per surface below), so returning resumes it.
    leaving = getattr(state, "profile", None)
    if leaving is not None and agent.history:
        _stash_history(state, _surface_key(leaving, getattr(state, "persona", None)),
                       list(agent.history[1:]))
    # pending_input is per-surface (yield/park, never clobber across /chat).
    if leaving is not None:
        _stash_pending_input(state, _surface_key(leaving, getattr(state, "persona", None)))

    _stash_displaced(state)
    _pause_loop_for_switch(state, new_profile)
    from xlii.cmds.sessions import restore_loadout_profile

    restore_loadout_profile(state)

    # Flush the CURRENT mode's session.json to its OWN project BEFORE repointing,
    # so it captures the surface we're leaving.
    try:
        state.save()
    except Exception:
        # A failed flush costs the leaving project's session.json, not the switch itself.
        pass

    # project — ToolContext is rebuilt from agent.project every turn, so file
    # roots / bash cwd / search_project collection / ignores all follow next turn.
    agent.project = new_profile.project
    state.project = new_profile.project

    # Start the target with a CLEAN attachment slate, then load its own durable
    # state. The clear is load-bearing for isolation: load() only resets
    # attachments when the target has a session.json, so switching into a
    # never-opened persona would otherwise leave the LEAVING surface's docs/refs
    # live and bleed them in (a persona-to-persona contamination leak). Clearing
    # first means a never-saved target starts empty; load() then restores the
    # target's OWN saved attachments when it does have a session.json (it
    # overwrites these fields), and the loadout (below) re-applies the target
    # persona's declared ones. Attachments are thus strictly per-surface; the
    # conversation is parked/restored per surface (the DETACH block below), not
    # carried. The trust tier is session-wide, not per-project, so preserve it —
    # both flags, so a live freeball survives load() overwriting yolo (restore
    # order matters: yolo first, then freeball re-raises the tier if it was on).
    _yolo = state.yolo
    _freeball = state.freeball
    _auto_approve = set(state.auto_approve)
    state.attached_refs = []
    state.attached_docs = []
    try:
        state.load()
    except Exception:
        # No saved session for the target -- start from the cleared state set above.
        pass
    state.yolo = _yolo
    state.freeball = _freeball
    state.auto_approve = _auto_approve

    # base system prompt for the target surface (run_turn re-derives history[0]
    # from it each turn).
    agent.base_system_prompt = new_profile.system_prompt()

    # DETACH: rebuild the conversation as the TARGET surface's own — its parked
    # thread, or a fresh seed from its own turn store. This is what keeps a chat
    # persona oblivious to the code (and to other personas): the leaving surface's
    # conversation is parked, never carried in.
    agent.history = ([{"role": "system", "content": agent.base_system_prompt}]
                     + _restore_or_seed(state, new_profile, persona))

    # clear affordances the TARGET surface lacks. Follow the h_plan convention:
    # write BOTH the Agent and the REPLState flag.
    if new_profile.mode != "code":
        # Chat has no ModeController slot — park any code-only mode (plan/rail/
        # debug/discovery/ops) so the wrong directive/tools don't leak onto the
        # persona surface.
        if getattr(agent, "active_mode", None) is not None:
            agent.set_mode(None)
        state.plan_mode = False
    else:
        if "rail" not in new_profile.affordances:
            agent.rail = None
            # Debug mode is a code-only staged mode like the rail; the chat surface
            # has no debug affordance, so leaving code parks it too.
            if getattr(agent, "debug", None) is not None:
                agent.debug = None
        if "plan" not in new_profile.affordances:
            agent.plan_mode = False
            state.plan_mode = False
    # A role equip (code) / become (chat) is surface state; the equipped
    # attachments are cleared + reloaded per surface above/below, so drop the
    # active-role marker too. A chat-become re-sets it after this switch returns.
    agent.session.active_role = None
    # /howto is an overlay on the surface you were in — leaving it exits howto
    # mode. The chat model role then follows the target surface (conversational
    # for chat, build-model for code).
    agent.session.howto_mode = False
    agent.session.conversational = (new_profile.mode == "chat")

    # reset sticky loadout overrides + the one-shot /temp (they would otherwise
    # leak across the switch); the new loadout (applied below, against the NEW
    # project) may re-pin model/temperature.
    agent.model_override = None
    agent.temperature_override = None
    agent.next_turn_temp_override = None
    agent.session.next_turn_temp_chat_only = False

    # identity/scope drivers: persona flips dispatch scope, shell-primary, title.
    state.persona = persona
    state.profile = new_profile
    state.command_scope = new_profile.command_scope()
    _restore_pending_input(state, _surface_key(new_profile, persona))

    # apply the new loadout against the NEW project (no-op for code; persona None).
    new_profile.loadout.apply(state, new_profile.project)

    # code needs a live shell_cwd (chat ignores it — _is_shell_primary short-
    # circuits on persona). Reseed from the project root (sessions.py parity).
    if new_profile.mode == "code":
        state.shell_cwd = new_profile.project.project_root.resolve()

    # Leaving scratch into a real project: drop scratch · no-sync chrome so
    # journal auto + sync match a normal `xlii code` launch of that project.
    _leave_scratch_if_entering_real_code(state, new_profile)

    # Journal is per-project and only built at cmd_code launch — rebind here or
    # code_auto never engages after /project find --go / switch from scratch.
    _rebind_journal_for_surface(state, new_profile)

    # keep the nested-session guard env pointed at the live project.
    from xlii.session_state import mark_session_active
    mark_session_active(new_profile.project.project_root)
    return True


def _parse_id(line: str) -> Optional[str]:
    """Pull the persona name from `/chat`, `/chat NAME`, or `/chat --id NAME`."""
    toks = line.split()[1:]  # drop "/chat"
    if not toks:
        return None
    if toks[0] in ("--id", "-i") and len(toks) > 1:
        return toks[1]
    return toks[0]


def _parse_chat_args(line: str) -> "tuple[Optional[str], bool]":
    """(persona name or None, read_proj) for `/chat [--id NAME] [--read-proj]`.

    ``--read-proj`` (V3b) opts the session into read-only project awareness —
    the one explicit escape from chat's project-blind default."""
    read_proj = "--read-proj" in line.split()[1:]
    return _parse_id(" ".join(t for t in line.split() if t != "--read-proj")), read_proj


def switch_to_persona(ctx: dict[str, Any], persona) -> bool:
    """Lazy-init `persona`'s chat project and live-switch onto it in place.

    Shared by `/chat` (raw persona switch) and `/role <name>` in chat (become).
    Returns True on success — the caller prints the surface-specific confirmation
    — or False if the project init failed (the REPL stays put, current session
    intact). The target's loadout (incl. a role's skills/docs/plugins/model) is
    materialized inside `_live_switch` via `new_profile.loadout.apply`."""
    state = ctx["state"]
    console = ctx["console"]
    from xlii.turn_store import CHAT_RECENT_TURNS
    from xlii.config import ProjectConfig
    from xlii.profile import chat_profile

    # Lazy persona-project init (parity with _chat_run_session) — its OWN
    # try/except so a network failure ABORTS the switch and keeps the REPL alive.
    persona.project_root.mkdir(parents=True, exist_ok=True)
    project = ProjectConfig.load(persona.project_root)
    if project is None:
        from xlii.sync import init_project
        try:
            with console.status(f"[cyan]initializing persona project for {persona.name}…[/cyan]"):
                project = init_project(state.pool.primary(), persona.project_root,
                                       name=f"chat/{persona.name}")
        except Exception as e:
            console.print(f"[red]could not init persona project: {e} — staying put[/red]")
            return False

    profile = chat_profile(persona, project, seed_limit=CHAT_RECENT_TURNS)
    persona.touch_used()
    _live_switch(ctx, profile, persona=persona)
    return True


def switch_to_code_project(
    ctx: dict[str, Any],
    project,
    *,
    reset: bool = False,
    reload_surface: bool = True,
) -> bool:
    """Live-switch the current interactive session onto another code project."""
    state = ctx.get("state")
    console = ctx["console"]
    if state is None:
        console.print("[red]project switching needs an interactive session[/red]")
        return True

    from xlii.turn_store import CHAT_RECENT_TURNS
    from xlii.profile import code_profile

    current = getattr(state, "project", None)
    if current is not None and current.project_root.resolve() == project.project_root.resolve():
        console.print(f"[dim]already in project {project.name}[/dim]")
        if reset:
            state.agent.history = state.agent.history[:1]
            state.plan_mode = False
            if state.agent.rail is not None:
                state.agent.rail.reset()
            if state.agent.debug is not None:
                state.agent.debug.reset()
            console.print("[dim]forgot this chat[/dim]")
        return True

    profile = code_profile(project, seed_limit=CHAT_RECENT_TURNS)
    _live_switch(ctx, profile, persona=None)
    try:
        from xlii.workbench import resolve_active

        state.workbench = resolve_active(project.xli_dir)
    except Exception:
        # The switch already happened; workbench stays unset until the next resolve.
        pass

    if reset:
        state.agent.history = state.agent.history[:1]
        state.plan_mode = False
        if state.agent.rail is not None:
            state.agent.rail.reset()
        if state.agent.debug is not None:
            state.agent.debug.reset()

    if reload_surface:
        try:
            from xlii.commands import reload_project_commands
            reload_project_commands(project.xli_dir)
        except Exception as e:
            console.print(f"[yellow]project commands reload failed: {type(e).__name__}: {e}[/yellow]")
        try:
            from xlii.tools import load_project_tools
            load_project_tools(project.xli_dir)
        except Exception as e:
            console.print(f"[yellow]project tools reload failed: {type(e).__name__}: {e}[/yellow]")

    try:
        from xlii.recent_desks import touch_desk

        touch_desk(project)
    except Exception:
        # Recent-desk bookkeeping is cosmetic.
        pass
    where = str(project.project_root)
    try:
        from xlii.desk_files import files_address

        pointed = files_address(project)
        if pointed:
            where = pointed
    except Exception as e:
        console.print(
            f"[dim yellow]files_root lookup failed: {type(e).__name__}: {e}[/dim yellow]"
        )
    console.print(f"[cyan]switched project[/cyan] [dim]· {project.name} · {where}[/dim]")
    if reset:
        console.print("[dim]forgot this chat[/dim]")
    # Startup-task fire lives HERE, not in _live_switch, so /howto / /chat
    # detour-returns through the stashed-profile path don't re-fire.
    try:
        from xlii.session_boot import apply_startup_task

        apply_startup_task(
            state, project, console=console,
            preview=False,
            scratch=bool(getattr(state, "scratch", False)),
            no_startup=False,
            interactive=True,
        )
    except Exception as exc:
        console.print(
            f"[dim]startup task skipped — {type(exc).__name__}: {exc}[/dim]"
        )
    hook = getattr(state, "on_desk_switched", None)
    if callable(hook):
        try:
            hook(project)
        except Exception as exc:
            console.print(
                f"[dim yellow]desk land skipped: {type(exc).__name__}: {exc}[/dim yellow]"
            )
    return True


def h_chat(line: str, ctx: dict[str, Any]) -> bool:
    """/chat [--id NAME] — switch to a chat persona in place (its OWN detached
    thread; the code conversation is parked, never carried in — RP7)."""
    state = ctx.get("state")
    console = ctx["console"]
    if state is None:
        console.print("[red]/chat needs an interactive session[/red]")
        return True

    from xlii.cmds.sessions import _resolve_persona_to_load

    # Bare `/chat` sits with the shipped chat persona (iXaac) — a costume,
    # not the journal. `[M]` / `/mojo` is the mojo. An explicit name wins.
    from xlii.persona import CHAT_DEFAULT_PERSONA_ID
    requested, read_proj = _parse_chat_args(line)
    if requested is None:
        requested = CHAT_DEFAULT_PERSONA_ID
    persona = _resolve_persona_to_load(requested)
    if persona is None:
        return True  # resolver already explained why (bad name / multi-persona bail)

    if getattr(state, "profile", None) and state.profile.mode == "chat" \
            and getattr(state.persona, "name", None) == persona.name:
        # Same persona, but the awareness flag may still be changing hands.
        state.agent.session.chat_read_proj = read_proj
        console.print(f"[dim]already chatting as {persona.name}[/dim]")
        return True

    if not switch_to_persona(ctx, persona):
        return True  # init failed — current session intact, message already printed
    # V3b: the awareness flag is set on the LIVE session after the switch —
    # every plain /chat re-arms the project-blind default.
    state.agent.session.chat_read_proj = read_proj
    note = " · read-only project awareness" if read_proj else ""
    console.print(f"[magenta]now chatting as {persona.name}[/magenta] "
                  f"[dim](its own thread{note} · /code to return)[/dim]")
    return True


def h_code(line: str, ctx: dict[str, Any]) -> bool:
    """/code — switch back to the code surface in place.

    With no code surface stashed this session (started in chat, or the scratch
    case), the door runs the ONE project gate (V3a) — the same
    detect → init → resolve flow `xlii code` runs, expressed via
    ``mode_contract``'s EntryGate for code — instead of just refusing."""
    state = ctx.get("state")
    console = ctx["console"]
    if state is None:
        console.print("[red]/code needs an interactive session[/red]")
        return True
    if getattr(state, "profile", None) and state.profile.mode == "code":
        console.print("[dim]already in code mode[/dim]")
        return True
    prof = (getattr(state, "_profile_stash", None) or {}).get("code")
    if prof is not None:
        _live_switch(ctx, prof, persona=None)
        console.print(f"[cyan]back in code mode[/cyan] [dim]· {prof.project.name}[/dim]")
        return True

    # V3a: no stashed code surface — run the shared gate over the live cwd.
    from pathlib import Path

    from xlii.session_boot import gate_code_entry

    root = Path(getattr(state, "shell_cwd", None) or Path.cwd())
    from xlii.cmds.sessions.code import _prompt_launch_gate
    gate = gate_code_entry(
        root,
        interactive=True,
        ask_launch=_prompt_launch_gate,
        trust_tier=("freeball" if getattr(state, "freeball", False)
                    else "yolo" if getattr(state, "yolo", False) else "safe"),
        loop_active=getattr(state, "loop", None) is not None,
        surface="chat" if getattr(state, "persona", None) is not None else "code",
    )
    if gate.choice == "cancel":
        console.print("[yellow]cancelled.[/yellow]")
        return True
    if gate.choice == "refuse" or gate.project is None:
        reasons = "; ".join(gate.verdict.reasons) or "no project here"
        console.print(
            f"[red]cannot enter code mode: {reasons}[/red] [dim]— "
            "[cyan]xlii init[/cyan] here, or [cyan]xlii code <dir>[/cyan] "
            "from the shell, initializes a project.[/dim]"
        )
        return True
    if gate.preview_state_dir is not None:
        # An in-session preview's temp state dir dies with the process (the CLI
        # door cleans it in its own finally; the REPL outlives this switch).
        import atexit
        import shutil

        atexit.register(shutil.rmtree, gate.preview_state_dir, ignore_errors=True)
    return switch_to_code_project(ctx, gate.project)


def register() -> None:
    register_repl_command(REPLCommand(
        name="chat", handler=h_chat, usage="/chat [--id NAME] [--read-proj]",
        description="Switch to a chat persona in place (its own detached thread; --read-proj opts into read-only project awareness)",
        category="session", repls=["code", "chat"]))
    register_repl_command(REPLCommand(
        name="code", handler=h_code,
        description="Switch back to the code surface in place",
        category="session", repls=["code", "chat"]))
