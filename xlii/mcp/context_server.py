"""
MCP Server for xlii DeepContexts (the bidirectional bridge to Grok Build).

This module exposes DeepContexts + live workspace state as MCP resources
and tools so Grok Build agents can attach xlii projects as durable memory
and custom capabilities, and push changes back.

Usage (from Grok Build or any MCP client):
    - Resource: xlii://contexts
    - Resource: xlii://contexts/{name}
    - Tool:     xlii_list_contexts()
    - Tool:     xlii_load_context(name)           # saved/curated DeepContext
    - Tool:     xlii_get_live_context(...)        # fresh from disk, no save needed
    - Tool:     xlii_sync_context(name, updates)  # write-back
    - Tool:     xlii_call_project_tool(name, args, project_path?)  # invoke custom AgentTools
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from xlii.context import (
    mcp_list_contexts,
    mcp_get_context,
    mcp_sync_context,
    get_live_context,
    call_project_tool,
)

# Create the MCP server instance
mcp = FastMCP("xlii-deep-contexts")


# ------------------------------------------------------------------
# Resources
# ------------------------------------------------------------------

@mcp.resource("xlii://contexts")
def list_contexts_resource() -> list[dict[str, Any]]:
    """List all available DeepContexts."""
    return mcp_list_contexts()


@mcp.resource("xlii://contexts/{name}")
def get_context_resource(name: str) -> dict[str, Any]:
    """Get a specific DeepContext by name."""
    ctx = mcp_get_context(name)
    if ctx is None:
        raise ValueError(f"DeepContext '{name}' not found")
    return ctx


# ------------------------------------------------------------------
# Tools
# ------------------------------------------------------------------

@mcp.tool()
def xlii_load_context(name: str) -> dict[str, Any]:
    """
    Load a DeepContext so a Grok Build agent can use it as long-term memory
    and custom capabilities.
    """
    ctx = mcp_get_context(name)
    if ctx is None:
        raise ValueError(f"DeepContext '{name}' not found")
    return ctx


@mcp.tool()
def xlii_sync_context(name: str, updates: dict[str, Any]) -> dict[str, Any]:
    """
    Write changes back from a Grok Build agent into the DeepContext.

    This is the write-back path for bidirectional bridging.
    """
    return mcp_sync_context(name, updates)


@mcp.tool()
def xlii_list_contexts() -> list[dict[str, Any]]:
    """List all DeepContexts (convenience tool)."""
    return mcp_list_contexts()


@mcp.tool()
def xlii_get_live_context(
    project_path: str | None = None,
    workspace: str | None = None,
) -> dict[str, Any]:
    """
    Get a live snapshot of attachments + custom tools directly from an
    xlii project's .xlii/session.json on disk.

    This is the recommended entry point for Grok Build when you want the
    absolute latest state without the user first running `/context save`
    inside xlii.

    - project_path: directory containing the .xlii folder (defaults to CWD)
    - workspace: specific workspace name, or omit for the current one

    Returns a dict shaped like a DeepContext (with origin="live").
    Returns an error object if no session.json is found.
    """
    data = get_live_context(project_path=project_path, workspace_name=workspace)
    if data is None:
        raise ValueError(
            f"No xlii session found at {project_path or 'current directory'}. "
            "Make sure the directory has been initialized with 'xlii init' "
            "and has a .xlii/ folder with session.json."
        )
    return data


@mcp.tool()
def xlii_call_project_tool(
    name: str,
    args: dict[str, Any] | None = None,
    project_path: str | None = None,
) -> dict[str, Any]:
    """
    Invoke a custom project AgentTool that was registered via the project's
    .xlii/tools.py (or .xlii/tools/*.py).

    The tool must appear in the `tools[]` list returned by a previous
    xlii_get_live_context or xlii_load_context call for the same project.

    This turns the "custom capabilities" advertised in a DeepContext into
    first-class callable functions from Grok Build.

    - name: exact tool name from the context's tools array
    - args: JSON object matching the tool's parameters schema
    - project_path: the project root (defaults to CWD of the MCP server)

    Returns {content, is_error, tool, project}.
    """
    return call_project_tool(name=name, args=args, project_path=project_path)


if __name__ == "__main__":
    # Run as standalone MCP server (useful for testing)
    mcp.run()