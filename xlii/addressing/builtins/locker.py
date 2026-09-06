"""LockerProvider provider."""
from __future__ import annotations

from pathlib import Path

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


class LockerProvider:
    """``locker://`` — the session's attached files (the image locker) as a browseable list.

    ``locker://`` lists every attached file (nodes carry ``type`` = the file's kind and its ``path``);
    ``locker://<name>`` reads that file's bytes. Reaches the live locker through the ambient session
    (:mod:`xlii.active_session`). The rich behaviour — selecting an image renders it in Pane 1 (the
    REPL), NOT in the pane — lives in :class:`~xlii.panes.locker.LockerPane`; this provider is the
    address behind the ``[images]`` doorway.
    """

    scheme = "locker"

    def resolve(self, address: Address) -> Resolution:
        name = address.key.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="locker")  # the browseable root
        ok = self._entry(name) is not None
        return Resolution(ok=ok, address=address, kind="locker",
                          reason="" if ok else f"no attached file {name!r}")

    def stat(self, address: Address) -> Node:
        name = address.key.strip()
        if not name:
            return Node(address="locker://", name="locker", kind="container")
        e = self._entry(name) or {}
        return Node(address=str(address), name=name, kind="leaf",
                    extra={"type": e.get("kind", "image"), "path": e.get("path")})

    def list(self, address: Address) -> "list[Node]":
        if address.key.strip():
            return []
        return [
            Node(address=f"locker://{e['name']}", name=e["name"], kind="leaf",
                 extra={"type": e.get("kind", "image"), "path": e.get("path"),
                        "enabled": e.get("enabled", True)})
            for e in self._entries()
        ]

    def read(self, address: Address) -> bytes:
        name = address.key.strip()
        if not name:
            raise IsADirectoryError("locker://: a locker root — use ls")
        e = self._entry(name)
        if e is None or not e.get("path"):
            raise FileNotFoundError(f"locker://{name}: no such attached file")
        return Path(e["path"]).read_bytes()

    def exists(self, address: Address) -> bool:
        name = address.key.strip()
        return True if not name else self._entry(name) is not None

    def shell_export(self, address: Address) -> ShellExport:
        name = address.key.strip()
        if not name:
            return ShellExport(kind="address")  # a session list — no directory behind it
        e = self._entry(name)
        if e is None or not e.get("path"):
            raise FileNotFoundError(f"locker://{name}: no such attached file")
        # The entry IS a pointer to a real (often binary) file — pass its path through.
        # Attach stores the path even for a since-vanished file; guard it.
        p = Path(e["path"])
        if not p.exists():
            raise FileNotFoundError(f"locker://{name}: attached file is gone ({p})")
        return ShellExport(kind="path", path=p)

    @staticmethod
    def _entries() -> "list[dict]":
        from xlii.active_session import active_session

        files = getattr(active_session(), "attached_files", None) or []
        return [e for e in files if isinstance(e, dict) and e.get("name")]

    @classmethod
    def _entry(cls, name: str) -> "dict | None":
        return next((e for e in cls._entries() if e.get("name") == name), None)


