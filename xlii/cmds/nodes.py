"""`xlii node` — stamp another limb of this house.

P0 of the install wizard: mint the two JIDs (daemon + Face glass) and
print the Face recipe. Full rsync/venv seed is the same ceremony; this
command must exist so the form is not a fake.
"""

from __future__ import annotations

import argparse

from xlii.config import GlobalConfig
from xlii.jid_house import (
    JidError,
    add_account,
    house_domain,
    is_limb_name,
    next_node_name,
)
from xlii.ui import console


def cmd_node_setup(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    name = (args.name or "").strip().lower()
    if not name:
        name = next_node_name(cfg)
        console.print(f"[dim]node setup: naming this limb {name}[/dim]")
    if not is_limb_name(name):
        console.print(
            f"[red]node setup:[/red] limbs are node1, node2, … — not hostnames. "
            f"Next free name is [cyan]{next_node_name(cfg)}[/cyan]"
        )
        return 1
    if not house_domain(cfg):
        console.print(
            "[red]node setup:[/red] no XMPP domain — "
            "[cyan]xlii jid house --domain home.example[/cyan] first"
        )
        return 1
    minted = []
    try:
        mint = add_account(cfg, name, role="node", node=name)
        minted.append(mint)
        how = "registered" if mint.registered else "command printed below"
        console.print(f"[green]✓[/green] {mint.jid} ({mint.role}) {how}")
        if not mint.registered:
            console.print(f"  [cyan]{mint.command}[/cyan]")
    except JidError as e:
        if "already" in str(e):
            console.print(f"[dim]jid {name}: already on the ledger[/dim]")
        else:
            console.print(f"[red]node setup:[/red] {e}")
            return 1
    from xlii.persona import factory_persona_id

    journal = (getattr(args, "journal", None) or "").strip() or factory_persona_id(cfg)
    remote = (getattr(args, "remote", None) or "").strip()
    if remote:
        nodes = dict(cfg.fabric_nodes or {})
        if name not in nodes:
            nodes[name] = {"remote": remote, "persona": journal}
            cfg.fabric_nodes = nodes
            cfg.save()
            console.print(f"[green]✓[/green] fabric node {name!r} → remote {remote!r}")
        else:
            console.print(f"[dim]fabric node {name} already rostered[/dim]")
            entry = dict(nodes.get(name) or {})
            if not str(entry.get("persona") or "").strip():
                entry["persona"] = journal
                nodes[name] = entry
                cfg.fabric_nodes = nodes
                cfg.save()
    gig = (getattr(args, "gig", None) or "").strip()
    if gig:
        console.print(f"[dim]larynx gig={gig} — set jobs.gig on the box, do not copy the throne vault[/dim]")
    if getattr(args, "mint_xai", False):
        console.print(
            "[dim]--mint-xai: throne mints a capped child on the next pass; "
            "management key never lands on the node[/dim]"
        )
    if getattr(args, "new_key", False):
        console.print(
            "[dim]--new-key: generate a dedicated SSH key on the next pass[/dim]"
        )
    console.print(
        f"[dim]same Mojo as this throne:[/dim] [magenta]{journal}[/magenta]  "
        f"— on that box run [cyan]xlii setup --journal {journal}[/cyan] "
        f"(daemon cannot /name)"
    )
    console.print(
        "[dim]Face on that limb:[/dim] "
        f"[cyan]xlii serve --face --host tailnet --expose[/cyan]  "
        f"then /remote-control open — phone POSTs /remote-turn on the tailnet door "
        f"(or DMs [cyan]{name}@{house_domain(cfg)}[/cyan] over XMPP)"
    )
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "node",
        help="Stamp a fabric node: mint JIDs, roster it, print the Face recipe.",
    )
    nsub = p.add_subparsers(dest="node_cmd", required=True)
    p_setup = nsub.add_parser(
        "setup",
        help="Mint daemon + Face JIDs for a named limb and roster it when --remote is set.",
    )
    p_setup.add_argument(
        "name", nargs="?", default="",
        help="Limb name (node1, node2, …). Default: next free nodeN",
    )
    p_setup.add_argument("--remote", default="", help="Existing xlii remote name that reaches this box")
    p_setup.add_argument("--key-path", default="", dest="key_path", help="SSH key path (existing)")
    p_setup.add_argument("--new-key", action="store_true", help="Generate a dedicated SSH key (option)")
    p_setup.add_argument("--gig", default="", help="jobs.gig larynx name on the box")
    p_setup.add_argument("--mint-xai", action="store_true", help="Mint a capped xAI child (not yet pushed)")
    p_setup.add_argument(
        "--journal", default="",
        help="Mojo nickname to stamp on the limb (default: this throne's /name).",
    )
    p_setup.set_defaults(func=cmd_node_setup)
