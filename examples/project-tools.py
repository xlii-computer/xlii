"""
Example of per-project agent tools for xlii.

Place this as `.xlii/tools.py` (or inside `.xlii/tools/mytools.py`) in your project.

Any function returned by `get_tools()` will be available for the *agent* to call
(just like read_file, bash, etc.).

These are different from `/commands` — those are for *you* to type.
These are for the model to decide to use.
"""

from __future__ import annotations

from typing import Any

from xlii.tools import AgentTool, ToolContext, ToolResult


def _project_specific_search(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Example tool: search only within certain directories or with project conventions."""
    query = args.get("query", "")
    # You have full access to the project via ctx.project, ctx.cfg, etc.

    # For demo purposes, just do a normal grep but pretend it's special
    result = ctx.project  # you can use this

    return ToolResult(
        content=f"[ProjectTool] Would search for: {query} (with your special logic here)\n"
                f"Project root: {ctx.project.project_root}"
    )


def _quick_test_runner(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Run the project's test command and return summary."""
    import subprocess
    from pathlib import Path

    root = ctx.project.project_root

    # Example: try common test commands
    for cmd in ["pytest -q --tb=no", "npm test", "go test ./..."]:
        try:
            result = subprocess.run(
                cmd.split(),
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                return ToolResult(content=f"Tests passed:\n{result.stdout[-500:]}")
            else:
                return ToolResult(content=f"Tests failed:\n{result.stdout}\n{result.stderr}")
        except Exception:
            continue

    return ToolResult(content="Could not find a test command to run.")


def get_tools() -> list[AgentTool]:
    return [
        AgentTool(
            name="project_search",
            description="Search the project using its own conventions and important directories.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"}
                },
                "required": ["query"],
            },
            handler=_project_specific_search,
            parallel_safe=True,
            category="project",
            source="project",
        ),
        AgentTool(
            name="run_project_tests",
            description="Run the project's test suite and return a summary.",
            parameters={"type": "object", "properties": {}},
            handler=_quick_test_runner,
            parallel_safe=False,
            category="project",
            source="project",
        ),
    ]