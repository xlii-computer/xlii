"""External agent harness adapters (Cursor, Claude Code, …).

xlii stays the substrate; harness CLIs provide multi-model consult, loop judges,
and (future) /delegate targets.
"""

from __future__ import annotations

from xlii.harness.base import HarnessResult
from xlii.harness.brief import HarnessBrief
from xlii.harness.detect import detect_all, harness_meta, list_harness_names, resolve_harness
from xlii.harness.runner import run_ask

__all__ = [
    "HarnessBrief",
    "HarnessResult",
    "detect_all",
    "harness_meta",
    "list_harness_names",
    "resolve_harness",
    "run_ask",
]
