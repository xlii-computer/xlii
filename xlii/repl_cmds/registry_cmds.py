"""Registry introspection slash commands: /commands, /tools, /interactive."""

from __future__ import annotations

from typing import Any

from xlii.commands import (
    REPLCommand,
    _COMMAND_LOAD_ERRORS,
    _REPL_COMMANDS,
    register_repl_command,
    reload_project_commands,
)


def _cmd_commands_list(line: str, ctx: dict[str, Any]) -> bool:
    """Handler for /commands and subcommands."""
    console = ctx["console"]
    parts = line.split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else None

    if sub == "reload":
        return _cmd_commands_reload(line, ctx)
    if sub == "errors":
        return _cmd_commands_errors(line, ctx)

    # Default: list
    project_cmds = [c for c in _REPL_COMMANDS if c.source == "project"]

    if not project_cmds:
        console.print("[dim](no project commands loaded for this REPL)[/dim]")
        console.print("[dim]Create .xlii/commands.py or .xlii/commands/ to add some.[/dim]")
        return True

    console.print(f"[bold]Project commands ({len(project_cmds)}):[/bold]")
    for cmd in sorted(project_cmds, key=lambda c: c.name):
        usage = cmd.usage or f"/{cmd.name}"
        console.print(f"  [cyan]{usage}[/cyan]  {cmd.description}")
    console.print("\n[dim]Use /commands reload to pick up changes without restarting the REPL.[/dim]")
    return True


def _cmd_commands_reload(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state or not hasattr(state, "project"):
        console.print("[red]Cannot reload — no project context available[/red]")
        return True

    count_before = len([c for c in _REPL_COMMANDS if c.source == "project"])
    count_after = reload_project_commands(state.project.xli_dir)

    console.print(f"[green]✓[/green] reloaded project commands "
                  f"({count_before} → {count_after})")
    return True


def _cmd_commands_errors(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    if not _COMMAND_LOAD_ERRORS:
        console.print("[dim](no command load errors recorded this session)[/dim]")
        return True

    console.print(f"[bold red]Command load errors ({len(_COMMAND_LOAD_ERRORS)}):[/bold red]")
    for path, tb in _COMMAND_LOAD_ERRORS.items():
        console.print(f"\n[yellow]{path}[/yellow]")
        console.print(tb)
    return True


def _cmd_tools(line: str, ctx: dict[str, Any]) -> bool:
    """Main /tools handler with subcommands."""
    console = ctx["console"]
    parts = line.split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else None
    arg = parts[2] if len(parts) > 2 else None

    from xlii import tools as _tools

    if sub == "reload":
        return _cmd_tools_reload(line, ctx)

    if sub == "show" and arg:
        tool = _tools._PROJECT_TOOLS.get(arg)
        if not tool:
            # Also check builtins for completeness
            if arg in _tools.REGISTRY:
                console.print(f"[cyan]{arg}[/cyan] is a built-in tool (no schema details exposed here).")
            else:
                console.print(f"[red]No project tool named {arg!r}[/red]")
            return True

        console.print(f"[bold cyan]{tool.name}[/bold cyan]  [dim]({tool.source})[/dim]")
        console.print(f"Description: {tool.description or '(none)'}")
        console.print(f"Parallel safe: {tool.parallel_safe}")
        console.print(f"Plan mode safe: {tool.plan_mode_safe}")
        console.print(f"Worker safe: {tool.worker_safe}")
        console.print("Parameters schema:")
        console.print_json(data=tool.parameters)
        return True

    # Default: list all
    project_tools = list(_tools._PROJECT_TOOLS.values())
    builtin_count = len(_tools.REGISTRY)

    console.print(f"[bold]Agent tools[/bold] — {builtin_count} built-in + {len(project_tools)} project")

    if project_tools:
        console.print("\n[bold cyan]Project tools:[/bold cyan]")
        for t in sorted(project_tools, key=lambda x: x.name):
            flags = []
            if t.parallel_safe:
                flags.append("parallel")
            if t.plan_mode_safe:
                flags.append("plan")
            if t.worker_safe:
                flags.append("worker")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            console.print(f"  [cyan]{t.name}[/cyan]{flag_str} — {t.description or '(no description)'}")
        console.print("\n[dim]Use /tools show <name> for schema, or /tools reload after editing.[/dim]")
    else:
        console.print("\n[dim]No project tools loaded yet.[/dim]")
        console.print("[dim]Create .xlii/tools.py (or .xlii/tools/) with a get_tools() function.[/dim]")

    return True


def _cmd_tools_reload(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state or not hasattr(state, "project"):
        console.print("[red]No project context available[/red]")
        return True

    from xlii.tools import load_project_tools
    load_project_tools(state.project.xli_dir)
    console.print("[green]✓[/green] Project tools reloaded")
    return True


def _cmd_interactive(line: str, ctx: dict[str, Any]) -> bool:
    """Manage the full-screen / interactive program list. Programs on this list
    (mc, vim, htop, … plus your additions) run in a REAL terminal automatically
    instead of being captured into a block — and any command can be forced there
    with the `!!` prefix."""
    console = ctx["console"]
    from xlii.interactive import (
        DEFAULTS, add_program, interactive_programs, remove_program,
    )
    parts = line.split(maxsplit=2)
    sub = parts[1] if len(parts) > 1 else "list"
    arg = parts[2].strip() if len(parts) > 2 else None

    if sub == "add" and arg:
        if add_program(arg):
            console.print(f"[green]✓[/green] [cyan]{arg}[/cyan] will now run full-screen "
                          "[dim](a real terminal, not a captured block)[/dim]")
        else:
            console.print(f"[dim]{arg} is already handled (built-in or already added)[/dim]")
        return True
    if sub == "remove" and arg:
        if remove_program(arg):
            console.print(f"[green]✓[/green] removed [cyan]{arg}[/cyan] from your list")
        elif arg in DEFAULTS:
            console.print(f"[yellow]{arg} is a built-in default — it can't be removed[/yellow]")
        else:
            console.print(f"[dim]{arg} isn't in your added list[/dim]")
        return True

    progs = sorted(interactive_programs())
    console.print(f"[bold]interactive programs[/bold] "
                  f"[dim]({len(progs)} — run in a real terminal; or prefix any command with [/dim]"
                  "[cyan]!![/cyan][dim])[/dim]")
    console.print("  " + ", ".join(f"[cyan]{p}[/cyan]" for p in progs))
    console.print("[dim]/interactive add <prog>  ·  /interactive remove <prog>[/dim]")
    return True


def register() -> None:
    register_repl_command(REPLCommand(
        name="interactive",
        handler=_cmd_interactive,
        description="List/add/remove full-screen programs that run in a real terminal (or use !!)",
        usage="/interactive [add <prog> | remove <prog> | list]",
        category="admin",
    ))
    register_repl_command(REPLCommand(
        name="commands",
        handler=_cmd_commands_list,
        description="Manage project commands (/commands, /commands reload, /commands errors)",
        usage="/commands [reload | errors]",
        category="admin",
    ))
    register_repl_command(REPLCommand(
        name="tools",
        handler=_cmd_tools,
        description="Inspect and reload project agent tools",
        usage="/tools [show <name> | reload]",
        category="admin",
    ))
    register_repl_command(REPLCommand(
        name="tools-reload",
        handler=_cmd_tools_reload,
        description="Reload project-defined agent tools (alias)",
        category="admin",
    ))
    register_repl_command(REPLCommand(
        name="reload",
        handler=lambda line, ctx: _cmd_commands_reload("/commands reload", ctx),
        description="Reload all project commands (alias for /commands reload)",
        category="admin",
    ))
