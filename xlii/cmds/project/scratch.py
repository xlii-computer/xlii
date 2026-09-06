"""Scratch session subcommands."""

from __future__ import annotations

import argparse
from pathlib import Path

from xlii.config import ProjectConfig
from xlii.sync import init_project
from xlii.ui import console


def cmd_scratch(args: argparse.Namespace) -> int:
    """Spin up a scratch session — ephemeral, unbound, never-sync (Vector S).

    Three forms:
      - ``xlii scratch``        bare → ``cd ~``: an ephemeral scratch session FROM
                                HOME. Roam the whole machine; nothing is written
                                under ~, nothing syncs (the daily-driver half of
                                the two-window day).
      - ``xlii scratch here``   a local, never-sync scratch anchored in the CWD: it
                                writes a local ``.xlii/`` so the session persists,
                                but it's local-only + no-sync and never
                                auto-promotes — only ``xlii code init`` graduates a
                                dir to syncable.
      - ``xlii scratch <name>`` the persisted home-store scratch under
                                ``~/.xlii/scratch/<name>/``.

    Use for: one-off file-management tasks ("rename these", "find duplicates"),
    quick experiments, anything you don't want to upload as a project. For
    snapshotting an existing big directory (NAS, media collection), run
    ``xlii init --local --snapshot`` *in that directory* instead — scratch never
    uploads.
    """
    name = (args.name or "").strip()
    if name.lower() == "here":
        return _scratch_here(args)
    if not name:
        return _scratch_home(args)
    return _scratch_named(args, name)


def _launch_scratch_session(
    target: str,
    *,
    yolo: bool,
    preview: bool,
    launch: bool,
    tui: bool = False,
    tauri: bool = False,
    face_resume: bool = False,
    face_replace: bool = False,
) -> int:
    """Launch a scratch session through the kernel seam (B4): typed args into
    ``session_boot.build_code_session`` (``scratch=True`` marks the new
    REPLState via the one-shot handoff, consumed at construction) and the code
    façade's run tail — no fabricated ``argparse.Namespace`` (the old S2
    convention), no manual ``_PENDING_SCRATCH`` poking.

    ``tauri=True`` (three-faces Q5): open the desktop face over the scratch
    root instead of the inline/TUI REPL — 7am home in a window.

    Bare home + ``--tauri``: *config* lives at ``~/.xlii/scratch/home`` (the
    face needs a real ``.xlii/``); the live shell still roams ``~`` (see
    ``scratch_home_roam_cwd``).
    """
    if tauri:
        if preview:
            # Ephemeral home has no .xlii — face server needs a project dir.
            # Desk = config only; shell_cwd is set to ~ at session boot.
            target = str(_ensure_home_desk())
        from xlii.cmds.sessions.code import launch_tauri

        instance = None
        if face_replace:
            instance = "replace"
        elif face_resume:
            instance = "resume"
        return launch_tauri(Path(target), instance=instance)

    from xlii.cmds.sessions.code import (
        _ask_episode,
        _prompt_launch_gate,
        _stdin_interactive,
        run_code_session,
    )
    from xlii.session_boot import build_code_session

    interactive = _stdin_interactive()
    boot = build_code_session(
        Path(target),
        yolo=yolo,
        no_sync=True,
        preview=preview,
        launch=launch,
        scratch=True,
        interactive=interactive,
        ask_launch=_prompt_launch_gate if interactive else None,
        ask_episode=_ask_episode,
    )
    if boot.status != "ok":
        return 0 if boot.status == "cancelled" else 1
    return run_code_session(boot.session, tui=tui)


def _ensure_home_desk() -> Path:
    """Durable 7am home desk config root: ``~/.xlii/scratch/home`` (local-only).

    Bare ``xlii scratch`` is ephemeral (no disk); the face/Tauri path needs a
    real ``.xlii/`` for ``serve --face``. That desk is **config/state only** —
    session boot sets ``shell_cwd`` to ``~`` so roam starts in the real home.
    """
    root = (Path.home() / ".xlii" / "scratch" / "home").resolve()
    root.mkdir(parents=True, exist_ok=True)
    existing = ProjectConfig.load(root)
    if existing is None:
        init_project(
            None,
            root,
            name="scratch/home",
            local_only=True,
            snapshot=False,
        )
        console.print(
            f"[green]✓[/green] home desk [bold]scratch/home[/bold] at [dim]{root}[/dim]"
        )
    # three-faces A: home desk pack puts **join** first (projects list), not chat-only doors.
    try:
        from xlii.workbench import load_active_type, save_active_type

        xli = root / ".xlii"
        if xli.is_dir() and load_active_type(xli) in ("chat", "general"):
            save_active_type(xli, "home")
    except Exception:  # noqa: BLE001 — pack prefer is best-effort
        pass
    return root


def _scratch_home(args: argparse.Namespace) -> int:
    """Bare ``xlii scratch`` → ``cd ~``: an ephemeral scratch session from home."""
    home = Path.home()
    if getattr(args, "no_chat", False):
        # The bare/home form creates nothing on disk (it's ephemeral), so there's
        # nothing to do without entering the session.
        console.print(
            "[yellow]nothing to do:[/yellow] bare [cyan]xlii scratch[/cyan] is an "
            "ephemeral session — drop [cyan]--no-chat[/cyan], or name a scratch "
            "([cyan]xlii scratch <name>[/cyan]) to create one without entering."
        )
        return 0
    console.print(
        "[green]✓[/green] [bold #d19a66]scratch[/bold #d19a66] from home "
        f"[dim]{home}[/dim] — ephemeral & never-sync; nothing is written under ~."
    )
    # preview=True → ephemeral on ~ (no .xlii written, temp state dir, no sync);
    # build_code_session seeds shell_cwd to the project root (= home), i.e. the `cd ~`.
    return _launch_scratch_session(
        str(home), yolo=args.yolo, preview=True, launch=False,
        tui=getattr(args, "tui", False),
        tauri=getattr(args, "tauri", False),
        face_resume=getattr(args, "face_resume", False),
        face_replace=getattr(args, "face_replace", False),
    )


def _scratch_here(args: argparse.Namespace) -> int:
    """``xlii scratch here`` → a local, never-sync scratch anchored in the CWD."""
    root = Path.cwd().resolve()
    existing = ProjectConfig.load(root)
    if existing is not None and not existing.local_only:
        console.print(
            f"[yellow]{root}[/yellow] is already a synced xlii project "
            f"([bold]{existing.name}[/bold]) — refusing to shadow it with scratch.\n"
            "[dim]open it normally with [/dim][cyan]xlii code[/cyan][dim], or run "
            "[/dim][cyan]/scratch[/cyan][dim] in-session for a no-sync session.[/dim]"
        )
        return 1
    if existing is not None:
        # Already a local-only project here — anchor the scratch session to it
        # (no re-init); the session just runs never-sync.
        console.print(
            f"[green]✓[/green] [bold #d19a66]scratch[/bold #d19a66] (never-sync) in "
            f"existing local project [bold]{existing.name}[/bold] [dim]{root}[/dim]"
        )
    else:
        init_project(None, root, name=f"scratch/{root.name}", local_only=True)
        console.print(
            f"[green]✓[/green] [bold #d19a66]scratch[/bold #d19a66] here → local "
            f"[cyan].xlii/[/cyan] in [dim]{root}[/dim] "
            "[dim](never-sync, not a syncing project — [/dim]"
            "[cyan]xlii code init[/cyan][dim] to graduate it)[/dim]"
        )

    if getattr(args, "no_chat", False):
        return 0
    return _launch_scratch_session(
        str(root), yolo=args.yolo, preview=False, launch=True,
        tui=getattr(args, "tui", False),
        tauri=getattr(args, "tauri", False),
        face_resume=getattr(args, "face_resume", False),
        face_replace=getattr(args, "face_replace", False),
    )


def _scratch_named(args: argparse.Namespace, name: str) -> int:
    """``xlii scratch <name>`` → the persisted home-store scratch under
    ``~/.xlii/scratch/<name>/`` (local-only; the session runs never-sync)."""
    scratch_root = (Path.home() / ".xlii" / "scratch" / name).resolve()
    scratch_root.mkdir(parents=True, exist_ok=True)

    existing = ProjectConfig.load(scratch_root)
    if existing and not args.force:
        console.print(f"[yellow]scratch project already exists at[/yellow] {scratch_root}")
    else:
        if existing and args.force:
            console.print(
                f"[yellow]--force set:[/yellow] re-initializing existing scratch project at {scratch_root}"
            )
        project = init_project(
            None,
            scratch_root,
            name=f"scratch/{name}",
            local_only=True,
            snapshot=False,  # empty dir; nothing to snapshot
        )
        console.print(
            f"[green]✓[/green] scratch project [bold]{project.name}[/bold] at {scratch_root}"
        )

    if args.no_chat:
        return 0
    # launch=True skips the `xlii code` launch gate — scratch just created and
    # confirmed this project, so re-asking "launch here?" would be redundant.
    return _launch_scratch_session(
        str(scratch_root), yolo=args.yolo, preview=False, launch=True,
        tui=getattr(args, "tui", False),
        tauri=getattr(args, "tauri", False),
        face_resume=getattr(args, "face_resume", False),
        face_replace=getattr(args, "face_replace", False),
    )
