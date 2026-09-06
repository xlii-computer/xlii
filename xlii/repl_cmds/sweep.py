"""``/sweep`` — throne housekeep from any surface (home included)."""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def _sweep_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    parts = line.split()
    flags = set(parts[1:])
    if any(p in ("-h", "--help", "help") for p in flags):
        console.print(
            "[dim]/sweep[/dim] inventory · "
            "[cyan]/sweep empty[/cyan] · [cyan]test[/cyan] · "
            "[cyan]ghosts[/cyan] · [cyan]keys[/cyan]  "
            "[dim](yes to apply)[/dim]"
        )
        return True
    from xlii.sweep import apply_sweep, gather_live, render_report

    report = gather_live()
    if report.error:
        console.print(f"[red]{report.error}[/red]")
        return True
    empty = "empty" in flags or "--empty" in flags
    test = "test" in flags or "--test" in flags
    ghosts = "ghosts" in flags or "--ghosts" in flags
    keys = "keys" in flags or "--keys" in flags
    yes = "yes" in flags or "--yes" in flags
    if not any((empty, test, ghosts, keys)):
        render_report(report, console)
        return True

    from xlii.client import Clients, MissingCredentials
    from xlii.config import GlobalConfig
    from xlii.registry import Registry
    from xlii.storage_backend import CollectionsBackend
    from xlii.ui import confirm

    cfg = GlobalConfig.load()
    try:
        clients = Clients.from_config(cfg)
    except MissingCredentials as e:
        console.print(f"[red]{e}[/red]")
        return True
    deleted: list[str] = []

    def _del(cid: str) -> None:
        CollectionsBackend.delete_collection(clients, cid)
        deleted.append(cid)

    def _ghosts() -> int:
        reg = Registry.load()
        dead = reg.prune_dead()
        reg.save()
        return len(dead)

    def _keys() -> int:
        from xlii.bootstrap import discover_team_id, execute_prune, plan_prune

        plan = plan_prune(cfg, discover_team_id(cfg))
        n = {"ok": 0}

        def _ev(kind, **_p):
            if kind == "deleted":
                n["ok"] += 1

        execute_prune(cfg, discover_team_id(cfg), plan.candidates, on_event=_ev)
        return n["ok"]

    apply_sweep(
        report,
        empty=empty, test=test, ghosts=ghosts, keys=keys, yes=yes,
        delete_collection=_del,
        prune_ghosts=_ghosts if ghosts else None,
        prune_keys=_keys if keys else None,
        confirm=lambda m: confirm(m + " [y/N] ", assume_yes=yes),
        out=console,
    )
    if deleted:
        reg = Registry.load()
        for cid in deleted:
            reg.remove(cid)
        reg.save()
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="sweep",
            handler=_sweep_handler,
            description="Throne housekeep: collections, empty/test leftovers, ghosts, dead keys",
            usage="/sweep [empty|test|ghosts|keys] [yes]",
            category="admin",
            repls=["code", "chat"],
        )
    )
