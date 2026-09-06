"""Chat key management (`xlii keys`)."""

from __future__ import annotations

import argparse

from xlii.bootstrap import (
    BootstrapError,
    discover_team_id,
    execute_prune,
    extract_api_key_id,
    list_api_keys,
    parse_xai_timestamp,
    plan_prune,
    reconcile_local_keys,
    require_management_key,
    revoke_keys_by_prefix,
    rotate_keys,
    set_keys_expiration,
)
from xlii.config import GlobalConfig
from xlii.ui import confirm, console


def cmd_keys(args: argparse.Namespace) -> int:
    """Manage chat keys: list / rotate / expire / revoke."""
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

    action = args.action
    if action == "list":
        return _keys_list(cfg, team_id)
    if action == "rotate":
        return _keys_rotate(cfg, team_id, args)
    if action == "expire":
        return _keys_expire(cfg, team_id, args)
    if action == "revoke":
        return _keys_revoke(cfg, team_id, args)
    if action == "prune":
        return _keys_prune(cfg, team_id, args)
    console.print(f"[red]unknown action: {action}[/red]")
    return 1


def _keys_list(cfg: GlobalConfig, team_id: str) -> int:
    """List local keys with their server-side expiration & status."""
    from datetime import datetime, timezone
    try:
        remote = list_api_keys(cfg.management_api_key, team_id)
    except BootstrapError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    if not cfg.keys:
        console.print("[yellow]no chat keys in config — run `xlii setup`[/yellow]")
        return 0

    console.print("[bold]chat keys:[/bold]")
    now = datetime.now(timezone.utc)
    for st in reconcile_local_keys(cfg.keys, remote, now):
        if not st.found:
            console.print(f"  · {st.label:<14}  [yellow]not found on xAI[/yellow]")
            continue
        days_left = "—"
        if st.expire_str:
            if st.parse_failed:
                days_left = "?"
            else:
                d = st.days_left
                days_left = f"{d}d"
                if d < 0:
                    days_left = "[red]EXPIRED[/red]"
                elif d < 7:
                    days_left = f"[yellow]{d}d[/yellow]"
        disabled = "[red](disabled)[/red]" if st.disabled else ""
        console.print(f"  · {st.label:<14}  expires={days_left}  name={st.name} {disabled}")
    return 0


def _keys_rotate(cfg: GlobalConfig, team_id: str, args: argparse.Namespace) -> int:
    def render(kind: str, **p) -> None:
        if kind == "no_targets":
            console.print("[yellow]no matching keys[/yellow]")
        elif kind == "rotating":
            console.print(f"rotating [bold]{p['count']}[/bold] key(s)…")
        elif kind == "skip_no_id":
            console.print(
                f"  [yellow]skip {p['label']}: no api_key_id stored "
                "(created before rotation support?)[/yellow]"
            )
        elif kind == "vault_error":
            console.print(
                f"  [red]✗[/red] {p['label']}: cannot open vault before rotating: {p['error']}"
            )
        elif kind == "failed":
            console.print(f"  [red]✗[/red] {p['label']}: {p['error']}")
        elif kind == "rotated":
            console.print(f"  [green]✓[/green] {p['label']}  rotated (new secret saved)")

    return rotate_keys(cfg, team_id, label=args.label, on_event=render)


def _keys_expire(cfg: GlobalConfig, team_id: str, args: argparse.Namespace) -> int:
    def render(kind: str, **p) -> None:
        if kind == "no_targets":
            console.print("[yellow]no matching keys[/yellow]")
        elif kind == "setting":
            console.print(
                f"setting expiration on [bold]{p['count']}[/bold] key(s) to +{p['days']}d…"
            )
        elif kind == "skip_no_id":
            console.print(f"  [yellow]skip {p['label']}: no api_key_id stored[/yellow]")
        elif kind == "failed":
            console.print(f"  [red]✗[/red] {p['label']}: {p['error']}")
        elif kind == "updated":
            console.print(f"  [green]✓[/green] {p['label']}  expireTime updated")

    return set_keys_expiration(cfg, team_id, days=args.days, label=args.label, on_event=render)


def _render_revoke_event(kind: str, **p) -> None:
    """Render revoke_keys_by_prefix events — shared by `keys revoke` and
    `bootstrap --revoke`, which expose the same flow."""
    if kind == "listing":
        console.print(
            "listing API keys on xAI to find names starting with "
            f"{' or '.join(repr(x) for x in p['prefixes'])}…"
        )
    elif kind == "error":
        console.print(f"[red]{p['error']}[/red]")
    elif kind == "none_found":
        console.print(
            f"[yellow]no API keys named '{p['prefixes'][0]}*' "
            f"(or legacy '{p['prefixes'][1]}*') on xAI[/yellow]"
        )
    elif kind == "will_delete":
        matches = p["matches"]
        console.print(f"will delete {len(matches)} API key(s) on xAI:")
        for k in matches:
            console.print(f"  · {k.get('name', '?')}  id={extract_api_key_id(k) or '?'}")
    elif kind == "cancelled":
        console.print("[dim]cancelled[/dim]")
    elif kind == "skip_no_id":
        console.print(f"  [yellow]skip {p['name']}: no id field[/yellow]")
    elif kind == "deleted":
        console.print(f"  [green]✓[/green] revoked {p['name']}")
    elif kind == "failed":
        console.print(f"  [red]✗[/red] {p['name']}: {p['error']}")
    elif kind == "config_removed":
        console.print(f"removed {p['count']} matching entry(s) from {p['path']}")


def _keys_revoke(cfg: GlobalConfig, team_id: str, args: argparse.Namespace) -> int:
    return revoke_keys_by_prefix(
        cfg,
        team_id,
        args.prefix,
        confirm_cb=lambda _matches: confirm("proceed? [y/N] ", assume_yes=args.yes),
        on_event=_render_revoke_event,
    )


def _keys_prune(cfg: GlobalConfig, team_id: str, args: argparse.Namespace) -> int:
    """Delete server-side API keys that are NOT part of this machine's pool.

    Unlike `keys revoke` (scoped to keys in *this* config), prune reaches the
    orphans left by old setup/bootstrap runs and other tooling — keys that exist
    on xAI but xlii no longer tracks. The active pool (anything in keys[]) is
    protected unless --include-active is given.
    """
    from datetime import datetime, timezone

    try:
        plan = plan_prune(
            cfg,
            team_id,
            name_glob=args.name,
            any_name=args.any_name,
            older_than=args.older_than,
            include_active=args.include_active,
        )
    except BootstrapError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    if plan.server_count == 0:
        console.print("[yellow]no API keys on xAI for this team[/yellow]")
        return 0

    if args.name:
        scope = f"name matches {args.name!r}"
    elif args.any_name:
        scope = "ANY name (--any-name)"
    else:
        # Default: only keys xlii itself provisions, so a shared management key's
        # OTHER keys (other projects/tools) are never touched without opting in.
        scope = "xlii-provisioned names only (pass --any-name or --name to widen)"

    now = datetime.now(timezone.utc)
    console.print(f"[dim]scope: {scope}[/dim]")
    if not plan.candidates:
        console.print(
            f"[green]nothing to prune[/green] "
            f"[dim]({plan.server_count} key(s) on xAI, {plan.protected} active/protected)[/dim]"
        )
        return 0

    console.print(
        f"[bold]{len(plan.candidates)}[/bold] key(s) match for deletion "
        f"[dim](of {plan.server_count} on xAI; {plan.protected} active key(s) protected)[/dim]:"
    )
    for k in sorted(plan.candidates, key=lambda x: x.get("createTime") or ""):
        created = parse_xai_timestamp(k.get("createTime") or k.get("create_time"))
        age = f"{(now - created).days}d old" if created else "age?"
        disabled = " [red](disabled)[/red]" if k.get("disabled") else ""
        console.print(f"  · {k.get('name', '?'):<30} [dim]{age}[/dim]{disabled}")

    if args.dry_run:
        console.print("[dim]dry-run — nothing deleted. Re-run without --dry-run to apply.[/dim]")
        return 0

    if args.include_active:
        console.print("[yellow]⚠ --include-active: your live pool keys are in this list[/yellow]")
    if not confirm(f"delete these {len(plan.candidates)} key(s) from xAI? [y/N] ", assume_yes=args.yes):
        console.print("[dim]aborted[/dim]")
        return 1

    def render(kind: str, **p) -> None:
        if kind == "skip_no_id":
            console.print(f"  [yellow]skip {p['name']}: no id field[/yellow]")
        elif kind == "deleted":
            console.print(f"  [green]✓[/green] deleted {p['name']}")
        elif kind == "failed":
            console.print(f"  [red]✗[/red] {p['name']}: {p['error']}")
        elif kind == "config_removed":
            console.print(f"[dim]removed {p['count']} now-deleted entry(s) from local config[/dim]")
        elif kind == "done":
            console.print(f"\n[green]pruned {p['deleted']}/{p['total']} key(s)[/green]")

    return execute_prune(cfg, team_id, plan.candidates, on_event=render)
