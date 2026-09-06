"""Lower-level key provisioning (`xlii bootstrap`)."""

from __future__ import annotations

import argparse

from xlii.bootstrap import (
    BootstrapError,
    discover_team_id,
    provision_worker_keys,
    require_management_key,
    revoke_keys_by_prefix,
    set_team_id_in_config,
)
from xlii.config import GlobalConfig
from xlii.ui import confirm, console

from .keys import _render_revoke_event


def cmd_bootstrap(args: argparse.Namespace) -> int:
    """Provision worker API keys via the xAI Management REST API."""
    cfg = GlobalConfig.load()
    try:
        require_management_key(cfg)
    except BootstrapError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    try:
        team_id = discover_team_id(cfg)
    except BootstrapError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    if not team_id:
        console.print("[red]team_id resolution returned empty[/red]")
        return 1
    if not cfg.team_id:
        # cache it so we skip discovery next time
        set_team_id_in_config(team_id)
        console.print(f"[dim]team_id discovered + cached: {team_id}[/dim]")
    else:
        console.print(f"[dim]team_id: {team_id}[/dim]")

    if args.revoke:
        return revoke_keys_by_prefix(
            cfg,
            team_id,
            args.prefix,
            confirm_cb=lambda _matches: confirm("proceed? [y/N] ", assume_yes=args.yes),
            on_event=_render_revoke_event,
        )

    def render(kind: str, **p) -> None:
        if kind == "prefix_exists":
            console.print(
                f"[yellow]you already have {p['existing']} key(s) labeled "
                f"'{p['prefix']}-*' in config[/yellow]. Re-run with --force to add "
                f"{p['count']} more, or use --revoke to remove them first."
            )
        elif kind == "creating":
            console.print(f"creating [bold]{p['count']}[/bold] worker key(s) on xAI…")
        elif kind == "key_created":
            exp_note = f"  (expires in {p['expire_days']}d)" if p["expire_days"] else ""
            console.print(
                f"  [green]✓[/green] {p['label']}  →  xAI name: {p['name_on_server']}{exp_note}"
            )
        elif kind == "key_failed":
            if p.get("error"):
                console.print(f"  [red]✗[/red] {p['label']}: {p['error']}")
            else:
                console.print(
                    f"  [red]✗[/red] {p['label']}: response did not contain a key string. "
                    f"raw response:\n  {p.get('raw')}"
                )
        elif kind == "aborted":
            console.print(
                f"[yellow]aborting — {p['saved']} key(s) created so far have been "
                "written to config[/yellow]"
            )
        elif kind == "written":
            console.print(
                f"\n[green]wrote {p['count']} key(s) to {p['path']}[/green]  "
                f"(pool now {p['pool_total']})"
            )
            console.print(
                "\n[dim]secrets stored in the vault — labels (not key material):[/dim]"
            )
            for label, ref in p["secrets"]:
                console.print(f"  {label:<14}  {ref}")
            console.print(
                "\n[dim]next:[/dim] [cyan]xlii init[/cyan]  in your project, "
                "or [cyan]xlii new <name>[/cyan] to spin up a fresh one"
            )

    return provision_worker_keys(
        cfg,
        team_id,
        prefix=args.prefix,
        count=args.count,
        expire_days=args.expire_days,
        force=args.force,
        on_event=render,
    )
