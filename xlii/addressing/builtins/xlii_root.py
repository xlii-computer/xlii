"""XliiProvider provider."""
from __future__ import annotations


from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
    providers,
    supports_vfs,
    supports_write,
)


class XliiProvider:
    """``xlii://`` — the kernel mounted as its own read-only VFS root (introspection).

    Cashes out the locked decision that *the kernel is itself a VFS root*: the address
    space can address the addresser. A small synthetic tree, no directory behind it::

        xlii://                  the kernel
        ├── version              the running version (e.g. 0.5.0)
        └── schemes/             every registered scheme = every provider mounted
            ├── file             cat it → that scheme's capabilities (browseable/writable)
            ├── project
            └── …                including ``xlii`` itself, reflexively

    Read-only (a :class:`VfsProvider`, not writable): you browse the kernel, you don't
    edit it through ``cp``.
    """

    scheme = "xlii"

    def resolve(self, address: Address) -> Resolution:
        try:
            kind = self._kind(self._segs(address))
        except FileNotFoundError as e:
            return Resolution(ok=False, address=address, kind="xlii", reason=str(e))
        return Resolution(ok=True, address=address, handle=str(address), kind="xlii", detail=kind)

    def stat(self, address: Address) -> Node:
        segs = self._segs(address)
        kind = self._kind(segs)
        return Node(address=str(address), name=(segs[-1] if segs else "xlii"), kind=kind)

    def list(self, address: Address) -> "list[Node]":
        segs = self._segs(address)
        if self._kind(segs) != "container":
            return []  # listing a leaf is empty, mirroring the fs providers
        if not segs:
            pairs = [("schemes", "container"), ("version", "leaf")]
        else:  # segs == ["schemes"]
            pairs = [(name, "leaf") for name in providers()]
        out = [Node(address=self._child_addr(segs, n), name=n, kind=k) for n, k in pairs]
        return sorted(out, key=lambda n: (n.kind != "container", n.name.lower()))

    def read(self, address: Address) -> bytes:
        segs = self._segs(address)
        kind = self._kind(segs)  # raises FileNotFoundError on a miss
        if kind == "container":
            raise IsADirectoryError(f"xlii://{'/'.join(segs)}: is a container — use ls")
        if segs == ["version"]:
            from xlii import __version__

            return (__version__ + "\n").encode()
        # segs == ["schemes", <name>] — report that scheme's capabilities.
        name = segs[1]
        lines = [
            f"scheme: {name}",
            f"browseable: {'yes' if supports_vfs(name) else 'no'}",
            f"writable: {'yes' if supports_write(name) else 'no'}",
        ]
        return ("\n".join(lines) + "\n").encode()

    def exists(self, address: Address) -> bool:
        try:
            self._kind(self._segs(address))
            return True
        except FileNotFoundError:
            return False

    def shell_export(self, address: Address) -> ShellExport:
        segs = self._segs(address)
        if self._kind(segs) == "container":  # raises FileNotFoundError on a miss
            return ShellExport(kind="address")  # a synthetic tree has no dir to hand over
        # Leaves are derived from live process state — snapshot, regenerate per call.
        return ShellExport(kind="content", content=self.read(address), suffix=".txt")

    @staticmethod
    def _segs(address: Address) -> "list[str]":
        return [s for s in address.target.split("/") if s]

    @staticmethod
    def _kind(segs: "list[str]") -> str:
        """The kind at a path in the synthetic tree, or FileNotFoundError if it's not there."""
        if not segs:
            return "container"
        if segs == ["version"] or segs == ["schemes"]:
            return "leaf" if segs == ["version"] else "container"
        if len(segs) == 2 and segs[0] == "schemes" and segs[1] in providers():
            return "leaf"
        raise FileNotFoundError(f"xlii://{'/'.join(segs)}: no such address")

    @staticmethod
    def _child_addr(segs: "list[str]", name: str) -> str:
        return "xlii://" + "/".join(segs + [name])


