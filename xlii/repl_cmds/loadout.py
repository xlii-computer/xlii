"""Loadout management — saved attachment bundles (/loadout, alias /workspace)."""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command
from xlii.context_budget import format_loadout_budget_line


def _model_pin_source(agent, state, persona) -> str | None:
    """Best-effort label for what set ``model_override`` (persona / role / skill)."""
    override = getattr(agent, "model_override", None)
    if not override:
        return None
    session = getattr(agent, "session", None)
    stack = getattr(session, "model_pin_stack", []) if session else []
    if stack:
        return "skill"
    active_role = getattr(state, "active_role", None) or (
        getattr(session, "active_role", None) if session else None
    )
    if active_role:
        from xlii.role import load_role

        root = getattr(getattr(state, "project", None), "project_root", None)
        role = load_role(active_role, root)
        if role is not None:
            lo = role.loadout()
            declared = lo.get("model")
            if isinstance(declared, str) and declared.strip() == override:
                return "role"
            if lo.get("profile") and not declared:
                return "role profile"
    if persona is not None:
        try:
            declared = persona.loadout().get("model")
            if isinstance(declared, str) and declared.strip() == override:
                return "persona"
        except OSError:
            # An unreadable persona file just means the pin isn't attributed to the persona.
            pass
    return "session"


def show_loadout(ctx: dict[str, Any]) -> None:
    """Print the active loadout (inspect-only view)."""
    state = ctx.get("state")
    console = ctx["console"]
    if state is None:
        console.print("[dim]/loadout needs an active session[/dim]")
        return

    prof = getattr(state, "profile", None)
    persona = (
        getattr(getattr(prof, "loadout", None), "persona", None)
        or getattr(state, "persona", None)
    )

    slot = state.get_current_workspace() if hasattr(state, "get_current_workspace") else "main"
    docs = [n for n, _ in state.attached_docs]
    subs: list[str] = []
    if getattr(state, "project", None):
        from xlii.plugin import load_subscriptions
        subs = load_subscriptions(state.project.xli_dir)
    agent = state.agent if state else ctx.get("agent")
    files = getattr(state, "attached_files", []) or []

    console.print(f"[bold]loadout — {persona.name}[/bold]" if persona else "[bold]loadout[/bold]")
    if slot != "main":
        console.print(f"  saved slot:     [cyan]{slot}[/cyan]")
    if persona is not None:
        try:
            declared = persona.loadout()
        except OSError:
            declared = {}
        if declared:
            for key in ("plugins", "docs", "profile", "model", "temperature"):
                if key in declared:
                    console.print(f"  declared {key}: [cyan]{declared[key]}[/cyan]")
        else:
            console.print("  [dim]no frontmatter loadout declared — /edit to add one[/dim]")
    console.print(f"  active plugins: {', '.join(subs) or '[dim](none)[/dim]'}")
    console.print(f"  active docs:    {', '.join(docs) or '[dim](none)[/dim]'}")
    if files:
        enabled = sum(1 for e in files if e.get("enabled"))
        console.print(f"  locker:         {enabled}/{len(files)} shared")
    if agent is not None:
        try:
            effective, role = agent.orchestrator_model_and_role()
            pin = " (pinned)" if getattr(agent, "model_override", None) else ""
            pin_src = _model_pin_source(agent, state, persona)
            src_note = f" · pin: {pin_src}" if pin_src else ""
            console.print(
                f"  active model:   [cyan]{effective}[/cyan] "
                f"[dim]({role} role{pin}{src_note})[/dim]"
            )
        except Exception:
            if getattr(agent, "model_override", None):
                console.print(f"  active model:   [cyan]{agent.model_override}[/cyan]")
    if agent is not None and getattr(agent, "temperature_override", None) is not None:
        console.print(f"  active temp:    [cyan]{agent.temperature_override}[/cyan]")
    console.print(f"  [dim]{format_loadout_budget_line(state)}[/dim]")
    console.print(
        "  [dim]/loadout save <name> banks this bundle · "
        "/loadout load <name> switches[/dim]"
    )


def _loadout_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[red]No active REPLState[/red]")
        return True

    parts = line.split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else None
    arg = parts[2] if len(parts) > 2 else None

    if sub is None:
        root = parts[0].lower()
        if root in ("/workspace", "/ws"):
            sub = "list"
        else:
            show_loadout(ctx)
            return True

    if sub in ("show", "current") and arg is None:
        show_loadout(ctx)
        return True

    if sub == "save" and arg:
        state.save_workspace(arg)
        console.print(f"[green]✓[/green] Saved current loadout as [cyan]{arg}[/cyan]")
        return True

    if sub == "load" and arg:
        if state.load_workspace(arg):
            console.print(f"[green]✓[/green] Loaded loadout [cyan]{arg}[/cyan]")
        else:
            console.print(f"[red]No loadout named {arg!r}[/red]")
        return True

    if sub == "delete" and arg:
        if state.delete_workspace(arg):
            console.print(f"[green]✓[/green] Deleted loadout [cyan]{arg}[/cyan]")
        else:
            console.print(f"[red]No loadout named {arg!r}[/red]")
        return True

    if sub == "list":
        names = state.list_workspaces()
        current = state.get_current_workspace()
        if not names:
            console.print("[dim](no saved loadouts — use /loadout save <name>)[/dim]")
            return True
        console.print("[bold]Saved loadouts:[/bold]")
        for name in names:
            marker = "[bold green]●[/bold green]" if name == current else " "
            console.print(f"  {marker} [cyan]{name}[/cyan]")
        return True

    if sub == "export" and arg:
        if state.export_workspace(arg):
            console.print(f"[green]✓[/green] Exported loadout globally as [cyan]{arg}[/cyan]")
        return True

    if sub == "import" and arg:
        as_name = parts[3] if len(parts) > 3 else None
        if state.import_workspace(arg, as_name):
            local = as_name or arg
            console.print(
                f"[green]✓[/green] Imported global loadout [cyan]{arg}[/cyan] "
                f"as [cyan]{local}[/cyan]"
            )
        else:
            console.print(f"[red]Global loadout {arg!r} not found[/red]")
        return True

    if sub == "global-list":
        globals_ = state.list_global_workspaces()
        if not globals_:
            console.print("[dim](no global loadouts saved yet)[/dim]")
        else:
            console.print("[bold]Global loadouts:[/bold]")
            for name in globals_:
                console.print(f"  [cyan]{name}[/cyan]")
        return True

    if sub == "global-delete" and arg:
        if state.delete_global_workspace(arg):
            console.print(f"[green]✓[/green] Deleted global loadout [cyan]{arg}[/cyan]")
        else:
            console.print(f"[red]Global loadout {arg!r} not found[/red]")
        return True

    console.print("[dim]Usage:[/dim]")
    console.print("  /loadout [show]              — inspect the active loadout")
    console.print("  /loadout list | save <name> | load <name> | delete <name>")
    console.print("  /loadout export <name> | import <global> [as <local>]")
    console.print("  /loadout global-list | global-delete <name>")
    console.print("[dim]Alias: /workspace[/dim]")
    return True


def register() -> None:
    cmd = REPLCommand(
        name="loadout",
        handler=_loadout_handler,
        aliases=["workspace", "ws"],
        description="Saved loadout bundles (docs, locker, model) — save, load, export",
        usage="/loadout [show | save|load|list|delete|export|import|global-list|global-delete] …",
        category="knowledge",
        repls=["code", "chat"],
    )
    register_repl_command(cmd)
