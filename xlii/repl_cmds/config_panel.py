"""``/config`` — open the session-knobs panel (models · budget · no-sync · theme).

The panel shows what each model role RESOLVES to right now (orchestrator ·
worker · chat · help, with a price hint when the pricing table knows the
model); selecting a role row opens the model picker (v2) and persists the
choice through the same config layer as ``xlii models set`` — the panel is a
client of existing seams, not a second config system (tui-config-panel
proposal). The budget row edits the session soft cap in place (the same
fields ``/budget`` writes) and the no-sync row toggles ``state.no_sync`` —
both session-only, never persisted. v3 makes identity live: persona binding
(picker → ``ProjectConfig.save``), daemon-JID editing (``valid_bare_jid``
gate, targeted daemon.toml rewrite), and an on-demand account privacy check
(tier · ZDR · data-sharing — a report, never a toggle: the sharing opt-in is
irreversible and console-side). Off the TUI it nudges to ``--tui``,
mirroring ``/theme``.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command

_USAGE = "/config"


def _cmd_config(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[red]No active REPLState[/red]")
        return True

    from xlii.tui import panels

    host = panels.current_panel_host()
    if host is None:
        console.print(
            "[dim]/config needs the full-screen TUI — run [cyan]xlii code --tui[/cyan] "
            "(or [cyan]/tui[/cyan]) first. Headless, use [cyan]xlii models set[/cyan] "
            "and [cyan]/budget[/cyan].[/dim]"
        )
        return True

    side_for_open = host.current_side() or "right"
    if panels.show_panel(side_for_open, "config", state=state):
        console.print(
            "[green]✓[/green] config panel docked — select a row to edit it"
        )
    else:
        console.print("[yellow]couldn't open the config panel[/yellow]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="config",
            handler=_cmd_config,
            description="Open the config panel — models & temps, budget, iterations, no-sync, hotkey, theme, identity & privacy (TUI)",
            usage=_USAGE,
            category="session",
        )
    )
