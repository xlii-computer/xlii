"""Remote-fs (proposals/remote-fs.md): the connection layer, the ftp://+sftp:// provider,
config+vault round-trip, and publish.

No network: provider/publish tests run against an in-memory FakeConnection (a
RemoteFsConnection subclass overriding only the protocol primitives, so the
protocol-neutral compositions — stat/makedirs/rmtree — are exercised for real).
The one live round-trip uses a pyftpdlib server on 127.0.0.1 and skips cleanly
when pyftpdlib isn't installed.
"""

from __future__ import annotations

import sys

import pytest

from xlii import remotefs as R
from xlii.addressing import (
    resolve,
    supports_vfs,
    supports_write,
    vfs_delete,
    vfs_exists,
    vfs_list,
    vfs_mkdir,
    vfs_read,
    vfs_stat,
    vfs_write,
)


class FakeConnection(R.RemoteFsConnection):
    """In-memory remote tree — overrides only the protocol primitives."""

    def __init__(self):
        super().__init__("local", {"host": "fake", "protocol": "ftp"})
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = {""}
        self.closed = False

    @property
    def connected(self) -> bool:
        return not self.closed

    def connect(self) -> None:  # never touches a socket
        pass

    def close(self) -> None:
        self.closed = True

    def _norm(self, path: str) -> str:
        return (path or "").strip("/")

    def listdir(self, path: str = ""):
        d = self._norm(path)
        if d not in self.dirs:
            raise FileNotFoundError(f"local:{d}: no such remote dir")
        out = []
        prefix = f"{d}/" if d else ""
        seen = set()
        for f, data in self.files.items():
            if f.startswith(prefix) and "/" not in f[len(prefix):]:
                out.append((f[len(prefix):], False, len(data)))
        for sub in self.dirs:
            if sub and sub.startswith(prefix) and "/" not in sub[len(prefix):]:
                name = sub[len(prefix):]
                if name and name not in seen:
                    out.append((name, True, None))
                    seen.add(name)
        return out

    def read(self, path: str) -> bytes:
        p = self._norm(path)
        if p not in self.files:
            raise FileNotFoundError(f"local:{p}: no such file")
        return self.files[p]

    def write(self, path: str, data: bytes) -> None:
        p = self._norm(path)
        parent = p.rpartition("/")[0]
        if parent not in self.dirs:
            raise OSError(f"local:{parent}: no such remote dir")
        self.files[p] = data

    def delete(self, path: str) -> None:
        p = self._norm(path)
        if p not in self.files:
            raise FileNotFoundError(f"local:{p}: no such file")
        del self.files[p]

    def rmdir(self, path: str) -> None:
        p = self._norm(path)
        if p not in self.dirs:
            raise OSError(f"local:{p}: no such dir")
        if self.listdir(p):
            raise OSError(f"local:{p}: directory not empty")
        self.dirs.discard(p)

    def mkdir(self, path: str) -> None:
        p = self._norm(path)
        parent = p.rpartition("/")[0]
        if parent not in self.dirs:
            raise OSError(f"local:{parent}: no such remote dir")
        if p in self.dirs:
            raise OSError(f"local:{p}: exists")
        self.dirs.add(p)


@pytest.fixture
def fake_remote(monkeypatch):
    fake = FakeConnection()
    monkeypatch.setattr(R.manager, "names", lambda: ["local"])
    monkeypatch.setattr(
        R.manager, "spec",
        lambda name: {"host": "fake", "protocol": "ftp"} if name == "local" else None,
    )

    def _get(name):
        if name != "local":
            raise FileNotFoundError(f"no configured remote connection {name!r}")
        return fake

    monkeypatch.setattr(R.manager, "get", _get)
    return fake


# --- the provider (P1): full WritableVfs over the fake ----------------------------


def test_ftp_scheme_is_browseable_and_writable():
    assert supports_vfs("ftp") and supports_write("ftp")
    assert supports_vfs("sftp") and supports_write("sftp")


def test_root_is_the_server_picker(fake_remote):
    nodes = vfs_list("ftp://")
    assert [n.name for n in nodes] == ["local"]
    assert nodes[0].kind == "container"
    assert nodes[0].address == "ftp://local"
    assert nodes[0].extra["type"] == "remote-host"
    assert resolve("ftp://").ok
    assert resolve("ftp://local").ok
    assert not resolve("ftp://nope").ok


def test_leaf_roundtrip_through_the_vfs(fake_remote):
    vfs_write("ftp://local/index.html", b"<h1>hi</h1>")
    assert vfs_read("ftp://local/index.html") == b"<h1>hi</h1>"
    node = vfs_stat("ftp://local/index.html")
    assert node.kind == "leaf" and node.size == len(b"<h1>hi</h1>")
    assert node.extra["type"] == "remote-file"
    assert vfs_exists("ftp://local/index.html")
    vfs_delete("ftp://local/index.html")
    assert not vfs_exists("ftp://local/index.html")


def test_remote_schemes_share_one_registry_with_honest_pickers(fake_remote):
    # ONE connection registry behind every remote scheme: name-resolution is
    # lenient (a saved sftp://local address reaches the same "local" connection),
    # but each PICKER is honest — the ftp-protocol fake lists under ftp://, and
    # not under sftp:// (D2, proposals/remote-backends.md).
    vfs_write("sftp://local/a.txt", b"x")
    assert vfs_read("ftp://local/a.txt") == b"x"
    assert vfs_list("ftp://")[0].address == "ftp://local"
    assert vfs_list("sftp://") == []


def test_listing_a_remote_dir(fake_remote):
    vfs_mkdir("ftp://local/public_html/css")  # makedirs: both levels created
    vfs_write("ftp://local/public_html/index.html", b"hi")
    nodes = vfs_list("ftp://local/public_html")
    # containers first, then leaves (the mc-style sort)
    assert [(n.name, n.kind) for n in nodes] == [("css", "container"), ("index.html", "leaf")]
    assert nodes[1].address == "ftp://local/public_html/index.html"
    home = vfs_list("ftp://local")
    assert [(n.name, n.kind) for n in home] == [("public_html", "container")]


def test_read_of_picker_or_home_raises(fake_remote):
    with pytest.raises(IsADirectoryError):
        vfs_read("ftp://")
    with pytest.raises(IsADirectoryError):
        vfs_read("ftp://local")


def test_recursive_delete(fake_remote):
    vfs_mkdir("ftp://local/site/img")
    vfs_write("ftp://local/site/index.html", b"a")
    vfs_write("ftp://local/site/img/logo.png", b"b")
    with pytest.raises(OSError):
        vfs_delete("ftp://local/site")  # non-empty, not recursive
    vfs_delete("ftp://local/site", recursive=True)
    assert not vfs_exists("ftp://local/site")


def test_unknown_connection_raises_filenotfound(fake_remote):
    with pytest.raises(FileNotFoundError):
        vfs_list("ftp://nope")


# --- config + vault round-trip (P0) ------------------------------------------------


def test_config_vault_roundtrip(monkeypatch):
    from cryptography.fernet import Fernet

    from xlii.config import GLOBAL_CONFIG_FILE, GlobalConfig
    from xlii.vault import ENV_VAR, Vault

    monkeypatch.setenv(ENV_VAR, Fernet.generate_key().decode())

    entry = R.add_connection(
        "myhost", host="example.com", port=2121, user="bob",
        protocol="ftps", secret="s3cret-pw", insecure=True,
    )
    assert entry["vault_ref"] == "myhost"

    # Non-secrets land in config.json; the secret never does.
    raw = GLOBAL_CONFIG_FILE.read_text()
    assert "example.com" in raw and "s3cret-pw" not in raw
    cfg = GlobalConfig.load()
    assert cfg.ftp_connections["myhost"]["protocol"] == "ftps"

    # The secret round-trips through the vault namespace.
    assert Vault.unlock().get(R.VAULT_FTP_NS)["myhost"] == "s3cret-pw"
    assert R._vault_secret("myhost") == "s3cret-pw"

    # The manager builds a connection from spec + secret (without connecting).
    mgr = R.RemoteFsManager()
    assert mgr.names() == ["myhost"]
    assert mgr.spec("myhost")["host"] == "example.com"

    assert R.remove_connection("myhost")
    assert "myhost" not in GlobalConfig.load().ftp_connections
    assert Vault.unlock().get(R.VAULT_FTP_NS) == {}
    assert not R.remove_connection("myhost")


def test_manager_unknown_name_has_actionable_error():
    mgr = R.RemoteFsManager()
    with pytest.raises(FileNotFoundError, match="xlii remote add"):
        mgr.get("ghost")


def test_connection_rejects_unknown_protocol():
    with pytest.raises(ValueError, match="protocol"):
        R.RemoteFsConnection("x", {"host": "h", "protocol": "gopher"})
    with pytest.raises(ValueError):
        R.add_connection("x", host="h", protocol="gopher")


# --- publish (P3) -------------------------------------------------------------------


def test_publish_mirrors_and_skips_unchanged(tmp_path, fake_remote):
    site = tmp_path / "public"
    (site / "css").mkdir(parents=True)
    (site / "index.html").write_text("<h1>hi</h1>")
    (site / "css" / "site.css").write_text("body{}")

    events: list[tuple[str, str]] = []
    uploaded, skipped, deleted = R.publish(fake_remote, site, "public_html",
                                           progress=lambda rel, st: events.append((rel, st)))
    assert (uploaded, skipped, deleted) == (2, 0, 0)
    assert fake_remote.read("public_html/index.html") == b"<h1>hi</h1>"
    assert fake_remote.read("public_html/css/site.css") == b"body{}"
    assert ("public_html", "mkdir") in events

    # Second run: size-unchanged files are skipped; an edit re-uploads just that file.
    uploaded, skipped, deleted = R.publish(fake_remote, site, "public_html")
    assert (uploaded, skipped, deleted) == (0, 2, 0)
    (site / "index.html").write_text("<h1>hello!</h1>")
    uploaded, skipped, deleted = R.publish(fake_remote, site, "public_html")
    assert (uploaded, skipped, deleted) == (1, 1, 0)
    assert fake_remote.read("public_html/index.html") == b"<h1>hello!</h1>"


def test_publish_delete_prunes_remote_orphans(tmp_path, fake_remote):
    site = tmp_path / "public"
    (site / "css").mkdir(parents=True)
    (site / "index.html").write_text("<h1>hi</h1>")
    (site / "css" / "site.css").write_text("body{}")
    R.publish(fake_remote, site, "public_html")

    # The next build drops css/ and gains app.js; stale.html appears remote-only.
    fake_remote.write("public_html/stale.html", b"old")
    (site / "css" / "site.css").unlink()
    (site / "css").rmdir()
    (site / "app.js").write_text("x()")

    # Default publish leaves orphans (documented behavior)…
    uploaded, skipped, deleted = R.publish(fake_remote, site, "public_html")
    assert deleted == 0
    assert fake_remote.exists("public_html/stale.html")
    assert fake_remote.exists("public_html/css")

    # …--delete prunes them: the orphan file and the orphan subtree (1 each).
    events: list[tuple[str, str]] = []
    uploaded, skipped, deleted = R.publish(fake_remote, site, "public_html", delete=True,
                                           progress=lambda rel, st: events.append((rel, st)))
    assert deleted == 2
    assert not fake_remote.exists("public_html/stale.html")
    assert not fake_remote.exists("public_html/css")
    assert fake_remote.read("public_html/app.js") == b"x()"
    assert fake_remote.read("public_html/index.html") == b"<h1>hi</h1>"
    assert ("stale.html", "deleted") in events
    assert ("css/", "deleted") in events


def test_publish_requires_a_directory(tmp_path, fake_remote):
    f = tmp_path / "index.html"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        R.publish(fake_remote, f, "public_html")


def test_publish_rejects_traversal_paths(tmp_path, fake_remote):
    site = tmp_path / "site"
    site.mkdir()
    with pytest.raises(ValueError, match=r"\.\."):
        R.publish(fake_remote, site, "../escape")
    with pytest.raises(ValueError, match=r"\.\."):
        R.publish(fake_remote, site, "site/../../escape")


def test_publish_delete_rejects_login_root(tmp_path, fake_remote):
    site = tmp_path / "site"
    site.mkdir()
    with pytest.raises(ValueError, match="non-empty"):
        R.publish(fake_remote, site, "", delete=True)
    with pytest.raises(ValueError, match="non-empty"):
        R.publish(fake_remote, site, "/", delete=True)


def test_publish_delete_rejects_non_directory_docroot(tmp_path, fake_remote):
    site = tmp_path / "site"
    site.mkdir()
    fake_remote.write("notadir", b"x")
    with pytest.raises(NotADirectoryError, match="must be a directory"):
        R.publish(fake_remote, site, "notadir", delete=True)


# --- attach-to-turn (P4): the live remote file rides the /doc channel ---------------


class _FakeState:
    def __init__(self):
        self.attached_docs: list[tuple[str, str]] = []

    def attach_doc(self, name, body):
        self.attached_docs = [(n, b) for n, b in self.attached_docs if n != name]
        self.attached_docs.append((name, body))

    def detach_doc(self, name):
        before = len(self.attached_docs)
        self.attached_docs = [(n, b) for n, b in self.attached_docs if n != name]
        return len(self.attached_docs) != before


def test_attach_remote_file(fake_remote):
    from xlii.attach import attach_address, attachable, detach_address, is_attached

    vfs_write("ftp://local/index.html", b"<h1>deployed</h1>")
    state = _FakeState()
    assert attachable("ftp://local/index.html")
    assert attach_address(state, "ftp://local/index.html")
    assert state.attached_docs == [("ftp:local/index.html", "<h1>deployed</h1>")]
    assert is_attached(state, "ftp://local/index.html")
    assert detach_address(state, "ftp://local/index.html")
    assert not is_attached(state, "ftp://local/index.html")
    # picker / host home / binary don't ride
    assert not attach_address(state, "ftp://")
    assert not attach_address(state, "ftp://local")
    vfs_write("ftp://local/logo.png", b"\x89PNG\xff\xfe")
    assert not attach_address(state, "ftp://local/logo.png")


# --- the SFTP branch degrades cleanly without paramiko -------------------------------


def test_sftp_missing_paramiko_yields_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "paramiko", None)  # import paramiko → ImportError
    conn = R.RemoteFsConnection("vps", {"host": "h", "protocol": "sftp", "user": "u"})
    with pytest.raises(RuntimeError, match=r"pip install 'xlii\[remote\]'"):
        conn.connect()


# --- command surfaces (P2) are registered --------------------------------------------


def test_command_surfaces_registered():
    from xlii.cli import build_parser
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    # /remote is canonical; /ftp survives as the hidden legacy alias.
    remote = find_repl_command("/remote", "code")
    assert remote is not None and remote.name == "remote"
    legacy = find_repl_command("/ftp", "code")
    assert legacy is not None and legacy.name == "remote"
    sub = next(a for a in build_parser()._actions
               if a.__class__.__name__ == "_SubParsersAction")
    assert "remote" in sub.choices and "ftp" in sub.choices  # ftp = argparse alias


# --- the handler seam + honest schemes (remote-backends Base) -----------------------


def test_handler_registry_resolves_and_rejects():
    from xlii import remote_handlers as H

    from xlii.remote_handlers.ftp import FtpHandler
    from xlii.remote_handlers.sftp import SftpHandler

    assert H.get_handler_class("ftp") is FtpHandler
    assert H.get_handler_class("ftps") is FtpHandler      # same ftplib wire, TLS on top
    assert H.get_handler_class("sftp") is SftpHandler
    with pytest.raises(ValueError, match="protocol"):
        H.get_handler_class("gopher")


def test_scheme_maps_are_honest():
    # D2 (proposals/remote-backends.md): a connection is addressed by the scheme
    # matching its actual wire — pre-seated for the W/M backends.
    assert R.scheme_for_protocol("ftp") == "ftp"
    assert R.scheme_for_protocol("ftps") == "ftp"
    assert R.scheme_for_protocol("sftp") == "sftp"
    assert R.scheme_for_protocol("webdav") == "dav"
    assert R.scheme_for_protocol("smb") == "smb"
    assert R.protocols_for_scheme("ftp") == ("ftp", "ftps")
    assert R.protocols_for_scheme("dav") == ("webdav",)
    assert R.REMOTE_SCHEMES == ("ftp", "sftp", "dav", "smb")


def test_dav_and_smb_schemes_are_registered_scaffold():
    # Base registers the providers; the handlers land with vectors W/M. A bare
    # picker just lists no connections until then.
    assert supports_vfs("dav") and supports_write("dav")
    assert supports_vfs("smb") and supports_write("smb")


def test_picker_filters_by_protocol_but_names_resolve_leniently(monkeypatch):
    """dav:// lists only WebDAV connections (honest pickers) while ftp://<name>
    still resolves a connection of any protocol (saved addresses never break)."""
    specs = {
        "site": {"host": "h1", "protocol": "ftp"},
        "box": {"host": "h2", "protocol": "sftp"},
    }
    monkeypatch.setattr(R.manager, "names", lambda: sorted(specs))
    monkeypatch.setattr(R.manager, "spec", lambda n: specs.get(n))

    from xlii.addressing import vfs_list

    ftp_names = [n.name for n in vfs_list("ftp://")]
    sftp_names = [n.name for n in vfs_list("sftp://")]
    dav_names = [n.name for n in vfs_list("dav://")]
    assert ftp_names == ["site"]          # the sftp box is NOT under ftp://
    assert sftp_names == ["box"]
    assert dav_names == []                # fixture has no webdav-protocol specs
    # remote:// is the union picker (the TUI doorway's view): EVERY connection,
    # each node addressed by its honest wire scheme — no host can vanish.
    union = {n.name: n.address for n in vfs_list("remote://")}
    assert union == {"site": "ftp://site", "box": "sftp://box"}
    # …and name-resolution stays lenient: ftp://box still resolves the sftp box.
    from xlii.addressing import resolve

    assert resolve("ftp://box").ok


def test_cli_alias_not_duplicated_in_generated_docs():
    """The hidden `ftp` argparse alias must not render as a duplicate command
    family — docgen dedupes aliases by parser identity (first name wins)."""
    from xlii.cli import build_parser
    from xlii.docgen import _iter_leaf_commands

    progs = [prog for prog, _h, _p in _iter_leaf_commands(build_parser(), "xlii")]
    assert any(p.startswith("xlii remote ") for p in progs)
    assert not any(p.startswith("xlii ftp") for p in progs)


def test_add_connection_validates_per_protocol_fields():
    # A truthy extra another protocol owns is refused at the door…
    with pytest.raises(ValueError, match="key_path"):
        R.add_connection("x", host="h", protocol="ftp", key_path="/id_rsa")
    # …while falsy extras are dropped (callers may pass every flag they know).
    # (No config write happens on the failure path above; this one is validated
    # in-memory too — protocol gate fires before any I/O.)
    with pytest.raises(ValueError, match="unknown protocol"):
        R.add_connection("x", host="h", protocol="s3")  # s3's round, not yet
    # webdav + smb are REGISTERED protocols now (vectors W + M) — the gate must
    # accept them (their positive add/vault roundtrips live in their own files).
    assert "webdav" in R.PROTOCOLS and "smb" in R.PROTOCOLS


def test_add_connection_refuses_credentials_in_base_url():
    """A user:pass@host base_url would persist the password in PLAINTEXT
    config.json and echo it in the saved-line — the one secret per connection
    lives in the vault, never in an address. Refused at the door, before any
    vault/config write."""
    with pytest.raises(ValueError, match="credential"):
        R.add_connection("cloud", protocol="webdav",
                         base_url="https://alice:hunter2@cloud.example.com/dav")


def test_connection_spec_rides_for_handlers():
    conn = R.RemoteFsConnection("x", {"host": "h", "protocol": "ftp", "share": "docs"})
    assert conn.spec["share"] == "docs"   # handlers read per-protocol extras here
    assert conn.port == 21                # DEFAULT_PORTS drives the default
    assert R.RemoteFsConnection("y", {"host": "h", "protocol": "sftp"}).port == 22


def test_guided_add_walks_the_wire_fields(monkeypatch):
    """/remote add <name> with no flags prompts protocol-first, then only that
    wire's fields, and lands on add_connection with the vault secret."""
    from xlii.repl_cmds import remote as RC

    got = {}
    monkeypatch.setattr(R, "add_connection",
                        lambda name, **kw: got.update(name=name, **kw) or {"protocol": kw.get("protocol")})
    answers = iter(["sftp", "example.net", "2222", "bob", "/home/bob/.ssh/id_ed25519"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "hunter2")
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO("x"))
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)

    class _Console:
        def print(self, *a, **k):
            pass

    RC._add(_Console(), ["mybox"])
    assert got["name"] == "mybox" and got["protocol"] == "sftp"
    assert got["host"] == "example.net" and got["port"] == "2222" and got["user"] == "bob"
    assert got["key_path"].endswith("id_ed25519") and got["secret"] == "hunter2"


def test_add_accepts_scheme_url_positional_and_key_alias(monkeypatch):
    """`/remote add <name> sftp://user@host[:port] --key <p>` — the module's own
    address form doubles as the add form (URL → protocol/host/user/port), and
    `--key` aliases `--key-path`. A later flag still applies. Regression: the URL
    used to be read as a stray token ("bad flag: sftp://…")."""
    from xlii.repl_cmds import remote as RC

    got: dict = {}
    monkeypatch.setattr(R, "add_connection",
                        lambda name, **kw: got.update(name=name, **kw) or {"protocol": kw.get("protocol")})
    monkeypatch.setattr("xlii.console_prompt.request_line", lambda *a, **k: "")  # no secret

    class _Console:
        def print(self, *a, **k):
            pass

    RC._add(_Console(), ["ec2", "sftp://admin@18.217.25.123:2222", "--key",
                         "/home/me/.ssh/id.pem"])
    assert got["name"] == "ec2" and got["protocol"] == "sftp"
    assert got["host"] == "18.217.25.123" and got["user"] == "admin"
    assert got["port"] == 2222
    assert got["key_path"].endswith("id.pem")


def test_ls_error_preserves_bracketed_install_hint(monkeypatch):
    """A RuntimeError whose text carries pip's bracketed extra
    (…install 'xlii[remote]') must render intact through Rich, which would
    otherwise eat [remote] as a style tag and mangle the hint to 'xlii'. Uses a
    REAL recording Console so the markup is actually rendered."""
    import io

    from rich.console import Console

    from xlii.repl_cmds import remote as RC

    hint = ("SFTP support needs the optional 'paramiko' package — "
            "install it with: pip install 'xlii[remote]'")
    monkeypatch.setattr("xlii.addressing.vfs_list",
                        lambda addr: (_ for _ in ()).throw(RuntimeError(hint)))
    con = Console(file=io.StringIO(), force_terminal=False, width=200)
    RC._ls(con, "sftp://xliiec2/")  # full URL → _addr_of returns it verbatim
    out = con.file.getvalue()
    assert "xlii[remote]" in out   # the extra survived; not mangled to 'xlii'


def test_sftp_connect_expands_tilde_in_key_path(monkeypatch):
    """The stored key_path keeps the portable literal (~/…); paramiko doesn't
    expand ~, so connect() must expanduser it or the SSH open fails with
    "No such file or directory: '~/…'". Regression for that live error. Uses a
    fake paramiko (lazy-imported inside connect) to capture the key_filename."""
    import os
    import sys
    import types

    from xlii.remote_handlers.sftp import SftpHandler

    captured: dict = {}

    class _FakeChan:
        def settimeout(self, t):
            pass

    class _FakeSftp:
        def get_channel(self):
            return _FakeChan()

    class _FakeClient:
        def load_system_host_keys(self):
            pass

        def set_missing_host_key_policy(self, p):
            pass

        def connect(self, host, **kw):
            captured.update(host=host, **kw)

        def open_sftp(self):
            return _FakeSftp()

    fake = types.ModuleType("paramiko")
    fake.SSHClient = _FakeClient
    fake.AutoAddPolicy = object
    monkeypatch.setitem(sys.modules, "paramiko", fake)

    conn = R.RemoteFsConnection("ec2", {"host": "h", "protocol": "sftp",
                                        "user": "admin", "key_path": "~/.ssh/id.pem"})
    SftpHandler(conn).connect()
    assert captured["key_filename"] == os.path.expanduser("~/.ssh/id.pem")
    assert "~" not in captured["key_filename"]


def test_opts_from_url_maps_scheme_and_refuses_embedded_password():
    from xlii.repl_cmds.remote import _opts_from_url

    assert _opts_from_url("dav://files.example.com")["protocol"] == "webdav"
    assert _opts_from_url("ftp://h")["protocol"] == "ftp"
    with pytest.raises(ValueError, match="password"):
        _opts_from_url("sftp://admin:hunter2@h")
    with pytest.raises(ValueError, match="unknown scheme"):
        _opts_from_url("bogus://h")


# --- live FTP round-trip (local throwaway server; skipped without pyftpdlib) ---------


@pytest.fixture
def ftp_server(tmp_path):
    pytest.importorskip("pyftpdlib")
    import threading

    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer

    home = tmp_path / "ftp-home"
    home.mkdir()
    authorizer = DummyAuthorizer()
    authorizer.add_user("u", "p", str(home), perm="elradfmwMT")
    handler = FTPHandler
    handler.authorizer = authorizer
    server = FTPServer(("127.0.0.1", 0), handler)
    port = server.socket.getsockname()[1]
    t = threading.Thread(target=server.serve_forever, kwargs={"blocking": True}, daemon=True)
    t.start()
    try:
        yield ("127.0.0.1", port, home)
    finally:
        server.close_all()
        t.join(timeout=5)


def test_live_ftp_roundtrip(ftp_server):
    host, port, home = ftp_server
    conn = R.RemoteFsConnection(
        "live", {"host": host, "port": port, "user": "u", "protocol": "ftp"}, secret="p",
    )
    conn.connect()
    try:
        conn.makedirs("public_html/css")
        conn.write("public_html/index.html", b"<h1>hi</h1>")
        assert conn.read("public_html/index.html") == b"<h1>hi</h1>"
        assert (home / "public_html" / "index.html").read_bytes() == b"<h1>hi</h1>"
        assert conn.stat("public_html") == (True, None)
        is_dir, size = conn.stat("public_html/index.html")
        assert not is_dir and size == len(b"<h1>hi</h1>")
        names = {n for n, _, _ in conn.listdir("public_html")}
        assert names == {"css", "index.html"}
        conn.rmtree("public_html")
        assert not conn.exists("public_html")
    finally:
        conn.close()
    assert not conn.connected


def test_live_ftp_through_provider_and_publish(ftp_server, monkeypatch, tmp_path):
    host, port, home = ftp_server
    spec = {"host": host, "port": port, "user": "u", "protocol": "ftp", "vault_ref": "live"}
    monkeypatch.setattr(R, "_vault_secret", lambda ref: "p")
    monkeypatch.setattr(R.manager, "_specs", staticmethod(lambda: {"live": spec}))
    try:
        vfs_write("ftp://live/hello.txt", b"hello")
        assert vfs_read("ftp://live/hello.txt") == b"hello"
        assert [n.name for n in vfs_list("ftp://")] == ["live"]

        site = tmp_path / "site"
        (site / "css").mkdir(parents=True)  # a subdir → lists a not-yet-existing remote dir
        (site / "index.html").write_text("deployed")
        (site / "css" / "site.css").write_text("body{}")
        uploaded, skipped, deleted = R.publish(R.manager.get("live"), site, "public_html")
        assert (uploaded, skipped, deleted) == (2, 0, 0)
        assert (home / "public_html" / "index.html").read_text() == "deployed"
        assert (home / "public_html" / "css" / "site.css").read_text() == "body{}"

        # missing remote dir surfaces as the uniform OSError family, not ftplib noise
        with pytest.raises(FileNotFoundError):
            R.manager.get("live").listdir("no/such/dir")
    finally:
        R.manager.close_all()


# --- unpublish (app-serving S3): the destructive inverse, --yes-gated ---------------


def _unpub_args(dest: str, yes: bool = False):
    from types import SimpleNamespace

    return SimpleNamespace(dest=dest, yes=yes)


def test_cmd_unpublish_refuses_without_yes(fake_remote):
    from xlii.cmds.remote import cmd_unpublish

    fake_remote.mkdir("public_html")
    fake_remote.write("public_html/index.html", b"x")
    assert cmd_unpublish(_unpub_args("ftp://local/public_html")) == 1
    assert fake_remote.exists("public_html/index.html")   # nothing deleted


def test_cmd_unpublish_deletes_docroot_with_yes(fake_remote):
    from xlii.cmds.remote import cmd_unpublish

    fake_remote.mkdir("public_html")
    fake_remote.mkdir("public_html/css")
    fake_remote.write("public_html/index.html", b"x")
    fake_remote.write("public_html/css/site.css", b"y")
    assert cmd_unpublish(_unpub_args("ftp://local/public_html", yes=True)) == 0
    assert not fake_remote.exists("public_html")


def test_cmd_unpublish_never_touches_the_login_root(fake_remote):
    from xlii.cmds.remote import cmd_unpublish

    fake_remote.mkdir("public_html")
    assert cmd_unpublish(_unpub_args("ftp://local/", yes=True)) == 1
    assert cmd_unpublish(_unpub_args("ftp://local", yes=True)) == 1
    assert fake_remote.exists("public_html")


def test_cmd_unpublish_missing_path_is_an_error(fake_remote):
    from xlii.cmds.remote import cmd_unpublish

    assert cmd_unpublish(_unpub_args("ftp://local/nope", yes=True)) == 1


def test_cmd_unpublish_rejects_traversal(fake_remote):
    from xlii.cmds.remote import cmd_unpublish

    fake_remote.mkdir("public_html")
    assert cmd_unpublish(_unpub_args("ftp://local/../public_html", yes=True)) == 1
    assert fake_remote.exists("public_html")


def test_cmd_unpublish_rejects_non_directory_docroot(fake_remote):
    from xlii.cmds.remote import cmd_unpublish

    fake_remote.write("notadir", b"x")
    assert cmd_unpublish(_unpub_args("ftp://local/notadir", yes=True)) == 1
    assert fake_remote.exists("notadir")
