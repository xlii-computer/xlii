"""Chat persona CRUD helpers."""

from __future__ import annotations

from xlii.persona import (
    Persona,
    create_persona,
    delete_persona,
    is_valid_name,
    open_in_editor,
)
from xlii.ui import confirm, console


def _chat_new_persona(name: str) -> int:
    if not is_valid_name(name):
        console.print(f"[red]invalid persona name: {name!r}[/red]")
        return 1
    p = Persona(name)
    if p.exists():
        console.print(f"[yellow]persona {name!r} already exists[/yellow] — use --edit instead")
        return 1
    create_persona(name)
    console.print(f"[green]✓[/green] created persona [bold]{name}[/bold] at {p.prompt_path}")
    console.print("[dim]opening $EDITOR — save and quit when done…[/dim]")
    open_in_editor(p.prompt_path)
    console.print(
        f"[dim]ready. Run [cyan]xlii chat {name}[/cyan] to start a session.[/dim]"
    )
    return 0


def _chat_edit_persona(name: str) -> int:
    p = Persona(name)
    if not p.exists():
        console.print(f"[red]no such persona: {name!r}[/red]")
        return 1
    open_in_editor(p.prompt_path)
    console.print(f"[dim]ready. Run [cyan]xlii chat {name}[/cyan] to start a session.[/dim]")
    return 0


def _chat_delete_persona(name: str, *, yes: bool) -> int:
    p = Persona(name)
    if not p.exists() and not p.project_root.exists():
        console.print(f"[red]no such persona: {name!r}[/red]")
        return 1
    if not yes:
        console.print(
            f"[yellow]about to delete persona [bold]{name}[/bold][/yellow]\n"
            f"  prompt:  {p.prompt_path}\n"
            f"  state:   {p.project_root}\n"
            f"  remote:  the persona's Collection will be left orphaned — "
            f"use [cyan]xlii gc[/cyan] to clean up afterwards"
        )
        if not confirm("delete? [y/N] "):
            console.print("[dim]aborted[/dim]")
            return 1
    prompt_removed, state_removed = delete_persona(name)
    console.print(
        f"[green]✓[/green] deleted persona {name!r} "
        f"(prompt={'yes' if prompt_removed else 'no'}, state={'yes' if state_removed else 'no'})"
    )
    return 0
