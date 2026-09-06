"""``/alias`` — promote a saved task into a first-class slash command.

Task-args **P2** (``proposals/task-args-and-alias.md``): a task is a function, so
naming it makes a callable verb. Minimal by design — **one alias per task,
sharing the task's name**, as a **live reference**: ``/<task>`` runs the *current*
task, forwarding its argv to the task's params (so ``/grep-explain needle`` binds
``needle`` to the first param). Aliases persist per-project in
``.xlii/aliases.toml`` and re-register at startup via the project-command
lifecycle (:func:`xlii.commands.load_project_commands`). A reserved-name guard
refuses to shadow a builtin.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any, Callable, Optional

from xlii import tasks as T
from xlii.atomicio import write_text_atomic
from xlii.commands import (
    REPLCommand,
    find_repl_command,
    register_repl_command,
    unregister_repl_command,
)

ALIASES_FILE = "aliases.toml"

_USAGE = (
    "[dim]usage:[/dim] [cyan]/alias <task>[/cyan] (promote) · "
    "[cyan]/alias list[/cyan] · [cyan]/alias rm <name>[/cyan]"
)


# --------------------------------------------------------------------------- #
#  storage (.xlii/aliases.toml)
# --------------------------------------------------------------------------- #

def _aliases_path(xli_dir: Path) -> Path:
    return Path(xli_dir) / ALIASES_FILE


def _xli_dir(ctx: dict[str, Any]) -> Optional[Path]:
    state = ctx.get("state")
    project = getattr(state, "project", None) or ctx.get("project")
    d = getattr(project, "xli_dir", None) if project is not None else None
    return Path(d) if d else None


def _read_aliases(xli_dir: Path) -> list[dict]:
    path = _aliases_path(xli_dir)
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return []
    out: list[dict] = []
    raw = data.get("alias")
    if isinstance(raw, list):
        for e in raw:
            if isinstance(e, dict):
                name = str(e.get("name", "") or "").strip()
                task = str(e.get("task", "") or "").strip()
                if name and task:
                    out.append({"name": name, "task": task})
    return out


def _write_aliases(xli_dir: Path, entries: list[dict]) -> None:
    lines: list[str] = []
    for e in entries:
        lines.append("[[alias]]")
        lines.append(f'name = "{e["name"]}"')
        lines.append(f'task = "{e["task"]}"')
        lines.append("")
    write_text_atomic(_aliases_path(xli_dir), "\n".join(lines))


# --------------------------------------------------------------------------- #
#  registration
# --------------------------------------------------------------------------- #

def _shadows_builtin(name: str) -> bool:
    """True if a builtin/plugin command already owns ``name`` (never shadow it)."""
    for repl in ("code", "chat"):
        cmd = find_repl_command("/" + name, repl)
        if cmd is not None and cmd.source in ("builtin", "plugin"):
            return True
    return False


def _make_handler(task: str) -> Callable[[str, dict[str, Any]], bool]:
    def _handler(line: str, ctx: dict[str, Any]) -> bool:
        from xlii.repl_cmds.tasks import _do_run

        parts = line.split(maxsplit=1)
        tail = parts[1] if len(parts) > 1 else ""
        _do_run(f"{task} {tail}".strip(), ctx)
        return True

    return _handler


def _register_alias(name: str, task: str) -> None:
    """(Re)register the alias command as a project-sourced slash command."""
    unregister_repl_command(name)  # idempotent replace
    register_repl_command(REPLCommand(
        name=name,
        handler=_make_handler(task),
        description=f"Alias → /tasks run {task}",
        usage=f"/{name} [args]",
        category="general",
        repls=["code", "chat"],
        source="project",
    ))


def load_task_aliases(xli_dir: Path) -> bool:
    """Register every saved task alias — called by ``load_project_commands`` so
    aliases ride the project-command load/reload/unload lifecycle. Skips aliases
    that would shadow a builtin or whose task no longer exists."""
    loaded = False
    for e in _read_aliases(xli_dir):
        if _shadows_builtin(e["name"]):
            continue
        try:
            T.pipeline_origin(xli_dir, e["task"])
        except T.TaskNotFound:
            continue  # a deleted task disables its alias (file entry kept)
        _register_alias(e["name"], e["task"])
        loaded = True
    return loaded


# --------------------------------------------------------------------------- #
#  the /alias command
# --------------------------------------------------------------------------- #

def _do_create(console: Any, xli_dir: Optional[Path], task: str) -> None:
    if xli_dir is None:
        console.print("[yellow]/alias needs a project[/yellow]")
        return
    try:
        origin = T.pipeline_origin(xli_dir, task)
    except T.TaskNotFound:
        console.print(f"[red]/alias: no saved task named {task!r}[/red] "
                      "[dim](see /tasks list)[/dim]")
        return
    name = task  # one alias, sharing the task's name
    if _shadows_builtin(name):
        console.print(f"[red]/alias: /{name} is already a builtin command[/red] "
                      "[dim](rename the task to alias it)[/dim]")
        return
    entries = [e for e in _read_aliases(xli_dir) if e["name"] != name]
    entries.append({"name": name, "task": task})
    _write_aliases(xli_dir, entries)
    _register_alias(name, task)
    console.print(f"[green]/{name}[/green] [dim]→ /tasks run {task} ({origin}) — "
                  "forwards args[/dim]")


def _do_rm(console: Any, xli_dir: Optional[Path], name: str) -> None:
    if xli_dir is None:
        console.print("[yellow]/alias needs a project[/yellow]")
        return
    entries = _read_aliases(xli_dir)
    kept = [e for e in entries if e["name"] != name]
    if len(kept) == len(entries):
        console.print(f"[yellow]no alias named {name!r}[/yellow]")
        return
    _write_aliases(xli_dir, kept)
    unregister_repl_command(name)
    console.print(f"[green]removed alias /{name}[/green]")


def _do_list(console: Any, xli_dir: Optional[Path]) -> None:
    entries = _read_aliases(xli_dir) if xli_dir is not None else []
    if not entries:
        console.print("[dim](no task aliases — /alias <task> to add one)[/dim]")
        return
    for e in entries:
        console.print(f"[cyan]/{e['name']}[/cyan] [dim]→ /tasks run {e['task']}[/dim]")


def _cmd_alias(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx.get("console")
    if console is None:
        return True
    xli_dir = _xli_dir(ctx)
    args = line.split()[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        console.print(_USAGE)
        return True
    sub = args[0]
    if sub == "list":
        _do_list(console, xli_dir)
        return True
    if sub in ("rm", "remove", "delete"):
        if len(args) < 2:
            console.print("[yellow]usage: /alias rm <name>[/yellow]")
            return True
        _do_rm(console, xli_dir, args[1])
        return True
    # create: `/alias <task>` or `/alias --from-task <task>`
    task = args[1] if sub in ("--from-task", "from-task") and len(args) > 1 else sub
    _do_create(console, xli_dir, task)
    return True


def register() -> None:
    register_repl_command(REPLCommand(
        name="alias",
        handler=_cmd_alias,
        description="Promote a saved task into a slash command (live: forwards args to its params).",
        usage="/alias <task> | list | rm <name>",
        category="general",
        repls=["code", "chat"],
    ))
