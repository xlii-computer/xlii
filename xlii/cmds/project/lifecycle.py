"""Project sync and status subcommands."""

from __future__ import annotations

import argparse
from pathlib import Path

from xlii import __version__
from xlii.client import Clients, MissingCredentials
from xlii.config import (
    GLOBAL_CONFIG_FILE,
    GlobalConfig,
    ProjectConfig,
)
from xlii.project_resolver import ProjectResolution, project_is_alive, resolve_registered_project
from xlii.registry import REGISTRY_FILE, Registry
from xlii.sync import sync_project
from xlii.ui import confirm, console


def cmd_sync(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    try:
        clients = Clients.from_config(cfg)
    except MissingCredentials as e:
        console.print(f"[red]{e}[/red]")
        return 1
    project = ProjectConfig.load(Path(args.path).resolve())
    if not project:
        console.print("[red]not an xlii project — run `xlii init` first[/red]")
        return 1
    from xlii.desk_files import files_root_of
    from xlii.sync import provision_collection

    if project.local_only:
        mgmt = bool(getattr(cfg, "management_api_key", None) or "")
        if not (mgmt and files_root_of(project)):
            console.print("[dim]local-only project — nothing to upload[/dim]")
            return 0
        try:
            cid = provision_collection(clients, project)
        except Exception as e:  # noqa: BLE001
            console.print(f"[red]could not attach collection: {e}[/red]")
            return 1
        console.print(
            f"[dim]attached collection {cid} — uploading from "
            f"{files_root_of(project)}[/dim]"
        )
    def _confirm_deletes(paths: list[str]) -> bool:
        console.print(f"[yellow]sync wants to delete {len(paths)} remote doc(s):[/yellow]")
        for p in paths:
            console.print(f"  [red]- {p}[/red]")
        return confirm("delete these from the collection? [y/N] ")

    with console.status("[cyan]syncing project…[/cyan]"):
        stats = sync_project(
            clients, project, cfg,
            dry_run=args.dry_run,
            confirm_deletes=None if args.dry_run else _confirm_deletes,
        )
    color = "yellow" if args.dry_run else "green"
    label = "would sync" if args.dry_run else "synced"
    console.print(f"[{color}]{label}:[/{color}] {stats.summary()}")
    if args.dry_run:
        for op, marker, style in (("upload", "+", "green"), ("update", "~", "cyan"), ("delete", "-", "red")):
            for p in stats.planned[op]:
                console.print(f"  [{style}]{marker} {p}[/{style}]")
    if stats.errors:
        for e in stats.errors[:5]:
            console.print(f"  [red]· {e}[/red]")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    project = ProjectConfig.load(Path(args.path).resolve())
    pairs = cfg.key_pairs()
    registry = Registry.load()
    console.print(f"[bold]xli[/bold] v{__version__}")
    console.print(f"  config file:        {GLOBAL_CONFIG_FILE}")
    console.print(f"  registry:           {REGISTRY_FILE} ({len(registry.entries)} project(s))")
    if cfg.management_api_key:
        console.print("  mgmt key:           [green]✓[/green] from env XAI_MANAGEMENT_API_KEY")
    else:
        console.print("  mgmt key:           [red]✗ unset[/red] — export XAI_MANAGEMENT_API_KEY")
    if GlobalConfig.mgmt_key_in_file():
        console.print(
            "  [yellow]⚠ legacy management_api_key found in config.json — remove it[/yellow]"
        )
    auto_tag = ""
    if cfg.models_detected_at:
        auto_tag = f"  [dim](auto-detected {cfg.models_detected_at[:10]})[/dim]"
    console.print(f"  orchestrator model: [cyan]{cfg.get_model_for_role('orchestrator')}[/cyan]{auto_tag}")
    console.print(f"  worker model:       [cyan]{cfg.get_model_for_role('worker')}[/cyan]")
    console.print(f"  orchestrator temp:  [cyan]{cfg.orchestrator_temp()}[/cyan]")
    console.print(f"  worker temp:        [cyan]{cfg.worker_temp()}[/cyan]")
    if cfg.pricing:
        priced = sum(
            1
            for m in (cfg.get_model_for_role("orchestrator"), cfg.get_model_for_role("worker"))
            if m in cfg.pricing
        )
        console.print(
            f"  cost tracking:      [green]enabled[/green] "
            f"({len(cfg.pricing)} models priced; {priced}/2 active models covered)"
        )
    else:
        console.print(
            "  cost tracking:      [yellow]disabled[/yellow] "
            "(add `pricing` map to config to enable)"
        )
    if pairs:
        for p in pairs:
            mgmt = "[green]✓[/green]" if p.management_api_key else "[red]missing[/red]"
            console.print(f"    · {p.label:<12} api=set  mgmt={mgmt}")
        console.print(f"  pool size:     {len(pairs)} key(s)")
    else:
        console.print("  [red]no keys configured[/red] — run `xlii config` to write a template")
    if project:
        console.print(f"\n[bold]project:[/bold] {project.name}")
        console.print(f"  root:          {project.project_root}")
        console.print(f"  collection_id: {project.collection_id}")
        if project.bound_persona:
            console.print(
                f"  chat persona:  [magenta]{project.bound_persona}[/magenta]  "
                f"[dim](bare /chat opens it; code memory stays project-local)[/dim]"
            )
        console.print(f"  manifest:      {project.manifest_path}")
        if project.conversation_id:
            console.print(
                f"  conv_id:       {project.conversation_id[:12]}…  "
                f"[dim](xAI prompt-cache key)[/dim]"
            )
    else:
        console.print("\n[yellow]no xlii project in this directory[/yellow]")
    return 0


def _print_project_entry(entry, *, current: Path | None = None) -> None:
    marker = "[green]●[/green]" if project_is_alive(entry) else "[red]✗[/red]"
    here = " [cyan](current)[/cyan]" if current and Path(entry.path).resolve() == current else ""
    cid = entry.collection_id or "local-only"
    console.print(
        f"  {marker} [bold]{entry.name:<24}[/bold]  {entry.path:<50}  [dim]{cid}[/dim]{here}"
    )


def _print_project_resolution(res: ProjectResolution) -> int:
    if res.ok and res.entry is not None:
        entry = res.entry
        console.print("[bold]project match:[/bold]")
        _print_project_entry(entry)
        console.print(f"[dim]open: xlii code {entry.name}[/dim]")
        return 0
    if res.ambiguous:
        console.print(f"[yellow]ambiguous project query {res.query!r}; matches:[/yellow]")
        for entry in res.matches:
            _print_project_entry(entry)
        return 1
    if res.entry is not None:
        console.print(f"[yellow]matched project is unavailable: {res.entry.name}[/yellow]")
        _print_project_entry(res.entry)
        if res.reason:
            console.print(f"[dim]{res.reason}[/dim]")
        return 1
    console.print(f"[yellow]no projects matching {res.query!r}[/yellow]")
    return 1


def _open_project(project: ProjectConfig, *, yolo: bool = False, no_sync: bool = False) -> int:
    """Launch a resolved project through the kernel seam (B4): typed args, no
    fabricated ``argparse.Namespace`` for cmd_code."""
    from xlii.cmds.sessions.code import (
        _ask_episode,
        _prompt_launch_gate,
        _stdin_interactive,
        run_code_session,
    )
    from xlii.session_boot import build_code_session

    interactive = _stdin_interactive()
    boot = build_code_session(
        Path(project.project_root),
        yolo=yolo,
        no_sync=no_sync,
        launch=True,
        interactive=interactive,
        ask_launch=_prompt_launch_gate if interactive else None,
        ask_episode=_ask_episode,
    )
    if boot.status != "ok":
        return 0 if boot.status == "cancelled" else 1
    return run_code_session(boot.session, tui=False)


def cmd_find(args: argparse.Namespace) -> int:
    res = resolve_registered_project(args.query)
    code = _print_project_resolution(res)
    if code == 0 and getattr(args, "open", False) and res.project is not None:
        return _open_project(
            res.project,
            yolo=getattr(args, "yolo", False),
            no_sync=getattr(args, "no_sync", False),
        )
    if code == 0 and not getattr(args, "open", False):
        console.print("[dim]pass --open to launch this project now[/dim]")
    return code


def cmd_projects(args: argparse.Namespace) -> int:
    if getattr(args, "filter", None) == "find":
        if not getattr(args, "query", None):
            console.print("[yellow]usage: xlii projects find <name> [--open][/yellow]")
            return 1
        return cmd_find(argparse.Namespace(
            query=args.query,
            open=getattr(args, "open", False),
            yolo=getattr(args, "yolo", False),
            no_sync=getattr(args, "no_sync", False),
        ))

    registry = Registry.load()
    entries = registry.entries
    flt = (args.filter or "").lower()
    if flt:
        entries = [
            e for e in entries
            if flt in e.name.lower() or flt in e.path.lower()
        ]
    if not entries:
        msg = (
            f"no projects matching {args.filter!r}" if flt
            else "no registered projects yet — run `xlii init` in a project dir"
        )
        console.print(f"[yellow]{msg}[/yellow]")
        return 0
    console.print("[bold]registered xlii projects:[/bold]")
    for entry in sorted(entries, key=lambda e: e.name.lower()):
        _print_project_entry(entry)
    return 0
