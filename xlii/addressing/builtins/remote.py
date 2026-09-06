"""RemoteFsProvider provider."""
from __future__ import annotations


from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)

from xlii.addressing.builtins._helpers import (
    _reject_traversal,
)

class RemoteFsProvider:
    """A remote scheme (``ftp://`` ``sftp://`` ``dav://`` ``smb://``) — a configured host's
    filesystem, browseable + writable.

    A remote host is just another filesystem, so this is one full
    :class:`~xlii.addressing.WritableVfs` rather than a bolt-on transfer tool. The address
    names a **connection, never credentials**: ``address.key`` is a connection configured in
    ``GlobalConfig.ftp_connections`` (secret in the vault), ``address.subpath`` is the remote
    path — the exact model :class:`ProjectProvider` uses. Three levels:

    * ``<scheme>://`` — the **server picker**: this scheme's configured connections as
      containers (filtered by the honest protocol→scheme map, locked D2 of
      proposals/remote-backends.md — ``dav://`` lists only WebDAV connections).
    * ``<scheme>://<name>`` — connects (lazily, via :data:`xlii.remotefs.manager`) and lists
      the remote login home.
    * ``<scheme>://<name>/<path>`` — a remote dir (container) or file (leaf) — read, write,
      attach.

    Registered once per remote scheme over ONE shared connection registry — the scheme is
    the doorway; the connection's stored ``protocol`` decides the wire (the handler seam in
    :mod:`xlii.remote_handlers`). **Pickers filter; name-resolution stays lenient** — an
    address like ``ftp://box`` still resolves a connection named ``box`` whatever its
    protocol, so saved addresses/attachments never break; every address xlii *emits* uses
    the honest scheme. The live socket lives behind
    :class:`~xlii.remotefs.RemoteFsManager` (lazy-open, reused, ``/remote close``), so this
    provider stays a thin stateless-looking wrapper and the pane invariant holds.
    I/O blocks with timeouts (v1) — a slow host briefly stalls the surface, never hangs it.
    """

    def __init__(self, scheme: str = "ftp"):
        self.scheme = scheme

    def _own_names(self, m) -> "list[str]":
        """This scheme's connections — the picker filter (honest schemes, D2).
        ``remote://`` is the exception by design: the manager's own union view,
        listing EVERY connection (each node still addressed by its honest wire
        scheme) — it's what the TUI's remote doorway opens, so no protocol's
        hosts can silently vanish from that surface."""
        if self.scheme == "remote":
            return list(m.names())
        from xlii.remotefs import protocols_for_scheme

        mine = set(protocols_for_scheme(self.scheme))
        return [n for n in m.names()
                if (m.spec(n) or {}).get("protocol", "ftp") in mine]

    @staticmethod
    def _manager():
        from xlii.remotefs import manager

        return manager

    def resolve(self, address: Address) -> Resolution:
        name = address.key.strip()
        if not name:
            return Resolution(ok=True, address=address, kind="remotefs")  # the server picker
        ok = name in self._manager().names()
        return Resolution(ok=ok, address=address, kind="remotefs",
                          reason="" if ok else f"no configured remote connection {name!r}")

    def stat(self, address: Address) -> Node:
        name = address.key.strip()
        if not name:
            return Node(address=f"{self.scheme}://", name=self.scheme, kind="container",
                        extra={"type": "remotefs"})
        if not address.subpath:
            return Node(address=str(address), name=name, kind="container",
                        extra={"type": "remote-host"})
        is_dir, size = self._manager().get(name).stat(address.subpath)
        leaf = address.subpath.rstrip("/").rsplit("/", 1)[-1]
        return Node(address=str(address), name=leaf,
                    kind="container" if is_dir else "leaf", size=size,
                    extra={"type": "remote-file"})

    def list(self, address: Address) -> "list[Node]":
        m = self._manager()
        name = address.key.strip()
        if not name:
            # The picker: only this scheme's own connections (D2 — dav:// lists
            # WebDAV hosts, not the FTP ones; remote:// lists them all). Every
            # node is addressed by its connection's HONEST scheme — for a wire
            # scheme that's self.scheme (filtered), for remote:// it's per-node.
            from xlii.remotefs import scheme_for_protocol

            return [
                Node(address=f"{scheme_for_protocol((m.spec(n) or {}).get('protocol', 'ftp'))}://{n}",
                     name=n, kind="container",
                     extra={"type": "remote-host",
                            "protocol": (m.spec(n) or {}).get("protocol", "ftp"),
                            "host": (m.spec(n) or {}).get("host", "")})
                for n in self._own_names(m)
            ]
        conn = m.get(name)
        base = f"{self.scheme}://{name}"
        if address.subpath:
            base += f"/{address.subpath.strip('/')}"
        nodes = [
            Node(address=f"{base}/{n}", name=n,
                 kind="container" if is_dir else "leaf", size=size,
                 extra={"type": "remote-file"})
            for n, is_dir, size in conn.listdir(address.subpath)
        ]
        return sorted(nodes, key=lambda n: (n.kind != "container", n.name.lower()))

    def read(self, address: Address) -> bytes:
        name = address.key.strip()
        if not name:
            raise IsADirectoryError(f"{self.scheme}://: the server picker — use ls")
        if not address.subpath:
            raise IsADirectoryError(f"{address}: a remote home directory — use ls")
        return self._manager().get(name).read(_reject_traversal(address.subpath))

    def exists(self, address: Address) -> bool:
        name = address.key.strip()
        if not name:
            return True
        m = self._manager()
        if name not in m.names():
            return False
        if not address.subpath:
            return True
        try:
            return m.get(name).exists(address.subpath)
        except (OSError, RuntimeError):
            return False

    def shell_export(self, address: Address) -> ShellExport:
        name = address.key.strip()
        if not name or not address.subpath:
            # The picker / a remote login home — nothing local to hand a shell.
            return ShellExport(kind="address")
        sub = _reject_traversal(address.subpath)
        try:
            is_dir, _size = self._manager().get(name).stat(sub)
            if is_dir:
                return ShellExport(kind="address")  # a remote dir can't materialize locally
            # Fetch the remote bytes (network I/O, timeouts apply) into a local
            # snapshot — a read-only freeze-frame; edits never round-trip to the host.
            content = self.read(address)
        except (OSError, RuntimeError):
            raise
        except Exception as e:
            # Post-connect wire failures surface protocol types (paramiko's
            # SSHException is NOT an OSError) — remap to the family the seam maps,
            # the same normalization the smb/webdav handlers apply per-op.
            raise OSError(f"{address}: {e}") from e
        from pathlib import PurePosixPath

        suffix = PurePosixPath(sub).suffix or ".bin"
        return ShellExport(kind="content", content=content, suffix=suffix)

    def write(self, address: Address, data: bytes) -> None:
        name, sub = address.key.strip(), _reject_traversal(address.subpath)
        if not name or not sub:
            raise IsADirectoryError(f"{address}: write needs a remote file path")
        self._manager().get(name).write(sub, data)

    def delete(self, address: Address, recursive: bool = False) -> None:
        name, sub = address.key.strip(), _reject_traversal(address.subpath)
        if not name or not sub:
            raise IsADirectoryError(f"{address}: delete needs a remote path (not the picker/home)")
        conn = self._manager().get(name)
        is_dir, _size = conn.stat(sub)
        if is_dir:
            if recursive:
                conn.rmtree(sub)
            else:
                conn.rmdir(sub)  # fails on a non-empty dir, matching file:// semantics
        else:
            conn.delete(sub)

    def mkdir(self, address: Address) -> None:
        name, sub = address.key.strip(), _reject_traversal(address.subpath)
        if not name or not sub:
            raise IsADirectoryError(f"{address}: mkdir needs a remote path")
        self._manager().get(name).makedirs(sub)


