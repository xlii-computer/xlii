"""``xlii destroy-all`` — CLI front door to the same ``run_destroy`` kernel."""

from __future__ import annotations

import argparse

from xlii.ui import console


def cmd_destroy_all(args: argparse.Namespace) -> int:
    from xlii.destroy import run_destroy

    # Dry-run is the default. Live wipe requires an explicit --keys-and-local.
    # --dry-run wins if both are passed.
    level = 1 if args.keys_and_local else 0
    if args.dry_run:
        level = 0
    dry_run = level == 0
    report = run_destroy(
        args.aim,
        target=args.target or "",
        level=level,
        dry_run=dry_run,
        local_only=args.local_only,
        console=console,
    )
    console.print(
        f"destroy aim={report.aim} level={report.level} dry_run={report.dry_run} "
        f"planned={len(report.planned)} done={len(report.done)} failed={len(report.failed)}"
    )
    for line in report.residuals:
        console.print(f"[dim]{line}[/dim]")
    return 1 if report.failed else 0


def register(sub) -> None:
    p = sub.add_parser(
        "destroy-all",
        help="Human-only deny ladder — dry-run default; keys-and-local wipes this body.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Level 0 inventory only (the default). Harmless if passed with --keys-and-local.",
    )
    p.add_argument(
        "--keys-and-local",
        action="store_true",
        help="Level 1: revoke minted ids and wipe this body's local state.",
    )
    p.add_argument(
        "--local-only",
        action="store_true",
        help="Skip cloud revokes; journal surviving ids.",
    )
    p.add_argument(
        "--aim",
        default="all",
        choices=("all", "throne", "node"),
        help="Destroy scope (Wave 1: all/throne = this body).",
    )
    p.add_argument(
        "--target",
        default="",
        help="Node name when --aim=node.",
    )
    p.set_defaults(func=cmd_destroy_all)
