"""``bindmake://`` — task chrome bind picker."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class BindMakeProvider:
    scheme = "bindmake"

    def resolve(self, address: Address) -> Resolution:
        return Resolution(ok=True, address=address, kind="bindmake")

    def stat(self, address: Address) -> Node:
        return Node(
            address="bindmake://",
            name="bind chrome",
            kind="container",
            extra={"type": "bindmake"},
        )

    def exists(self, address: Address) -> bool:
        return True

    def list(self, address: Address) -> list[Node]:
        del address
        return []

    def read(self, address: Address) -> bytes:
        raise IsADirectoryError("bindmake://: open as a panel")

    def shell_export(self, address: Address) -> ShellExport:
        del address
        return ShellExport(kind="address")
