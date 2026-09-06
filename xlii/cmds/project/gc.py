"""Orphan collection garbage collection."""

from __future__ import annotations

import argparse
from pathlib import Path

from xlii.client import Clients, MissingCredentials
from xlii.config import GlobalConfig, JOURNAL_COLLECTION_PREFIX
from xlii.registry import Registry
from xlii.ui import console

from xlii.cmds.project._collections import _claimed_collection, _list_cloud_collections


def cmd_gc(args: argparse.Namespace) -> int:
    """List + (optionally) delete orphan xAI collections.

    Categories:
      - tracked-alive   : in registry, project on disk, in cloud  -> keep
      - tracked-dead    : in registry, project DELETED on disk    -> orphan
      - untracked-cloud : in cloud (xli/* or xlii/* prefix), not in registry -> orphan
      - tracked-missing : in registry, NOT in cloud (already gone) -> registry stale
    """
    cfg = GlobalConfig.load()
    try:
        clients = Clients.from_config(cfg)
    except MissingCredentials as e:
        console.print(f"[red]{e}[/red]")
        return 1

    registry = Registry.load()
    # Local-only projects use an empty collection_id sentinel. It is not a cloud
    # id and Registry.remove("") would remove every local-only entry at once.
    by_id = {e.collection_id: e for e in registry.entries if e.collection_id}

    with console.status("[cyan]listing collections…[/cyan]"):
        cloud_ids = _list_cloud_collections(clients)  # id -> name

    tracked_alive: list[tuple[str, str, str]] = []   # (path, name, id)
    tracked_dead: list[tuple[str, str, str]] = []
    untracked_cloud: list[tuple[str, str]] = []      # (id, name)
    tracked_missing: list[tuple[str, str, str]] = []

    for cid, entry in by_id.items():
        if cid not in cloud_ids:
            tracked_missing.append((entry.path, entry.name, cid))
            continue
        # Cross-check: alive means the path's project.json CLAIMS this
        # collection_id — not merely that some project.json exists there.
        path_alive = _claimed_collection(Path(entry.path)) == cid
        bucket = tracked_alive if path_alive else tracked_dead
        bucket.append((entry.path, entry.name, cid))

    for cid, name in cloud_ids.items():
        if cid in by_id:
            continue
        if not (name.startswith("xli/") or name.startswith("xlii/")):
            continue  # only consider xli/xlii-prefixed collections (legacy + new)
        if name.startswith(JOURNAL_COLLECTION_PREFIX):
            continue  # journal Collections are lifecycle-managed via project rm
        untracked_cloud.append((cid, name))

    def _print_section(title: str, items: list, fmt) -> None:
        if not items:
            return
        console.print(f"\n[bold]{title}[/bold] ({len(items)})")
        for item in items:
            console.print(f"  · {fmt(item)}")

    _print_section(
        "[green]tracked & alive[/green]",
        tracked_alive,
        lambda x: f"{x[1]:<30} {x[2]}  →  {x[0]}",
    )
    _print_section(
        "[yellow]tracked but path deleted[/yellow]",
        tracked_dead,
        lambda x: f"{x[1]:<30} {x[2]}  ✗  {x[0]}",
    )
    _print_section(
        "[yellow]untracked cloud collection[/yellow]",
        untracked_cloud,
        lambda x: f"{x[1]:<30} {x[0]}",
    )
    _print_section(
        "[dim]registry stale (cloud already gone)[/dim]",
        tracked_missing,
        lambda x: f"{x[1]:<30} {x[2]}  →  {x[0]}",
    )

    deletable = len(tracked_dead) + len(untracked_cloud)
    if not deletable and not tracked_missing:
        console.print("\n[green]nothing to clean up[/green]")
        return 0

    if args.dry_run:
        console.print(f"\n[yellow]dry-run:[/yellow] would delete {deletable} collection(s)")
        return 0

    if deletable:
        if args.yes:
            choice = "a"
        else:
            console.print(
                f"\nDelete: [a]ll {deletable} orphans, [d]ead-path-only "
                f"({len(tracked_dead)}), [n]one ?"
            )
            choice = input("> ").strip().lower() or "n"

        targets: list[tuple[str, str]] = []
        if choice == "a":
            targets = [(cid, name) for _, name, cid in tracked_dead] + untracked_cloud
        elif choice == "d":
            targets = [(cid, name) for _, name, cid in tracked_dead]

        from xlii.storage_backend import CollectionsBackend
        for cid, name in targets:
            try:
                CollectionsBackend.delete_collection(clients, cid)
                registry.remove(cid)
                console.print(f"  [green]✓[/green] deleted {name} ({cid})")
            except Exception as e:
                console.print(f"  [red]✗[/red] {name} ({cid}): {e}")

    # Always prune stale registry entries (cloud already gone — nothing to delete remotely)
    for _, _, cid in tracked_missing:
        registry.remove(cid)

    registry.save()
    return 0
