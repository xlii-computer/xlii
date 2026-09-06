"""/lock /unlock — Face glass. Overlay is the real door; these are the slash twins."""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def h_lock(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    from xlii.glass import lock_face

    lock_face()
    console.print("[dim]face locked — overlay to unlock[/dim]")
    return True


def h_unlock(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    rest = line.split(maxsplit=1)
    code = rest[1].strip() if len(rest) > 1 else ""
    if not code:
        from xlii.console_prompt import request_line

        got = request_line(console, "unlock code", secret=True)
        code = (got or "").strip()
    from xlii.glass import unlock_face

    ok, reason = unlock_face(code=code)
    if ok:
        console.print("[dim]face unlocked[/dim]")
    else:
        console.print(f"[yellow]unlock refused ({reason})[/yellow]")
    return True


def register() -> None:
    register_repl_command(REPLCommand(
        name="lock",
        handler=h_lock,
        description="Lock this Face. Overlay to unlock. Not /kill.",
        usage="/lock",
        category="session",
        repls=["code", "chat"],
    ))
    register_repl_command(REPLCommand(
        name="unlock",
        handler=h_unlock,
        description="Unlock this Face (desk; phone cannot hostage).",
        usage="/unlock [code]",
        category="session",
        repls=["code", "chat"],
    ))
