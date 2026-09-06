"""Plan-domain tool handlers (check / amend)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xlii.tool_context import (
    ToolContext,
    ToolResult,
)

from ._common import _mark_dirty


def t_plan_check(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """The implementer's ONE sanctioned write into plans/ (plan-write-domain
    P2): flip a `- [ ] {#id}` checkbox and annotate evidence — never edit plan
    text. Deliberately NOT routed through _resolve_in_project or
    write_path_refusal: the op takes an id plus an optional bare plan NAME (not
    a path — resolve_plan_file rejects separators/`..`/anchors and enforces
    resolved containment inside plans/), so it is confined to plans/ and works
    even when plans_dir lives outside project_root (state_dir_override
    preview). The call lands in the turn receipt's tool_names — no extra
    receipts.jsonl write here."""
    from xlii.plan_ops import PlanOpError, check_plan_item

    plans_dir = (Path(ctx.project.xli_dir) / "plans").resolve()
    try:
        res = check_plan_item(
            plans_dir,
            # `or ""` (not a bare .get default) so item_id=null can never
            # stringify into a literal "None" lookup.
            str(args.get("item_id") or ""),
            evidence=(args.get("evidence") or None),
            plan=(args.get("plan") or None),
        )
    except PlanOpError as e:
        return ToolResult(str(e), is_error=True)
    if not res.changed:
        return ToolResult(f"{{#{res.item_id}}} in {res.file.name}: {res.note}")
    _mark_dirty(ctx, res.file)  # skips out-of-root plans dirs (preview mode)
    return ToolResult(
        f"{res.note} — {{#{res.item_id}}} in {res.file.name}: {res.line.strip()}"
    )


def t_plan_amend(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """The implementer's PROPOSAL channel into plans/ (plan-write-domain P3):
    append one `- [?] {#am-N}` entry to the plan's `## Amendments` queue —
    the planner resolves it; the implementer never edits the plan itself.
    Same posture as t_plan_check: deliberately NOT routed through
    _resolve_in_project or write_path_refusal (containment comes from
    resolve_plan_file's bare-name rules), preview-safe when plans_dir lives
    outside project_root, and the call already lands in the turn receipt's
    tool_names."""
    from xlii.plan_ops import PlanOpError, amend_plan

    # Guard the payload BEFORE it can stringify: text=null would otherwise
    # queue the literal amendment "None".
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        return ToolResult(
            "plan_amend needs `text` — one line: what you found, what the plan "
            "assumes, what you propose",
            is_error=True,
        )
    plans_dir = (Path(ctx.project.xli_dir) / "plans").resolve()
    try:
        res = amend_plan(
            plans_dir,
            text,
            item_id=(args.get("item_id") or None),
            plan=(args.get("plan") or None),
        )
    except PlanOpError as e:
        return ToolResult(str(e), is_error=True)
    _mark_dirty(ctx, res.file)  # skips out-of-root plans dirs (preview mode)
    return ToolResult(
        f"queued amendment {{#{res.amend_id}}} in {res.file.name} — the planner "
        f"resolves it: {res.line.strip()}"
    )
