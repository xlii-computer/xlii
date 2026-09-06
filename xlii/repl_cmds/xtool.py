"""/xtool — project-sniffed lint/format catalog (Track J).

``/xtool ls`` lists grouped tools with availability. ``/xtool <tool> [target]``
seeds ``!<argv>`` into the command line for review-before-run — it never executes.
Destructive variants (``--fix`` / ``--write``) appear in the seeded line. Missing
binaries are marked in ``ls`` and refused on prefill.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xlii.commands import REPLCommand, register_repl_command
from xlii.xtool_catalog import (
    entry_available,
    format_argv,
    lookup,
    ls_lines,
    project_fingerprints,
    resolve_tool_id,
)

_USAGE = "/xtool ls | <tool-id> [target] [--fix]"


def _project_root(state: Any) -> Path | None:
    root = getattr(getattr(state, "project", None), "project_root", None)
    return Path(str(root)) if root else None


def _target_path(target: str) -> Path | None:
    """Explicit target argument, else None — the ``<path>``/``<dir>`` placeholders stay."""
    if target:
        return Path(target).expanduser()
    return None


def _prefill(state: Any, line: str, console: Any) -> None:
    from xlii.repl_state import queue_pending_input

    queue_pending_input(state, line, replace=True)
    console.print(f"[dim]seeded:[/dim] [cyan]{line}[/cyan]")
    console.print("[dim]review the line, then Enter to run[/dim]")


def _handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx.get("console")
    state = ctx.get("state")
    tokens = line.split()[1:]
    if not tokens or tokens[0] in ("help", "-h", "--help"):
        console.print(_USAGE)
        return True

    root = _project_root(state)
    fps = project_fingerprints(root)

    if tokens[0] == "ls":
        body = ls_lines(fps)
        if not body:
            console.print("[dim]no catalog entries[/dim]")
        else:
            console.print("\n".join(body).rstrip())
        return True

    fix = False
    args: list[str] = []
    for tok in tokens:
        if tok == "--fix":
            fix = True
        else:
            args.append(tok)
    if not args:
        console.print(_USAGE)
        return True

    tool_id = resolve_tool_id(args[0], fix=fix)
    entry = lookup(tool_id)
    if entry is None:
        console.print(f"[red]unknown tool:[/red] {args[0]!r} [dim](try /xtool ls)[/dim]")
        return True
    if not entry_available(entry):
        console.print(f"[red]missing binary:[/red] {entry.binary!r} [dim](not on PATH)[/dim]")
        return True

    target = " ".join(args[1:]).strip()
    argv = format_argv(entry, dock_path=_target_path(target))
    if state is not None:
        _prefill(state, f"!{argv}", console)
    else:
        console.print(f"[cyan]!{argv}[/cyan]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="xtool",
            handler=_handler,
            description="Lint/format catalog — list tools or seed !<argv> for review",
            usage=_USAGE,
            category="console",
            repls=["code"],
        )
    )
