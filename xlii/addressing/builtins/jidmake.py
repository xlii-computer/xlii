"""``jidmake://`` — XMPP address mint form."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class JidMakeProvider:
    scheme = "jidmake"

    def resolve(self, address: Address) -> Resolution:
        return Resolution(ok=True, address=address, kind="jidmake")

    def stat(self, address: Address) -> Node:
        return Node(
            address="jidmake://",
            name="jid form",
            kind="container",
            extra={"type": "jidmake"},
        )

    def exists(self, address: Address) -> bool:
        return True

    def list(self, address: Address) -> list[Node]:
        del address
        return []

    def read(self, address: Address) -> bytes:
        raise IsADirectoryError("jidmake://: open as a panel")

    def shell_export(self, address: Address) -> ShellExport:
        del address
        return ShellExport(kind="address")
