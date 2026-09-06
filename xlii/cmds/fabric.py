"""`xlii fabric` — the named-node control plane (the mojo's node→center pull).

A node accrues persona turns locally (keyless). Run `xlii fabric pull` on the
THRONE (which holds the management key) to drain a node's accrued turns down and
archive them to the persona's shared Collection, so every surface recalls them.
Chat and mojo on the throne also pull on `fabric_pull_interval_s` (default 15m).
The node NAME is the roster key + the provenance tag on pulled turns; keep it
matching the node daemon's `[daemon] node_name`.

SFTP transport (reuses the shipped remote-fs provider): a node's `--remote`
points at a configured `xlii remote` connection that reaches it. XMPP-native
pull is the deferred durable path.
"""

from __future__ import annotations

import argparse
import sys

# The renameable default mascot; a node with no configured persona pulls this.
DEFAULT_PERSONA = "mojo"


def register(sub) -> None:
    p = sub.add_parser(
        "fabric",
        help="Named-node control plane: pull the mojo (a node's persona memory) "
             "down to the throne and archive it to the shared Collection.",
    )
    fsub = p.add_subparsers(dest="fabric_cmd", required=True)

    p_pull = fsub.add_parser(
        "pull",
        help="Pull a node's accrued persona turns to the throne and archive them "
             "to the shared Collection (run on the throne — needs the management key).",
    )
    p_pull.add_argument("--node", metavar="NAME",
                        help="Pull just this node (default: every configured node)")
    p_pull.add_argument("--persona", metavar="NAME", default="",
                        help="Persona whose memory to pull (default: the node's "
                             "configured persona, else 'mojo')")
    p_pull.add_argument("--dry-run", action="store_true",
                        help="Report what would be pulled without writing or archiving")
    p_pull.set_defaults(func=cmd_fabric_pull)

    p_nodes = fsub.add_parser("nodes", help="List the configured fabric nodes.")
    p_nodes.set_defaults(func=cmd_fabric_nodes)

    p_add = fsub.add_parser(
        "add-node",
        help="Register a node: a name + an existing remote-fs connection (xlii remote add).",
    )
    p_add.add_argument("name", help="Node name (throne, node1, …): roster key + provenance tag")
    p_add.add_argument("--remote", required=True, metavar="CONN",
                       help="Name of a configured sftp connection (xlii remote add) "
                            "that reaches this node")
    p_add.add_argument("--chat-state", default="", metavar="PATH", dest="chat_state",
                       help="Remote path to the node's chat-state dir (default: .xlii/chat)")
    p_add.add_argument("--persona", default="", metavar="NAME",
                       help="Default persona to pull from this node (default: mojo)")
    p_add.set_defaults(func=cmd_fabric_add_node)

    p_rm = fsub.add_parser("rm-node", help="Remove a node from the roster.")
    p_rm.add_argument("name")
    p_rm.set_defaults(func=cmd_fabric_rm_node)

    p_sync = fsub.add_parser(
        "sync-projects",
        help="Pull every node's project registry onto this throne, then push "
             "the shared catalog back. Throne Home never travels.",
    )
    p_sync.add_argument("--dry-run", action="store_true",
                        help="Read nodes; do not write pointers or registries")
    p_sync.set_defaults(func=cmd_fabric_sync_projects)

    p_new = fsub.add_parser(
        "new",
        help="Mint a Collection-first project attributed to a fabric node. "
             "No box is the file home until someone adopts.",
    )
    p_new.add_argument("node", help="Fabric node the project is attributed to")
    p_new.add_argument("name", help="Project name (letters, digits, . _ -)")
    p_new.add_argument(
        "--kind",
        choices=("code", "collection"),
        default="collection",
        help="Folder kind stamp on the pointer (default: collection)",
    )
    p_new.set_defaults(func=cmd_fabric_new)


def cmd_fabric_nodes(args: argparse.Namespace) -> int:
    from xlii.config import GlobalConfig

    nodes = GlobalConfig.load().fabric_nodes or {}
    if not nodes:
        print("no fabric nodes configured — add one with `xlii fabric add-node`", file=sys.stderr)
        return 0
    for name in sorted(nodes):
        spec = nodes[name] or {}
        remote = spec.get("remote", "?")
        persona = spec.get("persona") or DEFAULT_PERSONA
        chat_state = spec.get("chat_state") or ".xlii/chat"
        print(f"{name}\tremote={remote}\tpersona={persona}\tchat_state={chat_state}")
    return 0


def _require_sftp_remote(remote_name: str) -> str | None:
    """Return an error message if ``remote_name`` is missing or not sftp."""
    from xlii.remotefs import manager

    spec = manager.spec(remote_name)
    if spec is None:
        return (f"no such remote connection: {remote_name!r} — add one with "
                f"`xlii remote add` (have: {', '.join(manager.names()) or 'none'})")
    if (spec.get("protocol") or "").lower() != "sftp":
        return (f"remote {remote_name!r} must use sftp (have: {spec.get('protocol', '?')!r}) "
                "— fabric pull is SFTP-only")
    return None


def cmd_fabric_add_node(args: argparse.Namespace) -> int:
    from xlii.config import GlobalConfig

    err = _require_sftp_remote(args.remote)
    if err is not None:
        print(f"fabric: {err}", file=sys.stderr)
        return 1
    cfg = GlobalConfig.load()
    spec = {"remote": args.remote}
    if args.chat_state:
        spec["chat_state"] = args.chat_state
    if args.persona:
        spec["persona"] = args.persona
    cfg.fabric_nodes = {**(cfg.fabric_nodes or {}), args.name: spec}
    cfg.save()
    print(f"fabric: registered node {args.name!r} → remote {args.remote!r}")
    return 0


def cmd_fabric_rm_node(args: argparse.Namespace) -> int:
    from xlii.config import GlobalConfig

    cfg = GlobalConfig.load()
    nodes = dict(cfg.fabric_nodes or {})
    if args.name not in nodes:
        print(f"fabric: no such node: {args.name}", file=sys.stderr)
        return 1
    del nodes[args.name]
    cfg.fabric_nodes = nodes
    cfg.save()
    print(f"fabric: removed node {args.name!r}")
    return 0


def cmd_fabric_sync_projects(args: argparse.Namespace) -> int:
    """One menu on every Face: merge project registries across the star."""
    from rich.console import Console

    from xlii.config import GlobalConfig
    from xlii.fabric import _default_connect, _default_require_remote
    from xlii.fabric_projects import sync_fabric_projects

    ui = Console(stderr=True)
    cfg = GlobalConfig.load()
    nodes = cfg.fabric_nodes or {}
    if not nodes:
        print("fabric: no nodes configured — add one with `xlii fabric add-node`",
              file=sys.stderr)
        return 1
    result = sync_fabric_projects(
        nodes,
        connect=_default_connect,
        require_remote=_default_require_remote,
        this_node="throne",
        dry_run=bool(args.dry_run),
    )
    for res in result.nodes:
        ui.print(f"[dim]fabric: projects: {res.summary()}[/dim]")
        for err in res.errors:
            ui.print(f"[yellow]  {err}[/yellow]")
    for line in result.pushed:
        ui.print(f"[dim]fabric: projects: pushed {line}[/dim]")
    for err in result.errors:
        ui.print(f"[red]fabric: projects: {err}[/red]")
    if args.dry_run:
        ui.print("[dim]fabric: projects: dry-run — nothing written[/dim]")
    return 1 if result.errors else 0


def cmd_fabric_new(args: argparse.Namespace) -> int:
    """Create a folder on a node and point this Face at it. No Collection."""
    from xlii.config import GlobalConfig
    from xlii.fabric import _default_connect, _default_require_remote
    from xlii.fabric_projects import create_fabric_project

    cfg = GlobalConfig.load()
    nodes = cfg.fabric_nodes or {}
    try:
        created = create_fabric_project(
            args.name,
            args.node,
            roster=nodes,
            connect=_default_connect,
            require_remote=_default_require_remote,
            kind=getattr(args, "kind", None),
        )
    except ValueError as e:
        print(f"fabric: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"fabric: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    note = " · on the node" if created.pushed else " · node not reached"
    print(
        f"fabric: {created.name}  node={created.node}  {created.path}{note}"
    )
    print("work it remote (Files). /sync on this desk if you want a Collection.")
    return 0


def cmd_fabric_pull(args: argparse.Namespace) -> int:
    """Pull one or all nodes' persona turns to the throne, then drain each
    touched persona to its shared Collection (the F3 center-half)."""
    from rich.console import Console

    from xlii.config import GlobalConfig
    from xlii.fabric import (
        _default_connect,
        _default_require_remote,
        _default_resolve_persona,
        drain_persona,
        pull_roster,
        stamp_pull,
    )

    ui = Console(stderr=True)
    cfg = GlobalConfig.load()
    nodes = cfg.fabric_nodes or {}
    if not nodes:
        print("fabric: no nodes configured — add one with `xlii fabric add-node`", file=sys.stderr)
        return 1
    if args.node and args.node not in nodes:
        print(f"fabric: no such node: {args.node}", file=sys.stderr)
        return 1

    batch = pull_roster(
        nodes,
        persona_override=args.persona or "",
        dry_run=args.dry_run,
        connect=_default_connect,
        resolve_persona=_default_resolve_persona,
        drain=(lambda p: drain_persona(p, cfg, ui)),
        require_remote=_default_require_remote,
        default_persona=DEFAULT_PERSONA,
        only=args.node or None,
    )
    any_error = False
    for res in batch.nodes:
        ui.print(f"[dim]fabric: {res.summary()}[/dim]")
        for err in res.errors:
            ui.print(f"[yellow]  {res.node}: {err}[/yellow]")
            any_error = True
    for mres in batch.media:
        if mres.pulled or mres.skipped or mres.errors:
            ui.print(f"[dim]fabric: media: {mres.summary()}[/dim]")
        if mres.pulled and not args.dry_run:
            ui.print(f"[dim]fabric: media: {mres.pulled} new — /media to list[/dim]")
        for err in mres.errors:
            ui.print(f"[yellow]  {mres.node}: media: {err}[/yellow]")
            any_error = True
    for err in batch.errors:
        ui.print(f"[red]fabric: {err}[/red]")
        any_error = True
    if args.dry_run:
        ui.print("[dim]fabric: dry-run — nothing written or archived[/dim]")
    else:
        for persona_name, msg, ok in batch.drains:
            ui.print(f"[dim]fabric: {persona_name}: {msg}[/dim]")
            if not ok:
                any_error = True
        # Manual pull resets the auto-pull clock so opening chat right after
        # this does not open SFTP again.
        try:
            stamp_pull()
        except OSError as e:
            # Best effort only: pull/drain already succeeded, so do not fail
            # the command if updating the local pull timestamp cannot be written.
            ui.print(f"[yellow]fabric: warning: could not update pull stamp: {e}[/yellow]")

    return 1 if any_error else 0
