"""`xlii jid` — mint house XMPP addresses from the throne.

The rider creates JIDs. An agent over SSH is not the product.
"""

from __future__ import annotations

import argparse

from xlii.config import GlobalConfig
from xlii.jid_house import (
    JidError,
    ROLES,
    add_account,
    face_localpart,
    house_admin_remote,
    house_domain,
    list_accounts,
    password_for,
    set_house,
)
from xlii.ui import console


def cmd_jid_house(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    domain = (getattr(args, "domain", None) or "").strip()
    remote = (getattr(args, "admin_remote", None) or "").strip()
    if not domain and not remote:
        d = house_domain(cfg) or "(unset)"
        r = house_admin_remote(cfg) or "(none — print the register command)"
        console.print(f"domain  {d}")
        console.print(f"admin   {r}")
        return 0
    try:
        set_house(cfg, domain=domain, admin_remote=remote)
    except JidError as e:
        console.print(f"[red]jid house:[/red] {e}")
        return 1
    console.print(
        f"[green]✓[/green] XMPP house  domain=[cyan]{house_domain(cfg)}[/cyan]  "
        f"admin_remote=[cyan]{house_admin_remote(cfg) or '—'}[/cyan]"
    )
    return 0


def cmd_jid_ls(args: argparse.Namespace) -> int:
    del args
    cfg = GlobalConfig.load()
    rows = list_accounts(cfg)
    if not rows:
        console.print(
            "[dim]no JIDs yet — `xlii jid house --domain …` then "
            "`xlii jid add me --role me`[/dim]"
        )
        return 0
    for m in rows:
        extra = []
        if m.role:
            extra.append(m.role)
        if m.node:
            extra.append(f"node={m.node}")
        extra.append("vault" if m.password_stored else "no-password")
        console.print(f"{m.jid}\t{', '.join(extra)}")
    console.print(
        "[dim]phone: add each JID as a Conversations contact and accept the "
        "subscribe — presence (online/offline) is the node name[/dim]"
    )
    return 0


def cmd_jid_add(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    role = (args.role or "node").strip().lower()
    node = (getattr(args, "node", None) or "").strip()
    local = (args.localpart or "").strip()
    if not local:
        if role == "face" and node:
            local = face_localpart(node)
        else:
            console.print("[red]jid add:[/red] need a localpart")
            return 1
    try:
        mint = add_account(
            cfg,
            local,
            role=role,
            node=node,
            domain=(getattr(args, "domain", None) or ""),
            password=(getattr(args, "password", None) or ""),
            adopt=bool(getattr(args, "adopt", False)),
            register=not bool(getattr(args, "adopt", False)),
        )
    except JidError as e:
        console.print(f"[red]jid add:[/red] {e}")
        return 1
    how = "registered" if mint.registered else (
        "adopted" if args.adopt else "ledgered — run this on the XMPP host"
    )
    console.print(f"[green]✓[/green] {mint.jid}  ({mint.role}{(' · ' + mint.node) if mint.node else ''})  {how}")
    if mint.password_stored:
        console.print("[dim]password in vault (xlii:xmpp) — not printed[/dim]")
    elif not args.adopt:
        console.print("[yellow]password not in vault[/yellow] — copy the command once:")
        console.print(f"  [cyan]{mint.command}[/cyan]")
    return 0


def cmd_jid_show(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    key = (args.localpart or "").strip().lower()
    if "@" in key:
        key = key.split("@", 1)[0]
    for m in list_accounts(cfg):
        if m.localpart == key or m.jid == args.localpart.strip().lower():
            console.print(f"jid     {m.jid}")
            console.print(f"role    {m.role or '—'}")
            console.print(f"node    {m.node or '—'}")
            console.print(f"vault   {'yes' if m.password_stored else 'no'}")
            if getattr(args, "reveal", False):
                pw = password_for(m.localpart)
                if pw:
                    console.print(f"pass    {pw}")
                else:
                    console.print("[dim]no password stored[/dim]")
            return 0
    console.print(f"[red]jid show:[/red] no account {args.localpart!r}")
    return 1


def register(sub) -> None:
    p = sub.add_parser(
        "jid",
        help="Mint house XMPP addresses (Prosody). You create JIDs; nodes do not self-enroll.",
    )
    jsub = p.add_subparsers(dest="jid_cmd", required=True)

    p_house = jsub.add_parser(
        "house",
        help="Set or show the XMPP house (domain + optional admin remote that can sudo prosodyctl).",
    )
    p_house.add_argument("--domain", default="", help="e.g. home.xlii-remote.com")
    p_house.add_argument(
        "--admin-remote", default="", dest="admin_remote",
        help="xlii remote name that SSH-execs on the Prosody host (empty = print the command)",
    )
    p_house.set_defaults(func=cmd_jid_house)

    p_ls = jsub.add_parser("ls", help="List ledgered house JIDs.")
    p_ls.set_defaults(func=cmd_jid_ls)

    p_add = jsub.add_parser(
        "add",
        help="Mint a JID: register on the house Prosody when an admin remote is set, else print the command.",
    )
    p_add.add_argument(
        "localpart", nargs="?", default="",
        help="localpart (me, throne, node1). One JID per body — not a *desk twin.",
    )
    p_add.add_argument(
        "--role", default="node", choices=list(ROLES),
        help="me | throne | daemon | node | face",
    )
    p_add.add_argument("--node", default="", help="limb name (required for --role face)")
    p_add.add_argument("--domain", default="", help="override house domain")
    p_add.add_argument(
        "--password", default="",
        help="set this password (default: generate). Never put this in a task file.",
    )
    p_add.add_argument(
        "--adopt", action="store_true",
        help="Record an existing account; do not call Prosody",
    )
    p_add.set_defaults(func=cmd_jid_add)

    p_show = jsub.add_parser("show", help="Show one ledgered JID.")
    p_show.add_argument("localpart")
    p_show.add_argument(
        "--reveal", action="store_true",
        help="Print the vault password (once, on this terminal)",
    )
    p_show.set_defaults(func=cmd_jid_show)
