"""``install://`` — stamp a fabric node (wizard)."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class InstallProvider:
    scheme = "install"

    def resolve(self, address: Address) -> Resolution:
        return Resolution(ok=True, address=address, kind="install")

    def stat(self, address: Address) -> Node:
        return Node(
            address="install://",
            name="install node",
            kind="container",
            extra={"type": "install"},
        )

    def exists(self, address: Address) -> bool:
        return True

    def list(self, address: Address) -> list[Node]:
        del address
        return []

    def read(self, address: Address) -> bytes:
        raise IsADirectoryError("install://: open as a panel")

    def shell_export(self, address: Address) -> ShellExport:
        del address
        return ShellExport(kind="address")
