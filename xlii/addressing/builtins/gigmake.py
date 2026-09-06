"""``gigmake://`` — gigwork add / jam form."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class GigMakeProvider:
    scheme = "gigmake"

    def resolve(self, address: Address) -> Resolution:
        return Resolution(ok=True, address=address, kind="gigmake")

    def stat(self, address: Address) -> Node:
        return Node(
            address="gigmake://",
            name="gigwork form",
            kind="container",
            extra={"type": "gigmake"},
        )

    def exists(self, address: Address) -> bool:
        return True

    def list(self, address: Address) -> list[Node]:
        del address
        return []

    def read(self, address: Address) -> bytes:
        raise IsADirectoryError("gigmake://: open as a panel")

    def shell_export(self, address: Address) -> ShellExport:
        del address
        return ShellExport(kind="address")
