"""``xlii email`` — IMAP read/search + gated SMTP send (email E0–E3)."""

from __future__ import annotations

import argparse
import sys

from pathlib import Path

from xlii.config import ProjectConfig
from xlii.ui import console


def _project_root() -> Path:
    root = Path.cwd().resolve()
    proj = ProjectConfig.load(root)
    return proj.project_root if proj else root


def _load_cfg():
    from xlii.config import GlobalConfig

    return GlobalConfig.load()


def cmd_accounts_add(args: argparse.Namespace) -> int:
    import getpass

    from xlii.email import add_account

    name = args.name.strip()
    imap_host = (args.imap_host or "").strip()
    smtp_host = (args.smtp_host or "").strip()
    user = (args.user or "").strip()
    if not imap_host:
        imap_host = input("IMAP host: ").strip()
    if not smtp_host:
        smtp_host = input("SMTP host: ").strip()
    if not user:
        user = input("Email user: ").strip()
    try:
        password = getpass.getpass(f"password for {name} (not echoed): ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print("\n[dim]aborted[/dim]")
        return 1
    if not password:
        console.print("[red]empty password — nothing stored[/red]")
        return 1
    try:
        entry = add_account(
            name,
            imap_host=imap_host,
            smtp_host=smtp_host,
            user=user,
            password=password,
            imap_port=args.imap_port,
            smtp_port=args.smtp_port,
            default=args.default,
        )
    except (ValueError, OSError) as e:
        console.print(f"[red]add failed:[/red] {e}")
        return 1
    console.print(
        f"[green]✓[/green] saved account [cyan]{name}[/cyan] "
        f"[dim]({entry['user']} · vault {entry['vault_ns']})[/dim]"
    )
    console.print(f"[dim]list inbox: [/dim][cyan]xlii email list --account {name}[/cyan]")
    return 0


def cmd_accounts_list(args: argparse.Namespace) -> int:
    from xlii.email import list_accounts

    cfg = _load_cfg()
    accounts = list_accounts(cfg)
    if not accounts:
        console.print(
            "[dim](no email accounts — add with `xlii email accounts add <name>`)[/dim]"
        )
        return 0
    for name, acct in sorted(accounts.items()):
        mark = " [yellow]default[/yellow]" if acct.default else ""
        console.print(
            f"  [cyan]{name}[/cyan]{mark}  {acct.user}  "
            f"[dim]imap={acct.imap_host}:{acct.imap_port} · "
            f"smtp={acct.smtp_host}:{acct.smtp_port} · vault={acct.vault_ns}[/dim]"
        )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    from xlii.email import EmailError, format_summary_line, list_messages, resolve_account

    cfg = _load_cfg()
    try:
        account = resolve_account(cfg, args.account)
    except EmailError as e:
        console.print(f"[red]{e.message}[/red]")
        return 1
    root = _project_root()
    try:
        rows = list_messages(
            account,
            project_root=root,
            unread_only=args.unread,
            limit=args.limit,
        )
    except EmailError as e:
        console.print(f"[red]{e.message}[/red]")
        return 1
    if not rows:
        console.print("[dim](inbox empty)[/dim]")
        return 0
    for row in rows:
        console.print(format_summary_line(row))
    if len(rows) >= args.limit:
        console.print(f"[dim](showing latest {args.limit} — use --limit to raise)[/dim]")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from xlii.email import EmailError, format_summary_line, resolve_account, search_messages

    cfg = _load_cfg()
    try:
        account = resolve_account(cfg, args.account)
    except EmailError as e:
        console.print(f"[red]{e.message}[/red]")
        return 1
    root = _project_root()
    try:
        rows = search_messages(
            account,
            args.query,
            project_root=root,
            unread_only=args.unread,
            limit=args.limit,
        )
    except EmailError as e:
        console.print(f"[red]{e.message}[/red]")
        return 1
    if not rows:
        console.print("[dim](no matches)[/dim]")
        return 0
    for row in rows:
        console.print(format_summary_line(row))
    return 0


def cmd_read(args: argparse.Namespace) -> int:
    from xlii.email import EmailError, format_message_text, read_message, resolve_account

    cfg = _load_cfg()
    try:
        account = resolve_account(cfg, args.account)
    except EmailError as e:
        console.print(f"[red]{e.message}[/red]")
        return 1
    root = _project_root()
    try:
        msg = read_message(account, args.message_id, project_root=root)
    except EmailError as e:
        console.print(f"[red]{e.message}[/red]")
        return 1
    console.print(format_message_text(msg))
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    from xlii.email import EmailError, gate_send_email, resolve_account, send_message
    from xlii.tool_context import ToolContext

    cfg = _load_cfg()
    try:
        account = resolve_account(cfg, args.account)
    except EmailError as e:
        console.print(f"[red]{e.message}[/red]")
        return 1

    body = args.body
    if body is None and not sys.stdin.isatty():
        body = sys.stdin.read()
    if body is None:
        console.print("[red]send failed:[/red] --body or stdin body is required")
        return 1

    # Sending needs an interactive 'send' confirmation. With a non-tty stdin
    # (piped body / headless) there's no way to confirm — and the body read
    # above may have consumed stdin, so a later input() would raise EOFError.
    # Refuse up front unless --yolo was passed.
    if not args.yolo and not sys.stdin.isatty():
        console.print(
            "[red]send refused:[/red] confirmation needs an interactive terminal; "
            "re-run with --yolo to send without confirming"
        )
        return 1

    root = _project_root()
    ctx = ToolContext(
        project=type("P", (), {"project_root": root})(),
        clients=None,
        cfg=cfg,
        console=console,
        yolo=args.yolo,
    )
    refusal = gate_send_email(ctx, to=args.to, subject=args.subject or "", body=body)
    if refusal is not None:
        console.print(f"[red]{refusal.content}[/red]")
        return 1
    try:
        send_message(
            account,
            to=args.to,
            subject=args.subject or "",
            body=body,
            html=args.html,
        )
    except EmailError as e:
        console.print(f"[red]{e.message}[/red]")
        return 1
    console.print(f"[green]✓[/green] sent to [cyan]{args.to}[/cyan]")
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "email",
        help="Read and send mail over IMAP/SMTP (credentials in vault; send is gated).",
    )
    f = p.add_subparsers(dest="email_cmd", required=True)

    p_acct = f.add_parser("accounts", help="Manage email account descriptors.")
    acct_sub = p_acct.add_subparsers(dest="accounts_cmd", required=True)

    p_add = acct_sub.add_parser("add", help="Add an account (password prompted → vault).")
    p_add.add_argument("name", help="Account name (e.g. personal)")
    p_add.add_argument("--imap-host", default="", help="IMAP hostname")
    p_add.add_argument("--imap-port", type=int, default=993)
    p_add.add_argument("--smtp-host", default="", help="SMTP hostname")
    p_add.add_argument("--smtp-port", type=int, default=465)
    p_add.add_argument("--user", default="", help="Login email address")
    p_add.add_argument("--default", action="store_true", help="Mark as default account")
    p_add.set_defaults(func=cmd_accounts_add)

    p_list_acct = acct_sub.add_parser("list", help="List configured accounts (no secrets).")
    p_list_acct.set_defaults(func=cmd_accounts_list)

    p_list = f.add_parser("list", help="List recent messages from INBOX.")
    p_list.add_argument("--account", default=None, help="Account name")
    p_list.add_argument("--unread", action="store_true", help="Unread only")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list)

    p_search = f.add_parser("search", help="Search messages (IMAP TEXT).")
    p_search.add_argument("query", help="Search text")
    p_search.add_argument("--account", default=None)
    p_search.add_argument("--unread", action="store_true")
    p_search.add_argument("--limit", type=int, default=50)
    p_search.set_defaults(func=cmd_search)

    p_read = f.add_parser("read", help="Fetch and display a message by id.")
    p_read.add_argument("message_id", help="Message id (account:folder:uid)")
    p_read.add_argument("--account", default=None)
    p_read.set_defaults(func=cmd_read)

    p_send = f.add_parser("send", help="Send a message (typed 'send' confirm unless --yolo).")
    p_send.add_argument("--account", default=None)
    p_send.add_argument("--to", required=True, help="Recipient address")
    p_send.add_argument("--subject", default="", help="Subject line")
    p_send.add_argument("--body", default=None, help="Plain-text body")
    p_send.add_argument("--html", default=None, help="Optional HTML alternative body")
    p_send.add_argument("--yolo", action="store_true", help="Skip send confirmation")
    p_send.set_defaults(func=cmd_send)
