"""
Example of per-project REPL commands for xlii.

HOW TO USE
----------
1. Copy this file to your project root as:
      .xlii/commands.py
   (or create .xlii/commands/mytools.py for multiple files)

2. Start a REPL in that directory:
      xlii code
      # or
      xlii chat my-persona

3. Your commands will be loaded automatically and will appear in `/help`
   under a "PROJECT COMMANDS" section.

See the docstring of `load_project_commands()` in xlii/commands.py for more details.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand


def _deploy(line: str, ctx: dict[str, Any]) -> bool:
    """Example: a project-specific deploy command."""
    console = ctx["console"]
    state = ctx.get("state")

    target = "staging"
    parts = line.split()
    if len(parts) > 1:
        target = parts[1]

    console.print(f"[bold green]Deploying[/bold green] {state.project.name} to [cyan]{target}[/cyan]...")
    console.print("[dim](this is just an example — replace with real deploy logic)[/dim]")

    # You have full access to:
    # - state.project
    # - state.agent
    # - ctx["console"]
    # - All normal tools via the agent if you want

    return True


def _lint_and_test(line: str, ctx: dict[str, Any]) -> bool:
    """Run project lint + test suite (common pattern)."""
    console = ctx["console"]

    console.print("[cyan]Running lint...[/cyan]")
    # You can call the real bash tool if you want:
    # result = ctx["agent"].tools["bash"](...)  # advanced

    console.print("[green]✓[/green] lint passed")
    console.print("[cyan]Running tests...[/cyan]")
    console.print("[green]✓[/green] all tests passed (example)")

    return True


def _attach_conventions(line: str, ctx: dict[str, Any]) -> bool:
    """Quickly attach this project's coding conventions doc."""
    console = ctx["console"]
    state = ctx.get("state")

    # You can programmatically attach docs
    try:
        state.attached_docs.append(("project-conventions", "# Project Conventions\n\n- Use ruff\n- 100% type coverage\n"))
        console.print("[green]✓[/green] Attached project conventions for this session")
    except Exception as e:
        console.print(f"[red]Failed to attach conventions: {e}[/red]")

    return True


def get_commands() -> list[REPLCommand]:
    """Return the list of commands this project wants to expose in the REPL."""
    return [
        REPLCommand(
            name="deploy",
            handler=_deploy,
            description="Deploy the project (custom for this codebase)",
            usage="/deploy [staging|prod]",
            category="project",
            source="project",
        ),
        REPLCommand(
            name="check",
            handler=_lint_and_test,
            description="Run lint + test suite for this project",
            category="project",
            source="project",
        ),
        REPLCommand(
            name="conventions",
            handler=_attach_conventions,
            description="Attach this project's coding standards for the current session",
            category="project",
            source="project",
        ),
    ]
