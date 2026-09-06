"""``faceconfig://`` — face Options → Config session-knobs pane root."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class FaceConfigProvider:
    scheme = "faceconfig"

    def resolve(self, address: Address) -> Resolution:
        return Resolution(ok=True, address=address, kind="faceconfig")

    def stat(self, address: Address) -> Node:
        return Node(
            address="faceconfig://",
            name="config",
            kind="container",
            extra={"type": "faceconfig"},
        )

    def exists(self, address: Address) -> bool:
        return True

    def list(self, address: Address) -> list[Node]:
        del address
        return []

    def read(self, address: Address) -> bytes:
        raise IsADirectoryError("faceconfig://: open as a panel")

    def shell_export(self, address: Address) -> ShellExport:
        del address
        return ShellExport(kind="address")
