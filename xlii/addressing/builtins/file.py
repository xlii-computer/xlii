"""FileProvider provider."""
from __future__ import annotations

from pathlib import Path

from xlii import atomicio
from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)

from xlii.addressing.builtins._helpers import (
    _fs_node,
    _fs_list,
    _fs_delete,
)

class FileProvider:
    """``file://<path>`` — a filesystem path (abs / rel / ``~``). Browseable + writable."""

    scheme = "file"

    def resolve(self, address: Address) -> Resolution:
        p = Path(address.target).expanduser()
        try:
            resolved = p.resolve()
        except OSError:
            resolved = p
        exists = resolved.exists()
        return Resolution(
            ok=exists,
            address=address,
            handle=resolved,
            kind="file",
            path=resolved,
            reason="" if exists else "no such path",
        )

    def stat(self, address: Address) -> Node:
        p = self._path(address)
        return _fs_node(p, f"file://{p}")

    def list(self, address: Address) -> "list[Node]":
        return _fs_list(self._path(address), lambda c: f"file://{c}")

    def read(self, address: Address) -> bytes:
        return self._path(address).read_bytes()

    def exists(self, address: Address) -> bool:
        return self._path(address).exists()

    def write(self, address: Address, data: bytes) -> None:
        # Atomic + creates parent dirs (write_bytes_atomic does parents=True), so a
        # write to a new nested path no longer FileNotFoundErrors and a crash mid-write
        # can't leave a truncated file. 0644 for user-facing files (not the 0600 default).
        atomicio.write_bytes_atomic(self._path(address), data, mode=0o644)

    def delete(self, address: Address, recursive: bool = False) -> None:
        _fs_delete(self._path(address), recursive)

    def mkdir(self, address: Address) -> None:
        self._path(address).mkdir(parents=True, exist_ok=False)

    def shell_export(self, address: Address) -> ShellExport:
        # A path designator — export even when the file doesn't exist yet (a legitimate
        # destination arg), matching resolve() setting path on a miss.
        return ShellExport(kind="path", path=self._path(address))

    @staticmethod
    def _path(address: Address) -> Path:
        return Path(address.target).expanduser().resolve()


