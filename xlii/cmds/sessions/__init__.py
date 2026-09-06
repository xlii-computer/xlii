"""Interactive session subcommands: code + chat REPLs and their helpers.

Moved out of the former monolithic cli.py (see proposals/done/cli-refactor.md).
"""

from __future__ import annotations

from .ask import cmd_ask
from .chat import cmd_chat
from .code import cmd_code
from .loadout import (
    _apply_persona_loadout,
    _attachment_tag,
    restore_loadout_profile,
)
from .loop import cmd_loop
from .memory import (
    CHAT_RECENT_TURNS,
    _final_reply_from_history,
    persist_code_turn,
    seed_code_history,
)
from .nesting import (
    _end_of_turn_sync,
    _mark_session_active,
    _nested_session_guard,
)
from .register import register
from .resolve import _resolve_persona_to_load, _resolve_project_target

__all__ = [
    "CHAT_RECENT_TURNS",
    "_apply_persona_loadout",
    "_attachment_tag",
    "_end_of_turn_sync",
    "_final_reply_from_history",
    "_mark_session_active",
    "_nested_session_guard",
    "_resolve_persona_to_load",
    "_resolve_project_target",
    "cmd_ask",
    "cmd_chat",
    "cmd_code",
    "cmd_loop",
    "persist_code_turn",
    "register",
    "restore_loadout_profile",
    "seed_code_history",
]
