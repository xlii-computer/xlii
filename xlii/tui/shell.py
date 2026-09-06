"""Compatibility shim — the shared shell runner moved to :mod:`xlii.shell_run`
(godzilla-mothra V1ab: subprocess mechanics are kernel code, not face code).
The names below are the same objects. New kernel code imports from
``xlii.shell_run``; this shim keeps untouched call sites working until the
Stage-1.5 sweep deletes it.
"""

from __future__ import annotations

from xlii.shell_run import (
    Capture,
    capture,
    looks_interactive,
    run_shell_captured,
    styled_enabled,
)

__all__ = [
    "Capture",
    "capture",
    "looks_interactive",
    "run_shell_captured",
    "styled_enabled",
]
