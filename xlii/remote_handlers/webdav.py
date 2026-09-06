"""WebDAV handler — HTTP(S) via ``requests`` (optional extra: ``pip install 'xlii[webdav]'``).

The Nextcloud/ownCloud/generic-DAV wire for the ``dav://`` provider
(proposals/remote-backends.md, Vector W). A thin client over ``requests`` maps
the seam's primitives onto WebDAV verbs — ``PROPFIND`` (Depth 1) → listdir,
``GET`` → read, ``PUT`` → write, ``DELETE`` → delete, ``MKCOL`` → mkdir — and
parses the multistatus XML with stdlib :mod:`xml.etree` (no dedicated webdav
client library).

``requests`` is imported lazily inside :meth:`WebdavHandler.connect` (the
``paramiko`` precedent), so nothing pays for the dep until a webdav connection
is actually used, and a missing package yields a clean install hint
(RuntimeError), never an import crash.

Trust posture (locked, proposals/remote-backends.md §5): the effective base URL
must be ``https://`` — a plain ``http://`` URL is refused unless the connection
is explicitly marked ``insecure``, and ``insecure`` also skips TLS certificate
verification for https hosts (mirroring the ftps escape hatch for hosts with
imperfect certs).

One semantic guard worth its comment: WebDAV's ``DELETE`` on a collection is
**recursive by protocol**, but the seam's ``rmdir`` contract is
fail-on-non-empty (the provider maps a non-recursive delete → ``rmdir`` and the
recursive one → ``rmtree``). So :meth:`WebdavHandler.rmdir` checks emptiness
first (PROPFIND Depth 1) and refuses a non-empty collection — it never silently
recurses.

``connect`` builds the session locally (URL trust check + auth mode, no
network); the first primitive touches the wire — HTTP is stateless, there is no
login handshake to front-load.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

_INSTALL_HINT = (
    "WebDAV support needs the optional 'requests' package — "
    "install it with: pip install 'xlii[webdav]'"
)

# Ask only for what listdir shapes: is-it-a-collection + the byte size. An empty
# PROPFIND body means allprop, which some servers answer expensively.
_PROPFIND_BODY = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<d:propfind xmlns:d="DAV:"><d:prop>'
    b"<d:resourcetype/><d:getcontentlength/>"
    b"</d:prop></d:propfind>"
)


class WebdavHandler:
    """One configured ``requests.Session`` for a connection whose protocol is webdav."""

    def __init__(self, conn) -> None:
        self._c = conn
        self._session = None   # requests.Session
        self._base = ""        # normalized base URL (no trailing slash)
        self._requests = None  # the lazily imported module (for its exception types)

    # ---- lifecycle ---- #

    @property
    def connected(self) -> bool:
        return self._session is not None

    def connect(self) -> None:
        try:
            import requests
        except ImportError as e:
            raise RuntimeError(_INSTALL_HINT) from e

        c = self._c
        # spec["base_url"] wins (it may carry a path — Nextcloud's
        # /remote.php/dav/files/<user> style); else https://host[:port].
        base = str(c.spec.get("base_url") or "").strip().rstrip("/")
        if not base:
            base = f"https://{c.host}" + ("" if c.port == 443 else f":{c.port}")
        scheme = urlsplit(base).scheme.lower()
        if scheme == "http" and not c.insecure:
            raise OSError(
                f"webdav connection {c.name!r}: plain http:// sends credentials unencrypted — "
                "use an https:// base_url, or re-add the connection as insecure to accept the risk"
            )
        if scheme not in ("http", "https"):
            raise OSError(f"webdav connection {c.name!r}: base_url must be http(s)://, got {base!r}")

        auth = str(c.spec.get("auth") or "basic").lower()
        if auth not in ("basic", "digest", "bearer"):
            raise OSError(
                f"webdav connection {c.name!r}: unknown auth mode {auth!r} "
                "(expected basic | digest | bearer)"
            )
        if auth == "bearer" and not c._secret:
            raise OSError(
                f"webdav connection {c.name!r}: bearer auth needs a token — "
                "re-add the connection and enter the token at the secret prompt"
            )

        session = requests.Session()
        session.verify = not c.insecure
        if c.insecure:
            try:  # hush urllib3's per-request nag — insecure was chosen explicitly
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                # This only silences a nag: if urllib3 is missing or restructured, the warning simply still
                # prints.
                pass
        if auth == "bearer":
            session.headers["Authorization"] = f"Bearer {c._secret}"
        elif auth == "digest":
            session.auth = requests.auth.HTTPDigestAuth(c.user or "", c._secret or "")
        elif c.user or c._secret:  # basic — requests' default header form
            session.auth = (c.user or "", c._secret or "")

        self._requests = requests
        self._base = base
        self._session = session

    def close(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            try:
                session.close()
            except Exception:
                # The session reference is already dropped; a failed close leaks nothing we can still reach.
                pass

    # ---- the thin HTTP client ---- #

    def _url(self, path: str, *, trailing: bool = False) -> str:
        """Base + percent-encoded relative path. Collections get the trailing
        slash WebDAV servers prefer for PROPFIND/MKCOL/DELETE-on-a-dir."""
        rel = "/".join(quote(seg) for seg in (path or "").strip("/").split("/") if seg)
        url = f"{self._base}/{rel}" if rel else self._base
        return url + "/" if trailing and not url.endswith("/") else url

    def _request(self, method: str, path: str, *, ok: "tuple[int, ...]",
                 trailing: bool = False, data: "Optional[bytes]" = None,
                 headers: "Optional[dict]" = None):
        """One HTTP round-trip under the seam's error contract: 404 →
        FileNotFoundError; transport failures and every other unexpected status
        (401/403/405/409/5xx…) → OSError with the status in the message."""
        try:
            resp = self._session.request(
                method, self._url(path, trailing=trailing),
                data=data, headers=headers, timeout=self._c._timeout,
            )
        except self._requests.RequestException as e:
            raise OSError(f"webdav {method} {path or '/'} failed: {e}") from e
        if resp.status_code == 404:
            raise FileNotFoundError(f"{self._c.name}:{path or '/'}: no such remote path")
        if resp.status_code not in ok:
            raise OSError(f"webdav {method} {path or '/'} failed: HTTP {resp.status_code}")
        return resp

    # ---- primitives ---- #

    def listdir(self, path: str = "") -> "list[tuple[str, bool, Optional[int]]]":
        resp = self._request(
            "PROPFIND", path, trailing=True, ok=(207, 200),
            data=_PROPFIND_BODY,
            headers={"Depth": "1", "Content-Type": "application/xml"},
        )
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            raise OSError(f"webdav PROPFIND {path or '/'}: bad multistatus XML: {e}") from e
        # Depth-1 multistatus includes the collection's OWN entry; recognize it
        # by comparing normalized (url-decoded, un-slashed) href paths.
        own = unquote(urlsplit(self._url(path, trailing=True)).path).rstrip("/")
        entries: "list[tuple[str, bool, Optional[int]]]" = []
        for node in root.findall("{DAV:}response"):
            href = (node.findtext("{DAV:}href") or "").strip()
            hpath = unquote(urlsplit(href).path if "://" in href else href).rstrip("/")
            if not hpath or hpath == own:
                continue  # the self entry (or an empty href) — never a child
            name = hpath.rsplit("/", 1)[-1]
            is_dir = node.find(".//{DAV:}resourcetype/{DAV:}collection") is not None
            size: Optional[int] = None
            if not is_dir:
                raw = (node.findtext(".//{DAV:}getcontentlength") or "").strip()
                if raw.isdigit():
                    size = int(raw)
            entries.append((name, is_dir, size))
        return entries

    def read(self, path: str) -> bytes:
        return self._request("GET", path, ok=(200,)).content

    def write(self, path: str, data: bytes) -> None:
        self._request("PUT", path, ok=(200, 201, 204), data=data,
                      headers={"Content-Type": "application/octet-stream"})

    def delete(self, path: str) -> None:
        self._request("DELETE", path, ok=(200, 202, 204))

    def rmdir(self, path: str) -> None:
        # WebDAV DELETE on a collection is RECURSIVE by protocol, but the seam's
        # rmdir contract is fail-on-non-empty (the provider maps non-recursive
        # delete here; rmtree is the explicit recursive path). Check first —
        # never silently recurse.
        if self.listdir(path):
            raise OSError(f"{self._c.name}:{path or '/'}: directory not empty")
        self._request("DELETE", path, trailing=True, ok=(200, 202, 204))

    def mkdir(self, path: str) -> None:
        self._request("MKCOL", path, trailing=True, ok=(201,))


HANDLER = WebdavHandler
