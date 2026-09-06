"""/map — the project's shape (repo map) rendered, or attached as session context.

A thin client of :func:`xlii.repo_map.build_map` (client-#1 doctrine — the same
bytes the agent tool, ``xlii map``, and the ``map://`` provider serve):

- Bare ``/map [path]`` — render the outline to the console, regenerated each run.
- ``/map attach [path]`` — attach it via the same ``attach_doc`` seam ``/howto``
  rides (reserved doc name ``map``, so ``/detach map`` works for free;
  replace-on-reattach; the :data:`~xlii.doc.INLINE_SOFT_CAP_BYTES` size warning).
- ``/map off`` — detach.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xlii.commands import REPLCommand, register_repl_command

# Reserved attachment name — also why `/detach map` works for free.
_DOC_NAME = "map"

_OFF_WORDS = {"off", "detach", "--off"}


def _root(ctx: dict[str, Any]) -> Path:
    """The project root when a project is live, else the cwd (orientation still works)."""
    root = getattr(ctx.get("project"), "project_root", None)
    return Path(root) if root else Path.cwd()


def _attachment_owner(ctx: dict[str, Any]):
    """REPLState (preferred) or the legacy Agent — whatever holds attached_docs."""
    state = ctx.get("state")
    if state is not None and hasattr(state, "attached_docs"):
        return state
    return ctx.get("agent")


def _detach(owner) -> bool:
    if owner is None:
        return False
    if hasattr(owner, "detach_doc"):
        return owner.detach_doc(_DOC_NAME)
    before = len(owner.attached_docs)
    owner.attached_docs = [(n, c) for n, c in owner.attached_docs if n != _DOC_NAME]
    return len(owner.attached_docs) < before


def _reattach(owner, content: str) -> int:
    """(Re)attach, replacing any prior copy so re-running /map attach refreshes it."""
    _detach(owner)
    if hasattr(owner, "attach_doc"):
        owner.attach_doc(_DOC_NAME, content)
    else:
        owner.attached_docs.append((_DOC_NAME, content))
    return len(content)


def _warn_if_large(console, size: int) -> None:
    from xlii.doc import INLINE_SOFT_CAP_BYTES

    if size > INLINE_SOFT_CAP_BYTES:
        console.print(
            f"[yellow]⚠ {size:,} bytes is large for the system prompt "
            f"(soft cap {INLINE_SOFT_CAP_BYTES:,}) — it adds tokens every turn.[/yellow] "
            "[dim]/map off when you're done.[/dim]"
        )


def _build(ctx: dict[str, Any], scope: str) -> str:
    from xlii.repo_map import build_map

    return build_map(_root(ctx), scope=scope or None)


def _handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    parts = line.split(maxsplit=2)
    arg = parts[1].strip() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""

    # /map off — detach the attached outline.
    if arg.lower() in _OFF_WORDS:
        if _detach(_attachment_owner(ctx)):
            console.print("[green]✓[/green] map detached — [dim]system prompt back to normal.[/dim]")
        else:
            console.print("[dim](no map attached)[/dim]")
        return True

    # /map attach [path] — session-long orientation via the attach_doc seam.
    if arg.lower() == "attach":
        owner = _attachment_owner(ctx)
        if owner is None:
            console.print("[red]/map attach needs an active session[/red]")
            return True
        try:
            content = _build(ctx, rest)
        except ValueError as e:
            console.print(f"[red]map: {e}[/red]")
            return True
        size = _reattach(owner, content)
        label = rest or "."
        console.print(
            f"[green]✓[/green] map of [cyan]{label}[/cyan] attached "
            f"[dim]({size:,} bytes — regenerate with /map attach; /map off to drop).[/dim]"
        )
        _warn_if_large(console, size)
        return True

    # Bare /map [path] — render to console, regenerated each run.
    scope = f"{arg} {rest}".strip() if rest else arg
    try:
        content = _build(ctx, scope)
    except ValueError as e:
        console.print(f"[red]map: {e}[/red]")
        return True
    from rich.text import Text

    console.print(Text(content))  # raw engine bytes — never parsed as markup
    console.print("[dim]/map attach" + (f" {scope}" if scope else "")
                  + " to keep it in context · /map off to drop it[/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="map",
            handler=_handler,
            description="Repo map: file tree + Python signatures — render it, or attach it as session context",
            usage="/map [path] | attach [path] | off",
            category="project",
        )
    )
