"""Server-backed and browser tool handlers."""

from __future__ import annotations

from typing import Any, Callable

from xlii.tool_context import (
    ToolContext,
    ToolResult,
)

from ._common import _cap_output


def _server_tool(
    tool_name: str,
    field_name: str,
    value: str,
    ctx: ToolContext,
    server_fn: Callable[..., str],
    **call_kwargs: Any,
) -> ToolResult:
    """Shared skeleton for the xAI server-backed tools (web_search / x_search /
    code_execute): require the primary argument, run the one-shot Responses call
    with `ctx` as the usage sink, and uniformly truncate output / wrap errors."""
    if not value:
        return ToolResult(f"{tool_name}: '{field_name}' is required", is_error=True)
    try:
        text = server_fn(ctx.clients, ctx.cfg, value, sink=ctx, **call_kwargs)
    except Exception as e:
        return ToolResult(f"{tool_name} failed: {type(e).__name__}: {e}", is_error=True)
    return ToolResult(_cap_output(ctx, text))


def t_web_search(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from xlii.server_tools import web_search
    return _server_tool(
        "web_search", "query", (args.get("query") or "").strip(), ctx, web_search,
        allowed_domains=args.get("allowed_domains") or None,
        excluded_domains=args.get("excluded_domains") or None,
    )


# Official hosted xAI docs MCP. This is DNA, not a marketplace: the house
# brain looking up how Grok and the API actually run. xlii's own wiki /
# howto stay the garage manual (docgen). Import ``xlii.mcp.doc_client``
# directly — ``xlii.mcp`` pulls FastMCP's outbound context server.
XAI_DOCS_MCP_URL = "https://docs.x.ai/api/mcp"
_XAI_DOCS_ACTIONS = {
    "search": "search_docs",
    "get": "get_doc_page",
    "list": "list_doc_pages",
}


def t_xai_docs(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Read docs.x.ai so the agent can look up how its own brain runs."""
    if getattr(ctx.cfg, "xai_docs", True) is False:
        return ToolResult(
            "xai_docs is off (config xai_docs=false). "
            "Turn it back on to read the official xAI docs.",
            is_error=True,
        )

    action = (args.get("action") or "").strip().lower()
    if not action:
        if (args.get("query") or "").strip():
            action = "search"
        elif (args.get("slug") or args.get("page") or "").strip():
            action = "get"
        else:
            return ToolResult(
                "xai_docs: action required (search|get|list)",
                is_error=True,
            )
    mcp_name = _XAI_DOCS_ACTIONS.get(action)
    if mcp_name is None:
        return ToolResult(
            "xai_docs: action must be search, get, or list "
            f"(got {action!r})",
            is_error=True,
        )

    call_args: dict[str, Any] = {}
    if mcp_name == "search_docs":
        query = (args.get("query") or "").strip()
        if not query:
            return ToolResult("xai_docs.search: 'query' is required", is_error=True)
        call_args["query"] = query
        max_results = args.get("max_results")
        if max_results is not None:
            try:
                n = int(max_results)
            except (TypeError, ValueError):
                return ToolResult(
                    "xai_docs.search: max_results must be an integer",
                    is_error=True,
                )
            call_args["max_results"] = max(1, min(n, 20))
    elif mcp_name == "get_doc_page":
        slug = (args.get("slug") or args.get("page") or "").strip().lstrip("/")
        if not slug:
            return ToolResult(
                "xai_docs.get: 'slug' is required "
                "(e.g. developers/models, developers/quickstart)",
                is_error=True,
            )
        call_args["slug"] = slug

    from xlii.mcp.doc_client import MCPError, call_tool

    try:
        text = call_tool(XAI_DOCS_MCP_URL, mcp_name, call_args, timeout=30)
    except MCPError as e:
        return ToolResult(f"xai_docs failed: {e}", is_error=True)
    except Exception as e:
        return ToolResult(
            f"xai_docs failed: {type(e).__name__}: {e}", is_error=True,
        )
    return ToolResult(_cap_output(ctx, text))


def t_browser(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Drive the process-local Chromium research browser (CDP).

    Actions: open | goto | extract | screenshot | status | close.
    Pair with archivebox plugin to park interesting URLs.
    """
    from pathlib import Path

    from xlii import agent_browser as ab

    action = (args.get("action") or "").strip().lower()
    url = (args.get("url") or "").strip()
    max_chars = args.get("max_chars")
    try:
        max_chars_i = int(max_chars) if max_chars is not None else 24_000
    except (TypeError, ValueError):
        max_chars_i = 24_000
    max_chars_i = max(500, min(max_chars_i, 80_000))

    headless = args.get("headless")
    if headless is None:
        headless_b = None  # desk default: window when DISPLAY is set
    elif isinstance(headless, bool):
        headless_b = headless
    else:
        headless_b = str(headless).strip().lower() not in ("0", "false", "no")

    if not action:
        return ToolResult(
            "browser: action required "
            "(open|goto|extract|screenshot|status|close)",
            is_error=True,
        )

    try:
        if action == "open":
            snap = ab.open_session(url=url, headless=headless_b)
        elif action == "goto":
            if not url:
                return ToolResult("browser.goto: url is required", is_error=True)
            snap = ab.goto(url)
        elif action == "extract":
            if url:
                # convenience: open/goto then extract
                ab.open_session(url=url, headless=headless_b)
            snap = ab.extract(max_chars=max_chars_i)
        elif action == "screenshot":
            dest = None
            if ctx.project is not None:
                root = getattr(ctx.project, "project_root", None) or getattr(
                    ctx.project, "root", None
                )
                if root:
                    dest = Path(root) / ".xlii" / "browser-shots" / f"shot-{int(__import__('time').time())}.png"
            snap = ab.screenshot(dest)
        elif action == "status":
            snap = ab.session_status()
        elif action == "close":
            snap = ab.close_session()
        elif action == "dump":
            # one-shot DOM dump, no session
            if not url:
                return ToolResult("browser.dump: url is required", is_error=True)
            snap = ab.dump_dom_once(url, max_chars=max_chars_i)
        else:
            return ToolResult(
                f"browser: unknown action {action!r} "
                "(open|goto|extract|screenshot|status|close|dump)",
                is_error=True,
            )
    except Exception as e:  # noqa: BLE001
        return ToolResult(
            f"browser.{action} failed: {type(e).__name__}: {e}", is_error=True,
        )

    text = snap.summary(max_chars=max_chars_i)
    return ToolResult(_cap_output(ctx, text), is_error=not snap.ok)


def t_x_search(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from xlii.server_tools import x_search
    return _server_tool(
        "x_search", "query", (args.get("query") or "").strip(), ctx, x_search,
        allowed_x_handles=args.get("allowed_x_handles") or None,
        excluded_x_handles=args.get("excluded_x_handles") or None,
        from_date=args.get("from_date") or None,
        to_date=args.get("to_date") or None,
        enable_image_understanding=args.get("enable_image_understanding"),
        enable_video_understanding=args.get("enable_video_understanding"),
    )


def t_code_execute(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from xlii.server_tools import code_execute
    return _server_tool(
        "code_execute", "task", (args.get("task") or args.get("code") or "").strip(),
        ctx, code_execute,
    )
