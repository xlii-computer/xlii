"""Project and persona target resolution."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from xlii.addressing import resolve
from xlii.persona import (
    CHAT_DEFAULT_PERSONA_DISPLAY,
    CHAT_DEFAULT_PERSONA_ID,
    DEFAULT_PERSONA_ID,
    Persona,
    create_persona,
    ensure_default_persona,
    ensure_stock_persona,
    is_valid_name,
    last_used,
)
from xlii.ui import console


def _resolve_project_target(arg: str | None) -> Path | None:
    """Resolve a `xlii chat [target]` argument.

    Rules:
      - None / "" → cwd
      - looks like a path (has / or starts with . or exists as a dir) → that path
      - otherwise → registry lookup by name (exact match preferred, then substring)

    Returns the absolute project path, or None if it can't be resolved. The cwd/path/name
    disambiguation now lives once, in the ``project://`` provider (``xlii.addressing``).
    """
    if not arg:
        return Path.cwd()
    r = resolve(f"project://{arg}")
    if r.ambiguous:
        console.print(f"[yellow]ambiguous: {arg!r} matches:[/yellow]")
        for e in r.matches:
            console.print(f"  · {e.name:<24} {e.path}")
        return None
    if r.detail is not None:
        # registry-name branch: honor the registry resolution's own ok-ness, so a
        # directory whose project config failed to load doesn't masquerade as a hit.
        return r.path if (r.ok and r.path is not None) else None
    # cwd/path branch: any existing directory (init/preview gating happens downstream).
    return r.path if (r.path is not None and r.path.is_dir()) else None


def _lookup_persona(name: str) -> Optional[Persona]:
    """Resolve an explicit persona name without creating or bootstrapping.

    Used by headless surfaces (``ask --persona``, daemon forwarding) where a
    typo or stale config must fail loudly instead of silently minting a new
    identity."""
    if not is_valid_name(name):
        return None
    p = Persona(name)
    return p if p.exists() else None


def _resolve_persona_to_load(requested: Optional[str]) -> Optional[Persona]:
    """Pick which persona to start a session with.

    - explicit name → use it (stock template if we ship one; else a blank)
    - no name → last-used *chat* persona, else the shipped chat default
      (iXaac). The journal (mojo) is not a last-used hit for this door.
    """
    if requested:
        if not is_valid_name(requested):
            console.print(f"[red]invalid persona name: {requested!r}[/red]")
            return None
        p = Persona(requested)
        if not p.exists():
            if requested == DEFAULT_PERSONA_ID:
                return ensure_default_persona(requested)
            try:
                return ensure_stock_persona(requested)
            except FileNotFoundError:
                console.print(
                    f"[dim]persona {requested!r} doesn't exist — creating with default prompt[/dim]"
                )
                create_persona(requested)
                console.print(
                    f"[dim](edit it later with [cyan]xlii chat --edit {requested}[/cyan])[/dim]"
                )
        return p
    journal = DEFAULT_PERSONA_ID
    try:
        from xlii.config import GlobalConfig
        from xlii.persona import factory_persona_id

        journal = factory_persona_id(GlobalConfig.load())
    except Exception:
        # No/unreadable global config — fall back to the default persona id
        # already in `journal` and carry on resolving.
        pass
    last = last_used()
    if last and last.name not in {journal, DEFAULT_PERSONA_ID}:
        return last
    ensure_default_persona()
    console.print(
        f"[dim]opening chat as [bold]{CHAT_DEFAULT_PERSONA_DISPLAY}[/bold]…[/dim]"
    )
    return ensure_stock_persona(CHAT_DEFAULT_PERSONA_ID)
