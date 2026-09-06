r"""SMB/CIFS handler — the ``smbprotocol`` wire (optional extra: ``pip install 'xlii[smb]'``).

The NAS / Windows-share case (proposals/remote-backends.md, Vector M). An smb
connection carries its per-wire extras in the spec: ``share`` (required — SMB
has no wire-level "login home", every path lives under a named share) and
``domain`` (optional — folded into the username as ``DOMAIN\user``). Paths stay
share-relative on the xlii side (``smb://<name>/a/b``); this handler maps them
onto UNC (``\\host\share\a\b``) for smbprotocol's high-level :mod:`smbclient`
API. ``smbclient`` is imported lazily inside :meth:`SmbHandler.connect` (the
``paramiko`` precedent), so nothing pays for the dep until an smb connection is
actually opened. Trust posture v1: intra-LAN/VPN oriented — no signing-policy
knob (noted in the proposal).

Session discipline (the verify-round findings): smbclient routes **every**
operation through a process-global connection pool keyed ``server:port`` —
an op without ``port=`` dials the default 445, and ``username=None`` reuses
"the first session found" for the server. So every op here carries the same
``port``/``username``/``password`` the session registered with
(:attr:`SmbHandler._kw`, built once at connect so no op can forget), and
``close()`` refcounts the ``(host, port)`` pool key (:data:`_SESSION_REFS`),
only calling ``delete_session`` — which pops the *process-global* pooled
connection — when the last xlii connection sharing it closes.

v1 boundary — **no per-operation deadline**: ``connection_timeout`` bounds the
session *setup* only; smbclient exposes no clean per-op timeout, so an op
against a host that hangs mid-session can block indefinitely (unlike the
ftp/sftp wires, which arm per-op socket timeouts). This rides the blocking-v1
discipline; the standing ``jobs://`` offload is the follow-up that bounds it.
"""

from __future__ import annotations

import errno
from contextlib import contextmanager
from typing import Optional

_INSTALL_HINT = (
    "SMB support needs the optional 'smbprotocol' package — "
    "install it with: pip install 'xlii[smb]'"
)

# Live-handler refcounts per smbclient pool key (host, port). The pool is
# process-global inside smbclient, so deleting a session while a sibling xlii
# connection still rides the same (host, port) would cut that sibling off
# under its feet — only the last close tears the pooled connection down.
_SESSION_REFS: dict[tuple[str, int], int] = {}

# FILE_ATTRIBUTE_DIRECTORY — the directory bit in a query response's
# file_attributes (kept as a literal: smbprotocol imports lazily).
_ATTR_DIRECTORY = 0x0010


def _info_field(info, name: str):
    """One field from a scandir entry's cached query response. smbprotocol
    structures are dict-style with ``.get_value()``; a test fake may hand
    plain values — accept both."""
    value = info[name]
    return value.get_value() if hasattr(value, "get_value") else value


class SmbHandler:
    """One registered smbclient session for a connection whose protocol is smb."""

    def __init__(self, conn) -> None:
        self._c = conn
        self._smb = None        # the lazily-imported smbclient module
        self._share = ""        # validated share name from the spec
        self._kw: dict = {}     # per-op session kwargs (built at connect)
        self._connected = False

    # ---- lifecycle ---- #

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        try:
            import smbclient
        except ImportError as e:
            raise RuntimeError(_INSTALL_HINT) from e

        c = self._c
        share = str(c.spec.get("share") or "").strip().strip("\\/")
        if not share:
            raise OSError(
                f"smb connection {c.name!r} has no share configured — re-add it: "
                f"xlii remote add {c.name} --protocol smb "
                f"--host {c.host or '<host>'} --share <share>"
            )
        user = c.user or None
        domain = str(c.spec.get("domain") or "").strip()
        if user and domain:
            user = f"{domain}\\{user}"
        smbclient.register_session(
            c.host,
            username=user,
            password=c._secret or None,
            port=c.port,
            connection_timeout=c._timeout,
        )
        # Every subsequent op must resolve the SAME pooled session it registered
        # (smbclient keys its pool on server:port and matches credentials) —
        # one dict, passed everywhere, so no op can fall back to 445/first-found.
        self._kw = {
            "port": c.port,
            "username": user,
            "password": c._secret or None,
            "connection_timeout": c._timeout,
        }
        key = (c.host, c.port)
        _SESSION_REFS[key] = _SESSION_REFS.get(key, 0) + 1
        self._smb = smbclient
        self._share = share
        self._connected = True

    def close(self) -> None:
        smb, self._smb = self._smb, None
        was_live, self._connected = self._connected, False
        if smb is None or not was_live:
            return
        # Refcounted teardown: delete_session pops smbclient's PROCESS-GLOBAL
        # pooled connection for (host, port) — only the last handler out closes.
        key = (self._c.host, self._c.port)
        remaining = _SESSION_REFS.get(key, 1) - 1
        if remaining > 0:
            _SESSION_REFS[key] = remaining
            return
        _SESSION_REFS.pop(key, None)
        try:
            smb.delete_session(self._c.host, port=self._c.port)
        except Exception:
            pass  # idempotent teardown — a dead session must not raise

    # ---- path + error mapping ---- #

    def _unc(self, path: str = "") -> str:
        r"""Share-relative path → UNC: ``a/b`` under ``\\host\share`` → ``\\host\share\a\b``."""
        rel = (path or "").strip("/").replace("/", "\\")
        base = f"\\\\{self._c.host}\\{self._share}"
        return f"{base}\\{rel}" if rel else base

    @contextmanager
    def _mapped(self, path: str):
        """Keep the uniform error contract: smbclient raises SMBOSError — an
        OSError subclass with ``errno`` seated from the NT status — so genuine
        OSError shapes flow through; not-found shapes are promoted to
        FileNotFoundError (SMBOSError itself never is one), and the non-OSError
        SMBException family (auth/protocol faults) wraps into plain OSError."""
        try:
            yield
        except FileNotFoundError:
            raise
        except OSError as e:
            if getattr(e, "errno", None) == errno.ENOENT:
                raise FileNotFoundError(f"{self._c.name}:{path}: {e}") from e
            raise
        except Exception as e:
            raise OSError(f"{self._c.name}:{path}: {e}") from e

    # ---- primitives ---- #

    def listdir(self, path: str = "") -> "list[tuple[str, bool, Optional[int]]]":
        out: "list[tuple[str, bool, Optional[int]]]" = []
        with self._mapped(path):
            for entry in self._smb.scandir(self._unc(path), **self._kw):
                # Read the scandir query response's CACHED info (entry.smb_info)
                # — entry.stat() would follow symlinks and cost one extra SMB
                # round-trip per entry, and a dangling link would blow up the
                # whole listing. One broken entry is skipped, never fatal.
                try:
                    attrs = int(_info_field(entry.smb_info, "file_attributes"))
                    is_dir = bool(attrs & _ATTR_DIRECTORY)
                    size = None if is_dir else int(_info_field(entry.smb_info, "end_of_file"))
                except Exception:
                    continue
                out.append((entry.name, is_dir, size))
        return out

    def read(self, path: str) -> bytes:
        with self._mapped(path):
            with self._smb.open_file(self._unc(path), mode="rb", **self._kw) as f:
                return f.read()

    def write(self, path: str, data: bytes) -> None:
        with self._mapped(path):
            with self._smb.open_file(self._unc(path), mode="wb", **self._kw) as f:
                f.write(data)

    def delete(self, path: str) -> None:
        with self._mapped(path):
            self._smb.remove(self._unc(path), **self._kw)

    def rmdir(self, path: str) -> None:
        # smbclient.rmdir refuses a non-empty directory (STATUS_DIRECTORY_NOT_EMPTY
        # → ENOTEMPTY → OSError) — exactly the contract; only not-found promotes.
        with self._mapped(path):
            self._smb.rmdir(self._unc(path), **self._kw)

    def mkdir(self, path: str) -> None:
        # Missing parent / already-exists surface as the OSError family
        # (makedirs upstream tolerates exists via its exists() probe).
        with self._mapped(path):
            self._smb.mkdir(self._unc(path), **self._kw)


HANDLER = SmbHandler
