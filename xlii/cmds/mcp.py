"""MCP bridge subcommands (deep-contexts server for external agents).

Moved out of the former monolithic cli.py (see proposals/done/cli-refactor.md).
"""

from __future__ import annotations

import argparse


def cmd_mcp_deep_contexts(args: argparse.Namespace) -> int:
    """Start the DeepContexts MCP server for Grok Build / external agents.

    IMPORTANT: This is a stdio MCP server. When Grok Build (or any MCP client)
    spawns it, stdout must be kept clean for the JSON-RPC protocol. All human
    messages must go to stderr (or be suppressed).
    """
    import sys
    from rich.console import Console

    try:
        from xlii.mcp.context_server import mcp
    except ImportError:
        # Error messages can go to stderr safely
        err = Console(stderr=True, highlight=False)
        err.print("[red]✗[/red] The MCP bridge requires the optional [bold]mcp[/bold] extra.")
        err.print()
        err.print("Install with:")
        err.print("  [cyan]pip install \"xlii\\[mcp]\"[/cyan]")
        err.print()
        err.print("If you are developing from this source tree (editable install):")
        err.print("  [cyan]pip install -e \".\\[mcp]\"[/cyan]")
        err.print("  (run the above after any changes to pyproject.toml or the mcp code)")
        err.print()
        err.print("[dim]This pulls in the FastMCP server (from the 'mcp' package) so that\nGrok Build can attach your DeepContexts and live xlii workspaces.[/dim]")
        return 1

    # Only print friendly banners when a human is running this directly in a terminal.
    # When Grok Build spawns us for stdio MCP, stdout is a pipe — any output here
    # corrupts the JSON-RPC protocol and causes "server may not be connected".
    if sys.stdout.isatty():
        err = Console(stderr=True, highlight=False)
        err.print("[cyan]Starting xlii DeepContexts MCP server...[/cyan]")
        err.print("[dim]Grok Build (and other MCP clients) can now connect and attach DeepContexts.[/dim]")
        err.print("[dim]Press Ctrl-C to stop.[/dim]")

    mcp.run()
    return 0

def register(sub) -> None:
        # MCP-related subcommands (for bridging)
        p_mcp = sub.add_parser("mcp", help="Start MCP servers for external tools (e.g. Grok Build)")
        p_mcp_sub = p_mcp.add_subparsers(dest="mcp_target", required=True)

        p_mcp_deep = p_mcp_sub.add_parser(
            "deep-contexts",
            help="Expose DeepContexts + live workspaces via MCP (for Grok Build attachment). Requires the [mcp] extra."
        )
        p_mcp_deep.set_defaults(func=cmd_mcp_deep_contexts)
