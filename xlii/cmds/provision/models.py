"""Model inspection and configuration (`xlii models`)."""

from __future__ import annotations

import argparse

from xlii.bootstrap import (
    BootstrapError,
    discover_models,
    discover_team_id,
    pick_best_models,
    require_management_key,
    set_models_in_config,
)
from xlii.config import GlobalConfig
from xlii.ui import console


def _resolved_chat_keys(cfg: GlobalConfig) -> list[str]:
    """Chat API secrets for /v1/models discovery.

    Prefer `key_pairs()` so vault-backed `vault_ref` entries resolve; raw
    `keys[].api_key` is empty after `xlii keys migrate`.
    """
    try:
        return [kp.api_key for kp in cfg.key_pairs() if kp.api_key]
    except RuntimeError as e:
        console.print(f"[red]could not unlock chat keys: {e}[/red]")
        return []


def cmd_models(args: argparse.Namespace) -> int:
    """Inspect & set the models xlii uses."""
    cfg = GlobalConfig.load()
    try:
        require_management_key(cfg)
    except BootstrapError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    # Pass every chat key so discovery can iterate past dead/revoked entries.
    chat_keys = _resolved_chat_keys(cfg)

    action = args.action
    if action == "list":
        return _models_list(cfg, chat_keys)
    if action == "recommended":
        return _models_recommended(cfg, chat_keys)
    if action == "set":
        return _models_set(args)
    if action == "profile":
        return _models_profile(args)
    console.print(f"[red]unknown action: {action}[/red]")
    return 1


def _models_list(cfg: GlobalConfig, chat_keys: list[str]) -> int:
    try:
        team_id = discover_team_id(cfg)
    except BootstrapError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    available = discover_models(cfg.management_api_key, team_id, chat_keys=chat_keys)
    if not available:
        console.print(
            "[yellow]no models returned — see stderr for the last endpoint error.\n"
            "If all keys are rejected, try `xlii keys list` then `xlii keys rotate` "
            "on a healthy one[/yellow]"
        )
        return 1
    orch = cfg.get_model_for_role("orchestrator")
    worker = cfg.get_model_for_role("worker")
    chat = cfg.get_model_for_role("chat")
    help_m = cfg.get_model_for_role("help")
    console.print(f"[bold]{len(available)} model(s) available:[/bold]")
    for m in sorted(available):
        marks = []
        if m == orch:
            marks.append("[cyan]orch[/cyan]")
        if m == worker:
            marks.append("[cyan]worker[/cyan]")
        if m == chat:
            marks.append("[cyan]chat[/cyan]")
        if m == help_m:
            marks.append("[cyan]help[/cyan]")
        tag = "  ←  " + " + ".join(marks) if marks else ""
        console.print(f"  · {m}{tag}")
    return 0


def _models_recommended(cfg: GlobalConfig, chat_keys: list[str]) -> int:
    try:
        team_id = discover_team_id(cfg)
    except BootstrapError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    available = discover_models(cfg.management_api_key, team_id, chat_keys=chat_keys)
    if not available:
        console.print("[yellow]no models returned[/yellow]")
        return 1
    orch, worker = pick_best_models(available)
    console.print("[bold]heuristic recommendations:[/bold]")
    console.print(f"  orchestrator: [cyan]{orch or '(none)'}[/cyan]")
    console.print(f"  worker:       [cyan]{worker or '(none)'}[/cyan]")
    cur_orch = cfg.get_model_for_role("orchestrator")
    cur_worker = cfg.get_model_for_role("worker")
    if orch and orch != cur_orch:
        console.print(
            f"  [dim]apply orch:   [/dim] [cyan]xlii models set --orchestrator {orch}[/cyan]"
        )
    if worker and worker != cur_worker:
        console.print(
            f"  [dim]apply worker: [/dim] [cyan]xlii models set --worker {worker}[/cyan]"
        )
    return 0


def _models_set(args: argparse.Namespace) -> int:
    help_model = getattr(args, "help_model", None)
    if not args.orchestrator and not args.worker and not args.chat and not help_model:
        console.print(
            "[red]nothing to set — pass --orchestrator, --worker, --chat, "
            "and/or --help-model[/red]"
        )
        return 1
    set_models_in_config(
        args.orchestrator,
        args.worker,
        chat_model=args.chat,
        help_model=help_model,
        auto_detected=False,
    )
    if args.orchestrator:
        console.print(f"[green]✓[/green] orchestrator_model = [cyan]{args.orchestrator}[/cyan]")
    if args.worker:
        console.print(f"[green]✓[/green] worker_model       = [cyan]{args.worker}[/cyan]")
    if args.chat:
        console.print(f"[green]✓[/green] chat_model         = [cyan]{args.chat}[/cyan]")
    if help_model:
        console.print(f"[green]✓[/green] help_model         = [cyan]{help_model}[/cyan]")
    return 0


def _models_profile(args: argparse.Namespace) -> int:
    from xlii.model_profiles import (
        apply_model_profile,
        effective_model_profiles,
        format_profile_line,
        profile_matches_cfg,
    )

    cfg = GlobalConfig.load()
    sub = getattr(args, "profile_action", None)
    if sub == "list":
        profiles = effective_model_profiles(cfg)
        console.print("[bold]model profiles[/bold] [dim](orchestrator · worker · chat)[/dim]")
        for name in sorted(profiles):
            console.print(
                format_profile_line(
                    name, profiles[name], active=profile_matches_cfg(cfg, name)
                )
            )
        console.print(
            "[dim]apply: [/dim][cyan]xlii models profile set <name>[/cyan]  "
            "[dim]or in-session [/dim][cyan]/model --profile <name>[/cyan]"
        )
        return 0
    if sub == "set":
        name = getattr(args, "name", None)
        if not name:
            console.print("[red]pass a profile name — e.g. xlii models profile set vision[/red]")
            return 1
        try:
            prof = apply_model_profile(cfg, name, persist=True)
        except KeyError:
            console.print(
                f"[red]unknown profile {name!r}[/red] "
                "[dim](`xlii models profile list` for built-ins)[/dim]"
            )
            return 1
        console.print(f"[green]✓[/green] applied profile [bold]{name}[/bold]")
        console.print(
            f"  orchestrator [cyan]{prof.get('orchestrator', '?')}[/cyan]  "
            f"worker [cyan]{prof.get('worker', '?')}[/cyan]  "
            f"chat [cyan]{prof.get('chat', '?')}[/cyan]"
        )
        return 0
    console.print(f"[red]unknown profile action: {sub!r}[/red]")
    return 1
