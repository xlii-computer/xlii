"""/claude — drive Claude Code (ACP) from inside xlii.

Alias for ``/delegate claude``. See ``xlii/repl_cmds/delegate.py`` and
``xlii/acp_client.py``.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def _claude_handler(line: str, ctx: dict[str, Any]) -> bool:
    from xlii.repl_cmds.delegate import run_delegate_command
    return run_delegate_command(line, ctx, default_harness="claude")


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="claude",
            handler=_claude_handler,
            description="Drive Claude Code (ACP) — one-shot or a persistent session/mode",
            usage="/claude [new <n>|@<n> [--bg] <task>|ls|on|off|close <n>] | [--plan|--ask] [--context] <task>",
            category="knowledge",
            repls=["code", "chat"],
            conversational=True,
        )
    )
