"""Project browser — git-aware orientation for the code REPL (project-browser.md P2).

``/browse`` answers "what is this repo, what changed, and which part do I mean?"
from local ``git`` + the filesystem only — no forge API. It is a *human-facing*
producer that complements ``/upload``: where upload picks arbitrary OS files,
browse orients inside *this* project and can attach in-repo paths to the locker.

This is the first consumer of the P0 fingerprint (``project_fingerprint.py``) and
the P1 git snapshot (``git_status.py``); both were shipped but unwired until now.

Headless by design (the P2 gate): every view prints to the console and works over
SSH. The tkinter popup (P3) and reference/edit actions (P4) layer on top later.

Views:
  /browse                      skeleton overview (default) — fingerprint + zones + git pulse
  /browse --tree [path]        directory tree from path (default: project root), git badges
  /browse --changed            working-tree dirty files
  /browse --staged             staged files
  /browse <path> [...] --attach   attach in-repo path(s) to the locker (jailed to root)
  /browse --refresh-profile    re-scan markers, rewrite .xlii/project-profile.json
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
# The jail primitive lives in xlii.project_paths (shared with /edit --file) so
# there is a single definition of "inside the project".
from xlii.project_paths import within_root as _within

# Bound the tree walk so `/browse --tree` on a large repo can never flood the
# terminal (or hang). Ignored dirs are pruned first; this is the backstop.
_MAX_TREE_NODES = 2000

# Order zones are presented in the skeleton (most-useful-first).
_ZONE_ORDER = ("entry", "config", "source", "tests", "tooling", "generated")

# Single-letter git status -> color for badges.
_BADGE_STYLE = {
    "M": "yellow", "A": "green", "D": "red", "R": "cyan", "C": "cyan",
    "U": "red", "?": "dim",
}


def _project_root(ctx: dict[str, Any]) -> Optional[Path]:
    state = ctx.get("state")
    project = state.project if state else ctx.get("project")  # house idiom (cf. code.py)
    root = getattr(project, "project_root", None) if project else None
    return Path(root) if root else None


def _badge(code: str) -> str:
    style = _BADGE_STYLE.get(code, "yellow")
    return f"[{style}]{code}[/{style}]"


def _branch_line(snap) -> str:
    """One-line git pulse: branch · N changed · ahead/behind — or 'not a git repo'."""
    if not snap.is_repo:
        return "[dim]not a git repo[/dim]"
    bits = [f"branch [cyan]{snap.branch or '?'}[/cyan]"]
    if snap.changed_count:
        bits.append(f"[yellow]{snap.changed_count} changed[/yellow]")
    else:
        bits.append("[green]clean[/green]")
    if snap.ahead_behind:
        ahead, behind = snap.ahead_behind
        if ahead:
            bits.append(f"↑{ahead}")
        if behind:
            bits.append(f"↓{behind}")
    return " · ".join(bits)


def _visible_relpaths(root: Path) -> list[str]:
    """Ignore-filtered *visible* file relpaths (every non-ignored file, not just
    git-tracked), or [] on any failure. Used for zone file counts.

    Delegates the prune-walk to ``ignore.walk_pruned`` so the ignore logic lives
    in one place (proposal design principle 6 — no second copy of the walk)."""
    try:
        from xlii.ignore import load_ignore_spec, walk_pruned

        spec = load_ignore_spec(root)
        return [p.relative_to(root).as_posix() for p in walk_pruned(root, spec)]
    except Exception:
        return []


def _zone_count(zone_path: str, visible: list[str]) -> Optional[int]:
    """Count visible files under a zone dir (``src/``); None for a single file."""
    if not zone_path.endswith("/"):
        return None
    return sum(1 for rel in visible if rel.startswith(zone_path))


def _render_skeleton(console, root: Path, profile, snap) -> None:
    fp = ", ".join(profile.fingerprints) if profile.fingerprints else "generic"
    console.print(f"project: [bold]{root.name}[/bold]  [dim]([/dim]{fp}[dim] · {_branch_line(snap)}[dim])[/dim]")

    origin = snap.remotes.get("origin") or next(iter(snap.remotes.values()), None)
    if origin:
        console.print(f"remote:  [dim]{origin}[/dim]")
    if profile.hints:
        hint_str = " · ".join(f"{k}={v}" for k, v in sorted(profile.hints.items()))
        console.print(f"hints:   [dim]{hint_str}[/dim]")

    visible = _visible_relpaths(root)
    console.print()
    for zone in _ZONE_ORDER:
        paths = profile.zones.get(zone)
        if not paths:
            continue
        rendered = []
        for p in paths:
            n = _zone_count(p, visible)
            changed_here = sum(1 for cp in snap.status if cp.startswith(p)) if p.endswith("/") else 0
            # Suppress "(0 files)" for ignored/generated dirs (.xlii/, .venv/, …).
            tag = f" [dim]({n} files)[/dim]" if n else ""
            if changed_here:
                tag += f" [yellow]● {changed_here} changed[/yellow]"
            rendered.append(f"{p}{tag}")
        marker = "[dim]·[/dim]" if zone == "generated" else "[cyan]▾[/cyan]"
        console.print(f"  {marker} [bold]{zone:<9}[/bold] " + ", ".join(rendered))

    # Monorepo signal: sub-packages that carry their own ecosystem markers (P7).
    try:
        from xlii.project_fingerprint import detect_package_roots

        pkg_roots = detect_package_roots(root)
    except Exception:
        pkg_roots = []
    if pkg_roots:
        console.print(f"  [cyan]▾[/cyan] [bold]{'packages':<9}[/bold] " + ", ".join(pkg_roots))

    console.print()
    console.print(
        "[dim]/browse --tree [path] · --changed · --diff [path] · "
        "<path> --attach|--reference|--edit · --popup · --refresh-profile[/dim]"
    )


def _build_tree(node, dir_path: Path, root: Path, spec, status_map: dict[str, str], counters: dict) -> None:
    try:
        entries = sorted(dir_path.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return
    for entry in entries:
        rel = entry.relative_to(root).as_posix()
        if spec.match_file(rel) or spec.match_file(rel + "/"):
            continue
        if counters["nodes"] >= _MAX_TREE_NODES:
            counters["truncated"] = True
            return
        counters["nodes"] += 1
        # Never follow symlinked directories: they can point outside the project
        # root (jail escape / out-of-tree disclosure) or form cycles. Show them
        # as a leaf with a trailing "@" instead of descending.
        if entry.is_dir() and not entry.is_symlink():
            child = node.add(f"[bold]{entry.name}/[/bold]")
            _build_tree(child, entry, root, spec, status_map, counters)
        elif entry.is_symlink():
            node.add(f"[dim]{entry.name}@[/dim]")
        else:
            code = status_map.get(rel)
            node.add(f"{entry.name}  {_badge(code)}" if code else entry.name)


def _render_tree(console, root: Path, snap, subpath: Optional[str]) -> None:
    root = root.resolve()  # so base.relative_to(root) holds even for a symlinked root
    base = root
    if subpath:
        cand = (root / subpath).resolve()
        if not _within(root, cand) or not cand.exists():
            console.print(f"[red]not inside the project (or missing):[/red] {subpath}")
            return
        base = cand

    try:
        from rich.tree import Tree

        from xlii.ignore import load_ignore_spec
    except Exception as e:  # pragma: no cover - defensive
        console.print(f"[red]cannot build tree:[/red] {e}")
        return

    spec = load_ignore_spec(root)
    label = base.relative_to(root).as_posix() or root.name
    tree = Tree(f"[bold]{label}/[/bold]  [dim]({_branch_line(snap)})[/dim]")
    counters = {"nodes": 0, "truncated": False}
    _build_tree(tree, base, root, spec, snap.status, counters)
    console.print(tree)
    if counters["truncated"]:
        console.print(f"[dim]… tree truncated at {_MAX_TREE_NODES} entries — scope it: /browse --tree <path>[/dim]")


def _render_diff(console, root: Path, subpath: Optional[str]) -> None:
    from xlii.git_status import diff_stat, find_repo_root

    if find_repo_root(root) is None:
        console.print("[dim]not a git repo — no diff[/dim]")
        return
    rel = None
    if subpath:
        cand = (root / subpath).resolve()
        if not _within(root, cand):
            console.print(f"[red]outside project root (refused):[/red] {subpath}")
            return
        rel = cand.relative_to(root.resolve()).as_posix()
    stat = diff_stat(root, rel)
    if not stat:
        console.print("[dim]no unstaged changes[/dim]")
        return
    console.print(f"[bold]git diff --stat{(' ' + rel) if rel else ''}[/bold]")
    console.print(stat)


def _render_changed(console, snap, *, staged: bool) -> None:
    if not snap.is_repo:
        console.print("[dim]not a git repo — nothing to show[/dim]")
        return
    from xlii.git_status import staged_status_map

    status = staged_status_map(Path(snap.root)) if staged else snap.status
    label = "staged" if staged else "changed (working tree)"
    if not status:
        console.print(f"[dim]no {label} files[/dim]")
        return
    console.print(f"[bold]{label}[/bold] [dim]— {len(status)}[/dim]")
    for path in sorted(status):
        console.print(f"  {_badge(status[path])}  {path}")


def _apply_action(console, ctx: dict[str, Any], root: Path, action: str, raws: list[str]) -> None:
    """Apply a browse action to in-repo paths — the single sink shared by the
    headless flags (--attach/--reference/--edit) and the popup manifest, so the
    GUI and CLI can never diverge. ``raws`` may be relative (flags) or absolute
    (manifest); every path is resolved and jailed to ``root`` here.

      attach    → locker (state.attach_file) — same sink as /upload
      reference → queue project-relative paths into the next prompt (pending_input)
      edit      → open in $EDITOR via the shared open_for_edit()
      cancel    → no-op
    """
    if action == "cancel":
        console.print("[dim](cancelled)[/dim]")
        return
    root = root.resolve()  # resolved candidates compare cleanly even if project_root is a symlink
    state = ctx.get("state")
    resolved: list[Path] = []
    for raw in raws:
        cand = Path(raw)
        cand = cand.resolve() if cand.is_absolute() else (root / cand).resolve()
        if not _within(root, cand):
            console.print(f"[red]outside project root (refused):[/red] {raw}")
            continue
        resolved.append(cand)
    if not resolved:
        console.print("[yellow]usage:[/yellow] /browse <path> [<path> …] --attach|--reference|--edit")
        return

    if action == "attach":
        if state is None or not hasattr(state, "attach_file"):
            console.print("[dim]attach needs an active code session[/dim]")
            return
        added: list[str] = []
        for p in resolved:
            if not p.is_file():
                console.print(f"[red]not a file:[/red] {p.name}")
                continue
            try:
                added.append(state.attach_file(str(p))["name"])
            except Exception as e:
                console.print(f"[red]could not attach {p.name}:[/red] {e}")
        if added:
            console.print(
                f"[green]✓[/green] locked {len(added)} file(s): "
                + ", ".join(f"[cyan]{n}[/cyan]" for n in added)
                + " [dim]— ride your next turn (see /locker)[/dim]"
            )
        return

    if action == "reference":
        rels = [p.relative_to(root).as_posix() for p in resolved]
        text = " ".join(rels)
        if state is not None:
            from xlii.repl_state import queue_pending_input

            queue_pending_input(state, text, replace=False)
            console.print(
                f"[green]✓[/green] queued {len(rels)} path(s) into your next prompt "
                f"[dim]({text})[/dim]"
            )
        else:
            console.print(text)  # headless without state: print for copy/paste
        return

    if action == "edit":
        from xlii.editor import open_for_edit
        for p in resolved:
            console.print(f"[dim]opening {p.relative_to(root).as_posix()} in $EDITOR…[/dim]")
            open_for_edit(p)
        return

    console.print(f"[yellow]unknown action:[/yellow] {action}")


def _launch_popup(console, ctx: dict[str, Any], root: Path) -> None:
    """Run the tkinter browse popup (P3) as a subprocess, then apply its action —
    mirrors /upload's popup isolation; headless callers get the views instead."""
    from xlii.repl_cmds.locker import _has_display

    if not _has_display():
        console.print(
            "[yellow]No display[/yellow] — use the headless views: "
            "[cyan]/browse[/cyan], [cyan]/browse --changed[/cyan], "
            "[cyan]/browse <path> --attach[/cyan]"
        )
        return

    import os
    import subprocess
    import sys
    import tempfile

    from xlii import browse_popup

    fd, manifest = tempfile.mkstemp(prefix="xlii-browse-", suffix=".json")
    os.close(fd)
    try:
        with console.status("[dim]browse popup open — pick files + action, then Done…[/dim]"):
            proc = subprocess.run(
                [sys.executable, "-m", "xlii.browse_popup", manifest, "--root", str(root)]
            )
        if proc.returncode == 1:
            console.print(
                "[yellow]couldn't open the popup[/yellow] "
                "[dim](is python3-tk installed?)[/dim] — use the headless views"
            )
            return
        action, files = browse_popup.read_manifest(manifest)
        _apply_action(console, ctx, root, action, files)
    finally:
        try:
            os.unlink(manifest)
        except OSError:
            # Temp-manifest cleanup in a finally: an already-removed file must not mask the command's own
            # result.
            pass


def _refresh(console, root: Path) -> None:
    from xlii.project_fingerprint import refresh_project_profile

    profile = refresh_project_profile(root)
    fp = ", ".join(profile.fingerprints)
    console.print(
        f"[green]✓[/green] refreshed [cyan].xlii/project-profile.json[/cyan] "
        f"[dim]— {fp}; {len(profile.zones)} zone(s)[/dim]"
    )


def _load_or_detect(root: Path):
    """Cached profile if present, else detect+write it (browse is the producer)."""
    from xlii.project_fingerprint import detect_project_fingerprint, load_project_profile, write_project_profile

    profile = load_project_profile(root)
    if profile is None:
        profile = detect_project_fingerprint(root)
        try:
            write_project_profile(root, profile)
        except OSError:
            pass  # read-only tree: still render from the in-memory detection
    return profile


def _cmd_browse(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    root = _project_root(ctx)
    if root is None:
        console.print("[dim]/browse needs a project — only available in `xlii code`[/dim]")
        return True

    import shlex
    try:
        tokens = shlex.split(line)[1:]  # drop "/browse"; honors quoted paths with spaces
    except ValueError:
        tokens = line.split()[1:]       # unbalanced quotes — fall back to naive split
    flags = {t for t in tokens if t.startswith("--")}
    positionals = [t for t in tokens if not t.startswith("--")]

    from xlii.git_status import git_snapshot

    if "--refresh-profile" in flags:
        _refresh(console, root)
        return True
    if "--popup" in flags or "--gui" in flags:
        _launch_popup(console, ctx, root)
        return True
    if "--attach" in flags:
        _apply_action(console, ctx, root, "attach", positionals)
        return True
    if "--reference" in flags or "--ref" in flags:
        _apply_action(console, ctx, root, "reference", positionals)
        return True
    if "--edit" in flags:
        _apply_action(console, ctx, root, "edit", positionals)
        return True
    if "--changed" in flags or "--staged" in flags:
        _render_changed(console, git_snapshot(root), staged="--staged" in flags)
        return True
    if "--diff" in flags:
        _render_diff(console, root, positionals[0] if positionals else None)
        return True
    if "--tree" in flags or positionals:
        # bare positional (no --attach) is a convenience alias for --tree <path>
        _render_tree(console, root, git_snapshot(root), positionals[0] if positionals else None)
        return True

    _render_skeleton(console, root, _load_or_detect(root), git_snapshot(root))
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="browse",
            handler=_cmd_browse,
            description="Git-aware project overview: skeleton, tree, changed files, attach/reference/edit in-repo paths",
            usage="/browse [--tree [path] | --changed | --staged | --diff [path] | <path> --attach|--reference|--edit | --popup | --refresh-profile]",
            category="knowledge",
            repls=["code"],
        )
    )
