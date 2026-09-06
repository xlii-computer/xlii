"""/destroy-all · /destroy · /factory-reset — human-only deny ladder (Wave 1 L0+L1)."""

from __future__ import annotations

import dataclasses
from typing import Any

from xlii.commands import REPLCommand, register_repl_command
from xlii.destroy import run_destroy


def _register_cmd(**kwargs: Any) -> None:
    fields = {f.name for f in dataclasses.fields(REPLCommand)}
    if "human_only" not in fields:
        kwargs.pop("human_only", None)
    register_repl_command(REPLCommand(**kwargs))


def _render_report(console, report) -> None:
    console.print(
        f"[bold]destroy[/bold] aim={report.aim} level={report.level} "
        f"dry_run={report.dry_run} local_only={report.local_only}"
    )
    console.print(f"  planned: {len(report.planned)} item(s)")
    if report.done:
        console.print(f"  done: {len(report.done)}")
    if report.failed:
        console.print(f"[yellow]  failed: {len(report.failed)}[/yellow]")
    for line in report.residuals:
        console.print(f"[dim]  · {line}[/dim]")
    if report.journal_path:
        console.print(f"[dim]  journal: {report.journal_path}[/dim]")


def _parse_destroy_all(line: str) -> tuple[int, bool]:
    parts = line.split()
    level = 0
    local_only = False
    for tok in parts[1:]:
        low = tok.lower()
        if low in ("keys-and-local", "keys_and_local"):
            level = 1
        elif low in ("--local-only", "local-only", "local_only"):
            local_only = True
    return level, local_only


def h_destroy_all(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    level, local_only = _parse_destroy_all(line)
    token = (line.split() or [""])[0].lstrip("/").lower()
    if token == "factory-reset":
        level = 1
    dry_run = level == 0
    report = run_destroy(
        "all",
        level=level,
        dry_run=dry_run,
        local_only=local_only,
        console=console,
    )
    _render_report(console, report)
    return True


def h_destroy(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    parts = line.split(maxsplit=3)
    if len(parts) < 2:
        console.print(
            "[dim]usage:[/dim] /destroy throne  ·  /destroy node <name>"
        )
        return True
    sub = parts[1].lower()
    target = parts[2] if len(parts) > 2 else ""
    if sub == "throne":
        aim = "throne"
        target = ""
    elif sub == "node":
        aim = "node"
        if not target:
            console.print("[yellow]usage: /destroy node <name>[/yellow]")
            return True
    else:
        console.print("[yellow]usage: /destroy throne | /destroy node <name>[/yellow]")
        return True
    report = run_destroy(
        aim,
        target=target,
        level=1,
        dry_run=False,
        console=console,
    )
    _render_report(console, report)
    return True


def register() -> None:
    _register_cmd(
        name="destroy-all",
        aliases=["factory-reset"],
        handler=h_destroy_all,
        capability="admin",
        human_only=True,
        category="danger",
        source="builtin",
        description="Dry-run inventory of what this body would erase (help all).",
        usage="/destroy-all | /destroy-all keys-and-local | /destroy throne | /destroy node <name>",
        repls=["code", "chat"],
    )
    _register_cmd(
        name="destroy",
        handler=h_destroy,
        capability="admin",
        human_only=True,
        category="danger",
        source="builtin",
        description="Scoped destroy: this throne or a named node (Wave 1 body-only).",
        usage="/destroy throne | /destroy node <name>",
        repls=["code", "chat"],
    )
