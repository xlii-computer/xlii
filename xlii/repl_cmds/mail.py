"""/mail — inbox triage in the REPL (email E4)."""

from __future__ import annotations

import shlex
from typing import Any

from xlii.commands import REPLCommand, register_repl_command
from xlii.email import (
    EmailError,
    format_message_text,
    format_summary_line,
    gate_send_email,
    list_messages,
    read_message,
    resolve_account,
    search_messages,
    send_message,
)
from xlii.tool_context import ToolContext


def _project_root_from_ctx(ctx: dict[str, Any]):
    state = ctx.get("state")
    if state is not None and getattr(state, "project", None) is not None:
        return state.project.project_root
    from pathlib import Path

    from xlii.config import ProjectConfig

    root = Path.cwd().resolve()
    proj = ProjectConfig.load(root)
    return proj.project_root if proj else root


def _cfg_from_ctx(ctx: dict[str, Any]):
    state = ctx.get("state")
    if state is not None and getattr(state, "cfg", None) is not None:
        return state.cfg
    from xlii.config import GlobalConfig

    return GlobalConfig.load()


def _parse_mail_args(parts: list[str]) -> tuple[str, list[str], dict[str, str]]:
    if len(parts) < 2:
        raise ValueError(
            "usage: /mail list|search|read|send … "
            "(try /mail list, /mail search <query>, /mail read <id>)"
        )
    verb = parts[1].lower()
    flags: dict[str, str] = {}
    rest: list[str] = []
    i = 2
    while i < len(parts):
        tok = parts[i]
        if tok == "--unread":
            flags["unread"] = "1"
            i += 1
        elif tok == "--account" and i + 1 < len(parts):
            flags["account"] = parts[i + 1]
            i += 2
        elif tok in ("--to", "--subject", "--body") and i + 1 < len(parts):
            flags[tok[2:]] = parts[i + 1]
            i += 2
        else:
            rest.append(tok)
            i += 1
    return verb, rest, flags


def _mail_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    try:
        parts = shlex.split(line.strip())
        verb, rest, flags = _parse_mail_args(parts)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return True

    cfg = _cfg_from_ctx(ctx)
    root = _project_root_from_ctx(ctx)
    try:
        account = resolve_account(cfg, flags.get("account"))
    except EmailError as e:
        console.print(f"[red]{e.message}[/red]")
        return True

    unread = "unread" in flags
    try:
        if verb == "list":
            rows = list_messages(
                account,
                project_root=root,
                unread_only=unread,
            )
            if not rows:
                console.print("[dim](inbox empty)[/dim]")
            else:
                for row in rows:
                    console.print(format_summary_line(row))
        elif verb == "search":
            if not rest:
                console.print("[red]usage: /mail search <query>[/red]")
                return True
            query = " ".join(rest)
            rows = search_messages(
                account,
                query,
                project_root=root,
                unread_only=unread,
            )
            if not rows:
                console.print("[dim](no matches)[/dim]")
            else:
                for row in rows:
                    console.print(format_summary_line(row))
        elif verb == "read":
            if not rest:
                console.print("[red]usage: /mail read <message-id>[/red]")
                return True
            msg = read_message(account, rest[0], project_root=root)
            console.print(format_message_text(msg))
        elif verb == "send":
            to = flags.get("to", "")
            subject = flags.get("subject", "")
            body = flags.get("body", "")
            if not to or not body:
                console.print(
                    "[red]usage: /mail send --to ADDR --subject SUBJ --body TEXT[/red]"
                )
                return True
            tctx = ToolContext(
                project=type("P", (), {"project_root": root})(),
                clients=None,
                cfg=cfg,
                console=console,
                yolo=bool(ctx.get("yolo")),
                is_worker=bool(ctx.get("is_worker")),
            )
            refusal = gate_send_email(tctx, to=to, subject=subject, body=body)
            if refusal is not None:
                console.print(f"[red]{refusal.content}[/red]")
                return True
            send_message(account, to=to, subject=subject, body=body)
            console.print(f"[green]✓[/green] sent to [cyan]{to}[/cyan]")
        else:
            console.print(f"[red]unknown /mail verb: {verb}[/red]")
    except EmailError as e:
        console.print(f"[red]{e.message}[/red]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="mail",
            handler=_mail_handler,
            description="Inbox triage — list, search, read, send (send confirms with 'send')",
            usage="/mail list|search <q>|read <id>|send --to … --subject … --body …",
            category="session",
        )
    )
