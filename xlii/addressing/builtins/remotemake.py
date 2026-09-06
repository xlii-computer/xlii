"""``remotemake://`` — remote-connection add form."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class RemoteMakeProvider:
    scheme = "remotemake"

    def resolve(self, address: Address) -> Resolution:
        return Resolution(ok=True, address=address, kind="remotemake")

    def stat(self, address: Address) -> Node:
        return Node(
            address="remotemake://",
            name="remote form",
            kind="container",
            extra={"type": "remotemake"},
        )

    def exists(self, address: Address) -> bool:
        return True

    def list(self, address: Address) -> list[Node]:
        del address
        return []

    def read(self, address: Address) -> bytes:
        raise IsADirectoryError("remotemake://: open as a panel")

    def shell_export(self, address: Address) -> ShellExport:
        del address
        return ShellExport(kind="address")
