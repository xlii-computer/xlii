"""/workbench — quick-launch pack + pane offers (three-faces.md / typed-workbenches).

B0: switching PERSISTS the project's active type (``.xlii/workbench.json``).
Q1/Q2 (three-faces): switching refreshes the **quick-launch strip** and pane
offers — it does **not** flip posture and does **not** rebind persona.
Pack ≠ identity. Optional ``--bind-persona`` restores the old B2 bind for
operators who want it.

Ambient fusion per type remains ``build_mojo_ambient``'s workbench note.
Registry data: ``xlii/workbench.py``.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.workbench import (
    BUILTIN_WORKBENCHES,
    WorkbenchType,
    get_workbench,
    list_workbenches,
    load_active_type,
    quick_launch_buttons,
    save_active_type,
)


def _xli_dir(ctx: dict[str, Any]) -> Optional[Path]:
    project = ctx.get("project")
    xli_dir = getattr(project, "xli_dir", None) if project is not None else None
    return Path(xli_dir) if xli_dir is not None else None


def _describe(t: WorkbenchType) -> str:
    """One-line summary: pack first (three-faces), then panes."""
    ql = " · ".join(b["label"] for b in quick_launch_buttons(t)) or "—"
    panes = " · ".join(t.panes) if t.panes else "no panes"
    return (
        f"quick: {ql} · panes: {panes}"
        + (f" · ambient: {t.ambient}" if t.ambient else "")
    )


def _bind_persona(console, project, persona: str) -> None:
    """Seed + bind a persona as the project's chat default (opt-in only)."""
    from xlii.persona import ensure_stock_persona

    ensure_stock_persona(persona)
    if project is not None and getattr(project, "bound_persona", None) != persona:
        previous = getattr(project, "bound_persona", None)
        project.bound_persona = persona
        try:
            project.save()
        except Exception:
            project.bound_persona = previous  # never bind what didn't persist
            raise
    console.print(
        f"[dim]chat persona → {persona} (project binding; "
        f"/persona in the config panel to override)[/dim]"
    )


def _pop_flag(args: list[str], *names: str) -> bool:
    """Remove the first matching flag from *args*; return whether it was present."""
    for n in names:
        if n in args:
            args.remove(n)
            return True
    return False


def _new(console, ctx: dict[str, Any], xli_dir: Optional[Path], args: list[str]) -> None:
    """/workbench new <type> <name> [path] [--bind-persona] — create + open."""
    bind = _pop_flag(args, "--bind-persona", "--bind")
    if len(args) < 2:
        console.print(
            "[yellow]usage: /workbench new <type> <name> [path] [--bind-persona][/yellow]"
        )
        return
    type_name, name = args[0].lower(), args[1]
    wb = BUILTIN_WORKBENCHES.get(type_name)
    if wb is None:
        valid = ", ".join(BUILTIN_WORKBENCHES)
        console.print(f"[yellow]unknown workbench type {type_name!r}[/yellow] — valid types: {valid}")
        return
    root = Path(args[2]).expanduser() if len(args) > 2 else Path.cwd() / name
    root = root.resolve()
    from xlii.config import ProjectConfig

    if ProjectConfig.load(root) is not None:
        console.print(f"[yellow]{root} is already an xlii project[/yellow]")
        return
    if root.exists() and not root.is_dir():
        console.print(f"[yellow]{root} exists and is not a directory[/yellow]")
        return
    if root.is_dir() and any(root.iterdir()):
        console.print(f"[yellow]{root} exists and is not empty[/yellow]")
        return
    from xlii.registry import Registry

    if any(e.name.lower() == name.lower() for e in Registry.load().entries):
        console.print(f"[yellow]project name {name!r} already exists in the registry[/yellow]")
        return
    root.mkdir(parents=True, exist_ok=True)
    try:
        from xlii.sync import init_project

        project = init_project(None, root, name=name, local_only=True)
    except Exception as e:  # noqa: BLE001 — a failed init must not half-switch
        console.print(f"[red]project init failed: {type(e).__name__}: {e}[/red]")
        return
    save_active_type(project.xli_dir, wb.name)
    console.print(f"[green]+ {name}[/green] — workbench {wb.name} · {_describe(wb)}")
    if bind and wb.persona:
        try:
            _bind_persona(console, project, wb.persona)
        except Exception as e:  # noqa: BLE001
            console.print(f"[yellow]persona bind failed ({e}) — project created unbound[/yellow]")
    elif wb.persona and not bind:
        console.print(
            f"[dim]pack suggests persona {wb.persona!r} — "
            f"/workbench {wb.name} --bind-persona or /persona to bind[/dim]"
        )
    state = ctx.get("state")
    if state is not None:
        state.workbench = wb  # the teleport reboots; this covers the failure path
    from xlii.repl_cmds.switch import switch_to_code_project

    switch_to_code_project(ctx, project)


def h_workbench(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    xli_dir = _xli_dir(ctx)
    parts = line.split(maxsplit=1)
    arg = parts[1].strip() if len(parts) > 1 else ""
    try:
        args = shlex.split(arg) if arg else []
    except ValueError as e:
        console.print(f"[yellow]bad quoting: {e}[/yellow]")
        return True

    if not arg:
        active = load_active_type(xli_dir)
        console.print(
            f"active workbench: [cyan]{active}[/cyan] "
            f"[dim](quick-launch pack · /workbench <type> · "
            f"/workbench new <type> <name>)[/dim]"
        )
        for t in list_workbenches(xli_dir):
            mark = "▸" if t.name == active else " "
            console.print(f" {mark} [cyan]{t.name}[/cyan] — {_describe(t)}")
        return True

    if args[0].lower() == "new":
        _new(console, ctx, xli_dir, args[1:])
        return True

    bind = _pop_flag(args, "--bind-persona", "--bind")
    if not args:
        console.print("[yellow]usage: /workbench <type> [--bind-persona][/yellow]")
        return True

    wb = get_workbench(args[0].lower(), xli_dir)
    if wb is None:
        valid = ", ".join(t.name for t in list_workbenches(xli_dir))
        console.print(
            f"[yellow]unknown workbench type '{args[0]}'[/yellow] — valid types: {valid}"
        )
        return True

    if xli_dir is None:
        console.print("[dim]/workbench: no project context — type not persisted[/dim]")
        return True

    save_active_type(xli_dir, wb.name)
    state = ctx.get("state")
    if state is not None:
        state.workbench = wb
    console.print(f"[green]workbench → {wb.name}[/green] — {_describe(wb)}")
    console.print(
        "[dim]quick-launch pack updated (not a mode · posture/persona unchanged)[/dim]"
    )

    # Opt-in only: pack ≠ identity (three-faces Q2).
    if bind:
        if not wb.persona:
            console.print(
                f"[dim]{wb.name!r} has no suggested persona — nothing to bind[/dim]"
            )
        else:
            try:
                _bind_persona(console, ctx.get("project"), wb.persona)
            except Exception as e:  # noqa: BLE001 — bind hiccup must not un-switch
                console.print(
                    f"[yellow]persona bind failed ({e}) — type switched, "
                    f"binding unchanged[/yellow]"
                )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="workbench",
            handler=h_workbench,
            usage=(
                "/workbench [type] [--bind-persona] | "
                "/workbench new <type> <name> [path] [--bind-persona]"
            ),
            description=(
                "Pack home|chat|code — face slot dropdown + Panel Workbench follow "
                "the pack. Not a mode. Chat holds research doors; home leads "
                "with switch; optional --bind-persona restores old persona apply"
            ),
            category="session",
        )
    )
