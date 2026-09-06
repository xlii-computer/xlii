"""Per-wire protocol handlers behind :class:`xlii.remotefs.RemoteFsConnection`.

The handler seam (proposals/remote-backends.md): every remote wire lives in its
own module here, so adding a backend is **one new file + one registry line** —
never another ``if protocol == …`` branch inside the connection. The connection
stays a thin delegator that owns the spec, the vault secret, and the
protocol-neutral compositions (``stat``/``makedirs``/``rmtree``); a handler owns
the sockets and implements only the primitives.

Handler interface (duck-typed; a handler is constructed with the owning
connection and reads its attrs — ``host port user key_path insecure name`` and
the private ``_secret``/``_timeout``):

* ``connect() -> None`` — open the wire (raw; the connection normalizes odd
  login/handshake exception types into ``OSError``)
* ``close() -> None`` — drop it (idempotent, never raises)
* ``connected: bool`` — is the wire live?
* ``listdir(path) -> list[(name, is_dir, size)]`` — raw entries; the connection
  applies the dirs-carry-``size=None`` normalization for every handler
* ``read(path) -> bytes`` · ``write(path, data)`` · ``delete(path)`` ·
  ``rmdir(path)`` · ``mkdir(path)``

Error contract: a missing path raises ``FileNotFoundError``; other remote
failures raise ``OSError``. Optional third-party clients are imported **inside**
the handler (lazy) so a missing extra yields a clean install hint, never an
import crash — the ``paramiko`` precedent.
"""

from __future__ import annotations

from importlib import import_module

# protocol → module that defines its handler class (as HANDLER). Append-only:
# a new backend adds its line here + its module file, and touches nothing else
# in this package. ftp + ftps share a module (same ftplib wire, TLS optional).
HANDLER_MODULES: dict[str, str] = {
    "ftp": "xlii.remote_handlers.ftp",
    "ftps": "xlii.remote_handlers.ftp",
    "sftp": "xlii.remote_handlers.sftp",
    "webdav": "xlii.remote_handlers.webdav",
    "smb": "xlii.remote_handlers.smb",
}


def get_handler_class(protocol: str):
    """The handler class for ``protocol`` (lazy module import). Raises
    ``ValueError`` for a protocol no installed handler claims — the same error
    family :func:`xlii.remotefs.add_connection` uses for validation."""
    mod_path = HANDLER_MODULES.get(protocol)
    if mod_path is None:
        raise ValueError(
            f"unknown protocol {protocol!r} (expected one of {tuple(HANDLER_MODULES)})"
        )
    return import_module(mod_path).HANDLER
