"""Tool security gating — path locks, write domains, debug marker enforcement."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from xlii.tool_context import ToolContext


def write_path_refusal(ctx: ToolContext, resolved: Path) -> Optional[str]:
    """Path-scoped write profiles (plan-write-domain P0): the write-domain check
    for write_file / edit_file.

    ``resolved`` comes from ``_resolve_in_project`` — already fully resolved
    (non-strict, so a not-yet-existing target resolves through its deepest
    existing ancestor). That is what makes containment symlink/`..`-safe: a
    symlink under plans/ pointing at a repo file resolves OUTSIDE plans/ and is
    refused. Deny wins over allow; ``write_allow=None`` means unrestricted.
    Returns the teaching refusal message, or None when the write may proceed."""
    for root in ctx.write_deny:
        # Equality refuses too — the domain root itself is never a write target.
        if resolved.is_relative_to(root):
            return (
                "refused: plans/ is the planner's domain — plan_check marks "
                "items done, plan_amend proposes changes; /plan to revise the plan"
            )
    if ctx.write_allow is None:
        return None
    for root in ctx.write_allow:
        # STRICTLY inside — writing the allow root itself would create a FILE
        # named plans, bricking every later write into the domain.
        if resolved != Path(root) and resolved.is_relative_to(root):
            return None
    return (
        "refused: plan mode writes are limited to .xlii/plans/ — this is where "
        "your plan lives; /execute to write the repo"
    )


def _test_path_locked(ctx: ToolContext, relpath: str) -> bool:
    if not ctx.loop_lock_tests:
        return False
    from xlii.loop_collusion import is_test_path

    norm = relpath.replace("\\", "/").lstrip("./")
    return is_test_path(norm)


def _debug_marker_violation(ctx: ToolContext, old: str, new: str) -> Optional[str]:
    """In the debug Instrument phase, every *added* line (present in `new` but
    not in `old`) must carry DEBUG_MARKER so the cleanup gate can find it. Returns
    an error string for the first offending lines, or None when clean / not in the
    Instrument phase. Whitespace-only added lines are allowed."""
    if not ctx.debug_instrument:
        return None
    from xlii.debug_mode import DEBUG_MARKER

    old_lines = set(old.splitlines())
    bad = [
        ln for ln in new.splitlines()
        if ln.strip() and ln not in old_lines and DEBUG_MARKER not in ln
    ]
    if not bad:
        return None
    sample = " | ".join(b.strip()[:60] for b in bad[:3])
    return (
        f"refused: debug Instrument phase — every added line must carry a "
        f"'{DEBUG_MARKER}' marker so cleanup can find it. Offending: {sample}"
    )
