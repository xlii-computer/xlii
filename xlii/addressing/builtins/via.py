"""``via://<node>/<inner-address>`` — browse a remote the node can see, this box can't.

A fabric pointer's program often lives on a connection that exists only on that
body (``sftp://appbox/srv/apps/fuel.xlii-code.com``). This desk has the node
but not ``appbox``. Files still has to list (and now write) the program.

``via://`` hops VFS ops over the already-open SFTP/SSH session. It does not
mint a connection or copy a key. Writes go to the inner address (the published
app), not the throne tree and not the local stub.

    via://xliiec2/sftp://appbox/srv/apps/fuel.xlii-code.com
    key=xliiec2  subpath=sftp://appbox/srv/apps/fuel.xlii-code.com
"""

from __future__ import annotations

from xlii.addressing import Address, Node, Resolution
from xlii.addressing.builtins.via_hop import hop_op

__all__ = ["ViaProvider", "hop_op", "split_via", "wrap_via"]


def wrap_via(node: str, inner: str) -> str:
    """``via://<node>/<inner>``, refusing to double-wrap."""
    node = (node or "").strip()
    inner = (inner or "").strip()
    if not node or not inner:
        return ""
    if inner.startswith("via://"):
        return inner
    return f"via://{node}/{inner}"


def split_via(address: Address) -> tuple[str, str]:
    """``(node_conn, inner_address)`` or ``("", "")`` if this isn't a hop."""
    if address.scheme != "via":
        return "", ""
    node = address.key.strip()
    inner = (address.subpath or "").strip()
    if not node or "://" not in inner:
        return "", ""
    return node, inner


def _node_from(item: dict, node: str) -> Node:
    inner = str(item.get("address") or "")
    return Node(
        address=wrap_via(node, inner) or inner,
        name=str(item.get("name") or ""),
        kind="container" if item.get("kind") == "container" else "leaf",
        size=item.get("size"),
        extra={"type": "via-file", "inner": inner},
    )


def _need_inner(address: Address) -> tuple[str, str]:
    node, inner = split_via(address)
    if not node or not inner:
        raise IsADirectoryError("via://: needs via://<node>/<scheme://path>")
    return node, inner


class ViaProvider:
    """Hop onto another body's VFS (read + write of the inner address)."""

    scheme = "via"

    def resolve(self, address: Address) -> Resolution:
        node, inner = split_via(address)
        if not node:
            return Resolution(
                ok=False, address=address, reason="via:// needs via://<node>/<scheme://…>",
            )
        try:
            from xlii.address_book import is_known_connection

            ok = is_known_connection(node)
        except Exception:
            ok = False
        return Resolution(
            ok=ok, address=address, kind="via",
            reason="" if ok else f"no configured remote connection {node!r}",
        )

    def stat(self, address: Address) -> Node:
        node, inner = split_via(address)
        if not node:
            return Node(address=str(address), name="via", kind="container",
                        extra={"type": "via"})
        data = hop_op(node, "stat", inner)
        item = data.get("node") if isinstance(data.get("node"), dict) else {}
        if not item:
            leaf = inner.rstrip("/").rsplit("/", 1)[-1]
            return Node(address=str(address), name=leaf, kind="container",
                        extra={"type": "via-file", "inner": inner})
        return _node_from(item, node)

    def list(self, address: Address) -> "list[Node]":
        node, inner = split_via(address)
        if not node:
            return []
        data = hop_op(node, "list", inner)
        nodes = [
            _node_from(item, node)
            for item in (data.get("nodes") or [])
            if isinstance(item, dict)
        ]
        return sorted(nodes, key=lambda n: (n.kind != "container", n.name.lower()))

    def read(self, address: Address) -> bytes:
        import base64

        node, inner = split_via(address)
        if not node:
            raise IsADirectoryError("via://: the hop picker — use a node")
        data = hop_op(node, "read", inner)
        try:
            return base64.b64decode(data.get("b64") or "")
        except Exception as e:
            raise OSError(f"{address}: bad hop payload ({e})") from e

    def exists(self, address: Address) -> bool:
        node, inner = split_via(address)
        if not node:
            return True
        try:
            return bool(hop_op(node, "exists", inner).get("exists"))
        except OSError:
            return False

    def write(self, address: Address, data: bytes) -> None:
        import base64

        node, inner = _need_inner(address)
        hop_op(node, "write", inner, extra={
            "b64": base64.b64encode(data).decode("ascii"),
        })

    def mkdir(self, address: Address) -> None:
        node, inner = _need_inner(address)
        hop_op(node, "mkdir", inner)

    def delete(self, address: Address, recursive: bool = False) -> None:
        node, inner = _need_inner(address)
        hop_op(node, "delete", inner, extra={"recursive": bool(recursive)})

    def shell_export(self, address: Address):
        from pathlib import PurePosixPath

        from xlii.addressing import ShellExport

        node, inner = split_via(address)
        if not node or not inner:
            return ShellExport(kind="address")
        try:
            if self.stat(address).kind == "container":
                return ShellExport(kind="address")
            content = self.read(address)
        except (OSError, RuntimeError):
            raise
        except Exception as e:
            raise OSError(f"{address}: {e}") from e
        return ShellExport(
            kind="content", content=content,
            suffix=PurePosixPath(inner).suffix or ".bin",
        )
