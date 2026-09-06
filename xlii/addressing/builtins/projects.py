"""ProjectsProvider provider."""
from __future__ import annotations

from urllib.parse import quote, unquote

from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


def project_address(name: str, path: str = "") -> str:
    """Encode a registry name (may contain ``/``) as a ``projects://`` address.

    Duplicate names (pytest leftovers) are disambiguated with ``?p=`` path.
    """
    addr = f"projects://{quote(name, safe='')}"
    if path:
        addr += f"?p={quote(path, safe='')}"
    return addr


def project_name_from_target(target: str) -> str:
    """Decode the registry name from a ``projects://`` target segment."""
    return unquote((target or "").strip())


def project_path_from_address(address: str) -> str:
    """The ``?p=`` path on a ``projects://`` address, if any."""
    from xlii.addressing import Address

    try:
        return unquote(Address.parse(address).query.get("p", "") or "")
    except Exception:
        return ""


class ProjectsProvider:
    """``projects://`` — the node-local project registry as a browseable list
    (typed-workbenches B5: the home launch state's data source).

    ``projects://`` lists every registered project (one leaf per registry
    entry); ``projects://<name>`` reads that project's card (path, workbench
    type, collection). Read-only through the VFS — projects are created by
    ``/workbench new`` / ``xlii init`` and opened by ``/project switch``.
    Node-local only: remote-node projects are a fabric question, not this
    provider's (the B5 proposal note)."""

    scheme = "projects"

    def _entries(self):
        from xlii.fabric_projects import federated_visible
        from xlii.registry import Registry

        return federated_visible(Registry.load().entries)

    def _find(self, name: str):
        for e in self._entries():
            if e.name == name:
                return e
        return None

    def _name(self, address: Address) -> str:
        return project_name_from_target(address.target)

    def resolve(self, address: Address) -> Resolution:
        name = self._name(address)
        if not name:
            return Resolution(ok=True, address=address, kind="projects")
        ok = self._find(name) is not None
        return Resolution(ok=ok, address=address, kind="projects",
                          reason="" if ok else f"no registered project {name!r}")

    def stat(self, address: Address) -> Node:
        name = self._name(address)
        return Node(address=str(address), name=name or "projects",
                    kind="container" if not name else "leaf",
                    extra={"type": "project"})

    def exists(self, address: Address) -> bool:
        name = self._name(address)
        return True if not name else self._find(name) is not None

    def shell_export(self, address: Address) -> ShellExport:
        name = self._name(address)
        if not name:
            return ShellExport(kind="address")  # the root is a virtual list — no one dir
        entry = self._find(name)
        if entry is None:
            raise FileNotFoundError(f"projects://{name}: no registered project")
        from pathlib import Path

        return ShellExport(kind="path", path=Path(entry.path))

    def list(self, address: Address) -> "list[Node]":
        return [
            Node(address=project_address(e.name), name=e.name, kind="leaf",
                 extra={"type": "project", "path": e.path})
            for e in sorted(self._entries(), key=lambda x: x.name.lower())
        ]

    def read(self, address: Address) -> bytes:
        name = self._name(address)
        entry = self._find(name)
        if entry is None:
            raise FileNotFoundError(f"no registered project {name!r}")
        from pathlib import Path

        from xlii.workbench import load_active_type

        wb = load_active_type(Path(entry.path) / ".xlii") if entry.path else "chat"
        lines = [
            f"name: {entry.name}",
            f"path: {entry.path}",
            f"workbench: {wb}",
            f"collection: {entry.collection_id or 'local-only'}",
            f"registered: {entry.created_at}",
            "",
            f"open: /project switch {entry.name}",
        ]
        return ("\n".join(lines) + "\n").encode()
