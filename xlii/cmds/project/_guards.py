"""Init safety guards for project commands."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from xlii.ui import confirm, console


def _sensitive_init_target(root: Path) -> Optional[str]:
    """Return a human-readable reason if `root` looks like a risky place to
    create a synced Collection, else None."""
    home = Path.home()
    if root == home:
        return "your home directory"
    # Filesystem root (`/`): root has no parent other than itself.
    if root.parent == root:
        return "a filesystem root"
    if str(root) in ("/tmp", "/var", "/usr", "/etc", "/mnt", "/media", "/opt"):
        return f"a system directory ({root})"
    # The usual oversized personal folders directly under home.
    if root.parent == home and root.name in (
        "Documents", "Downloads", "Desktop", "Pictures",
        "Music", "Videos", "Movies",
    ):
        return f"your {root.name} folder"
    return None


# Above this many tracked files (after ignores), bulk-uploading to a Collection
# is probably a mistake — warn and confirm regardless of location.
_LARGE_TREE_FILE_WARN = 2000


def _count_tracked_files(
    root: Path, extra_ignores: list[str], cap: int, *, max_visits: int = 40_000
) -> tuple[int, bool]:
    """Cheaply count files that *would* be synced (ignores applied), bounded.

    Returns (count, hit_limit) where hit_limit is True if we stopped early —
    either because `cap` tracked files were reached OR because we visited
    `max_visits` directory entries (a hard budget so this never hangs on a huge
    tree like ~). Unlike walk_paths_only, this prunes ignored directories in
    place (os.walk), so it doesn't descend into node_modules/.cache/venv at all.
    """
    import os
    from xlii.ignore import load_ignore_spec
    spec = load_ignore_spec(root, extra_ignores)
    n = 0
    visits = 0
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        # Prune ignored subdirectories before descending into them.
        kept = []
        for name in dirnames:
            rel = (d / name).relative_to(root).as_posix()
            if not (spec.match_file(rel) or spec.match_file(rel + "/")):
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            visits += 1
            rel = (d / name).relative_to(root).as_posix()
            if spec.match_file(rel):
                continue
            n += 1
            if n >= cap:
                return n, True
        if visits >= max_visits:
            return n, True
    return n, False


def _confirm_risky_init(root: Path) -> bool:
    """Pre-flight guard for `xlii init` in non-local sync mode.

    Returns True if init should proceed, False if the user backed out.
    Warns when the target is a sensitive/oversized directory and offers the
    safer local-snapshot path. Auto-proceeds (with no prompt) when nothing
    looks risky.
    """
    reason = _sensitive_init_target(root)

    # Bounded count (prunes ignored dirs, hard visit budget) so producing the
    # warning never hangs — even in ~. hit_limit means "we stopped early", so
    # "N+" honestly conveys "at least this many, probably more".
    count, hit_limit = _count_tracked_files(root, [], _LARGE_TREE_FILE_WARN)
    large = count >= _LARGE_TREE_FILE_WARN

    if not reason and not large:
        return True  # looks like a normal project — no nag.

    n_label = f"{count:,}{'+' if hit_limit else ''}"
    console.print()
    if reason:
        console.print(
            f"[yellow]⚠ heads up:[/yellow] [bold]{root}[/bold] is [bold]{reason}[/bold]."
        )
    console.print(
        f"  `xlii init` here will create a Collection and "
        f"[bold]upload ~{n_label} files[/bold] (after .gitignore/.xliiignore + defaults)."
    )
    console.print(
        "  That can be slow, costs upload quota, and may send data you didn't mean to sync."
    )
    console.print(
        "\n  Safer options:\n"
        "    • [cyan]xlii init --local[/cyan]            local-only — no upload, no Collection\n"
        "    • [cyan]xlii init --local --snapshot[/cyan] local + a fast paths/sizes index for structural search\n"
        "    • [cyan]xlii init --no-sync[/cyan]          create the Collection now, sync later (after you add .xliiignore)\n"
        "    • add a [cyan].xliiignore[/cyan] to trim what gets uploaded, then re-run\n"
    )
    if not sys.stdin.isatty():
        # Non-interactive (script/pipe): refuse to silently bulk-upload.
        console.print(
            "[red]aborting:[/red] non-interactive shell. Re-run with [cyan]--local[/cyan], "
            "[cyan]--no-sync[/cyan], or [cyan]--yes[/cyan] to confirm the upload."
        )
        return False
    return confirm(f"  upload ~{n_label} files from {root.name or root}? [y/N] ")


def _ensure_bind_persona(name: str) -> bool:
    """Ensure the persona being bound exists — auto-create its prompt file (like
    `_resolve_persona_to_load`). The persona's project dir/Collection stay lazy
    (materialized on first `xlii chat`); binding only needs the prompt to exist.

    Returns False for an invalid name so the caller refuses to persist an
    unusable (or path-traversal-shaped) binding."""
    from xlii.persona import (
        Persona,
        canonicalize_persona_id,
        create_persona,
        is_valid_name,
    )
    name = canonicalize_persona_id(name)
    if not is_valid_name(name):
        console.print(
            f"[red]invalid persona name {name!r}[/red] — not binding "
            "(use letters, digits, _ . - ; start with a letter/digit)"
        )
        return False
    if not Persona(name).exists():
        create_persona(name)
    return True
