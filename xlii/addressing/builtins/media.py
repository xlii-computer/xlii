"""MediaProvider — ``media://``, the persona's media inbox on this machine.

What the phone sent iXaac, persisted (daemon media-persist) and carried home
by ``xlii fabric pull``: the durable store at ``~/.xlii/chat/<persona>/media``.
Distinct from ``artifacts://`` (what xlii MADE, per-project) and ``locker://``
(what THIS session staged to send) — this is what arrived from other mouths.

Rows list newest-first with the capture-time sidecar (caption · sender · ts)
as the detail; ``*.meta.json`` sidecars never render as rows. The persona is
the ambient session's default (project binding > cfg > shipped id); a bare
seat degrades to empty, never crashes the address space.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)

_SIDECAR_SUFFIX = ".meta.json"


def sidecar_meta(path: Path) -> "dict[str, Any]":
    """The capture-time metadata for a media file ({} when absent/corrupt)."""
    sidecar = path.with_name(path.name + _SIDECAR_SUFFIX)
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


class MediaProvider:
    scheme = "media"

    def resolve(self, address: Address) -> Resolution:
        name = address.key.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="media")
        ok = self._file(name) is not None
        return Resolution(ok=ok, address=address, kind="media",
                          reason="" if ok else f"no media {name!r}")

    def stat(self, address: Address) -> Node:
        name = address.key.strip()
        if not name:
            return Node(address="media://", name="media", kind="container")
        p = self._file(name)
        extra: dict[str, Any] = {}
        if p is not None:
            extra = self._extra(p)
        return Node(address=str(address), name=name, kind="leaf", extra=extra)

    def list(self, address: Address) -> "list[Node]":
        if address.key.strip():
            return []
        return [
            Node(address=f"media://{p.name}", name=p.name, kind="leaf",
                 extra=self._extra(p))
            for p in self._files()
        ]

    def read(self, address: Address) -> bytes:
        name = address.key.strip()
        if not name:
            raise IsADirectoryError("media://: the inbox root — use ls")
        p = self._file(name)
        if p is None:
            raise FileNotFoundError(f"media://{name}: no such media")
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
            raise FileNotFoundError(f"media://{name}: no such media")
        return ShellExport(kind="path", path=p)

    # ---- the store ---- #

    @staticmethod
    def _extra(p: Path) -> "dict[str, Any]":
        st = p.stat()
        meta = sidecar_meta(p)
        return {"type": "media", "path": str(p), "bytes": st.st_size,
                "mtime": st.st_mtime,
                "caption": str(meta.get("caption", "") or ""),
                "sender": str(meta.get("sender", "") or "")}

    @staticmethod
    def _root() -> Optional[Path]:
        """The default persona's media store via the ambient session; None on
        a bare seat — callers degrade to empty."""
        try:
            from xlii.active_session import active_session
            from xlii.persona import CHAT_STATE_DIR, resolve_default_persona

            sess = active_session()
            pid = resolve_default_persona(
                project=getattr(sess, "project", None),
                cfg=getattr(sess, "cfg", None),
            )
            return CHAT_STATE_DIR / pid / "media"
        except Exception:
            return None

    @classmethod
    def _files(cls) -> "list[Path]":
        root = cls._root()
        if root is None or not root.is_dir():
            return []
        try:
            files = [p for p in root.iterdir()
                     if p.is_file() and not p.name.endswith(_SIDECAR_SUFFIX)]
        except OSError:
            return []
        return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)

    @classmethod
    def _file(cls, name: str) -> Optional[Path]:
        # Containment: a name that escapes the store resolves to nothing.
        if "/" in name or ".." in name:
            return None
        return next((p for p in cls._files() if p.name == name), None)
