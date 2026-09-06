"""Remote filesystems — the connection layer behind the remote ``scheme://`` providers.

A remote host is *just another filesystem*: this module owns the live wires
(:class:`RemoteFsConnection`, one per configured host) and the registry that hands them out
(:class:`RemoteFsManager`), so the provider in :mod:`xlii.addressing._builtin_providers` can stay
a thin, stateless-looking wrapper (a pane is a pure projection of ``(address, selection)`` — the
socket lives *behind* the provider, here).

Protocol-neutral by design (the one user-facing manager command is ``/remote``): each **named
connection** stores its own ``protocol`` — the scheme is the doorway; the connection decides the
wire. The wires themselves live in :mod:`xlii.remote_handlers`, **one module per protocol**
(proposals/remote-backends.md): the connection is a thin delegator that owns the spec, the vault
secret, and the protocol-neutral compositions, while a handler owns the sockets and implements
only the primitives. Adding a backend = one handler module + one registry line — never another
``if protocol == …`` branch here. Optional third-party clients import lazily inside their
handler (the ``paramiko`` precedent), so the stdlib FTP path never pays for a dep it doesn't use.

Each protocol is addressed by the scheme matching its actual wire
(:func:`scheme_for_protocol` — ``ftp/ftps → ftp://``, ``sftp → sftp://``, ``webdav → dav://``,
``smb → smb://``): no scheme ever lies about the wire.

Credentials follow the exact split xAI chat keys use: non-secret fields (host/port/user/
protocol/``vault_ref`` + per-protocol extras, see :data:`SPEC_FIELDS`) live in
``GlobalConfig.ftp_connections`` (field name kept for config stability); the one secret per
connection lives encrypted in the vault under the :data:`VAULT_FTP_NS` namespace. Addresses
name a *connection*, never credentials — no ``user:pass@host`` ever appears in an address.

v1 boundaries: blocking I/O with sane timeouts (a slow host briefly stalls the surface; the
``jobs://`` offload is the planned follow-up), ``publish`` is always explicit (no watch/sync).
"""

from __future__ import annotations

import os
import posixpath
from pathlib import Path
from typing import Callable, Optional

from xlii import remote_handlers

# Namespace under which remote-connection secrets live in the encrypted vault
# ({namespace: {ref: secret}} — the same layout chat keys use under
# xlii:chat-keys). Keep this stable — changing it strands stored passwords.
VAULT_FTP_NS = "xlii:ftp"

# The available protocols = the installed handler registry (append-only there).
PROTOCOLS = tuple(remote_handlers.HANDLER_MODULES)

# protocol → the scheme that addresses it. THE honest-scheme map (locked D2,
# proposals/remote-backends.md): a connection is addressed by the scheme matching
# its actual wire, so no scheme ever lies. webdav/smb rows are pre-seated for the
# W/M backends (their handlers register in remote_handlers.HANDLER_MODULES); s3
# is reserved for its own round.
_PROTOCOL_SCHEME: dict[str, str] = {
    "ftp": "ftp", "ftps": "ftp", "sftp": "sftp",
    "webdav": "dav", "smb": "smb",
}

# Every scheme that names a remote connection (drives attach/publish guards and
# provider registration). Order: stable, first-seen.
REMOTE_SCHEMES = tuple(dict.fromkeys(_PROTOCOL_SCHEME.values()))

# Default port per protocol (webdav's 443 assumes https, the required default).
DEFAULT_PORTS: dict[str, int] = {
    "ftp": 21, "ftps": 21, "sftp": 22, "webdav": 443, "smb": 445,
}

# Per-protocol OPTIONAL spec fields accepted by add_connection beyond the shared
# host/port/user set. One central table (not per-handler) so `add` validation and
# the guided prompts read one source; s3's row is reserved for the next round.
SPEC_FIELDS: dict[str, tuple] = {
    "ftp": ("insecure",),
    "ftps": ("insecure",),
    "sftp": ("key_path",),
    "webdav": ("base_url", "auth", "insecure"),
    "smb": ("share", "domain"),
    "s3": ("bucket", "region", "endpoint", "access_key"),
}


def scheme_for_protocol(protocol: str) -> str:
    """The honest scheme for a wire protocol (unknown → ``ftp``, the legacy root)."""
    return _PROTOCOL_SCHEME.get((protocol or "").lower(), "ftp")


def protocols_for_scheme(scheme: str) -> tuple:
    """Every protocol a scheme's bare picker lists (inverse of the map above)."""
    return tuple(p for p, s in _PROTOCOL_SCHEME.items() if s == scheme)


# Connect + per-operation socket timeout (seconds). Blocking-v1 discipline:
# a dead host must fail fast, not hang the surface.
DEFAULT_TIMEOUT = 15.0


def _join(base: str, name: str) -> str:
    """Join remote path segments (always ``/``-separated, relative to the login home)."""
    base = (base or "").strip("/")
    name = (name or "").strip("/")
    if not base:
        return name
    return f"{base}/{name}" if name else base


class RemoteFsConnection:
    """One configured remote host, with a protocol-uniform surface.

    A thin delegator (the handler seam, proposals/remote-backends.md): the wire
    lives in a per-protocol handler from :mod:`xlii.remote_handlers`, built
    lazily on first :meth:`connect`. ``listdir/read/write/delete/rmdir/mkdir``
    delegate to it; ``stat``/``makedirs``/``rmtree`` are protocol-neutral
    compositions of those primitives (so a test fake only has to override the
    primitives — exactly as before the seam).
    """

    def __init__(self, name: str, spec: dict, *, secret: Optional[str] = None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.name = name
        self.protocol = str(spec.get("protocol") or "ftp").lower()
        if self.protocol not in PROTOCOLS:
            raise ValueError(f"unknown protocol {self.protocol!r} (expected one of {PROTOCOLS})")
        self.host = str(spec.get("host") or "")
        self.port = int(spec.get("port") or DEFAULT_PORTS.get(self.protocol, 21))
        self.user = str(spec.get("user") or "")
        self.key_path = spec.get("key_path") or None
        self.insecure = bool(spec.get("insecure"))
        # The full stored spec — handlers read their per-protocol extras here
        # (webdav base_url/auth, smb share/domain; see SPEC_FIELDS).
        self.spec = dict(spec)
        self._secret = secret
        self._timeout = float(spec.get("timeout") or timeout)
        self._handler = None  # per-protocol wire, built lazily on first connect

    # ---- lifecycle ---- #

    @property
    def connected(self) -> bool:
        return self._handler is not None and self._handler.connected

    def connect(self) -> None:
        if self.connected:
            return
        if not self.host and not self.spec.get("base_url"):
            raise OSError(f"remote connection {self.name!r} has no host configured")
        if self._handler is None:
            self._handler = remote_handlers.get_handler_class(self.protocol)(self)
        try:
            self._handler.connect()
        except (OSError, RuntimeError):
            raise
        except Exception as e:
            # The login/handshake raises types that are NOT OSError — ftplib.error_perm
            # (bad password), paramiko AuthenticationException/SSHException — so callers
            # guarding on (OSError, RuntimeError) (e.g. RemoteFsProvider.exists) would
            # crash the whole VFS on a bad-credential host. Normalize into OSError, the
            # same family the read/write/listdir paths already remap error_perm to, so a
            # bad host reads as a clean miss instead of an uncaught traceback. Kept HERE
            # (not per-handler) so every wire gets it for free.
            raise OSError(f"{self.protocol} connect to {self.host!r} failed: {e}") from e

    def close(self) -> None:
        if self._handler is not None:
            self._handler.close()

    def _h(self):
        """Connect (lazily) and hand back the live handler — every primitive's door."""
        self.connect()
        return self._handler

    # ---- primitives (delegated to the protocol handler) ---- #

    def listdir(self, path: str = "") -> "list[tuple[str, bool, Optional[int]]]":
        """List a remote directory → ``[(name, is_dir, size)]``. Directories always carry
        ``size=None`` (some servers report one via MLSD; it's meaningless — match the
        ``file://`` container convention), files ``None`` only when the server won't say.
        The normalization lives here so every handler gets it for free."""
        raw = self._h().listdir(path)
        return [(n, d, None if d else s) for n, d, s in raw]

    def read(self, path: str) -> bytes:
        return self._h().read(path)

    def write(self, path: str, data: bytes) -> None:
        self._h().write(path, data)

    def delete(self, path: str) -> None:
        self._h().delete(path)

    def rmdir(self, path: str) -> None:
        self._h().rmdir(path)

    def mkdir(self, path: str) -> None:
        self._h().mkdir(path)

    def run(self, command: str, stdin: bytes = b"", timeout: Optional[float] = None) -> bytes:
        """SSH exec when the wire is SFTP. Other protocols raise."""
        h = self._h()
        runner = getattr(h, "run", None)
        if not callable(runner):
            raise OSError(
                f"{self.protocol} connection {self.name!r} cannot exec "
                "(via:// hop needs sftp)"
            )
        return runner(command, stdin=stdin, timeout=timeout)

    # ---- protocol-neutral compositions ---- #

    def stat(self, path: str) -> "tuple[bool, Optional[int]]":
        """``(is_dir, size)`` for a remote path, via its parent's listing (uniform across
        protocols and MLSD-less servers). The empty path is the login home (a dir)."""
        path = (path or "").strip("/")
        if not path:
            return (True, None)
        parent, _, base = path.rpartition("/")
        for name, is_dir, size in self.listdir(parent):
            if name == base:
                return (is_dir, size)
        raise FileNotFoundError(f"{self.name}:{path}: no such remote path")

    def exists(self, path: str) -> bool:
        try:
            self.stat(path)
        except OSError:
            return False
        return True

    def makedirs(self, path: str) -> None:
        """``mkdir -p`` — create each missing level, tolerating already-exists."""
        parts = [p for p in (path or "").strip("/").split("/") if p]
        cur = ""
        for part in parts:
            cur = _join(cur, part)
            try:
                self.mkdir(cur)
            except OSError:
                if not self.exists(cur):
                    raise

    def rmtree(self, path: str) -> None:
        """Recursively delete a remote directory."""
        for name, is_dir, _size in self.listdir(path):
            child = _join(path, name)
            if is_dir:
                self.rmtree(child)
            else:
                self.delete(child)
        self.rmdir(path)


# --------------------------------------------------------------------------- #
#  Connection registry — config + vault + socket cache
# --------------------------------------------------------------------------- #


def _vault_secret(ref: Optional[str]) -> Optional[str]:
    """Decrypt one connection secret from the vault (``None`` ref → no secret)."""
    if not ref:
        return None
    from xlii.vault import Vault, VaultError

    try:
        secrets = Vault.unlock(create_if_missing=False).get(VAULT_FTP_NS)
    except VaultError as e:
        raise RuntimeError(
            f"a remote connection is vault-backed but the vault could not be opened: {e}"
        ) from e
    secret = secrets.get(ref, "")
    if not secret:
        raise RuntimeError(
            f"vault_ref {ref!r} not found in vault namespace {VAULT_FTP_NS!r} — "
            "re-add the connection with `xlii remote add`."
        )
    return secret


class RemoteFsManager:
    """Named-connection registry: config specs in, cached live connections out.

    ``get(name)`` reads ``GlobalConfig.ftp_connections[name]``, decrypts the secret
    from the vault, lazy-connects, and caches by name; ``close``/``close_all`` for
    teardown (``/remote close``). One module-level singleton (:data:`manager`).
    """

    def __init__(self):
        self._conns: dict[str, RemoteFsConnection] = {}

    @staticmethod
    def _specs() -> dict:
        from xlii.config import GlobalConfig

        specs = GlobalConfig.load().ftp_connections
        return specs if isinstance(specs, dict) else {}

    def names(self) -> "list[str]":
        return sorted(self._specs())

    def spec(self, name: str) -> "dict | None":
        s = self._specs().get(name)
        return s if isinstance(s, dict) else None

    def get(self, name: str) -> RemoteFsConnection:
        conn = self._conns.get(name)
        if conn is not None and conn.connected:
            return conn
        if conn is not None:
            conn.close()
        spec = self.spec(name)
        if spec is None:
            raise FileNotFoundError(
                f"no configured remote connection {name!r} — add one with `xlii remote add {name} --host …`"
            )
        conn = RemoteFsConnection(name, spec, secret=_vault_secret(spec.get("vault_ref")))
        conn.connect()
        self._conns[name] = conn
        return conn

    def close(self, name: str) -> bool:
        conn = self._conns.pop(name, None)
        if conn is None:
            return False
        conn.close()
        return True

    def close_all(self) -> int:
        n = len(self._conns)
        for name in list(self._conns):
            self.close(name)
        return n

    def open_names(self) -> "list[str]":
        return sorted(n for n, c in self._conns.items() if c.connected)


# The one socket owner. Sockets are lazy-opened on first use, reused across
# panes/commands, and closed by `/remote close` (or process exit).
manager = RemoteFsManager()


# --------------------------------------------------------------------------- #
#  Config mutation (xlii remote add/rm — non-secrets to config.json, secret to vault)
# --------------------------------------------------------------------------- #


def add_connection(name: str, *, host: str = "", port: Optional[int] = None, user: str = "",
                   protocol: str = "ftp", secret: Optional[str] = None,
                   **fields) -> dict:
    """Create/replace the named connection. Returns the (non-secret) spec stored.

    ``fields`` carries the per-protocol extras (see :data:`SPEC_FIELDS` — e.g.
    ``key_path`` for sftp, ``base_url``/``auth`` for webdav, ``share``/``domain``
    for smb). Falsy extras are dropped (so a caller may pass every flag it knows
    unconditionally); a truthy extra another protocol owns is refused — cross-wire
    config is a mistake worth stopping at the door."""
    from xlii.config import GlobalConfig

    protocol = (protocol or "ftp").lower()
    if protocol not in PROTOCOLS:
        raise ValueError(f"unknown protocol {protocol!r} (expected one of {PROTOCOLS})")
    if not name or "/" in name:
        scheme = scheme_for_protocol(protocol)
        raise ValueError(f"invalid connection name {name!r} (it becomes {scheme}://<name>/… — no slashes)")
    fields = {k: v for k, v in fields.items() if v}
    allowed = set(SPEC_FIELDS.get(protocol, ()))
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise ValueError(
            f"field(s) {', '.join(unknown)} don't apply to protocol {protocol!r}"
            + (f" (it takes: {', '.join(sorted(allowed))})" if allowed else " (it takes none)")
        )
    if not host and "base_url" not in fields:
        raise ValueError(f"connection {name!r} needs a host (or a base_url, for webdav)")
    if "base_url" in fields:
        from urllib.parse import urlsplit

        # A user:pass@host URL would persist the password in PLAINTEXT config.json
        # (and get echoed back in saved-lines/ps/shell history via the flag) — the
        # one secret per connection lives in the vault. Refuse at the door.
        if urlsplit(str(fields["base_url"])).username is not None:
            raise ValueError(
                "base_url must not embed credentials (user:pass@…) — the secret is "
                "prompted separately and stored in the encrypted vault"
            )
    entry: dict = {"protocol": protocol}
    if host:
        entry["host"] = host
    if port:
        entry["port"] = int(port)
    if user:
        entry["user"] = user
    for k, v in fields.items():
        entry[k] = str(v) if k in ("key_path", "base_url") else v
    if secret:
        from xlii.vault import Vault

        Vault.unlock().set(VAULT_FTP_NS, name, secret)  # vault_ref == connection name
        entry["vault_ref"] = name
    cfg = GlobalConfig.load()
    conns = dict(cfg.ftp_connections or {})
    conns[name] = entry
    cfg.ftp_connections = conns
    cfg.save()
    manager.close(name)  # a stale cached socket must not outlive its old spec
    return entry


def remove_connection(name: str) -> bool:
    """Drop the named connection from config + its secret from the vault."""
    from xlii.config import GlobalConfig

    manager.close(name)
    cfg = GlobalConfig.load()
    conns = dict(cfg.ftp_connections or {})
    entry = conns.pop(name, None)
    if entry is None:
        return False
    cfg.ftp_connections = conns
    cfg.save()
    ref = entry.get("vault_ref") if isinstance(entry, dict) else None
    if ref:
        from xlii.vault import Vault, VaultError

        try:
            Vault.unlock(create_if_missing=False).unset(VAULT_FTP_NS, ref)
        except VaultError:
            pass  # no vault → nothing stored → nothing to clean
    return True


# --------------------------------------------------------------------------- #
#  publish — recursive local-dir → remote-docroot upload (shared by /remote + CLI)
# --------------------------------------------------------------------------- #

_DOCROOT_RELATIVE = (
    "docroot is relative to the connection's login root; a leading / is stripped, never honored"
)


def _normalize_remote_subpath(subpath: str) -> str:
    return (subpath or "").strip("/")


def _safe_remote_subpath(subpath: str, *, require_nonempty: bool = False) -> str:
    """Normalize and validate a path relative to the connection login root."""
    norm = _normalize_remote_subpath(subpath)
    if require_nonempty and not norm:
        raise ValueError("remote docroot must be a non-empty path under the login root")
    if norm:
        for part in norm.replace("\\", "/").split("/"):
            if part in (".", ".."):
                raise ValueError(f"remote path may not contain {part!r} segment: {subpath!r}")
    return norm


def _require_remote_directory(conn: RemoteFsConnection, path: str) -> None:
    """Refuse a path that exists but is not a directory (e.g. a symlink leaf)."""
    is_dir, _size = conn.stat(path)
    if not is_dir:
        raise NotADirectoryError(f"{conn.name}:{path}: remote docroot must be a directory")


def unpublish(conn: RemoteFsConnection, remote_base: str) -> None:
    """Recursively delete a published remote docroot under the login root."""
    docroot = _safe_remote_subpath(remote_base, require_nonempty=True)
    if not conn.exists(docroot):
        raise FileNotFoundError(f"{conn.name}:{docroot}: no such remote path")
    _require_remote_directory(conn, docroot)
    conn.rmtree(docroot)


def publish(conn: RemoteFsConnection, local_root: Path, remote_base: str, *,
            delete: bool = False,
            progress: "Optional[Callable[[str, str], None]]" = None) -> "tuple[int, int, int]":
    """Mirror ``local_root`` into ``remote_base``: ``mkdir -p`` remote dirs, upload each
    file, **skip unchanged by size** when the server reports one. With ``delete``,
    prune remote-only entries after each directory syncs (a file counts 1; a
    remote-only subtree is one ``rmtree`` and counts 1). Returns
    ``(uploaded, skipped, deleted)``; ``progress(rel_path, status)`` gets one call
    per file (status ∈ uploaded/skipped/deleted) and per created dir (mkdir).

    Remote paths are relative to the connection's login root (FTP-hosting
    semantics — the SFTP wire resolves against the login home); a leading ``/``
    is stripped, never honored."""
    local_root = Path(local_root).expanduser().resolve()
    if not local_root.is_dir():
        raise NotADirectoryError(f"{local_root}: publish needs a local directory")
    remote_base = _safe_remote_subpath(remote_base, require_nonempty=delete)
    if delete and remote_base and conn.exists(remote_base):
        _require_remote_directory(conn, remote_base)

    def _notify(rel: str, status: str) -> None:
        if progress is not None:
            progress(rel, status)

    if remote_base and not conn.exists(remote_base):
        conn.makedirs(remote_base)
        _notify(remote_base, "mkdir")

    uploaded = skipped = deleted = 0
    for dirpath, dirnames, filenames in os.walk(local_root):
        dirnames.sort()
        rel_dir = Path(dirpath).relative_to(local_root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        remote_dir = _join(remote_base, rel_dir)
        # One listing per directory: the size map that powers skip-unchanged
        # (and, under --delete, the entry sets that power the prune).
        remote_sizes: dict[str, Optional[int]] = {}
        remote_subdirs: set[str] = set()
        try:
            entries = conn.listdir(remote_dir)
            remote_sizes = {n: sz for n, is_dir, sz in entries if not is_dir}
            remote_subdirs = {n for n, is_dir, _sz in entries if is_dir}
            have_dir = True
        except OSError:
            have_dir = False
        if not have_dir and remote_dir:
            conn.makedirs(remote_dir)
            _notify(remote_dir, "mkdir")
        for fname in sorted(filenames):
            local_file = Path(dirpath) / fname
            rel = posixpath.join(rel_dir, fname) if rel_dir else fname
            remote_size = remote_sizes.get(fname)
            if remote_size is not None and remote_size == local_file.stat().st_size:
                skipped += 1
                _notify(rel, "skipped")
                continue
            conn.write(_join(remote_dir, fname), local_file.read_bytes())
            uploaded += 1
            _notify(rel, "uploaded")
        if delete and have_dir:
            for name in sorted(set(remote_sizes) - set(filenames)):
                conn.delete(_join(remote_dir, name))
                deleted += 1
                _notify(posixpath.join(rel_dir, name) if rel_dir else name, "deleted")
            for name in sorted(remote_subdirs - set(dirnames)):
                conn.rmtree(_join(remote_dir, name))
                deleted += 1
                rel = posixpath.join(rel_dir, name) if rel_dir else name
                _notify(rel + "/", "deleted")
    return uploaded, skipped, deleted
