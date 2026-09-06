"""``/bind`` — pin a saved task to a menu row and/or an F-key.

The task is the recipe. The bind is a symlink. Chrome reads
``~/.config/xlii/binds.toml`` (and optional ``.xlii/binds.toml``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from xlii import binds as B
from xlii import tasks as T
from xlii.commands import REPLCommand, register_repl_command

_USAGE = (
    "[dim]usage:[/dim] [cyan]/bind <task> [menu=project|tools|xlii] [fkey=f11] [label=…][/cyan] · "
    "[cyan]/bind list[/cyan] · [cyan]/bind rm [task|label|f11][/cyan]"
)


def _xli_dir(ctx: dict[str, Any]) -> Optional[Path]:
    state = ctx.get("state")
    project = getattr(state, "project", None) or ctx.get("project")
    d = getattr(project, "xli_dir", None) if project is not None else None
    return Path(d) if d else None


def _parse_kv(tokens: list[str]) -> tuple[str, dict[str, str]]:
    """``task [menu=…] [fkey=…] [label=…]`` — leftover tokens join as the task if needed."""
    fields: dict[str, str] = {}
    rest: list[str] = []
    for tok in tokens:
        if "=" in tok and not tok.startswith("="):
            key, _, val = tok.partition("=")
            k = key.strip().lower()
            if k in {"menu", "fkey", "label", "where"}:
                fields[k] = val.strip()
                continue
        rest.append(tok)
    task = rest[0] if rest else ""
    return task, fields


def _do_list(console: Any, xli_dir: Optional[Path]) -> None:
    rows = B.load_binds(xli_dir)
    if not rows:
        console.print("[dim](no binds — /bind <task> menu=project  or  fkey=f11)[/dim]")
        return
    for b in rows:
        bits = []
        if b.menu:
            bits.append(f"menu={b.menu}")
        if b.fkey:
            bits.append(f"fkey={b.fkey}")
        if b.label:
            bits.append(f"label={b.label}")
        where = " · ".join(bits) if bits else "?"
        tag = " [dim]project[/dim]" if b.origin == "project" else ""
        console.print(f"  · [cyan]{b.task}[/cyan] [dim]{where}[/dim]{tag}")


def _do_add(console: Any, xli_dir: Optional[Path], task: str, fields: dict[str, str]) -> None:
    task = (task or "").strip()
    if not task:
        console.print(_USAGE)
        return
    if xli_dir is not None:
        try:
            T.pipeline_origin(xli_dir, task)
        except T.TaskNotFound:
            console.print(f"[red]/bind: no saved task named {task!r}[/red] "
                          "[dim](see /tasks list)[/dim]")
            return
    menu = fields.get("menu") or fields.get("where") or ""
    fkey = fields.get("fkey", "")
    label = fields.get("label", "")
    try:
        entries = B._read_file(B.user_binds_path(), origin="user")
        entries = B.upsert_bind(
            entries, task=task, menu=menu, fkey=fkey, label=label, origin="user",
        )
        B.save_user_binds(entries)
    except B.BindError as e:
        console.print(f"[red]/bind: {e}[/red]")
        return
    line = B.bind_run_line(task, xli_dir)
    where = []
    got = next((b for b in entries if b.task == task), None)
    if got and got.menu:
        where.append(f"menu={got.menu}")
    if got and got.fkey:
        where.append(f"fkey={got.fkey}")
    console.print(
        f"[green]bound[/green] [cyan]{task}[/cyan] "
        f"[dim]{' · '.join(where) or 'project'} → {line.rstrip()}[/dim]"
    )


def _do_rm(console: Any, token: str, xli_dir: Optional[Path] = None) -> None:
    try:
        entries = B._read_file(B.user_binds_path(), origin="user")
        kept, n = B.remove_binds(entries, token)
    except B.BindError as e:
        console.print(f"[red]/bind: {e}[/red]")
        return
    if n == 0:
        live = B.load_binds(xli_dir)
        if not token.strip() and len(live) > 1:
            console.print("[yellow]which bind?[/yellow] [dim]/bind rm <task|label|f11>[/dim]")
            _do_list(console, xli_dir)
            return
        if token.strip() and any(
            (b.task.lower() == token.strip().lower()
             or (b.label or "").lower() == token.strip().lower())
            and b.origin == "project"
            for b in live
        ):
            console.print(
                f"[yellow]{token!r} is a project bind[/yellow] "
                "[dim](.xlii/binds.toml — edit that file)[/dim]"
            )
            return
        console.print(f"[yellow]no bind matching {token!r}[/yellow]")
        if live:
            _do_list(console, xli_dir)
        return
    B.save_user_binds(kept)
    names = ", ".join(b.task for b in entries if b not in kept) or token
    console.print(f"[green]removed[/green] {names}")


def _cmd_bind(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx.get("console")
    if console is None:
        return True
    xli_dir = _xli_dir(ctx)
    args = line.split()[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        _do_list(console, xli_dir)
        console.print(_USAGE)
        console.print("[dim]form: /panel bindmake · Options → Bind chrome[/dim]")
        return True
    if args[0] == "list":
        _do_list(console, xli_dir)
        return True
    if args[0] in ("rm", "remove", "delete", "off"):
        token = " ".join(args[1:]).strip()
        _do_rm(console, token, xli_dir)
        return True
    task, fields = _parse_kv(args)
    _do_add(console, xli_dir, task, fields)
    return True


def register() -> None:
    register_repl_command(REPLCommand(
        name="bind",
        handler=_cmd_bind,
        description="Pin a saved task to a menu row and/or an F-key (symlink to /tasks run).",
        usage="/bind <task> [menu=project|tools|xlii] [fkey=f11] [label=…] | list | rm <task|f11>",
        category="general",
        repls=["code", "chat"],
    ))
