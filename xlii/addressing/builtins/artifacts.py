"""ArtifactsProvider — ``artifacts://``, the gallery of what xlii MADE.

The disk-backed sibling of ``locker://`` and its deliberate opposite (the crux
of tui-media-delivery P2): the **Tray** (locker) is files you *staged to send*
(session state, ``attached_files``); **Artifacts** is things *xlii generated*
(``.xlii/artifacts/`` on disk — /imagine renders, generate_image output, edits).
Do not conflate them again.

``artifacts://`` lists the store's media files newest-first; ``artifacts://<name>``
reads one. Reaches the project through the ambient session and degrades to an
empty container when there is none — a bare seat never crashes the address space.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)

# Media the gallery lists; bookkeeping files (last-session.json, video-*.json)
# never render as rows.
_MEDIA_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".webm", ".pdf"}


def _kind_for(path: Path) -> str:
    suf = path.suffix.lower()
    if suf in (".mp4", ".webm"):
        return "video"
    if suf == ".pdf":
        return "pdf"
    return "image"


class ArtifactsProvider:
    scheme = "artifacts"

    def resolve(self, address: Address) -> Resolution:
        name = address.key.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="artifacts")
        ok = self._file(name) is not None
        return Resolution(ok=ok, address=address, kind="artifacts",
                          reason="" if ok else f"no artifact {name!r}")

    def stat(self, address: Address) -> Node:
        name = address.key.strip()
        if not name:
            return Node(address="artifacts://", name="artifacts", kind="container")
        p = self._file(name)
        extra = {}
        if p is not None:
            st = p.stat()
            extra = {"type": _kind_for(p), "path": str(p),
                     "bytes": st.st_size, "mtime": st.st_mtime}
        return Node(address=str(address), name=name, kind="leaf", extra=extra)

    def list(self, address: Address) -> "list[Node]":
        if address.key.strip():
            return []
        out: list[Node] = []
        for p in self._files():
            st = p.stat()
            out.append(Node(
                address=f"artifacts://{p.name}", name=p.name, kind="leaf",
                extra={"type": _kind_for(p), "path": str(p),
                       "bytes": st.st_size, "mtime": st.st_mtime},
            ))
        return out

    def read(self, address: Address) -> bytes:
        name = address.key.strip()
        if not name:
            raise IsADirectoryError("artifacts://: the gallery root — use ls")
        p = self._file(name)
        if p is None:
            raise FileNotFoundError(f"artifacts://{name}: no such artifact")
        return p.read_bytes()

    def exists(self, address: Address) -> bool:
        name = address.key.strip()
        return True if not name else self._file(name) is not None

    def shell_export(self, address: Address) -> ShellExport:
        name = address.key.strip()
        if not name:
            root = self._root()
            if root is None or not root.is_dir():
                return ShellExport(kind="address")
            return ShellExport(kind="path", path=root)
        p = self._file(name)
        if p is None:
            raise FileNotFoundError(f"artifacts://{name}: no such artifact")
        return ShellExport(kind="path", path=p)

    # ---- the store ---- #

    @staticmethod
    def _root() -> Optional[Path]:
        """The live project's artifacts dir, via the ambient session; None on a
        bare seat (no session / no project) — callers degrade to empty."""
        from xlii.active_session import active_session

        sess = active_session()
        root = getattr(getattr(sess, "project", None), "project_root", None)
        if root is None:
            return None
        from xlii.artifacts import artifacts_dir
        return artifacts_dir(Path(root))

    @classmethod
    def _files(cls) -> "list[Path]":
        root = cls._root()
        if root is None or not root.is_dir():
            return []
        try:
            files = [p for p in root.iterdir()
                     if p.is_file() and p.suffix.lower() in _MEDIA_SUFFIXES]
        except OSError:
            return []
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    @classmethod
    def _file(cls, name: str) -> Optional[Path]:
        # Containment: a name that escapes the store resolves to nothing.
        if "/" in name or ".." in name:
            return None
        return next((p for p in cls._files() if p.name == name), None)
