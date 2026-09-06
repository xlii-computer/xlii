"""OS / distro environment profile slash command."""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command
from xlii.system_profile import (
    format_profile,
    load_system_profile,
    refresh_system_profile,
    summary_line,
)


def _os_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    parts = line.split()
    refresh = "--refresh" in parts[1:]

    if refresh:
        profile = refresh_system_profile()
        if profile is None:
            console.print("[red]OS detection failed[/red]")
            return True
        console.print("[green]OS profile refreshed[/green]")
    else:
        profile = load_system_profile()
        if profile is None:
            console.print("[red]OS detection failed[/red]")
            return True

    console.print("[bold]system profile[/bold]")
    console.print(format_profile(profile))
    console.print(f"\n[dim]prompt line:[/dim] [SYSTEM] {summary_line(profile)}")
    console.print("[dim]cache:[/dim] in-memory (this session) — /os --refresh to re-probe")
    if not refresh:
        console.print("[dim]re-probe:[/dim] /os --refresh")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="os",
            handler=_os_handler,
            description="Show detected OS/distro profile (injected as [SYSTEM] in prompts).",
            usage="/os [--refresh]",
            category="session",
            repls=["code", "chat"],
        )
    )
