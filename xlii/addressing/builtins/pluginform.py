"""``pluginform://<plugin>/<action>`` — closed HTML form for one action."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class PluginFormProvider:
    scheme = "pluginform"

    def resolve(self, address: Address) -> Resolution:
        return Resolution(ok=True, address=address, kind="pluginform")

    def stat(self, address: Address) -> Node:
        target = address.target.strip().strip("/")
        return Node(
            address=f"pluginform://{target}" if target else "pluginform://",
            name=target or "plugin form",
            kind="container",
            extra={"type": "pluginform"},
        )

    def exists(self, address: Address) -> bool:
        del address
        return True

    def list(self, address: Address) -> list[Node]:
        del address
        return []

    def read(self, address: Address) -> bytes:
        raise IsADirectoryError("pluginform://: open as a panel")

    def shell_export(self, address: Address) -> ShellExport:
        del address
        return ShellExport(kind="address")
