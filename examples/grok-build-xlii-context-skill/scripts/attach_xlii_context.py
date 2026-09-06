"""
Grok Build ↔ xlii DeepContext attachment helper (reference + test utility).

This module shows the consumption side of the bidirectional bridge.

In a real Grok Build skill execution environment, the model (guided by
SKILL.md) calls the MCP tools directly once `xlii mcp deep-contexts` is
connected. The functions below are useful for:

- Manual testing of the bridge from a Python shell
- Stand-alone demo scripts
- Future skill runtime glue that wants a slightly higher-level API

Run from the repo root with the xlii package importable:

    python -m examples.grok-build-xlii-context-skill.scripts.attach_xlii_context
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# When this script is run inside the xlii worktree, these are real imports.
# In a pure Grok Build environment they would be replaced by MCP client calls.
try:
    from xlii.context import (
        get_live_context,
        load_deep_context,
        mcp_sync_context,
        call_project_tool,
    )
    XLI_AVAILABLE = True
except ImportError:
    XLI_AVAILABLE = False


def attach_live_context(
    project_path: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any] | None:
    """
    Preferred path for Grok Build: get the absolute latest attachments + tools
    straight from the project's on-disk session without any prior ` /context save`.

    This calls the same logic that the MCP tool `xlii_get_live_context` exposes.
    """
    if not XLI_AVAILABLE:
        print("[attach] xlii not importable — in a real Grok Build env you would call the MCP tool xlii_get_live_context")
        return None

    data = get_live_context(project_path=project_path, workspace_name=workspace)
    if data is None:
        print(f"[attach] No xlii session found under {project_path or Path.cwd()}")
        return None

    print(f"[attach] Live context loaded: {data['name']}")
    print(f"         Workspace: {data['source_workspace_name']}")
    print(f"         Refs: {len(data['attached_refs'])}, Docs: {len(data['attached_docs'])}, Tools: {len(data['tools'])}")
    return data


def attach_named_context(name: str) -> dict[str, Any] | None:
    """Load a previously saved/curated DeepContext by name (the `xlii_load_context` path)."""
    if not XLI_AVAILABLE:
        print("[attach] Would call MCP tool: xlii_load_context with", {"name": name})
        return None

    ctx = load_deep_context(name)
    if ctx is None:
        print(f"[attach] DeepContext {name!r} not found in ~/.config/xlii/contexts/")
        return None

    data = ctx.to_dict()
    print(f"[attach] Named DeepContext loaded: {name}")
    print(f"         Source: {data['source_workspace_name']} @ {data['source_project_path']}")
    return data


def sync_back(
    context_name: str,
    *,
    attached_refs: list | None = None,
    attached_docs: list | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """
    Write changes back into both the global DeepContext (if it exists) and the
    original xlii project's session.json.

    This is exactly what the MCP tool `xlii_sync_context` does.
    """
    updates: dict[str, Any] = {}
    if attached_refs is not None:
        updates["attached_refs"] = attached_refs
    if attached_docs is not None:
        updates["attached_docs"] = attached_docs
    if description is not None:
        updates["description"] = description
    if tags is not None:
        updates["tags"] = tags

    if not updates:
        return {"status": "noop", "message": "No updates supplied"}

    if not XLI_AVAILABLE:
        print("[sync] Would call MCP tool xlii_sync_context with:", {"name": context_name, "updates": updates})
        return {"status": "simulated", "updates": updates}

    try:
        result = mcp_sync_context(context_name, updates)
        print(f"[sync] Successfully wrote back to DeepContext {context_name}")
        return result
    except Exception as e:
        print(f"[sync] Write-back failed: {e}")
        return {"status": "error", "error": str(e)}


def call_project_tool(
    name: str,
    args: dict[str, Any] | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """
    Execute one of the custom AgentTools belonging to an xlii project
    (the ones listed in the `tools[]` array of a DeepContext / live context).

    This is the direct Python equivalent of the new MCP tool
    `xlii_call_project_tool`.
    """
    if not XLI_AVAILABLE:
        print("[call] Would call MCP tool xlii_call_project_tool with", {"name": name, "args": args})
        return {"status": "simulated"}

    try:
        return call_project_tool(name=name, args=args, project_path=project_path)
    except Exception as e:
        return {"status": "error", "error": str(e)}


def demo_this_project() -> None:
    """Self-test: attach the live context of the xlii-build repo itself."""
    print("=== xlii ↔ Grok Build bridge self-test ===\n")

    live = attach_live_context()  # uses CWD
    if live:
        print("\nSample attached_docs (first 1-2):")
        for i, (title, content) in enumerate(live.get("attached_docs", [])[:2]):
            print(f"  {i+1}. {title}: {content[:120]}...")

        print("\nCustom project tools discovered:")
        for t in live.get("tools", []):
            print(f"  - {t['name']}: {t.get('description', '')[:70]}")

    print("\n=== Done. In Grok Build you would now have this in long-term memory. ===")


if __name__ == "__main__":
    demo_this_project()

