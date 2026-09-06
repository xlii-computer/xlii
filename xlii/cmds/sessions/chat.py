"""`xlii chat` command and session runner — a façade over xlii.session_boot (B4).

The session assembly (persona project, startup sync, agent/state, profile) lives
kernel-side in ``xlii.session_boot``; this file keeps the argparse shape, the
persona resolution, the banner, and the inline/TUI run tail (incl. the
persona-switch restart).
"""

from __future__ import annotations

from typing import Any, Optional

import argparse

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.panel import Panel

from xlii import __version__
from xlii.repl import _QuitSession, run_repl_loop
from xlii.repl_cmds.chat import _chat_list_personas
from xlii.session_boot import launch_tui_or_inline, start_chat_session
from xlii.ui import console, format_turn_line

from .personas import _chat_delete_persona, _chat_edit_persona, _chat_new_persona
from .resolve import _resolve_persona_to_load


def cmd_chat(args: argparse.Namespace) -> int:
    """Persona-based conversational agent with persistent memory.

    Sub-routes for maintenance flags (--list / --new / --edit / --delete);
    otherwise launches a chat REPL backed by the named persona.
    """
    if args.list:
        return _chat_list_personas()
    if args.new:
        return _chat_new_persona(args.new)
    if args.edit:
        return _chat_edit_persona(args.edit)
    if args.delete:
        return _chat_delete_persona(args.delete, yes=args.yes)
    try:
        return _chat_run_session(args.name, yolo=args.yolo, force=getattr(args, "force", False),
                                 tui=getattr(args, "tui", False))
    except _QuitSession:
        # A2: /exit·/quit must beat the persona-restart recursion. A _QuitSession
        # raised inside a nested session unwinds past every _chat_run_session
        # frame (skipping the pending-persona-switch restart) to here, so the
        # process exits instead of relaunching as the next persona.
        return 0


def _chat_run_session(requested_name: Optional[str], *, yolo: bool,
                      force: bool = False, is_restart: bool = False,
                      tui: bool = False) -> int:
    persona = _resolve_persona_to_load(requested_name)
    if persona is None:
        return 1

    cs = start_chat_session(persona, yolo=yolo, force=force, is_restart=is_restart)
    if cs is None:
        return 1
    state, agent, project, cfg = cs.state, cs.agent, cs.project, cs.cfg
    persona = cs.persona

    # `xlii chat --tui` (RP4): the Textual UI hosts whatever profile is active, so
    # "chat in the TUI" is just "launch the TUI with the chat profile already on
    # state". Mirrors cmd_code's --tui branch; the inherited agent is fully seeded.
    if tui:
        from xlii.tui_textual import run_tui_over_session

        outcome = launch_tui_or_inline(
            state, agent, project_name=persona.name, run_tui=run_tui_over_session)
        if outcome == "quit":
            # /terminal·/inline drops back to the inline chat REPL below; only a
            # full /exit·/quit ends the process (mirrors cmd_code's --tui path).
            return 0

    history_path = project.xli_dir / "repl_history"
    session: PromptSession[str] = PromptSession(history=FileHistory(str(history_path)))

    yolo_banner = "  ·  [red]YOLO[/red]" if state.yolo else ""
    memory_line = (
        f"memory: {cs.total_turns} turn(s) on disk · {cs.recent_count} loaded inline · "
        f"older searchable via search_project"
    )
    orch_model, orch_role = agent.orchestrator_model_and_role()
    pin = "  [dim](pinned)[/dim]" if agent.model_override else ""
    console.print(
        Panel.fit(
            f"[bold cyan]xlii chat[/bold cyan] v{__version__}  ·  "
            f"[magenta]{persona.name}[/magenta]{yolo_banner}\n"
            f"model: {orch_model} ({orch_role}){pin}  ·  worker: {cfg.worker()}\n"
            f"{memory_line}\n"
            "[dim]type [/dim][bold]/help[/bold][dim] for slash commands · "
            "[/dim][bold]/persona[/bold][dim] to switch[/dim]",
            border_style="magenta",
        )
    )

    def _chat_get_prompt_prefix() -> str:
        # Delegate to the LIVE profile so a /code<->/chat switch is reflected
        # next prompt (the body moved verbatim into Profile.prompt_prefix).
        return state.profile.prompt_prefix(state)

    def _chat_render(result: Any, prompt: str) -> None:
        # Render-ONLY main-turn delivery for the kernel spine (drive_turn owns
        # the chat Profile's TurnStore persist + sync — convergence Phase 4).
        if result.reply:
            console.print()
            console.print(result.reply)
        console.print(format_turn_line(result.stats))
        for warn in getattr(result.stats, "warnings", ()) or ():
            console.print(f"  [yellow]⚠ {warn}[/yellow]")

    run_repl_loop(
        state,
        session=session,
        get_prompt_prefix=_chat_get_prompt_prefix,
        run_turn=agent.run_turn,
        render_turn=_chat_render,
    )

    # /persona <name> mid-session: restart the whole session as the new persona.
    switch_to = getattr(state, "pending_persona_switch", None)
    if switch_to:
        return _chat_run_session(switch_to, yolo=state.yolo, is_restart=True)
    return 0
