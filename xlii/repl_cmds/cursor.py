"""/cursor — drive Cursor's Composer agent over ACP from inside xlii.

Alias for ``/delegate cursor``. See ``xlii/repl_cmds/delegate.py`` and
``xlii/acp_client.py``.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def _cursor_handler(line: str, ctx: dict[str, Any]) -> bool:
    from xlii.repl_cmds.delegate import run_delegate_command
    return run_delegate_command(line, ctx, default_harness="cursor")


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="cursor",
            handler=_cursor_handler,
            description="Drive Cursor's Composer agent (ACP) — one-shot or a persistent session/mode",
            usage="/cursor [new <name>|@<name> [--bg] <task>|ls|on|off|close <name>] | [--plan|--ask] [--model <name>] [--context] <task>",
            category="knowledge",
            repls=["code", "chat"],
            conversational=True,
        )
    )
