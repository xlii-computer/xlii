"""The WebDAV backend (proposals/remote-backends.md, Vector W): handler registry,
config round-trip, the https-unless-insecure trust posture, multistatus parsing,
the error contract, the rmdir recursion guard, auth modes, and the dav:// picker.

No network anywhere: a fake ``requests`` module (scripted Session/Response) is
injected via monkeypatch, so the tests run identically with or without the
optional ``xlii[webdav]`` extra installed.
"""

from __future__ import annotations

import sys
import types

import pytest

from xlii import remotefs as R


# --- the scripted HTTP layer ---------------------------------------------------------


class _Resp:
    def __init__(self, status_code: int = 200, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class _Session:
    """Recording fake for requests.Session — FIFO-scripted responses."""

    def __init__(self):
        self.headers: dict = {}
        self.auth = None
        self.verify = True
        self.calls: list[tuple] = []       # (method, url, data, headers, timeout)
        self.responses: list[_Resp] = []   # preloaded by each test
        self.closed = False

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append((method, url, data, headers, timeout))
        return self.responses.pop(0) if self.responses else _Resp(200)

    def close(self):
        self.closed = True


def _fake_requests() -> types.ModuleType:
    mod = types.ModuleType("requests")
    mod.Session = _Session

    class RequestException(Exception):
        pass

    mod.RequestException = RequestException
    auth = types.ModuleType("requests.auth")

    class HTTPDigestAuth:
        def __init__(self, username, password):
            self.username, self.password = username, password

    auth.HTTPDigestAuth = HTTPDigestAuth
    mod.auth = auth
    return mod


def _connect(monkeypatch, spec: dict | None = None, secret: str | None = None,
             name: str = "cloud") -> tuple:
    """A connected webdav RemoteFsConnection over the fake requests → (conn, session)."""
    monkeypatch.setitem(sys.modules, "requests", _fake_requests())
    full = {"protocol": "webdav", "base_url": "https://h/dav"}
    full.update(spec or {})
    conn = R.RemoteFsConnection(name, full, secret=secret)
    conn.connect()
    return conn, conn._handler._session


# --- registry + config (the two seams the handler plugs into) ------------------------


def test_handler_registry_resolves_webdav():
    from xlii import remote_handlers as H
    from xlii.remote_handlers.webdav import WebdavHandler

    assert H.get_handler_class("webdav") is WebdavHandler
    assert "webdav" in R.PROTOCOLS
    assert R.scheme_for_protocol("webdav") == "dav"


def test_add_connection_webdav_roundtrip(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    import xlii.config as config
    import xlii.vault as vault
    from xlii.config import GlobalConfig
    from xlii.vault import Vault

    # Pin config + vault to this test's tmp dir (the test_admin_gate/
    # test_keys_vault_migrate pattern) so the fresh Fernet key can't strand a
    # vault another test's key later fails to decrypt.
    monkeypatch.setattr(config, "GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(vault, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(vault, "KEY_FILE", tmp_path / ".vault-key")
    monkeypatch.setenv(vault.ENV_VAR, Fernet.generate_key().decode())

    entry = R.add_connection(
        "mycloud", protocol="webdav", user="alice", secret="s3cret-tok3n",
        base_url="https://cloud.example.com/remote.php/dav/files/alice",
        auth="bearer",
    )
    try:
        assert entry["protocol"] == "webdav"
        assert entry["base_url"].startswith("https://cloud.example.com")
        assert entry["auth"] == "bearer"
        assert entry["vault_ref"] == "mycloud"
        assert "host" not in entry  # base_url replaces host for webdav

        # Non-secrets land in config.json; the token never does.
        raw = (tmp_path / "config.json").read_text()
        assert "cloud.example.com" in raw and "s3cret-tok3n" not in raw
        assert GlobalConfig.load().ftp_connections["mycloud"]["protocol"] == "webdav"
        assert Vault.unlock().get(R.VAULT_FTP_NS)["mycloud"] == "s3cret-tok3n"
    finally:
        assert R.remove_connection("mycloud")


def test_add_connection_webdav_needs_host_or_base_url():
    with pytest.raises(ValueError, match="base_url"):
        R.add_connection("x", protocol="webdav")


# --- trust posture: https unless the connection says insecure ------------------------


def test_http_base_url_refused_without_insecure(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", _fake_requests())
    conn = R.RemoteFsConnection("plain", {"protocol": "webdav", "base_url": "http://h/dav"})
    with pytest.raises(OSError, match="http"):
        conn.connect()
    assert not conn.connected


def test_insecure_allows_http_and_skips_tls_verify(monkeypatch):
    conn, session = _connect(monkeypatch,
                             {"base_url": "http://nas.lan/dav", "insecure": True})
    assert conn.connected
    # insecure sets verify=False on the session (moot for this http URL; the
    # same flag is what skips certificate checks on an https host).
    assert session.verify is False


def test_default_base_url_is_https_host(monkeypatch):
    conn, session = _connect(monkeypatch, {"base_url": "", "host": "dav.example.com"})
    assert session.verify is True  # the SECURE default: certs verified unless insecure
    session.responses = [_Resp(200, b"x")]
    conn.read("f.txt")
    assert session.calls[0][1] == "https://dav.example.com/f.txt"
    assert session.calls[0][4] == conn._timeout  # every request carries the timeout


# --- listdir: multistatus parsing (namespaces, self-href skip, sizes, url-decode) ----


_MULTISTATUS = b"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/docs/</d:href>
    <d:propstat>
      <d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/docs/img%20dir/</d:href>
    <d:propstat>
      <d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/dav/docs/notes%20file.txt</d:href>
    <d:propstat>
      <d:prop><d:resourcetype/><d:getcontentlength>11</d:getcontentlength></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>"""


def test_listdir_parses_multistatus(monkeypatch):
    conn, session = _connect(monkeypatch)
    session.responses = [_Resp(207, _MULTISTATUS)]
    entries = conn.listdir("docs")
    # The collection's own entry is skipped; names are url-decoded; the dir's
    # size is normalized to None upstream; the file keeps getcontentlength.
    assert entries == [("img dir", True, None), ("notes file.txt", False, 11)]
    method, url, data, headers, timeout = session.calls[0]
    assert method == "PROPFIND" and url == "https://h/dav/docs/"
    assert headers["Depth"] == "1" and b"resourcetype" in data


def test_listdir_missing_dir_raises_filenotfound(monkeypatch):
    conn, session = _connect(monkeypatch)
    session.responses = [_Resp(404)]
    with pytest.raises(FileNotFoundError):
        conn.listdir("no/such/dir")


# --- read/write/delete/mkdir under the error contract --------------------------------


def test_read_roundtrip_and_404(monkeypatch):
    conn, session = _connect(monkeypatch)
    session.responses = [_Resp(200, b"<h1>hi</h1>"), _Resp(404)]
    assert conn.read("index.html") == b"<h1>hi</h1>"
    assert session.calls[0][:2] == ("GET", "https://h/dav/index.html")
    with pytest.raises(FileNotFoundError):
        conn.read("nope.html")


def test_write_put_and_auth_failure_is_oserror(monkeypatch):
    conn, session = _connect(monkeypatch)
    session.responses = [_Resp(201), _Resp(403)]
    conn.write("site/index.html", b"deployed")
    method, url, data, _h, _t = session.calls[0]
    assert (method, url, data) == ("PUT", "https://h/dav/site/index.html", b"deployed")
    with pytest.raises(OSError, match="403"):
        conn.write("forbidden.txt", b"x")


def test_rmdir_refuses_nonempty_collection(monkeypatch):
    # WebDAV DELETE on a collection recurses by protocol — rmdir must check
    # emptiness first and refuse, never silently recurse.
    conn, session = _connect(monkeypatch)
    session.responses = [_Resp(207, _MULTISTATUS)]
    with pytest.raises(OSError, match="not empty"):
        conn.rmdir("docs")
    assert [c[0] for c in session.calls] == ["PROPFIND"]  # no DELETE was issued


_EMPTY_MULTISTATUS = b"""<?xml version="1.0" encoding="utf-8"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/dav/empty/</d:href>
    <d:propstat>
      <d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>"""


def test_rmdir_deletes_empty_collection(monkeypatch):
    conn, session = _connect(monkeypatch)
    session.responses = [_Resp(207, _EMPTY_MULTISTATUS), _Resp(204)]
    conn.rmdir("empty")
    assert [c[0] for c in session.calls] == ["PROPFIND", "DELETE"]
    assert session.calls[1][1] == "https://h/dav/empty/"


def test_mkdir_mkcol_and_conflict(monkeypatch):
    conn, session = _connect(monkeypatch)
    session.responses = [_Resp(201), _Resp(409)]
    conn.mkdir("newdir")
    assert session.calls[0][:2] == ("MKCOL", "https://h/dav/newdir/")
    with pytest.raises(OSError, match="409"):
        conn.mkdir("no/parent/dir")


# --- auth modes ----------------------------------------------------------------------


def test_basic_auth_is_the_default(monkeypatch):
    _conn, session = _connect(monkeypatch, {"user": "alice"}, secret="pw")
    assert session.auth == ("alice", "pw")
    assert "Authorization" not in session.headers


def test_bearer_auth_sends_the_token_header(monkeypatch):
    _conn, session = _connect(monkeypatch, {"auth": "bearer"}, secret="tok-123")
    assert session.headers["Authorization"] == "Bearer tok-123"
    assert session.auth is None


def test_bearer_auth_without_a_token_fails_clean(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", _fake_requests())
    conn = R.RemoteFsConnection("c", {"protocol": "webdav", "base_url": "https://h",
                                      "auth": "bearer"})
    with pytest.raises(OSError, match="token"):
        conn.connect()


def test_digest_auth_uses_the_digest_helper(monkeypatch):
    _conn, session = _connect(monkeypatch, {"auth": "digest", "user": "bob"}, secret="pw")
    assert type(session.auth).__name__ == "HTTPDigestAuth"
    assert (session.auth.username, session.auth.password) == ("bob", "pw")


# --- the optional dep degrades cleanly (the paramiko precedent) -----------------------


def test_missing_requests_yields_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "requests", None)  # import requests → ImportError
    conn = R.RemoteFsConnection("c", {"protocol": "webdav", "base_url": "https://h"})
    with pytest.raises(RuntimeError, match=r"pip install 'xlii\[webdav\]'"):
        conn.connect()


# --- dav:// picker + CLI surface ------------------------------------------------------


def test_dav_picker_lists_webdav_connections(monkeypatch):
    specs = {
        "mycloud": {"base_url": "https://h/dav", "protocol": "webdav"},
        "site": {"host": "h1", "protocol": "ftp"},
    }
    monkeypatch.setattr(R.manager, "names", lambda: sorted(specs))
    monkeypatch.setattr(R.manager, "spec", lambda n: specs.get(n))

    from xlii.addressing import vfs_list

    dav = vfs_list("dav://")
    assert [n.name for n in dav] == ["mycloud"]
    assert dav[0].address == "dav://mycloud" and dav[0].kind == "container"
    assert [n.name for n in vfs_list("ftp://")] == ["site"]  # pickers stay honest
    # …and the union picker addresses the webdav host by its honest scheme.
    union = {n.name: n.address for n in vfs_list("remote://")}
    assert union == {"mycloud": "dav://mycloud", "site": "ftp://site"}


def test_cli_add_validates_before_the_secret_prompt(monkeypatch):
    """No --host and no --base-url must fail usage BEFORE getpass — a user must
    never type a secret into a call that was always going to be rejected."""
    import getpass as G

    from xlii.cli import build_parser

    monkeypatch.setattr(G, "getpass",
                        lambda *a, **k: pytest.fail("secret prompted before validation"))
    args = build_parser().parse_args(["remote", "add", "x", "--protocol", "webdav"])
    assert args.func(args) == 1


def test_cli_add_passes_webdav_fields(monkeypatch):
    import getpass as G

    from xlii.cli import build_parser

    got: dict = {}
    monkeypatch.setattr(G, "getpass", lambda *a, **k: "pw")
    monkeypatch.setattr(
        R, "add_connection",
        lambda name, **kw: got.update(name=name, **kw)
        or {"protocol": "webdav", "base_url": kw.get("base_url")},
    )
    args = build_parser().parse_args([
        "remote", "add", "mycloud", "--protocol", "webdav",
        "--base-url", "https://h/dav", "--auth", "bearer", "--user", "alice",
    ])
    assert args.func(args) == 0
    assert got["name"] == "mycloud" and got["protocol"] == "webdav"
    assert got["base_url"] == "https://h/dav" and got["auth"] == "bearer"
    assert got["user"] == "alice" and got["secret"] == "pw"
