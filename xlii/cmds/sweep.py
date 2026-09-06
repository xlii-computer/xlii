"""``xlii sweep`` — throne housekeep (collections, ghosts, dead keys)."""

from __future__ import annotations

import argparse

from xlii.ui import confirm, console


def cmd_sweep(args: argparse.Namespace) -> int:
    from xlii.sweep import apply_sweep, gather_live, render_report

    report = gather_live()
    if report.error:
        console.print(f"[red]{report.error}[/red]")
        return 1
    do = any((args.empty, args.test, args.ghosts, args.keys))
    if not do:
        render_report(report, console)
        return 0

    from xlii.client import Clients, MissingCredentials
    from xlii.config import GlobalConfig
    from xlii.registry import Registry
    from xlii.storage_backend import CollectionsBackend

    cfg = GlobalConfig.load()
    try:
        clients = Clients.from_config(cfg)
    except MissingCredentials as e:
        console.print(f"[red]{e}[/red]")
        return 1

    def _ghosts() -> int:
        reg = Registry.load()
        dead = reg.prune_dead()
        reg.save()
        return len(dead)

    def _keys() -> int:
        from xlii.bootstrap import discover_team_id, execute_prune, plan_prune

        team_id = discover_team_id(cfg)
        plan = plan_prune(cfg, team_id)
        expired = [
            k for k in plan.candidates
            if k.get("disabled")
        ]
        # Default key sweep: prune candidates that are disabled or already
        # selected by plan_prune (orphans not in this pool).
        targets = expired or list(plan.candidates)
        n = {"ok": 0}

        def _ev(kind, **p):
            if kind == "deleted":
                n["ok"] += 1

        execute_prune(cfg, team_id, targets, on_event=_ev)
        return n["ok"]

    # Re-save registry after each collection delete without reloading twice.
    deleted_ids: list[str] = []

    def _del_batch(cid: str) -> None:
        CollectionsBackend.delete_collection(clients, cid)
        deleted_ids.append(cid)

    rc = apply_sweep(
        report,
        empty=args.empty,
        test=args.test,
        ghosts=args.ghosts,
        keys=args.keys,
        yes=args.yes,
        delete_collection=_del_batch,
        prune_ghosts=_ghosts if args.ghosts else None,
        prune_keys=_keys if args.keys else None,
        confirm=lambda m: confirm(m + " [y/N] ", assume_yes=args.yes),
        out=console,
    )
    if deleted_ids:
        reg = Registry.load()
        for cid in deleted_ids:
            reg.remove(cid)
        reg.save()
    return rc


def register(sub) -> None:
    p = sub.add_parser(
        "sweep",
        help="Throne housekeep: list collections / ghosts / keys; sweep empties, tests, dead keys.",
        description=(
            "Inventory xAI Collections, dead registry rows, and expired/orphan "
            "chat keys. Default is look-only. Add --empty / --test / --ghosts / "
            "--keys to clean. Never deletes a Collection a live project still claims."
        ),
    )
    p.add_argument("--empty", action="store_true", help="Delete untracked empty collections")
    p.add_argument("--test", action="store_true", help="Delete untracked test/tmp-named collections")
    p.add_argument("--ghosts", action="store_true", help="Drop dead registry rows (disk untouched)")
    p.add_argument("--keys", action="store_true", help="Prune orphan/disabled server keys not in this pool")
    p.add_argument("--yes", action="store_true", help="Apply without prompting")
    p.set_defaults(func=cmd_sweep)
