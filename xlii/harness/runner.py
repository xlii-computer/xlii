"""Dispatch harness ask runs."""

from __future__ import annotations

from xlii.harness.base import HarnessResult
from xlii.harness.brief import HarnessBrief
from xlii.harness.detect import harness_meta, resolve_harness


def run_ask(
    name: str,
    brief: HarnessBrief,
    *,
    model: str | None = None,
    timeout_s: int = 600,
    mode: str = "ask",
    permission: str = "allow",
) -> HarnessResult:
    try:
        adapter = resolve_harness(name)
    except ValueError as e:
        return HarnessResult(
            text="",
            model=model or "",
            harness=name,
            tier=brief.tier or "",
            error=str(e),
        )
    if not brief.tier:
        brief.tier = harness_meta(name)["tier"]
    # Every adapter shares the HarnessAdapter signature (model/timeout_s/mode/
    # permission); unsupported flags are ignored by the adapter, so a single
    # uniform call works for built-in and community harnesses alike.
    return adapter.run_ask(
        brief, model=model, timeout_s=timeout_s, mode=mode, permission=permission
    )
