"""Shared FS helpers for built-in addressing providers."""
from __future__ import annotations

from pathlib import Path

from xlii.addressing import (
    Node,
)


# --- shared filesystem helpers -----------------------------------------------


def _fs_node(path: Path, address: str) -> Node:
    kind = "container" if path.is_dir() else "leaf"
    if not path.exists():
        return Node(address=address, name=path.name or str(path), kind=kind)
    st = path.stat()
    size = st.st_size if path.is_file() else None
    extra: dict = {}
    if path.is_file():
        extra["mtime_ns"] = st.st_mtime_ns
    return Node(address=address, name=path.name or str(path), kind=kind, size=size, extra=extra)


def _fs_list(root: Path, address_for) -> "list[Node]":
    """List a directory into Nodes — containers first, then case-insensitive by name
    (mc-style). ``address_for(child_path) -> str`` builds each child's address in the
    caller's scheme."""
    if not root.is_dir():
        return []
    nodes = [_fs_node(c, address_for(c)) for c in root.iterdir()]
    return sorted(nodes, key=lambda n: (n.kind != "container", n.name.lower()))


def _safe_join(root: Path, subpath: str) -> Path:
    """Join ``subpath`` under ``root``, refusing to escape it.

    ``..`` segments that climb above ``root`` (or an absolute ``subpath``) raise
    ``ValueError`` — a provider maps that to a clean miss/error rather than reading
    or writing outside the intended entity. Shared by the ``file``/``project``/``conv``
    filesystem providers so the containment check lives in exactly one place.
    """
    root = Path(root).resolve()
    if not subpath:
        return root
    target = (root / subpath).resolve()
    if not target.is_relative_to(root):
        raise ValueError(f"path escapes {root}: {subpath!r}")
    return target


def _reject_traversal(subpath: str) -> str:
    """Refuse a remote subpath containing a ``..`` segment (can't resolve it locally,
    so we simply forbid climbing). Returns the path unchanged when it's safe."""
    if subpath and any(part == ".." for part in subpath.replace("\\", "/").split("/")):
        raise ValueError(f"remote path may not contain '..': {subpath!r}")
    return subpath


def _fs_delete(path: Path, recursive: bool) -> None:
    """Delete a path: a leaf via unlink; a dir via rmdir (empty) or rmtree (recursive)."""
    if path.is_dir():
        if recursive:
            import shutil

            shutil.rmtree(path)
        else:
            path.rmdir()
    else:
        path.unlink()


def _project_root_from_key(key: str) -> "Path | None":
    """Project root from a ``project://`` / ``conv://`` key — ``.``/``''`` → cwd, else a
    registry name. Paths are NOT accepted here (browse path-rooted dirs via ``file://``)."""
    key = (key or ".").strip()
    if key in (".", ""):
        return Path.cwd().resolve()
    from xlii.project_resolver import resolve_registered_project

    res = resolve_registered_project(key)
    return res.path if (res.ok and res.path is not None) else None


# --- providers ---------------------------------------------------------------


