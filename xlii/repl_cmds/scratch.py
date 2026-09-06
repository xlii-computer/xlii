"""/scratch — enter scratch mode: an ephemeral, unbound, never-sync session.

Scratch mode (Vector S, interaction-III) is the daily-driver half of the
two-window day: traverse the shell freely, never sync. It marks the live session
never-sync (``state.no_sync = True``, *always* — the locked Q3 contract) and
surfaces ``scratch · no-sync`` in the status bar / input frame (xlii.tui.status)
with its own input hint (xlii.hints).

It deliberately does NOT bind or sync to any Collection. A directory becomes
syncable only via an explicit ``xlii code init`` there — the sanctioned
graduation; there is no auto-promote. Even inside a real project, scratch never
collects.

This is the in-session entry point. ``xlii scratch`` (cmds/project.py) spawns a
fresh scratch session that starts in this mode directly.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command

# Words that turn the mode off (mirrors /image, /howto, the mode toggles).
_OFF_WORDS = {"off", "stop", "exit", "--off"}
_STATUS_WORDS = {"status", "show", "?"}

# Stash key for the session's pre-scratch never-sync setting, so /scratch off
# restores it rather than blindly re-enabling sync (a `--no-sync` project that
# toggled scratch must not start syncing again on the way out).
_PREV_NO_SYNC = "_scratch_prev_no_sync"


def _set_scratch(state, on: bool) -> None:
    """Flip scratch mode on the live REPLState. Scratch is inherently never-sync,
    so entering forces ``no_sync`` True and leaving restores the prior setting."""
    if on:
        if not getattr(state, "scratch", False):
            setattr(state, _PREV_NO_SYNC, getattr(state, "no_sync", False))
        state.scratch = True
        state.no_sync = True
    else:
        state.scratch = False
        state.no_sync = getattr(state, _PREV_NO_SYNC, False)


def _scratch_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if state is None:
        console.print("[red]/scratch needs an active session[/red]")
        return True

    parts = line.split(maxsplit=1)
    sub = parts[1].strip().lower() if len(parts) > 1 else ""

    if sub in _OFF_WORDS:
        was_on = bool(getattr(state, "scratch", False))
        _set_scratch(state, False)
        if was_on:
            console.print(
                "[green]✓[/green] [bold #d19a66]scratch mode[/bold #d19a66] off — "
                "[dim]this session's sync setting restored.[/dim]"
            )
        else:
            console.print("[dim](scratch mode wasn't on)[/dim]")
        return True

    if sub in _STATUS_WORDS:
        if getattr(state, "scratch", False):
            console.print("scratch mode: [bold #d19a66]ON[/bold #d19a66] · [dim]no-sync[/dim]")
        else:
            console.print("scratch mode: off")
        return True

    if sub and sub != "on":
        console.print(
            "[dim]usage:[/dim] [cyan]/scratch[/cyan] [dim]| [/dim][cyan]/scratch off[/cyan] "
            "[dim]| [/dim][cyan]/scratch status[/cyan]"
        )
        return True

    _set_scratch(state, True)
    console.print(
        "[green]✓[/green] [bold #d19a66]scratch mode[/bold #d19a66] on — "
        "[dim]ephemeral & never-sync: traverse freely, nothing uploads. A dir "
        "becomes syncable only via [/dim][cyan]xlii code init[/cyan][dim]. "
        "[/dim][cyan]/scratch off[/cyan][dim] to leave.[/dim]"
    )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="scratch",
            handler=_scratch_handler,
            usage="/scratch [off|status]",
            description="Scratch mode: ephemeral, unbound, never-sync session (free-traversal daily driver)",
            category="mode",
            repls=["code"],
        )
    )
