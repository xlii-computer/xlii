"""Email tool handlers."""

from __future__ import annotations

from typing import Any

from xlii.tool_context import (
    ToolContext,
    ToolResult,
)


def t_read_email(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """email E2 — fetch one message; parsed text + headers, never raw MIME."""
    from xlii.config import GlobalConfig
    from xlii.email import EmailError, format_message_text, read_message, resolve_account

    msg_id = (args.get("id") or args.get("message_id") or "").strip()
    if not msg_id:
        return ToolResult("read_email: id is required", is_error=True)
    account_name = (args.get("account") or "").strip() or None
    cfg = ctx.cfg if ctx.cfg is not None else GlobalConfig.load()
    try:
        account = resolve_account(cfg, account_name)
        msg = read_message(
            account,
            msg_id,
            project_root=ctx.project.project_root,
        )
    except EmailError as e:
        return ToolResult(f"read_email: {e.message}", is_error=True)
    return ToolResult(format_message_text(msg))


def t_search_email(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """email E2 — search inbox; summaries only (no MIME blobs)."""
    from xlii.config import GlobalConfig
    from xlii.email import EmailError, format_summary_line, resolve_account, search_messages

    query = (args.get("query") or "").strip()
    if not query:
        return ToolResult("search_email: query is required", is_error=True)
    account_name = (args.get("account") or "").strip() or None
    unread_only = bool(args.get("unread_only"))
    cfg = ctx.cfg if ctx.cfg is not None else GlobalConfig.load()
    try:
        account = resolve_account(cfg, account_name)
        rows = search_messages(
            account,
            query,
            project_root=ctx.project.project_root,
            unread_only=unread_only,
        )
    except EmailError as e:
        return ToolResult(f"search_email: {e.message}", is_error=True)
    if not rows:
        return ToolResult("(no matches)")
    lines = [format_summary_line(r) for r in rows]
    return ToolResult("\n".join(lines))


def t_send_email(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """email E3 — gated SMTP send (typed 'send' confirm unless yolo)."""
    from xlii.config import GlobalConfig
    from xlii.email import EmailError, gate_send_email, resolve_account, send_message

    to = (args.get("to") or "").strip()
    subject = (args.get("subject") or "").strip()
    body = (args.get("body") or "").strip()
    if not to:
        return ToolResult("send_email: to is required", is_error=True)
    if not body:
        return ToolResult("send_email: body is required", is_error=True)
    account_name = (args.get("account") or "").strip() or None
    cfg = ctx.cfg if ctx.cfg is not None else GlobalConfig.load()
    refusal = gate_send_email(ctx, to=to, subject=subject, body=body)
    if refusal is not None:
        return refusal
    try:
        account = resolve_account(cfg, account_name)
        send_message(
            account,
            to=to,
            subject=subject,
            body=body,
            html=(args.get("html") or "").strip() or None,
        )
    except EmailError as e:
        return ToolResult(f"send_email: {e.message}", is_error=True)
    return ToolResult(f"sent to {to}")
