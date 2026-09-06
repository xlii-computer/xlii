"""Side-panel slash commands (/panel · /file-tab · /file-view) — Vector P/A.

``/panel <target>`` opens any content panel — a content-type doorway
(skills/docs/bookmarks/images/wiki/tasks/jobs) in Pane 2, or the file explorer —
now that the input-frame chips only surface a kind while it's riding the turn. It
is the command home for the panels the chips used to be the only door to.
``/file-tab`` remains as the file-explorer-specific opener (**selecting an item in
the tree attaches it** for the next turn; **clicking a file tab swaps the panel**
to that file's view).

The commands are front-end-agnostic: they route through the published panel host
seam (``xlii.tui.panels``), which ``launch()`` wires to the running Textual app.
Off the TUI (the inline REPL) there is no host, so they nudge the user to
``--tui`` instead of failing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command

_USAGE = "/file-tab [on|off] [explorer|locker|vfs|transcript] [--image] [--set left|right]"
_FILE_VIEW_USAGE = "/file-view <path>"
_PANEL_USAGE = "/panel [home|skills|docs|bookmarks|images|wiki|tasks|jobs|gigwork|plan|files] [off] [--set left|right]"

# A /panel target word (+ aliases) → how to open it: a ("door", scheme) opens ``scheme://`` in Pane 2
# through the open_doorway seam; ("files", None) docks the vfs file explorer. Mirrors
# XliiApp._DOORWAY_SCHEMES (keyed by hotkey letter) plus files→vfs — duplicated here on purpose so the
# command lives off tui_textual.py (where other work is active).
_PANEL_TARGETS: "dict[str, tuple[str, Optional[str]]]" = {
    "skills": ("door", "skills"), "skill": ("door", "skills"),
    "docs": ("door", "docs"), "doc": ("door", "docs"),
    "bookmarks": ("door", "mark"), "bookmark": ("door", "mark"),
    "marks": ("door", "mark"), "mark": ("door", "mark"),
    "images": ("door", "locker"), "image": ("door", "locker"), "locker": ("door", "locker"),
    "wiki": ("door", "wiki"),
    "tasks": ("door", "tasks"), "task": ("door", "tasks"),
    "jobs": ("door", "jobs"), "job": ("door", "jobs"),
    "gigwork": ("door", "gigwork"), "gig": ("door", "gigwork"),
    "jams": ("door", "gigwork"), "jam": ("door", "gigwork"),
    "plan": ("door", "plan"), "plans": ("door", "plan"),
    "git": ("door", "git"),
    "home": ("door", "home"),
    "projects": ("door", "projects"), "project": ("door", "projects"),
    "join": ("door", "projects"),
    "switch": ("door", "projects"),
    "sources": ("door", "sources"), "source": ("door", "sources"),
    "results": ("door", "results"), "result": ("door", "results"),
    "artifacts": ("door", "artifacts"), "assets": ("door", "artifacts"),
    "menu": ("door", "menu"), "commands": ("door", "menu"), "cmds": ("door", "menu"),
    "history": ("door", "history"), "hist": ("door", "history"),
    "remote": ("door", "remote"), "remotes": ("door", "remote"),
    "files": ("files", None), "file": ("files", None),
    "explorer": ("files", None), "vfs": ("files", None),
}


def _persist_panel_side(host: Any, side: str) -> None:
    """Write an explicit ``--set`` / ``--left`` / ``--right`` through the host seam."""
    setter = getattr(host, "set_panel_side", None)
    if callable(setter):
        setter(side, persist=True)


def _panel_targets_help() -> str:
    return ("[dim]open a panel:[/dim] "
            "[cyan]/panel home|projects|skills|docs|bookmarks|images|wiki|tasks|"
            "jobs|gigwork|plan|git|files|sources|results[/cyan] "
            "[dim](· [/dim][cyan]/panel off[/cyan][dim] to close · --set left|right on TUI)[/dim]")


def _parse(tokens: list[str], console: Any) -> tuple[bool, bool, Optional[str], Optional[str], bool]:
    """Return ``(want_on, want_off, view, side, error)`` from the arg tokens.

    ``error`` is set for a malformed option (a bad ``--set``); the handler then
    warns-and-stops rather than half-applying. An *unknown* flag is tolerated (a
    warning, parse continues)."""
    want_on = False
    want_off = False
    view: Optional[str] = None
    side: Optional[str] = None
    error = False
    k = 0
    while k < len(tokens):
        t = tokens[k].lower()
        if t in ("off", "close", "hide"):
            want_off = True
        elif t in ("on", "open", "show"):
            want_on = True
        elif t in ("--image", "--images", "--locker"):
            view = "locker"
            want_on = True
        elif t == "--left":
            side = "left"
            want_on = True
        elif t == "--right":
            side = "right"
            want_on = True
        elif t in ("--set", "--side", "--dock"):
            if k + 1 < len(tokens) and tokens[k + 1].lower() in ("left", "right"):
                side = tokens[k + 1].lower()
                want_on = True
                k += 1
            else:
                console.print("[yellow]/file-tab --set needs 'left' or 'right'[/yellow]")
                error = True
        elif t.startswith("-"):
            console.print(f"[yellow]ignoring unknown option {tokens[k]}[/yellow]")
        else:
            view = t
            want_on = True
        k += 1
    return want_on, want_off, view, side, error


def _resolve_file_view_path(console: Any, state: Any, raw: str) -> Optional[Path]:
    """Resolve a user path, jailed to the project root (like ``/editthis``)."""
    from xlii.project_paths import PathOutsideProject, resolve_project_path

    project = getattr(state, "project", None)
    root = getattr(project, "project_root", None) if project is not None else None
    if root is None:
        console.print("[dim]/file-view needs a project — run it in [cyan]xlii code[/cyan][/dim]")
        return None
    try:
        path = resolve_project_path(raw, Path(str(root)), cwd=getattr(state, "shell_cwd", None))
    except PathOutsideProject:
        console.print(f"[red]refused — outside the project root:[/red] {raw}")
        return None
    if path.is_dir():
        console.print(f"[red]not a file (it's a directory):[/red] {raw}")
        return None
    return path


def _no_host_message(console: Any) -> None:
    console.print(
        "[dim]panel commands need a surface with a panel host — run "
        "[cyan]xlii code --tui[/cyan], [cyan]xlii scratch --tauri[/cyan], "
        "or [cyan]xlii serve --face[/cyan].[/dim]"
    )


def open_door(console: Any, scheme: str, *, hint: str = "") -> None:
    """Open the ``scheme://`` doorway in Pane 2 — the shared opener for panel
    verbs on other commands (``/gigwork panel`` → ``open_door(…, "gigwork")``),
    so this module stays the one command home for the panel-host seam. Off the
    TUI it nudges (with the caller's inline ``hint`` when given) instead of
    failing."""
    from xlii.tui import panels

    if panels.current_panel_host() is None:
        extra = f" {hint}" if hint else ""
        console.print(
            "[dim]this panel needs the full-screen TUI — run "
            f"[cyan]xlii code --tui[/cyan] (or [cyan]/tui[/cyan]) first.{extra}[/dim]"
        )
        return
    if panels.open_doorway(scheme):
        console.print(f"[green]✓[/green] [cyan]{scheme}[/cyan] panel")
    else:
        console.print("[yellow]couldn't open the panel[/yellow]")


def _cmd_file_view(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[red]No active REPLState[/red]")
        return True

    from xlii.tui import panels

    tokens = line.split()[1:]
    if not tokens:
        console.print(f"[yellow]usage:[/yellow] {_FILE_VIEW_USAGE}")
        return True

    path = _resolve_file_view_path(console, state, tokens[0])
    if path is None:
        return True

    if panels.current_panel_host() is None:
        _no_host_message(console)
        return True

    if panels.show_file_view(path):
        console.print(f"[green]✓[/green] viewing [cyan]{path.name}[/cyan]")
    else:
        console.print("[yellow]couldn't open the file view[/yellow]")
    return True


def _cmd_file_tab(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[red]No active REPLState[/red]")
        return True

    from xlii.tui import panels

    tokens = line.split()[1:]  # drop the leading '/file-tab'
    want_on, want_off, view, side, error = _parse(tokens, console)
    if error:
        return True  # malformed option already warned; do nothing rather than half-apply

    known = panels.panel_views()
    if view is not None and view not in known:
        avail = ", ".join(known) or "(none)"
        console.print(f"[yellow]unknown view '{view}'[/yellow] — available: [cyan]{avail}[/cyan]")
        return True

    host = panels.current_panel_host()
    if host is None:
        _no_host_message(console)
        return True

    if side is not None:
        _persist_panel_side(host, side)

    open_now = host.is_open()
    cur_side = host.current_side() or "right"

    if want_off:
        if open_now:
            panels.hide_panel()
            console.print("[green]✓[/green] file panel closed")
        else:
            console.print("[dim]file panel already closed[/dim]")
        return True

    requested_open = want_on or view is not None or side is not None
    if not requested_open:
        # bare /file-tab — open the one file explorer (the kernel Dock), same as `/file-tab vfs`.
        side_for = side or cur_side or "right"
        if panels.show_panel(side_for, "vfs", state=state):
            console.print(f"[green]✓[/green] files [dim]{side_for}[/dim]")
        else:
            console.print("[yellow]couldn't open the file panel[/yellow]")
        return True

    target_side = side or cur_side or "right"
    if view == "locker":
        ok = panels.show_gallery(side=target_side)
        label = "gallery"
    elif view in (None, "explorer", "vfs"):
        # One file explorer: the kernel Dock. `/file-tab`, `/file-tab explorer`, and
        # `/file-tab vfs` all dock the same Dock surface — the legacy tree (show_tree) is retired
        # as the default so there's a single file-tab the chips can drive.
        ok = panels.show_panel(target_side, "vfs", state=state)
        label = "files"
    else:
        # any other registered view (e.g. the transcript surface) docks generically.
        ok = panels.show_panel(target_side, view, state=state)
        label = view
    if ok:
        console.print(
            f"[green]✓[/green] [cyan]{label}[/cyan] panel docked "
            f"[dim]{target_side}[/dim] — select a file to attach it for the next turn"
        )
    else:
        console.print("[yellow]couldn't open the file panel[/yellow]")
    return True


def _cmd_panel(line: str, ctx: dict[str, Any]) -> bool:
    """/panel <target> — open a content panel: a content-type doorway (skills/docs/bookmarks/images/
    wiki/tasks/jobs) in Pane 2, or the file explorer. Bare ``/panel`` opens the file explorer (the
    old ``/panel`` alias's behaviour); ``/panel off`` closes the panel; ``/panel ?`` lists targets."""
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[red]No active REPLState[/red]")
        return True

    from xlii.tui import panels

    tokens = line.split()[1:]  # drop the leading '/panel'
    target: Optional[str] = None
    want_off = False
    want_list = False
    side: Optional[str] = None
    k = 0
    while k < len(tokens):
        t = tokens[k].lower()
        if t in ("off", "close", "hide"):
            want_off = True
        elif t in ("?", "help", "list", "--help", "-h"):
            want_list = True
        elif t == "--left":
            side = "left"
        elif t == "--right":
            side = "right"
        elif t in ("--set", "--side", "--dock"):
            if k + 1 < len(tokens) and tokens[k + 1].lower() in ("left", "right"):
                side = tokens[k + 1].lower()
                k += 1
            else:
                console.print("[yellow]/panel --set needs 'left' or 'right'[/yellow]")
                return True
        elif t.startswith("-"):
            console.print(f"[yellow]ignoring unknown option {tokens[k]}[/yellow]")
        elif target is None:
            target = t
        k += 1

    if want_list:
        console.print(_panel_targets_help())
        return True

    host = panels.current_panel_host()
    if host is None:
        _no_host_message(console)
        return True

    if side is not None:
        _persist_panel_side(host, side)

    if want_off:
        if panels.hide_panel():
            console.print("[green]✓[/green] panel closed")
        else:
            console.print("[dim]panel already closed[/dim]")
        return True

    # bare /panel opens the file explorer — the old /panel alias & bare /file-tab default.
    kind, scheme = _PANEL_TARGETS.get(target or "files", ("", None))
    if not kind:
        console.print(f"[yellow]unknown panel '{target}'[/yellow]")
        console.print(_panel_targets_help())
        return True

    if kind == "files":
        target_side = side or host.current_side() or "right"
        if panels.show_panel(target_side, "vfs", state=state):
            console.print(
                f"[green]✓[/green] files [dim]{target_side}[/dim] — "
                "select a file to attach it for the next turn"
            )
        else:
            console.print("[yellow]couldn't open the file panel[/yellow]")
        return True

    # a content-type doorway — open scheme:// in Pane 2 (mounts the Dock if none is docked). The
    # doorway's side follows the app's current dock side, so --set is a no-op here (files only).
    if panels.open_doorway(scheme or ""):
        console.print(f"[green]✓[/green] [cyan]{target}[/cyan] panel")
    else:
        console.print("[yellow]couldn't open the panel[/yellow]")
    return True


def _cmd_home(line: str, ctx: dict[str, Any]) -> bool:
    """``/home`` — alias for ``/panel home`` (the home hub)."""
    extra = line.split(maxsplit=1)
    rest = extra[1] if len(extra) > 1 else ""
    return _cmd_panel(f"/panel home {rest}".rstrip(), ctx)


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="panel",
            handler=_cmd_panel,
            description="Open a side panel — home/projects/skills/docs/… (TUI or face)",
            usage=_PANEL_USAGE,
            category="knowledge",
            repls=["code", "chat"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="home",
            handler=_cmd_home,
            description="Open the home hub panel (alias of /panel home)",
            usage="/home",
            category="knowledge",
            repls=["code", "chat"],
        )
    )
    register_repl_command(
        REPLCommand(
            name="file-tab",
            aliases=["filetab"],
            handler=_cmd_file_tab,
            description="Dock the file explorer; select an item to attach it (TUI) — prefer /panel",
            usage=_USAGE,
            category="knowledge",
        )
    )
    register_repl_command(
        REPLCommand(
            name="file-view",
            aliases=["fileview"],
            handler=_cmd_file_view,
            description="Open the file-tab panel to a file's contents (TUI)",
            usage=_FILE_VIEW_USAGE,
            category="knowledge",
        )
    )
