"""Vault credential management (`xlii auth`)."""

from __future__ import annotations

import argparse

from xlii.ui import console


def cmd_auth(args: argparse.Namespace) -> int:
    """Manage plugin credentials in the encrypted vault (set/list/clear).

    Values are read via getpass (never argv, never echoed) and only ever
    stored encrypted. `list` shows names, never values.
    """
    from xlii.vault import Vault, VaultError

    try:
        vault = Vault.unlock(create_if_missing=(args.auth_action == "set"))
    except VaultError as e:
        console.print(f"[red]vault unavailable: {e}[/red]")
        return 1

    if args.auth_action == "set":
        import getpass
        try:
            value = getpass.getpass(f"value for {args.env_var} ({args.plugin_id}): ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]aborted[/dim]")
            return 1
        if not value:
            console.print("[red]empty value — nothing stored[/red]")
            return 1
        vault.set(args.plugin_id, args.env_var, value)
        console.print(f"[green]✓[/green] stored [cyan]{args.env_var}[/cyan] for plugin [cyan]{args.plugin_id}[/cyan]")
        return 0

    if args.auth_action == "list":
        plugins = vault.list_plugins()
        if not plugins:
            console.print("[dim](vault is empty — add with `xlii auth set <plugin-id> <ENV_VAR>`)[/dim]")
            return 0
        for pid in plugins:
            names = ", ".join(vault.list_keys(pid))
            console.print(f"  [cyan]{pid}[/cyan]: {names}")
        return 0

    if args.auth_action == "clear":
        removed = vault.unset(args.plugin_id, args.env_var)
        if removed:
            what = args.env_var or "all credentials"
            console.print(f"[green]✓[/green] cleared {what} for [cyan]{args.plugin_id}[/cyan]")
            return 0
        console.print("[dim]nothing matched — see `xlii auth list`[/dim]")
        return 1

    return 1
