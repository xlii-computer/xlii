"""Upload locker and attachment slash commands (/locker, /upload, /attachments)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def _current_orch_model(state) -> str | None:
    from xlii.agent import resolve_orchestrator_model_for_session

    agent = getattr(state, "agent", None)
    cfg = getattr(state, "cfg", None)
    if agent is None or cfg is None:
        return None
    try:
        model, _ = resolve_orchestrator_model_for_session(
            cfg=cfg, agent=agent, state=state
        )
        return model
    except Exception:
        return getattr(cfg, "orchestrator_model", None)


def _warn_if_blind_to_images(state, console) -> None:
    from xlii.multimodal import model_supports_vision

    has_image = any(
        e.get("kind") == "image" and e.get("enabled")
        for e in getattr(state, "attached_files", [])
    )
    if not has_image:
        return
    model = _current_orch_model(state)
    if not model_supports_vision(model):
        console.print(
            f"[yellow]heads up:[/yellow] [cyan]{model}[/cyan] can't see images — "
            "image turns come back text-only. Switch to a vision model "
            "[dim](grok-4 family): [/dim][cyan]/model --profile vision --session[/cyan] "
            "[dim]or[/dim] [cyan]xlii models profile set vision[/cyan]"
        )


def _cmd_attachments(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[dim]No REPLState available[/dim]")
        return True

    refs = state.attached_refs
    docs = state.attached_docs
    files = getattr(state, "attached_files", [])

    if not refs and not docs and not files:
        console.print("[dim](no attachments in this session)[/dim]")
        console.print(
            "[dim]Use /attach doc <name>, /attach ref <mark>, and /locker add <path> "
            "to attach knowledge, marked turns, and files.[/dim]"
        )
        return True

    console.print("[bold]Current attachments (persisted across restarts):[/bold]")
    if refs:
        console.print("\n[cyan]Memory refs[/cyan] (search_project will include these):")
        for name, cid in refs:
            short = cid[:20] + "…" if len(cid) > 20 else cid
            console.print(f"  • [bold]{name}[/bold]  [dim]{short}[/dim]")
    if docs:
        console.print("\n[cyan]Reference docs[/cyan] (inlined into system prompt):")
        for name, content in docs:
            console.print(f"  • [bold]{name}[/bold]  [dim]{len(content):,} bytes[/dim]")
    if files:
        console.print("\n[cyan]Locker[/cyan] (files folded into the turn; ● shared · ○ held):")
        for e in files:
            mark = "[green]●[/green]" if e.get("enabled") else "[dim]○[/dim]"
            once = " [dim](once)[/dim]" if e.get("once") else ""
            console.print(
                f"  {mark} [bold]{e.get('name')}[/bold]  "
                f"[dim]{e.get('kind', '?')}[/dim]{once}"
            )
    from xlii.context_budget import format_loadout_budget_line
    console.print(f"\n[dim]{format_loadout_budget_line(state)}[/dim]")
    console.print(
        "\n[dim]Use /unref, /undoc, and /locker to manage these. "
        "Changes are saved automatically.[/dim]"
    )
    return True


def _cmd_clear_attachments(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[red]No active session state[/red]")
        return True
    if (
        not state.attached_refs
        and not state.attached_docs
        and not getattr(state, "attached_files", [])
    ):
        console.print("[dim](nothing to clear)[/dim]")
        return True
    state.clear_attachments()
    console.print("[green]✓[/green] Cleared all attachments for this session (persisted)")
    return True


def _cmd_locker(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[red]No active session state[/red]")
        return True

    parts = line.split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "list"
    arg = parts[2].strip() if len(parts) > 2 else None

    if sub == "add":
        if not arg:
            console.print("[yellow]usage:[/yellow] /locker add <path> [--once]")
            return True
        tokens = arg.split()
        once = "--once" in tokens
        path = " ".join(t for t in tokens if t != "--once").strip().strip('"').strip("'")
        from pathlib import Path as _P
        if not path or not _P(path).expanduser().is_file():
            console.print(f"[red]not a file:[/red] {path or '(empty)'}")
            return True
        entry = state.attach_file(path, once=once)
        tag = " [dim](once)[/dim]" if once else ""
        console.print(
            f"[green]✓[/green] locked [cyan]{entry['name']}[/cyan] "
            f"[dim]({entry['kind']})[/dim]{tag} — shared on your next turn"
        )
        if entry["kind"] == "other":
            console.print(
                f"[yellow]note:[/yellow] {entry['name']} is an unsupported type "
                "(images, text, and PDFs are sent) — it'll be named, not embedded"
            )
        elif entry["kind"] == "image":
            _warn_if_blind_to_images(state, console)
        return True

    if sub in ("on", "off"):
        if not arg:
            console.print(f"[yellow]usage:[/yellow] /locker {sub} <name>")
            return True
        if state.set_file_enabled(arg, sub == "on"):
            word = "shared" if sub == "on" else "held back"
            console.print(f"[green]✓[/green] [cyan]{arg}[/cyan] is now {word}")
        else:
            console.print(f"[dim]no locker entry named {arg}[/dim]")
        return True

    if sub in ("remove", "rm", "del"):
        if not arg:
            console.print("[yellow]usage:[/yellow] /locker remove <name>")
            return True
        if state.remove_file(arg):
            console.print(f"[green]✓[/green] removed [cyan]{arg}[/cyan] from the locker")
        else:
            console.print(f"[dim]no locker entry named {arg}[/dim]")
        return True

    files = state.attached_files
    if not files:
        console.print("[dim](locker empty)[/dim]")
        console.print("[dim]/locker add <path> [--once]  — stage a file to share[/dim]")
        console.print(
            "[dim]in the TUI, the [/dim][cyan]/file-tab[/cyan][dim] panel tree "
            "attaches files too (same attach_file producer).[/dim]"
        )
        return True

    from xlii.multimodal import estimate_live_cost, human_bytes

    console.print("[bold]Locker[/bold] [dim](● shared on each turn · ○ held)[/dim]")
    for e in files:
        mark = "[green]●[/green]" if e.get("enabled") else "[dim]○[/dim]"
        once = " [dim](once)[/dim]" if e.get("once") else ""
        try:
            size = human_bytes(os.path.getsize(e["path"]))
        except OSError:
            size = "missing"
        console.print(
            f"  {mark} [bold]{e['name']}[/bold]  "
            f"[dim]{e.get('kind', '?')} · {size}[/dim]{once}"
        )
    console.print(f"\n[dim]live: {estimate_live_cost(state.live_attachment_paths())}[/dim]")
    _warn_if_blind_to_images(state, console)
    console.print(
        "[dim]/locker on|off <name> · /locker remove <name> · "
        "/locker add <path> [--once][/dim]"
    )
    console.print(
        "[dim]producers feed this one locker: [/dim][cyan]/file-tab[/cyan][dim] "
        "panel tree (in-TUI) · [/dim][cyan]/upload[/cyan][dim] popup (desktop sugar) "
        "· [/dim][cyan]/locker add[/cyan][dim] (path).[/dim]"
    )
    return True


def _has_display() -> bool:
    if sys.platform in ("darwin", "win32"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _ingest_upload_manifest(state, manifest_path: str, *, once: bool) -> list[str]:
    from xlii.upload_popup import read_manifest

    names: list[str] = []
    for path in read_manifest(manifest_path):
        try:
            names.append(state.attach_file(path, once=once)["name"])
        except Exception:
            # One unattachable path is skipped so the rest of the manifest still uploads.
            pass
    return names


def _attach_inline(state, console, paths: list[str], *, once: bool) -> None:
    from pathlib import Path as _P

    added: list[str] = []
    for raw in paths:
        p = _P(raw).expanduser()
        if p.is_file():
            added.append(state.attach_file(str(p), once=once)["name"])
        else:
            console.print(f"[red]not a file:[/red] {raw}")
    if added:
        tag = " [dim](once)[/dim]" if once else ""
        console.print(
            f"[green]✓[/green] locked {len(added)} file(s): "
            + ", ".join(f"[cyan]{n}[/cyan]" for n in added)
            + tag
        )


def _cmd_upload(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if not state:
        console.print("[red]No active session state[/red]")
        return True

    tokens = line.split()[1:]
    once = "--once" in tokens
    inline = [t for t in tokens if t != "--once"]

    if inline:
        _attach_inline(state, console, inline, once=once)
        return True

    if not _has_display():
        console.print("[yellow]No display detected[/yellow] — the upload popup needs a GUI.")
        console.print(
            "[dim]Use [/dim][cyan]/locker add <path>[/cyan][dim] "
            "(or [/dim][cyan]/upload <path> …[/cyan][dim]) instead.[/dim]"
        )
        return True

    # The in-TUI file-tab panel tree is the default attach path now; the popup
    # stays as desktop sugar (spec §5: one consumer, many producers). Drag-drop
    # needs the optional `tkinterdnd2` dep — without it the popup falls back to
    # click-to-pick, which is expected, not a bug.
    console.print(
        "[dim]drag-drop needs the optional tkinterdnd2 dep; without it the popup "
        "falls back to click-to-pick (not a bug). In the TUI, the "
        "[/dim][cyan]/file-tab[/cyan][dim] panel tree attaches files directly.[/dim]"
    )
    fd, manifest = tempfile.mkstemp(prefix="xlii-upload-", suffix=".json")
    os.close(fd)
    try:
        with console.status("[dim]upload popup open — pick files, then Done…[/dim]"):
            proc = subprocess.run([sys.executable, "-m", "xlii.upload_popup", manifest])
        if proc.returncode == 1:
            console.print(
                "[yellow]couldn't open the popup[/yellow] "
                "[dim](is python3-tk installed?)[/dim] — use "
                "[cyan]/locker add <path>[/cyan]"
            )
            return True
        names = _ingest_upload_manifest(state, manifest, once=once)
        if names:
            tag = " [dim](once)[/dim]" if once else ""
            console.print(
                f"[green]✓[/green] locked {len(names)} file(s): "
                + ", ".join(f"[cyan]{n}[/cyan]" for n in names)
                + tag
            )
            console.print("[dim]see /locker — they ride your next turn[/dim]")
        else:
            console.print("[dim](nothing added)[/dim]")
    finally:
        try:
            os.unlink(manifest)
        except OSError:
            # Temp-manifest cleanup in a finally: an already-removed file must not mask the command's own
            # result.
            pass
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="attachments",
            handler=_cmd_attachments,
            description="Show currently attached refs, docs, and locker files",
            category="knowledge",
        )
    )
    register_repl_command(
        REPLCommand(
            name="clear-attachments",
            handler=_cmd_clear_attachments,
            aliases=["clearatt", "forget-attachments"],
            description="Remove all refs, docs, and locker files from this session (durable)",
            category="knowledge",
        )
    )
    register_repl_command(
        REPLCommand(
            name="locker",
            handler=_cmd_locker,
            description="Stage local files (images/text) to share with the model on your next turn",
            usage="/locker [add <path> [--once] | on <name> | off <name> | remove <name> | list]",
            category="knowledge",
        )
    )
    register_repl_command(
        REPLCommand(
            name="upload",
            handler=_cmd_upload,
            description="Drag-drop popup to stage files in the locker (or /upload <path> …; headless → /locker add)",
            usage="/upload [<path> … [--once]]",
            category="knowledge",
        )
    )
