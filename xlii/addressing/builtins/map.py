"""MapProvider provider."""
from __future__ import annotations

from pathlib import Path

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)

from xlii.addressing.builtins._helpers import (
    _safe_join,
)

class MapProvider:
    """``map://`` — the repo map (the project's shape) as a read-only, computed VFS.

    A thin client of :func:`xlii.repo_map.build_map` (client-#1 doctrine — the same
    bytes ``/map``, ``xlii map``, and the agent's ``map`` tool serve), and the simplest
    possible case of a path-shaped, read-only, computed provider:

    * ``map://`` — the project root. A **container that reads**: ``ls`` browses the
      real tree (children are ``map://<relpath>`` addresses that round-trip), while
      ``read`` renders the whole project outline — a subtree's outline is a perfectly
      readable value, so this provider deliberately relaxes the fs providers'
      read-a-container-raises convention.
    * ``map://<relpath>`` — that file's (leaf) or subtree's (container) outline;
      containers list their children, and reading either renders its outline.

    Listing consumes :func:`xlii.repo_map.list_children` — the SAME file source the
    engine maps (client-#1: no surface owns a walker), so gitignored paths neither
    browse nor map. Read-only through the VFS (no write/delete/mkdir) — the map is
    *computed* from the tree on every read, never stored (locked decision #1:
    regenerate, never cache). Roots on the ambient session's project
    (:mod:`xlii.active_session`), falling back to the cwd, mirroring
    :class:`GitProvider`. Traversal outside the root is a miss everywhere —
    ``resolve``/``exists``/``list`` report it, ``read``/``stat`` raise
    ``FileNotFoundError``.
    """

    scheme = "map"

    @staticmethod
    def _root() -> Path:
        from xlii.active_session import active_cwd, active_session

        root = getattr(getattr(active_session(), "project", None), "project_root", None)
        return Path(str(root)) if root else (active_cwd() or Path.cwd())

    def _target(self, address: Address) -> "tuple[Path, Path]":
        root = self._root().resolve()
        return root, _safe_join(root, address.target.strip().strip("/"))

    def resolve(self, address: Address) -> Resolution:
        try:
            _root, target = self._target(address)
        except ValueError as e:
            return Resolution(ok=False, address=address, kind="map", reason=str(e))
        ok = target.exists()
        return Resolution(ok=ok, address=address, kind="map", path=target,
                          reason="" if ok else f"no such path in the project: {address.target!r}")

    def stat(self, address: Address) -> Node:
        rel = address.target.strip().strip("/")
        if not rel:
            return Node(address="map://", name="map", kind="container", extra={"type": "map"})
        try:
            _root, target = self._target(address)
        except ValueError as e:  # traversal — an honest miss, not a crash
            raise FileNotFoundError(f"map://{rel}: {e}") from e
        kind = "container" if target.is_dir() else "leaf"
        size = target.stat().st_size if target.is_file() else None
        return Node(address=f"map://{rel}", name=target.name, kind=kind, size=size,
                    extra={"type": "map"})

    def list(self, address: Address) -> "list[Node]":
        from xlii.repo_map import list_children

        rel = address.target.strip().strip("/")
        try:
            children = list_children(self._root(), rel)
        except ValueError:
            return []  # traversal or a nonexistent path — nothing to browse
        base = f"map://{rel}/" if rel else "map://"
        return [
            Node(address=f"{base}{name}", name=name,
                 kind="container" if is_dir else "leaf", size=size,
                 extra={"type": "map"})
            for name, is_dir, size in children
        ]

    def read(self, address: Address) -> bytes:
        from xlii.repo_map import build_map

        rel = address.target.strip().strip("/")
        try:
            root, target = self._target(address)
        except ValueError as e:  # traversal — an honest miss, not a crash
            raise FileNotFoundError(f"map://{rel}: {e}") from e
        if not target.exists():
            raise FileNotFoundError(f"map://{rel}: no such path in the project")
        return build_map(root, scope=rel or None).encode()

    def exists(self, address: Address) -> bool:
        try:
            _root, target = self._target(address)
        except ValueError:
            return False
        return target.exists()

    def shell_export(self, address: Address) -> ShellExport:
        # The address denotes a PLACE in the project tree (read()'s outline is a view
        # of it) — export the real path, mirroring resolve()'s existence semantics.
        rel = address.target.strip().strip("/")
        try:
            _root, target = self._target(address)
        except ValueError as e:  # traversal — an honest miss, not a crash
            raise FileNotFoundError(f"map://{rel}: {e}") from e
        if not target.exists():
            raise FileNotFoundError(f"map://{rel}: no such path in the project")
        return ShellExport(kind="path", path=target)


