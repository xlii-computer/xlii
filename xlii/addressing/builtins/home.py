"""HomeProvider — ``home://`` panel hub (Track I)."""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport
from xlii.home_catalog import HOME_CATALOG


class HomeProvider:
    """``home://`` — the Dock chassis: a directory of panel surfaces.

    Lists the same catalog as the Panel menu (plus Jobs). Selecting a row navigates
    to that scheme; ``‹`` from a scheme root returns here. Read-only hub — not a
    second Options menu.
    """

    scheme = "home"

    def resolve(self, address: Address) -> Resolution:
        key = address.key.strip()
        if not key:
            return Resolution(ok=True, address=address, kind="home")
        for entry in HOME_CATALOG:
            if entry.slug == key:
                return Resolution(ok=True, address=address, kind="home")
        return Resolution(ok=False, address=address, kind="home", reason=f"no home row {key!r}")

    def stat(self, address: Address) -> Node:
        key = address.key.strip()
        if not key:
            return Node(address="home://", name="home", kind="container", extra={"type": "home"})
        for entry in HOME_CATALOG:
            if entry.slug == key:
                return Node(
                    address=f"home://{entry.slug}",
                    name=entry.label,
                    kind="leaf",
                    extra={"type": "home", "target": entry.target},
                )
        return Node(address=str(address), name=key or "home", kind="leaf", extra={"type": "home"})

    def list(self, address: Address) -> list[Node]:
        if address.key.strip():
            return []
        return [
            Node(
                address=f"home://{entry.slug}",
                name=entry.label,
                kind="leaf",
                extra={"type": "home", "target": entry.target},
            )
            for entry in HOME_CATALOG
        ]

    def read(self, address: Address) -> bytes:
        raise IsADirectoryError("home://: panel hub — use ls / open a row")

    def exists(self, address: Address) -> bool:
        key = address.key.strip()
        if not key:
            return True
        return any(entry.slug == key for entry in HOME_CATALOG)

    def shell_export(self, address: Address) -> ShellExport:
        key = address.key.strip()
        if key and not any(entry.slug == key for entry in HOME_CATALOG):
            raise FileNotFoundError(f"home://{key}: no home row")
        return ShellExport(kind="address")  # a hub row denotes a surface, never content
