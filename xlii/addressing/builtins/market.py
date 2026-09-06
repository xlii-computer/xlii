"""MarketProvider — public offers wall (``market://``). Beacons + rep, never tickets."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport


class MarketProvider:
    scheme = "market"

    def resolve(self, address: Address) -> Resolution:
        return Resolution(ok=True, address=address, kind="market")

    def stat(self, address: Address) -> Node:
        key = address.key.strip()
        return Node(
            address=str(address),
            name=key or "market",
            kind="container" if not key else "leaf",
            extra={"type": "market"},
        )

    def list(self, address: Address) -> "list[Node]":
        return []

    def read(self, address: Address) -> bytes:
        if not address.key.strip():
            raise IsADirectoryError("market://: offers wall — use ls")
        return b""

    def exists(self, address: Address) -> bool:
        return not address.key.strip()

    def shell_export(self, address: Address) -> ShellExport:
        return ShellExport(kind="address")
