"""Vault migration for chat keys (`xlii keys migrate`)."""

from __future__ import annotations

import argparse

from xlii.config import migrate_chat_keys
from xlii.ui import console


def cmd_keys_migrate(args: argparse.Namespace) -> int:
    """Move plaintext chat-key secrets out of config.json into the encrypted
    vault, replacing each with a `vault_ref`.

    Local-only — needs neither the management key nor the network (unlike the
    other `keys` ops). Idempotent: entries already vault-backed are left alone.
    A timestamped backup of config.json is written before any change.
    """
    from xlii.vault import VaultError

    # Plan first (detection only — never touches the vault or the file) so the
    # report prints before any vault-unlock failure, as it always has.
    plan = migrate_chat_keys(dry_run=True)
    if plan.config_missing:
        console.print("[yellow]no config.json — nothing to migrate.[/yellow]")
        return 0
    if not plan.pending:
        console.print("[green]✓[/green] no plaintext chat keys — already encrypted (or none configured).")
        return 0

    console.print(f"[bold]{len(plan.pending)} plaintext chat key(s)[/bold] will move into the encrypted vault.")
    if getattr(args, "dry_run", False):
        for p in plan.pending:
            if p.bare:
                console.print(f"  · {p.label}  [dim](bare string → vault)[/dim]")
            else:
                console.print(f"  · {p.label}  [dim](api_key → vault)[/dim]")
        console.print("[dim]dry run — re-run without --dry-run to apply.[/dim]")
        return 0

    try:
        report = migrate_chat_keys(
            dry_run=False,
            backup=not getattr(args, "no_backup", False),
        )
    except VaultError as e:
        console.print(f"[red]cannot open vault: {e}[/red]")
        return 1

    console.print(f"[green]✓[/green] migrated {report.migrated} key(s) into the vault "
                  f"(master-key backend: [cyan]{report.vault_backend}[/cyan]).")
    console.print("  config.json now stores only vault refs — secrets removed from plaintext.")
    if report.backup_path:
        console.print(f"  [dim]backup (still holds the old plaintext): {report.backup_path}[/dim]")
        console.print("  [dim]delete the backup once `xlii doctor` confirms things work.[/dim]")
    return 0
