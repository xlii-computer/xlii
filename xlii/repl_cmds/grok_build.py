"""/grok-build — drive Grok Build (ACP) from inside xlii.

Alias for ``/delegate grok``. The command surface is ``grok-build`` (and ``build``);
the internal harness key stays ``grok``. See ``xlii/repl_cmds/delegate.py``.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def _grok_build_handler(line: str, ctx: dict[str, Any]) -> bool:
    from xlii.repl_cmds.delegate import run_delegate_command
    return run_delegate_command(line, ctx, default_harness="grok")


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="grok-build",
            handler=_grok_build_handler,
            aliases=["build"],
            description="Drive Grok Build (ACP) — one-shot or a persistent session/mode",
            usage="/grok-build [new <n>|@<n> [--bg] <task>|ls|on|off|close <n>] | [--plan|--ask] [--context] <task>",
            category="knowledge",
            repls=["code", "chat"],
            conversational=True,
        )
    )
