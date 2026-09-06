"""Nested session guard and end-of-turn sync.

Façade — the XLII_SESSION env protocol lives in the kernel at
``xlii/session_state.py`` (``mark_session_active`` / ``detect_nested_session``)
and the end-of-turn sync gate at ``xlii/sync.py`` (``end_of_turn_sync``),
godzilla-mothra Stage 1 (B2). This module keeps the old import paths working;
guard rendering is owned by :func:`xlii.session_boot.nested_session_guard`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from xlii.repl import REPLState
from xlii.session_state import mark_session_active
from xlii.sync import end_of_turn_sync


def _end_of_turn_sync(state: REPLState, dirty: set[str]) -> None:
    return end_of_turn_sync(state, dirty)


def _mark_session_active(project_root: Optional[Path]) -> None:
    return mark_session_active(project_root)


def _nested_session_guard(console, *, project_root: Optional[Path] = None,
                          force: bool = False) -> bool:
    from xlii.session_boot import nested_session_guard
    return nested_session_guard(console, project_root=project_root, force=force)
