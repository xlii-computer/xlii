"""`xlii code` command and launch helpers — a façade over xlii.session_boot (B4).

The session assembly (gate, project init, sync, agent/state, journal, episode,
loop restore) lives kernel-side in ``xlii.session_boot``; this file keeps the
argparse shape, the interactive prompts (injected as callbacks), the banners,
and the inline/TUI run tail.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from rich.panel import Panel

from xlii import __version__
from xlii.config import ProjectConfig
from xlii.exit_sequence import end_code_session
from xlii.project_paths import user_home
from xlii.repl import _QuitSession, run_repl_loop
from xlii.repl_cmds.mode import _rail_brief
from xlii.session_boot import build_code_session, launch_tui_or_inline, resolve_launch
from xlii.ui import console, format_turn_line

from .resolve import _resolve_project_target


def _prompt_launch_gate(project: Optional[ProjectConfig], root: Path) -> str:
    """The crude 'launch in this folder?' prompt. Returns one of
    'launch' | 'preview' | 'init' | 'cancel'. Only called interactively."""
    from rich.prompt import Prompt

    if project is None:
        console.print(Panel.fit(
            f"[bold]{root}[/bold]\n[dim]not an xlii project yet[/dim]",
            title="[cyan]launch xlii code here?[/cyan]", border_style="cyan"))
        console.print(
            "  [cyan]p[/cyan] preview     – open the REPL now; no .xlii, no snapshot, no sync\n"
            "  [cyan]i[/cyan] initialize  – create a local .xlii here, then launch\n"
            "  [cyan]n[/cyan] no          – cancel")
        try:
            pick = Prompt.ask("  choice", choices=["p", "i", "n"], default="p")
        except (EOFError, KeyboardInterrupt):
            return "cancel"
        return {"p": "preview", "i": "init", "n": "cancel"}[pick]

    tag = " · local-only" if project.local_only else ""
    sync_note = "" if project.local_only else " (syncs at startup)"
    console.print(Panel.fit(
        f"[bold]{root}[/bold]\n[dim]xlii project: {project.name}{tag}[/dim]",
        title="[cyan]launch xlii code here?[/cyan]", border_style="cyan"))
    console.print(
        f"  [cyan]l[/cyan] launch   – open the REPL{sync_note}\n"
        "  [cyan]p[/cyan] preview  – open without the startup sync\n"
        "  [cyan]n[/cyan] no       – cancel")
    try:
        pick = Prompt.ask("  choice", choices=["l", "p", "n"], default="l")
    except (EOFError, KeyboardInterrupt):
        return "cancel"
    return {"l": "launch", "p": "preview", "n": "cancel"}[pick]


def _resolve_launch_choice(
    args: argparse.Namespace, project: Optional[ProjectConfig], root: Path,
    *, interactive: bool,
) -> str:
    """Façade over session_boot.resolve_launch (kept for tests, which pin the
    ``_prompt_launch_gate`` monkeypatch seam)."""
    return resolve_launch(
        root,
        preview=getattr(args, "preview", False),
        init=getattr(args, "init", False),
        launch=getattr(args, "launch", False),
        project=project,
        interactive=interactive,
        ask=_prompt_launch_gate if interactive else None,
    )


def _stdin_interactive() -> bool:
    """The one isatty probe for session launch (the ban-grep baseline lives in
    this file). Callers (scratch / lifecycle) take the answer from here as a
    parameter instead of probing the terminal themselves (check_contracts)."""
    return sys.stdin.isatty()


def _ask_episode(line: str) -> bool:
    """The keep-session offer's one interactive question ([Y/n]) — the tty seam
    the kernel episode flow calls only when interactive."""
    try:
        answer = console.input(f"[cyan]?[/cyan] {line} [dim]\\[Y/n][/dim] ")
    except (EOFError, KeyboardInterrupt):
        return False
    return answer.strip().lower() not in ("n", "no")


def cmd_code(args: argparse.Namespace) -> int:
    """Entry point for `xlii code` — an argparse façade over
    ``session_boot.build_code_session`` (typed; no Namespace crosses the seam)."""
    target = _resolve_project_target(getattr(args, "target", None))
    if target is None:
        console.print("[red]could not resolve target[/red]")
        return 1

    if getattr(args, "tauri", False):
        instance = "replace" if getattr(args, "face_replace", False) else None
        return _launch_tauri(target, instance=instance)

    interactive = sys.stdin.isatty()
    boot = build_code_session(
        target,
        yolo=args.yolo,
        rail=getattr(args, "rail", False),
        discovery=getattr(args, "discovery", False),
        ops=getattr(args, "ops", False),
        no_sync=getattr(args, "no_sync", False),
        preview=getattr(args, "preview", False),
        init=getattr(args, "init", False),
        launch=getattr(args, "launch", False),
        force=getattr(args, "force", False),
        interactive=interactive,
        resume_episode=getattr(args, "resume_episode", None),
        keep_session=getattr(args, "keep_session", False),
        no_startup=getattr(args, "no_startup", False),
        ask_launch=_prompt_launch_gate if interactive else None,
        ask_episode=_ask_episode,
    )
    if boot.status == "cancelled":
        return 0
    if boot.status != "ok":
        return 1
    return run_code_session(boot.session, tui=getattr(args, "tui", False))


def launch_tauri(
    target: Path,
    *,
    instance: Optional[str] = None,
) -> int:
    """Open the face over *target* (three-faces Q5).

    **Default:** native ``xlii-desktop`` when found. Rebuilt desktops navigate
    to the sidecar's live face URL after handshake, so ``face_assets`` match
    this Python install (no cargo rebuild for every UI tweak).

    **Browser:** if desktop is missing, or ``XLII_FACE_BROWSER=1``, run
    ``serve --face`` and open the system browser (same live assets).

    **One face per environment:** if a face is already live, resume (open its
    URL) or replace (stop then start) — never a second desk by default.
    *instance*: ``resume`` | ``replace`` | None (prompt / env ``XLII_FACE_INSTANCE``).
    """
    root = Path(target).expanduser().resolve()
    if not root.is_dir():
        console.print(f"[red]not a directory:[/red] {root}")
        return 1

    from xlii.face_instance import prepare_launch

    prefer = (instance or "").strip().lower() or None
    if prefer in ("", "auto", "ask", "attach"):  # attach = legacy synonym
        prefer = "resume" if prefer == "attach" else None
    action, live = prepare_launch(prefer=prefer, interactive=_stdin_interactive())
    if action == "cancel":
        console.print("[dim]cancelled[/dim]")
        return 0
    if action == "resume" and live is not None:
        return _resume_live_face(live)

    # Replace path sets XLII so the sidecar may reclaim the lock if a race remains.
    if prefer == "replace" or (
        os.environ.get("XLII_FACE_INSTANCE", "").strip().lower() in ("replace", "r", "new")
    ):
        os.environ["XLII_FACE_INSTANCE"] = "replace"

    force_browser = os.environ.get("XLII_FACE_BROWSER", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if force_browser:
        return _launch_live_face_browser(root)
    if _resolve_desktop_exe():
        return _launch_desktop(root)
    console.print(
        "[dim]xlii-desktop not on PATH — using live browser face "
        "(build desktop/ for the native window)[/dim]"
    )
    return _launch_live_face_browser(root)


def _resume_live_face(live) -> int:
    """Continue an already-running face (one desk — no second process)."""
    import webbrowser

    url = live.url
    console.print(
        f"[green]✓[/green] resuming face "
        f"[dim](pid {live.pid} · port {live.port})[/dim]"
    )
    console.print(f"[cyan]resume[/cyan] {url}")
    console.print(
        "[dim]same desk — switch into a project to change clothes · "
        "or relaunch with --replace for a clean start[/dim]"
    )
    try:
        webbrowser.open(url)
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]open browser manually:[/yellow] {url} ({e})")
    return 0


def _resolve_desktop_exe() -> Optional[str]:
    """Prefer a freshly built tree binary, then ~/.local/bin, then PATH."""
    import shutil

    here = Path(__file__).resolve()
    # xlii/cmds/sessions/code.py → repo root is parents[3]
    candidates = [
        here.parents[3] / "desktop" / "src-tauri" / "target" / "release" / "xlii-desktop",
        user_home() / ".local" / "bin" / "xlii-desktop",
    ]
    for p in candidates:
        try:
            if p.is_file() and os.access(p, os.X_OK):
                return str(p)
        except OSError:
            continue
    return shutil.which("xlii-desktop")


def _launch_desktop(root: Path) -> int:
    """Exec xlii-desktop (navigates to live face URL after handshake)."""
    exe = _resolve_desktop_exe()
    if not exe:
        console.print(
            "xlii-desktop not found — build from desktop/ "
            "(`cargo build --release` in src-tauri) or set XLII_FACE_BROWSER=1"
        )
        return 1
    console.print(f"[dim]desktop face · {exe}[/dim]")
    os.chdir(root)
    os.execvp(exe, [exe])
    return 0  # unreachable


def _launch_live_face_browser(root: Path) -> int:
    """Serve the face from this package and open the system browser.

    Sidecar protocol matches Tauri (handshake JSON on stdout) but the page is
    loaded from ``http://127.0.0.1:<port>/`` so ``face_assets`` is always live.

    Keep the child's stdin open — handshake mode exits when stdin EOF
    (``_watch_stdin`` → ``os._exit``); a closed pipe would kill the server
    before the browser connects.
    """
    import json
    import subprocess
    import sys
    import time
    import webbrowser

    console.print(
        f"[green]✓[/green] live face at [dim]{root}[/dim] — "
        "browser opens when ready (assets from this xlii install)"
    )
    console.print(
        "[dim]Ctrl+C stops the server · install xlii-desktop for the native window[/dim]"
    )

    env = os.environ.copy()
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "xlii", "serve", "--face",
             "--handshake", "--port", "0"],
            cwd=str(root),
            stdin=subprocess.PIPE,   # held open until we exit (see above)
            stdout=subprocess.PIPE,
            stderr=None,  # inherit — boot refusals stay visible
            text=True,
            env=env,
        )
    except OSError as e:
        console.print(f"[red]could not start face server:[/red] {e}")
        return 1

    assert proc.stdout is not None
    # Handshake is one JSON line on stdout.
    line = ""
    deadline = time.monotonic() + 60.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            console.print("[red]face server exited before handshake[/red]")
            return 1
        line = proc.stdout.readline()
        if line:
            break
    if not line.strip():
        console.print("[red]no handshake from face server[/red]")
        proc.kill()
        return 1
    try:
        hs = json.loads(line)
        port = int(hs["port"])
        token = str(hs["token"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        console.print(f"[red]bad handshake:[/red] {line!r} ({e})")
        proc.kill()
        return 1

    url = f"http://127.0.0.1:{port}/?token={token}"
    console.print(f"[cyan]face[/cyan] {url}")
    console.print("[dim]try: /panel home · Workbench menu · Panel Workbench menu[/dim]")
    try:
        webbrowser.open(url)
    except Exception as e:  # noqa: BLE001
        console.print(f"[yellow]open browser manually:[/yellow] {url} ({e})")

    try:
        return int(proc.wait())
    except KeyboardInterrupt:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            # Ctrl-C teardown: the child is already going away, so a failed stdin close changes nothing.
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        console.print("\n[dim]face stopped[/dim]")
        return 0


def _launch_tauri(target: Path, *, instance: Optional[str] = None) -> int:
    """`xlii code --tauri` — desktop face over a project directory."""
    return launch_tauri(target, instance=instance)


def run_code_session(cs, *, tui: bool = False) -> int:
    """The full run tail over an assembled CodeSession: TUI handoff or inline
    REPL loop, ``_QuitSession`` → clean 0, and the ephemeral-preview temp-dir
    cleanup however the session exits. This is also the scratch/lifecycle entry
    (B4): they build through session_boot and land here — typed args, never a
    fabricated ``argparse.Namespace`` (the S2 convention, dead)."""
    try:
        return _cmd_code_run(cs, tui=tui)
    except _QuitSession:
        # A2: /exit·/quit fired from anywhere (incl. a /tui nested in the inline
        # REPL). The exit sequence (save + bye) already ran where it was raised;
        # here we just end the command cleanly so the process exits.
        return 0
    finally:
        if cs.preview_state_dir is not None:
            import shutil
            shutil.rmtree(cs.preview_state_dir, ignore_errors=True)


def _cmd_code_run(cs, *, tui: bool = False) -> int:
    """The launch tail (TUI handoff or inline REPL loop) over the assembled
    CodeSession — split out so run_code_session can wrap it in the preview cleanup."""
    state, agent, project, rail = cs.state, cs.agent, cs.project, cs.rail
    total_turns, seeded, preview = cs.total_turns, cs.seeded, cs.preview

    if tui:
        from xlii.tui_textual import run_tui_over_session

        outcome = launch_tui_or_inline(
            state, agent, project_name=project.name, run_tui=run_tui_over_session)
        if outcome == "quit":
            # Episode continuity (P1): a TUI /exit is a clean exit too.
            from xlii.episode import mark_clean
            mark_clean(state)
            return 0

    history_path = project.xli_dir / "repl_history"
    # Inline-REPL shell ghost text (fish-style) — the inline twin of the --tui's
    # render_line ghost, reading the same xlii.shell_suggest table. Fires only in
    # shell-primary context; right-arrow accepts (prompt_toolkit default).
    from xlii.tui import input_chrome
    session = PromptSession(history=FileHistory(str(history_path)),
                            auto_suggest=input_chrome.make_shell_autosuggest(state))

    console.print(Panel.fit(f"xlii v{__version__} · {project.name}", border_style="cyan"))
    if preview:
        if project.state_dir_override is not None:  # ephemeral (non-project) preview
            console.print(
                "[dim]preview — ephemeral: no .xlii written, nothing saved or synced. "
                "Run [cyan]/sync[/cyan] or [cyan]xlii init[/cyan] to make it a real project.[/dim]"
            )
        else:  # existing project, opened with syncing off
            console.print(
                "[dim]preview — syncing disabled this session; your turns still persist locally.[/dim]"
            )
    if total_turns:
        # Local memory only — code turns are not synced/searchable (see
        # persist_code_turn), so this banner does NOT promise search_project
        # recall the way chat's does.
        console.print(
            f"[dim]memory: {total_turns} turn(s) on disk · {seeded} loaded inline[/dim]"
        )
    if rail is not None:
        console.print(
            "[magenta]rail ON[/magenta] — stage-gated coding. Type [cyan]? your task[/cyan] to begin "
            "stage 0; /rail next to advance, /rail status for the brief, /rail off to exit."
        )
        _rail_brief(console, rail)
    elif agent.discovery_mode:
        console.print(
            "[cyan]discovery mode ON[/cyan] — read-only discussion & research. The agent reads, "
            "greps, and explains but [bold]won't change any code[/bold]. "
            "/discovery off to unlock writes; /plan when you're ready to act."
        )
    elif agent.ops_mode:
        console.print(
            "[green]ops mode ON[/green] — OS diagnostics & workflow. Ask about disk, ports, "
            "services, or load; the agent runs platform-correct probes (read-only first). "
            "/ops off to return to coding."
        )

    def _code_prefix():
        # Delegate to the LIVE profile so a /code<->/chat switch is reflected
        # next prompt (the body moved verbatim into Profile.prompt_prefix).
        return state.profile.prompt_prefix(state)

    def _rail_nudge(st):
        # The rail is user-gated: after each stage the user must advance. Nudge
        # them so they aren't left wondering why nothing progresses.
        r = st.agent.rail
        if r is not None:
            if r.is_final_stage:
                console.print("[dim]final stage — /rail off when the review passes, "
                              "or /rail start for a new task[/dim]")
            else:
                console.print(f"[dim]stage {r.current_stage.value} done — /rail next to advance, "
                              "/rail back to redo, /rail status for the brief[/dim]")

    def _code_render(result, prompt):
        # Render-ONLY turn delivery for the kernel spine (drive_turn owns
        # persistence/journal/sync/receipt — and since Phase 5b serves main,
        # loop-continuation, and policy turns alike).
        if result.reply:
            console.print(result.reply)
        console.print(format_turn_line(result.stats))
        _rail_nudge(state)

    def _code_on_exit():
        # The exit tail lives in xlii.exit_sequence (B4): JRN-1 fast exit (defer
        # the summary to next open) + episode clean-mark.
        end_code_session(state, console=console)

    run_repl_loop(state, session=session, get_prompt_prefix=_code_prefix,
                  run_turn=agent.run_turn,
                  render_turn=_code_render, on_exit=_code_on_exit)
    return 0
