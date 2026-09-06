"""/media — the persona's media inbox in a live session (tauri-face V5).

What the phone sent mojo, persisted by the daemon and pulled home by
``xlii fabric pull``, surfaced where you're coding: ``/media`` lists newest
first (with the capture-time caption + sender), ``/media attach <n|name>``
drops one into the Tray so the NEXT turn's agent actually sees the pixels —
the screenshot-with-arrows finally reaches the coding agent's eyes.

Not ``/attach`` (doc/ref — text context channels) and not ``artifacts://``
(what xlii made): this is what ARRIVED. The store is ``media://``.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def _rows():
    from xlii.addressing import Address
    from xlii.addressing.builtins.media import MediaProvider

    return MediaProvider().list(Address.parse("media://"))


def h_media(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    parts = line.split()
    args = parts[1:]

    if args and args[0] == "attach":
        if state is None:
            console.print("[red]/media attach needs an interactive session[/red]")
            return True
        target = " ".join(args[1:]).strip()
        if not target:
            console.print("[dim]usage: [cyan]/media attach <n|name>[/cyan][/dim]")
            return True
        rows = _rows()
        pick = None
        if target.isdigit() and 1 <= int(target) <= len(rows):
            pick = rows[int(target) - 1]
        else:
            pick = next((n for n in rows if n.name == target), None)
        if pick is None:
            console.print(f"[red]/media attach: no such item {target!r}[/red] "
                          "[dim](/media lists them)[/dim]")
            return True
        state.attach_file(pick.extra["path"])
        console.print(f"[dim]attached: {pick.name} — in the Tray; the next "
                      "turn sees it[/dim]")
        return True

    limit = 10
    if args and args[0].isdigit():
        limit = max(1, int(args[0]))
    elif args:
        console.print("[dim]usage: [cyan]/media [n] | attach <n|name>[/cyan][/dim]")
        return True

    rows = _rows()
    if not rows:
        console.print("[dim]media inbox empty — nothing persisted here or "
                      "pulled from the fabric yet ([cyan]xlii fabric pull[/cyan])[/dim]")
        return True
    from xlii.fleet_status import format_age
    for i, node in enumerate(rows[:limit], 1):
        extra = node.extra or {}
        kb = int(extra.get("bytes", 0)) // 1024
        age = format_age(extra.get("mtime"))
        detail = " · ".join(
            s for s in (extra.get("sender", ""), extra.get("caption", "")) if s
        )
        tail = f" · {detail}" if detail else ""
        console.print(f"[dim]{i}.[/dim] {node.name} [dim]· {kb} KB · {age}{tail}[/dim]")
    if len(rows) > limit:
        console.print(f"[dim]… {len(rows) - limit} more — /media {len(rows)}[/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="media",
            handler=h_media,
            usage="/media [n] | attach <n|name>",
            description="The media inbox — files the phone sent mojo; attach one to the Tray",
            category="knowledge",
            repls=["code", "chat"],
        )
    )
