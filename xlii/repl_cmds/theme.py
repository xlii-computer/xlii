"""``/theme`` — open the theme picker panel (Options → Theme…).

The primary door to theme-switching is the **Options → Theme…** menu item; this command is the
keyboard/REPL twin. It docks the clickable Themes panel in Pane 2 (a click applies + persists the
theme, repainting the whole TUI) through the published panel-host seam (``xlii.tui.panels``), which
``launch()`` wires to the running Textual app. Off the TUI (the inline REPL) there is no host, so it
nudges the user to ``--tui`` instead of failing — mirroring ``/panel`` and ``/file-tab``.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command

_USAGE = "/theme"


def _cmd_theme(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[red]No active REPLState[/red]")
        return True

    from xlii.tui import panels

    host = panels.current_panel_host()
    if host is None:
        console.print(
            "[dim]/theme needs the full-screen TUI — run [cyan]xlii code --tui[/cyan] "
            "(or [cyan]/tui[/cyan]) first.[/dim]"
        )
        return True

    side_for_open = host.current_side() or "right"
    if panels.show_panel(side_for_open, "themes", state=state):
        console.print("[green]✓[/green] theme picker docked — click a theme to apply it")
    else:
        console.print("[yellow]couldn't open the theme panel[/yellow]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="theme",
            handler=_cmd_theme,
            description="Open the theme picker panel — click a theme to apply it (TUI)",
            usage=_USAGE,
            category="session",
        )
    )
