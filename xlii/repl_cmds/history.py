"""``/history`` — open the input-line history panel (Track F).

Browses ``.xlii/repl_history`` newest-first; selecting a row prefills the
command line for review-before-run. Works on TUI and face (``history://``
side dock). Bare REPL without a panel host nudges to ``--tui`` / face.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command

_USAGE = "/history"


def _cmd_history(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[red]No active REPLState[/red]")
        return True

    from xlii.tui import panels

    host = panels.current_panel_host()
    if host is None:
        console.print(
            "[dim]/history needs a panel surface — [cyan]xlii code --tui[/cyan] "
            "· [cyan]/tui[/cyan] · or the face (Commands → Input history)[/dim]"
        )
        return True

    side_for_open = host.current_side() or "right"
    if panels.show_panel(side_for_open, "history", state=state):
        console.print(
            "[green]✓[/green] history panel docked — select a row to prefill the input"
        )
    else:
        console.print("[yellow]couldn't open the history panel[/yellow]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="history",
            handler=_cmd_history,
            description=(
                "Open the input history panel — browse, prefill, or clear typed "
                "lines (TUI + face)"
            ),
            usage=_USAGE,
            category="session",
        )
    )
