"""MCP integration — inbound doc client and outbound DeepContext server."""

from xlii.mcp.doc_client import MCPError, call_tool, list_tools

# ``mcp`` is deliberately absent: a star-import would eagerly pull in FastMCP's
# context server, which is exactly what the lazy __getattr__ below avoids. Reach
# it explicitly via ``xlii.mcp.mcp`` or ``xlii.mcp.context_server``.
__all__ = ["MCPError", "call_tool", "list_tools"]


def __getattr__(name: str):
    if name == "mcp":
        from xlii.mcp.context_server import mcp

        return mcp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
