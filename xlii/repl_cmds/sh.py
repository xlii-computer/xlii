"""Shell NL→command and output post-process slash commands (Phase 7)."""

from __future__ import annotations

from typing import Any, Literal

from xlii.commands import REPLCommand, register_repl_command
from xlii.shell_toolkit import (
    explain_last,
    nl_command_flow,
    require_last_shell,
    summarize_last,
)

ShMode = Literal["usage", "nl", "explain", "transform"]

_SH_USAGE = (
    "[dim]usage:[/dim]\n"
    "  [cyan]/sh <natural language task>[/cyan]  "
    "[dim]propose a shell command[/dim]\n"
    "  [cyan]/sh --explain[/cyan]  "
    "[dim]explain the last captured shell command + output[/dim]\n"
    "  [cyan]/sh --transform [instruction][/cyan]  "
    "[dim]summarize or transform the last shell output[/dim]"
)


def _sh_parse_mode(line: str) -> tuple[ShMode, str]:
    """Classify a /sh-family line into nl, explain, transform, or usage."""
    body = line.lstrip()
    if not body.startswith("/"):
        return "usage", ""
    parts = body[1:].split(maxsplit=1)
    if not parts:
        return "usage", ""
    cmd = parts[0]
    rest = parts[1].strip() if len(parts) > 1 else ""

    if cmd == "explain":
        return "explain", ""
    if cmd == "shum":
        return "transform", rest
    if cmd != "sh":
        return "usage", ""

    if not rest:
        return "usage", ""
    if rest == "--explain" or rest.startswith("--explain "):
        return "explain", ""
    if rest == "--transform" or rest.startswith("--transform "):
        extra = rest[len("--transform"):].strip()
        return "transform", extra
    return "nl", rest


def _sh_explain(state, console) -> bool:
    ev = require_last_shell(state)
    if ev is None:
        return True
    try:
        text = explain_last(ev)
    except Exception as e:
        console.print(f"[red]explain failed: {e}[/red]")
        return True
    console.print(text)
    return True


def _sh_transform(state, console, extra: str) -> bool:
    ev = require_last_shell(state)
    if ev is None:
        return True
    try:
        text = summarize_last(ev, extra=extra)
    except Exception as e:
        console.print(f"[red]transform failed: {e}[/red]")
        return True
    console.print(text)
    return True


def _sh_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if state is None:
        console.print("[red]no session state[/red]")
        return True

    mode, payload = _sh_parse_mode(line)
    if mode == "usage":
        console.print(_SH_USAGE)
        return True
    if mode == "explain":
        return _sh_explain(state, console)
    if mode == "transform":
        return _sh_transform(state, console, payload)
    nl_command_flow(state, payload)
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="sh",
            handler=_sh_handler,
            aliases=["shum", "explain"],
            description=(
                "Natural language → shell command; or --explain / --transform on last output"
            ),
            usage="/sh <task> | /sh --explain | /sh --transform [instruction]",
            category="session",
            repls=["code"],
        )
    )
