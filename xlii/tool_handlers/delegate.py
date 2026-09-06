"""Cross-org task-delegation tool handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xlii.tool_context import (
    ToolContext,
    ToolResult,
)

from ._common import _cap_output


def t_codex_run_task(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Delegate a bounded task to OpenAI Codex CLI (cross_org harness)."""
    task = str(args.get("task", "")).strip()
    if not task:
        return ToolResult("task is required", is_error=True)
    mode = str(args.get("mode", "ask"))
    if mode not in ("ask", "plan", "agent"):
        return ToolResult("mode must be ask, plan, or agent", is_error=True)
    # ctx.plan_mode mirrors read_only_tool_palette, so this blocks workspace-write
    # Codex runs in every read-only palette (plan / discovery / debug analyze /
    # rail read-only stages) where direct edit_file/bash are also withheld.
    if ctx.plan_mode and mode == "agent":
        return ToolResult(
            "codex_run_task: agent mode is not available in read-only mode; use ask or plan",
            is_error=True,
        )
    model = args.get("model")
    try:
        timeout_s = int(args.get("timeout_s", 600))
    except (TypeError, ValueError):
        return ToolResult("timeout_s must be an integer", is_error=True)
    if timeout_s <= 0:
        return ToolResult("timeout_s must be a positive integer", is_error=True)
    from xlii.harness.codex_tool import run_codex_task

    text, err = run_codex_task(
        task,
        project_root=Path(ctx.project.project_root),
        mode=mode,
        model=model,
        timeout_s=timeout_s,
    )
    if err:
        return ToolResult(f"codex_run_task unavailable: {err}", is_error=True)
    return ToolResult(_cap_output(ctx, text or "(empty response)"))
