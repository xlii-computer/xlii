"""Shared project-path resolution + jail.

One jail, multiple sinks: `/edit --file` and `/browse` both resolve user-supplied
paths through here so a single rule decides what counts as "inside the project"
(edit-command.md Phase 2 / project-browser.md path conventions). Relative paths
resolve against the live shell cwd when it is inside the project, else against the
project root; the result is always confined to the root.

`/upload` and `/locker` are deliberately NOT routed here — they are the OS-wide
producers for arbitrary files (a screenshot on the Desktop), which by design are
not jailed to a project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class PathOutsideProject(ValueError):
    """Raised when a resolved path escapes the project root."""

    def __init__(self, raw: str, root: Path):
        super().__init__(f"path escapes project root: {raw!r}")
        self.raw = raw
        self.root = root


def scratch_store_root() -> Path:
    """User-level scratch desk store: ``~/.xlii/scratch`` (home/named desks).

    Distinct from per-project ``.xlii/scratch/tool-output/``. Path.home() lives
    here (the designated path seam) so callers do not open a path_home contract
    violation.
    """
    return (xlii_user_root() / "scratch").resolve()


def user_home() -> Path:
    """The user's home directory (``~``).

    Kernel code must not call :func:`pathlib.Path.home` directly — route through
    this seam so ``scripts/check_contracts.py`` can enforce the ratchet.
    """
    return Path.home().resolve()


def xlii_user_root() -> Path:
    """User-level xlii state root: ``~/.xlii`` (browser profiles, chat, etc.)."""
    return (user_home() / ".xlii").resolve()


def is_home_desk_project(project) -> bool:
    """True when *project* is the durable 7am home desk (``scratch/home``).

    That desk holds config under ``~/.xlii/scratch/home``; the shell roams
    :func:`Path.home`, not the config directory.
    """
    if project is None:
        return False
    name = (getattr(project, "name", "") or "").strip().lower()
    if name == "scratch/home":
        return True
    root = getattr(project, "project_root", None)
    if root is None:
        return False
    try:
        return Path(root).resolve() == (scratch_store_root() / "home").resolve()
    except OSError:
        return False


def scratch_home_roam_cwd(project) -> Optional[Path]:
    """Shell cwd for the home desk: ``~`` (config stays under the desk).

    Named desks (``scratch/<other>``) return ``None`` — keep cwd at the desk.
    """
    if not is_home_desk_project(project):
        return None
    try:
        return user_home()
    except OSError:
        return None


def within_root(root: Path, cand: Path) -> bool:
    """Is `cand` inside `root`? Resolves BOTH sides so a symlinked or
    un-normalized root (git worktrees, macOS /tmp) can neither defeat the jail nor
    wrongly trip it for a legitimate in-tree path."""
    try:
        cand.resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def resolve_project_path(raw: str, root: Path, *, cwd: Optional[Path] = None) -> Path:
    """Resolve `raw` to an absolute path jailed to `root`.

    Absolute paths are taken as-is (then jailed). Relative paths resolve against
    `cwd` when `cwd` is inside `root`, else against `root`. Raises
    :class:`PathOutsideProject` if the resolved path escapes the root.
    """
    root = Path(root)
    p = Path(raw).expanduser()
    if p.is_absolute():
        cand = p.resolve()
    else:
        base = root
        if cwd is not None and within_root(root, Path(cwd)):
            base = Path(cwd)
        cand = (base / p).resolve()
    if not within_root(root, cand):
        raise PathOutsideProject(raw, root)
    return cand


def resolve_adopt_path(
    raw: str,
    *,
    shell_cwd: "Path | str | None" = None,
    selected: "Path | str | None" = None,
) -> "tuple[Optional[Path], str]":
    """Resolve a folder to *adopt* as an xlii project.

    Existing directory only — this is not ``mkdir``. Relative names resolve
    against the desk's ``shell_cwd`` (Home roams ``~``), never the process
    cwd. Bare ``.`` / empty uses *selected* (Files focus) then ``shell_cwd``.
    Refuses ``~`` and the Home desk store. Returns ``(path, "")`` or
    ``(None, reason)``.
    """
    text = (raw or "").strip()
    if text.startswith("file://"):
        text = text[7:]
    if text in ("", ".", "./"):
        cand = Path(selected).expanduser() if selected else None
        if cand is None or not cand.exists():
            cand = Path(str(shell_cwd)).expanduser() if shell_cwd else None
        if cand is None:
            return None, "need a folder — select one in Files, or type ~/path"
    else:
        p = Path(text).expanduser()
        if not p.is_absolute():
            base = Path(str(shell_cwd)).expanduser() if shell_cwd else user_home()
            p = base / p
        cand = p
    try:
        cand = cand.resolve()
    except OSError as e:
        return None, f"can't resolve {text or raw!r}: {e}"
    if cand.is_file():
        cand = cand.parent
    if not cand.is_dir():
        return None, f"no folder at {cand} — adopt an existing directory"
    try:
        home = user_home()
        if cand == home:
            return None, "won't adopt your home directory — pick the project folder"
        desk = (scratch_store_root() / "home").resolve()
        if cand == desk:
            return None, "that's the Home desk itself — pick another folder"
    except OSError:
        # Can't stat home or the desk root — skip these two "don't adopt this"
        # guards rather than block an otherwise legitimate adopt.
        pass
    return cand, ""
