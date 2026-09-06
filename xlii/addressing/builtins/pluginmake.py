"""``pluginmake://`` — face/TUI plugin-scaffold pane root."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class PluginMakeProvider:
    scheme = "pluginmake"

    def resolve(self, address: Address) -> Resolution:
        return Resolution(ok=True, address=address, kind="pluginmake")

    def stat(self, address: Address) -> Node:
        raw = address.raw or (
            f"pluginmake://{address.target}" if address.target else "pluginmake://"
        )
        name = address.key or "plugin maker"
        return Node(
            address=raw,
            name=name,
            kind="container",
            extra={"type": "pluginmake", "plugin": address.key},
        )

    def exists(self, address: Address) -> bool:
        return True

    def list(self, address: Address) -> list[Node]:
        del address
        return []

    def read(self, address: Address) -> bytes:
        raise IsADirectoryError("pluginmake://: open as a panel")

    def shell_export(self, address: Address) -> ShellExport:
        del address
        return ShellExport(kind="address")
