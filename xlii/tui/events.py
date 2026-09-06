"""Compatibility shim — the typed events moved to :mod:`xlii.turn_events`
(godzilla-mothra V1ab: they are kernel vocabulary, not face code). The names
below are the same objects, so ``isinstance`` checks are unaffected by which
path an importer uses. New kernel code imports from ``xlii.turn_events``; this
shim keeps untouched face/importer call sites working until the Stage-1.5
sweep deletes it.
"""

from __future__ import annotations

from xlii.turn_events import (
    AssistantAnswer,
    MetaMessage,
    ShellRan,
    ShellSource,
    ToolFinished,
    ToolStarted,
    UserTurn,
)

__all__ = [
    "AssistantAnswer",
    "MetaMessage",
    "ShellRan",
    "ShellSource",
    "ToolFinished",
    "ToolStarted",
    "UserTurn",
]
