"""``canvas://`` — the work surface over the same store as ``artifacts://``.

Artifacts is the pile. Canvas is **one** make (image or PDF) the rider and
agent are looking at. Opening a canvas-worthy artifact puts it here.
"""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution, ShellExport
from xlii.addressing.builtins.artifacts import ArtifactsProvider


def _canvas(node: Node) -> Node:
    addr = node.address.replace("artifacts://", "canvas://", 1)
    return Node(address=addr, name=node.name, kind=node.kind,
                size=node.size, extra=node.extra)


class CanvasProvider:
    scheme = "canvas"

    def __init__(self) -> None:
        self._art = ArtifactsProvider()

    def _via(self, address: Address) -> Address:
        return Address(scheme="artifacts", target=address.target,
                       query=address.query, raw=f"artifacts://{address.target}")

    def resolve(self, address: Address) -> Resolution:
        r = self._art.resolve(self._via(address))
        return Resolution(ok=r.ok, address=address, kind="canvas",
                          path=r.path, reason=r.reason)

    def stat(self, address: Address) -> Node:
        if not address.key.strip():
            return Node(address="canvas://", name="canvas", kind="container")
        return _canvas(self._art.stat(self._via(address)))

    def list(self, address: Address) -> "list[Node]":
        if address.key.strip():
            return []
        return [_canvas(n) for n in self._art.list(Address(scheme="artifacts"))]

    def read(self, address: Address) -> bytes:
        return self._art.read(self._via(address))

    def exists(self, address: Address) -> bool:
        if not address.key.strip():
            return True
        return self._art.exists(self._via(address))

    def shell_export(self, address: Address) -> ShellExport:
        return self._art.shell_export(self._via(address))
