"""ConfigProvider provider."""
from __future__ import annotations

import json
from pathlib import Path

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


_REDACT_KEYS = frozenset({"api_key", "management_api_key"})


def _redact_config(node):
    """Strip literal secrets from a config JSON tree (VFS browse / export)."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k in _REDACT_KEYS and v:
                out[k] = "[redacted]"
            else:
                out[k] = _redact_config(v)
        return out
    if isinstance(node, list):
        return [_redact_config(x) for x in node]
    return node


class ConfigProvider:
    """``config://<global|project>/<key/path>`` — browse a config JSON as a tree.

    ``global`` → ``~/.config/xlii/config.json``; ``project`` → the cwd project's
    ``.xlii/project.json``. Browseable read-only: ``ls`` lists the keys at a path, ``cat``
    prints the value (pretty JSON for containers, the scalar for leaves). Demonstrates a VFS
    provider backed by *structured data* rather than a directory.
    """

    scheme = "config"

    def resolve(self, address: Address) -> Resolution:
        try:
            node, _ = self._navigate(address)
        except (FileNotFoundError, ValueError) as e:
            return Resolution(ok=False, address=address, kind="config", reason=str(e))
        return Resolution(ok=True, address=address, handle=node, kind="config")

    def stat(self, address: Address) -> Node:
        node, segs = self._navigate(address)
        kind = "container" if isinstance(node, (dict, list)) else "leaf"
        return Node(address=str(address), name=(segs[-1] if segs else address.key), kind=kind)

    def list(self, address: Address) -> "list[Node]":
        node, _ = self._navigate(address)
        if isinstance(node, dict):
            items = list(node.items())
        elif isinstance(node, list):
            items = [(str(i), v) for i, v in enumerate(node)]
        else:
            return []
        out = [
            Node(
                address=self._child_addr(address, str(k)),
                name=str(k),
                kind="container" if isinstance(v, (dict, list)) else "leaf",
            )
            for k, v in items
        ]
        return sorted(out, key=lambda n: (n.kind != "container", n.name.lower()))

    def read(self, address: Address) -> bytes:
        node, segs = self._navigate(address)
        if segs and segs[-1] in _REDACT_KEYS:
            return b"[redacted]\n"
        node = _redact_config(node)
        if isinstance(node, (dict, list)):
            return (json.dumps(node, indent=2, sort_keys=True) + "\n").encode()
        return (("null" if node is None else str(node)) + "\n").encode()

    def exists(self, address: Address) -> bool:
        try:
            self._navigate(address)
            return True
        except (FileNotFoundError, ValueError):
            return False

    def shell_export(self, address: Address) -> ShellExport:
        # Never hand the shell the raw global config.json — it may hold
        # plaintext chat keys. Project config is metadata-only and can stay
        # a path. Keyed nodes always materialize a redacted slice.
        if not address.subpath and address.key != "global":
            path = self._doc_path(address.key)
            if not path.is_file():
                raise FileNotFoundError(f"config://{address.key}: {path} not found")
            return ShellExport(kind="path", path=path)
        node, _ = self._navigate(address)
        suffix = ".json" if isinstance(node, (dict, list)) or address.key == "global" else ".txt"
        if address.subpath and not isinstance(node, (dict, list)):
            suffix = ".txt"
        return ShellExport(kind="content", content=self.read(address), suffix=suffix)

    def _navigate(self, address: Address):
        path = self._doc_path(address.key)
        if not path.is_file():
            raise FileNotFoundError(f"config://{address.key}: {path} not found")
        node = json.loads(path.read_text())
        segs = [s for s in address.subpath.split("/") if s]
        for s in segs:
            if isinstance(node, dict):
                if s not in node:
                    raise FileNotFoundError(f"config://{address.key}: no key {s!r}")
                node = node[s]
            elif isinstance(node, list):
                try:
                    node = node[int(s)]
                except (ValueError, IndexError):
                    raise FileNotFoundError(f"config://{address.key}: no index {s!r}") from None
            else:
                raise FileNotFoundError(f"config://{address.key}: {s!r} is not a container")
        return node, segs

    @staticmethod
    def _doc_path(key: str) -> Path:
        from xlii.config import GLOBAL_CONFIG_FILE, PROJECT_CONFIG_FILE, PROJECT_DIR_NAME

        if key == "global":
            return GLOBAL_CONFIG_FILE
        if key == "project":
            return Path.cwd() / PROJECT_DIR_NAME / PROJECT_CONFIG_FILE
        raise FileNotFoundError(f"config://{key}: unknown root (use 'global' or 'project')")

    @staticmethod
    def _child_addr(address: Address, child_key: str) -> str:
        inner = f"{address.subpath}/{child_key}" if address.subpath else child_key
        return f"config://{address.key}/{inner}"


