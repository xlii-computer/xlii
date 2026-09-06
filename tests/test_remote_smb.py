r"""SMB backend (proposals/remote-backends.md, Vector M): the SmbHandler wire,
its UNC path mapping, the smb spec fields through add_connection, and the
smb:// picker.

No network and no smbprotocol requirement: every test injects a fake
``smbclient`` module via sys.modules (the handler imports it lazily inside
connect(), the paramiko precedent), so the real dependency never loads.

The fake models the parts of smbclient that bit the verify round, so the
handler is tested against the REAL contract:

* a **process-global session pool keyed (server, port)** holding per-user
  credentials — every op function *requires* matching ``port=``/``username=``
  kwargs and raises if they're missing or resolve no registered session
  (exactly how a bare op would dial default-445 / ride "the first session
  found" in real smbclient);
* **SMBOSError-shaped errors** — OSError *subclasses* carrying ``errno``
  (never FileNotFoundError instances), so the not-found promotion is real;
* scandir entries carrying ``smb_info`` (the cached query response) — the
  handler must read that, never per-entry ``stat()`` round-trips.
"""

from __future__ import annotations

import errno
import io
import sys

import pytest

from xlii import remotefs as R
from xlii.remote_handlers import smb as smb_mod


class _SmbOSError(OSError):
    """SMBOSError stand-in: an OSError subclass with errno seated manually.

    Subclassing matters — ``OSError(errno.ENOENT, …)`` auto-maps to
    FileNotFoundError in CPython, but smbprotocol's SMBOSError does not, which
    is precisely why the handler must promote not-found shapes itself.
    """

    def __init__(self, msg: str, eno: int):
        super().__init__(msg)
        self.errno = eno


def _nf(unc: str) -> _SmbOSError:
    return _SmbOSError(f"{unc}: STATUS_OBJECT_NAME_NOT_FOUND", errno.ENOENT)


_ATTR_DIR = 0x0010     # FILE_ATTRIBUTE_DIRECTORY
_ATTR_NORMAL = 0x0080  # FILE_ATTRIBUTE_NORMAL


class _Entry:
    """smbclient.scandir entry lookalike: name + cached ``smb_info`` from the
    query-directory response (dict-shaped; the handler's field reader accepts
    plain values as well as smbprotocol's ``.get_value()`` structures)."""

    def __init__(self, name: str, is_dir: bool, size: int):
        self.name = name
        self.smb_info = {
            "file_attributes": _ATTR_DIR if is_dir else _ATTR_NORMAL,
            # dirs report a junk allocation size — the handler must None it
            "end_of_file": size,
        }

    def stat(self, follow_symlinks=True):  # pragma: no cover — guard rail
        raise AssertionError(
            "listdir must read the cached smb_info, not stat() — stat() costs "
            "one extra SMB round-trip per entry and follows symlinks")


class _BrokenEntry:
    """A scandir entry whose cached info is unreadable (the dangling-symlink /
    malformed-response analog) — the handler must skip it, not blow up."""

    name = "dangling.lnk"
    smb_info: dict = {}  # no fields → extraction raises


class _Writer(io.BytesIO):
    def __init__(self, store: dict, key: str):
        super().__init__()
        self._store, self._key = store, key

    def close(self):
        if not self.closed:
            self._store[self._key] = self.getvalue()
        super().close()


class FakeSmbClient:
    """Global-pool ``smbclient`` module lookalike, keyed by full UNC path.

    Sessions live in ``pool[(server, port)] = {username: password}`` — the
    same process-global keying real smbclient uses — and every op function
    validates its ``port=``/``username=``/``password=`` kwargs against it
    before touching the in-memory tree.
    """

    def __init__(self):
        self.pool: dict[tuple[str, int], dict] = {}
        self.sessions: list[tuple[str, dict]] = []   # register_session calls
        self.deleted: list[tuple[str, int]] = []     # delete_session calls
        self.files: dict[str, bytes] = {}
        self.dirs: set[str] = set()
        self.opened: list[tuple[str, str]] = []
        self.op_users: list[tuple[str, str, "str | None"]] = []  # (op, unc, username)
        self.broken_entries: list = []               # injected into every scandir

    # -- session lifecycle -- #

    def register_session(self, server, username=None, password=None, port=445, **kw):
        self.sessions.append((server, {"username": username, "password": password,
                                       "port": port, **kw}))
        self.pool.setdefault((server, port), {})[username] = password

    def delete_session(self, server, port=445, **kw):
        self.deleted.append((server, port))
        self.pool.pop((server, port), None)

    # -- the session-resolution contract every op must honor -- #

    def _resolve(self, op: str, unc: str, kw: dict):
        server = unc.lstrip("\\").split("\\", 1)[0]
        if "port" not in kw:
            raise AssertionError(
                f"{op}({unc}): no port= — real smbclient would dial default 445")
        if "username" not in kw:
            raise AssertionError(
                f"{op}({unc}): no username= — real smbclient would ride the "
                "first pooled session for the server")
        sessions = self.pool.get((server, kw["port"]))
        if sessions is None:
            raise AssertionError(
                f"{op}({unc}): no registered session for {(server, kw['port'])} "
                "— the op would dial a fresh connection")
        user = kw["username"]
        if user not in sessions or sessions[user] != kw.get("password"):
            raise AssertionError(
                f"{op}({unc}): credentials for {user!r} don't match the "
                "registered session")
        self.op_users.append((op, unc, user))

    # -- fs ops (UNC in, SMBOSError-shaped errors out) -- #

    @staticmethod
    def _parent(unc: str) -> str:
        return unc.rsplit("\\", 1)[0]

    def _children(self, unc: str):
        prefix = unc + "\\"
        for f in self.files:
            if f.startswith(prefix):
                yield f
        for d in self.dirs:
            if d.startswith(prefix):
                yield d

    def scandir(self, unc: str, **kw):
        self._resolve("scandir", unc, kw)
        if unc not in self.dirs:
            raise _nf(unc)
        prefix = unc + "\\"
        entries = list(self.broken_entries)
        for f, data in sorted(self.files.items()):
            if f.startswith(prefix) and "\\" not in f[len(prefix):]:
                entries.append(_Entry(f[len(prefix):], False, len(data)))
        for d in sorted(self.dirs):
            if d.startswith(prefix) and "\\" not in d[len(prefix):]:
                entries.append(_Entry(d[len(prefix):], True, 4096))
        return iter(entries)

    def open_file(self, unc: str, mode: str = "rb", **kw):
        self._resolve("open_file", unc, kw)
        self.opened.append((unc, mode))
        if mode == "rb":
            if unc not in self.files:
                raise _nf(unc)
            return io.BytesIO(self.files[unc])
        if self._parent(unc) not in self.dirs:
            raise _nf(unc)
        return _Writer(self.files, unc)

    def remove(self, unc: str, **kw):
        self._resolve("remove", unc, kw)
        if unc not in self.files:
            raise _nf(unc)
        del self.files[unc]

    def rmdir(self, unc: str, **kw):
        self._resolve("rmdir", unc, kw)
        if unc not in self.dirs:
            raise _nf(unc)
        if any(self._children(unc)):
            raise _SmbOSError(f"{unc}: STATUS_DIRECTORY_NOT_EMPTY", errno.ENOTEMPTY)
        self.dirs.discard(unc)

    def mkdir(self, unc: str, **kw):
        self._resolve("mkdir", unc, kw)
        if self._parent(unc) not in self.dirs:
            raise _nf(self._parent(unc))
        if unc in self.dirs:
            raise _SmbOSError(f"{unc}: STATUS_OBJECT_NAME_COLLISION", errno.EEXIST)
        self.dirs.add(unc)


ROOT = "\\\\nas.local\\media"


@pytest.fixture(autouse=True)
def _fresh_session_refs(monkeypatch):
    """The handler refcounts smbclient's global pool per (host, port) — keep
    that module-level state hermetic per test."""
    monkeypatch.setattr(smb_mod, "_SESSION_REFS", {})


@pytest.fixture
def smb_conn(monkeypatch):
    """A domain-joined smb connection wired to a fresh FakeSmbClient."""
    fake = FakeSmbClient()
    fake.dirs.add(ROOT)
    monkeypatch.setitem(sys.modules, "smbclient", fake)
    conn = R.RemoteFsConnection(
        "nas",
        {"host": "nas.local", "protocol": "smb", "share": "media",
         "domain": "CORP", "user": "bob"},
        secret="s3cret-smb",
    )
    return conn, fake


# --- the registry line: smb resolves to the handler --------------------------------


def test_registry_resolves_smb():
    from xlii import remote_handlers as H
    from xlii.remote_handlers.smb import SmbHandler

    assert H.get_handler_class("smb") is SmbHandler
    assert "smb" in R.PROTOCOLS
    assert R.scheme_for_protocol("smb") == "smb"
    assert R.protocols_for_scheme("smb") == ("smb",)


# --- degrade + validate cleanly -----------------------------------------------------


def test_missing_smbprotocol_yields_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "smbclient", None)  # import → ImportError
    conn = R.RemoteFsConnection("nas", {"host": "h", "protocol": "smb", "share": "s"})
    with pytest.raises(RuntimeError, match=r"pip install 'xlii\[smb\]'"):
        conn.connect()
    assert not conn.connected


def test_missing_share_is_a_clean_oserror(monkeypatch):
    fake = FakeSmbClient()
    monkeypatch.setitem(sys.modules, "smbclient", fake)
    conn = R.RemoteFsConnection("nas", {"host": "h", "protocol": "smb"})
    with pytest.raises(OSError, match="share"):
        conn.connect()
    assert not conn.connected
    assert fake.sessions == []  # refused at the door — no session attempt


def test_auth_failure_normalizes_to_oserror(monkeypatch):
    # SMBAuthenticationError et al. are NOT OSError — the connection layer
    # normalizes them so callers guarding on (OSError, RuntimeError) survive.
    class _AuthBoom(Exception):
        pass

    fake = FakeSmbClient()

    def _fail(server, **kw):
        raise _AuthBoom("STATUS_LOGON_FAILURE")

    fake.register_session = _fail
    monkeypatch.setitem(sys.modules, "smbclient", fake)
    conn = R.RemoteFsConnection("nas", {"host": "h", "protocol": "smb", "share": "s"})
    with pytest.raises(OSError, match="connect"):
        conn.connect()


# --- session shape, per-op routing + UNC path building -------------------------------


def test_session_carries_domain_user_port_and_timeout(smb_conn):
    conn, fake = smb_conn
    conn.connect()
    assert conn.connected
    (server, kw), = fake.sessions
    assert server == "nas.local"
    assert kw["username"] == "CORP\\bob"       # the DOMAIN\user form
    assert kw["password"] == "s3cret-smb"
    assert kw["port"] == 445                   # DEFAULT_PORTS seats smb's 445
    assert kw["connection_timeout"] == conn._timeout


def test_nonstandard_port_rides_every_op(monkeypatch):
    """smbclient's pool is keyed server:port — an op without port= would dial a
    fresh default-445 connection. Prove the whole primitive surface stays on
    10445 (the fake raises on any op whose port/credentials miss the session)."""
    fake = FakeSmbClient()
    fake.dirs.add("\\\\box\\backup")
    monkeypatch.setitem(sys.modules, "smbclient", fake)
    conn = R.RemoteFsConnection(
        "box", {"host": "box", "protocol": "smb", "share": "backup",
                "user": "alice", "port": 10445})
    conn.connect()
    (server, kw), = fake.sessions
    assert (server, kw["username"], kw["port"]) == ("box", "alice", 10445)
    assert kw["password"] is None
    conn.mkdir("dumps")
    conn.write("dumps/latest.tar", b"tar!")
    assert conn.read("dumps/latest.tar") == b"tar!"
    assert conn.listdir("") == [("dumps", True, None)]
    conn.delete("dumps/latest.tar")
    conn.rmdir("dumps")
    # every op resolved the registered (box, 10445) session as alice
    assert fake.op_users and all(u == "alice" for _op, _unc, u in fake.op_users)


def test_two_users_same_host_ride_their_own_credentials(monkeypatch):
    """Two xlii connections to one host:port under different accounts must not
    ride whichever session registered first — each op carries its own
    username/password (the fake rejects mismatched credentials)."""
    fake = FakeSmbClient()
    fake.dirs.update({"\\\\nas.local\\media", "\\\\nas.local\\private"})
    monkeypatch.setitem(sys.modules, "smbclient", fake)
    bob = R.RemoteFsConnection(
        "nas-bob", {"host": "nas.local", "protocol": "smb", "share": "media",
                    "domain": "CORP", "user": "bob"}, secret="pw-bob")
    alice = R.RemoteFsConnection(
        "nas-alice", {"host": "nas.local", "protocol": "smb", "share": "private",
                      "user": "alice"}, secret="pw-alice")
    bob.write("shared.txt", b"from bob")
    alice.write("diary.txt", b"from alice")
    assert bob.read("shared.txt") == b"from bob"
    assert alice.read("diary.txt") == b"from alice"
    by_user = {u for _op, _unc, u in fake.op_users}
    assert by_user == {"CORP\\bob", "alice"}
    # …and each op ran under the right account for its share
    for _op, unc, user in fake.op_users:
        assert user == ("CORP\\bob" if "\\media" in unc else "alice")


def test_unc_paths_for_subdirs(smb_conn):
    conn, fake = smb_conn
    conn.mkdir("movies")
    conn.mkdir("movies/scifi")
    conn.write("movies/scifi/list.txt", b"dune")
    assert ROOT + "\\movies\\scifi" in fake.dirs
    assert fake.files[ROOT + "\\movies\\scifi\\list.txt"] == b"dune"
    assert (ROOT + "\\movies\\scifi\\list.txt", "wb") in fake.opened
    assert conn.read("/movies/scifi/list.txt") == b"dune"  # leading / tolerated
    assert (ROOT + "\\movies\\scifi\\list.txt", "rb") in fake.opened


# --- primitives over the fake wire ---------------------------------------------------


def test_listdir_maps_entries_and_nones_dir_sizes(smb_conn):
    conn, fake = smb_conn
    fake.dirs.add(ROOT + "\\photos")
    fake.files[ROOT + "\\notes.txt"] = b"hello nas"
    entries = conn.listdir("")
    assert set(entries) == {("photos", True, None), ("notes.txt", False, 9)}
    assert conn.listdir("photos") == []


def test_listdir_skips_broken_entries(smb_conn):
    # One dangling-symlink/malformed entry must not kill the whole listing
    # (per-entry extraction is fenced; no per-entry stat() round-trips either —
    # _Entry.stat raises AssertionError if the handler regresses to it).
    conn, fake = smb_conn
    fake.files[ROOT + "\\ok.txt"] = b"fine"
    fake.broken_entries.append(_BrokenEntry())
    assert conn.listdir("") == [("ok.txt", False, 4)]


def test_read_missing_raises_filenotfound(smb_conn):
    conn, _fake = smb_conn
    with pytest.raises(FileNotFoundError):
        conn.read("nope.txt")
    with pytest.raises(FileNotFoundError):
        conn.listdir("no/such/dir")
    with pytest.raises(FileNotFoundError):
        conn.delete("nope.txt")


def test_rmdir_refuses_non_empty(smb_conn):
    conn, fake = smb_conn
    conn.mkdir("stash")
    conn.write("stash/keep.txt", b"x")
    with pytest.raises(OSError) as ei:
        conn.rmdir("stash")
    assert not isinstance(ei.value, FileNotFoundError)  # not-empty ≠ not-found
    conn.delete("stash/keep.txt")
    conn.rmdir("stash")
    assert ROOT + "\\stash" not in fake.dirs


def test_compositions_ride_the_primitives(smb_conn):
    # stat/makedirs/rmtree are protocol-neutral — prove they compose over smb.
    conn, fake = smb_conn
    conn.makedirs("a/b/c")
    conn.write("a/b/c/deep.txt", b"deep")
    assert conn.stat("a/b/c/deep.txt") == (False, 4)
    assert conn.stat("a/b") == (True, None)
    conn.rmtree("a")
    assert not conn.exists("a")
    assert ROOT + "\\a" not in fake.dirs and not fake.files


# --- close(): refcounted global-pool teardown ----------------------------------------


def test_close_is_idempotent_and_never_raises(smb_conn):
    conn, fake = smb_conn
    conn.connect()
    conn.close()
    assert not conn.connected
    assert fake.deleted == [("nas.local", 445)]  # last ref out → session dropped
    conn.close()  # second close: no-op, no second delete_session
    assert fake.deleted == [("nas.local", 445)]

    def _boom(server, **kw):
        raise RuntimeError("session already dead")

    fake.delete_session = _boom
    conn.connect()
    conn.close()  # a failing delete_session must not raise
    assert not conn.connected


def test_close_refcounts_the_shared_pool(monkeypatch):
    """smbclient pools ONE connection per (host, port) process-wide: closing
    one xlii connection must not tear the pooled session out from under a
    sibling — delete_session fires only when the last ref closes."""
    fake = FakeSmbClient()
    fake.dirs.update({"\\\\nas.local\\media", "\\\\nas.local\\private"})
    monkeypatch.setitem(sys.modules, "smbclient", fake)
    bob = R.RemoteFsConnection(
        "nas-bob", {"host": "nas.local", "protocol": "smb", "share": "media",
                    "user": "bob"}, secret="pw-bob")
    alice = R.RemoteFsConnection(
        "nas-alice", {"host": "nas.local", "protocol": "smb", "share": "private",
                      "user": "alice"}, secret="pw-alice")
    bob.connect()
    alice.connect()
    bob.close()
    assert fake.deleted == []                    # alice still rides the pool
    alice.write("still-alive.txt", b"yes")       # …and it still works
    assert alice.read("still-alive.txt") == b"yes"
    alice.close()
    assert fake.deleted == [("nas.local", 445)]  # last ref out → one teardown


# --- config + vault round-trip for the smb spec fields -------------------------------


def test_add_connection_smb_roundtrip(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet

    import xlii.vault as vault_mod
    from xlii.config import GLOBAL_CONFIG_FILE, GlobalConfig
    from xlii.vault import ENV_VAR, Vault

    # Hermetic vault (the test_admin_gate pattern): pin the vault file + master
    # key to tmp_path so this test never seeds the session-shared vault.enc out
    # from under test_remotefs's own roundtrip (which mints a different key).
    monkeypatch.setattr(vault_mod, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(vault_mod, "KEY_FILE", tmp_path / ".vault-key")
    monkeypatch.setenv(ENV_VAR, Fernet.generate_key().decode())

    entry = R.add_connection(
        "nas", host="nas.local", user="bob", protocol="smb",
        secret="s3cret-smb", share="media", domain="CORP",
    )
    assert entry["protocol"] == "smb"
    assert entry["share"] == "media" and entry["domain"] == "CORP"
    assert entry["vault_ref"] == "nas"

    raw = GLOBAL_CONFIG_FILE.read_text()
    assert "media" in raw and "s3cret-smb" not in raw
    cfg = GlobalConfig.load()
    assert cfg.ftp_connections["nas"]["share"] == "media"
    assert Vault.unlock().get(R.VAULT_FTP_NS)["nas"] == "s3cret-smb"

    assert R.remove_connection("nas")
    assert "nas" not in GlobalConfig.load().ftp_connections


def test_smb_fields_are_refused_on_other_wires():
    with pytest.raises(ValueError, match="share"):
        R.add_connection("x", host="h", protocol="ftp", share="docs")
    with pytest.raises(ValueError, match="domain"):
        R.add_connection("x", host="h", protocol="sftp", domain="CORP")
    # falsy extras are dropped — callers may pass every flag they know
    with pytest.raises(ValueError, match="needs a host"):
        R.add_connection("x", protocol="smb", share="", domain=None)


# --- the smb:// picker (honest schemes, D2) ------------------------------------------


def test_smb_picker_lists_smb_connections_only(monkeypatch):
    specs = {
        "nas": {"host": "nas.local", "protocol": "smb", "share": "media"},
        "site": {"host": "h1", "protocol": "ftp"},
    }
    monkeypatch.setattr(R.manager, "names", lambda: sorted(specs))
    monkeypatch.setattr(R.manager, "spec", lambda n: specs.get(n))

    from xlii.addressing import resolve, vfs_list

    smb_nodes = vfs_list("smb://")
    assert [n.name for n in smb_nodes] == ["nas"]
    assert smb_nodes[0].address == "smb://nas"
    assert smb_nodes[0].kind == "container"
    assert [n.name for n in vfs_list("ftp://")] == ["site"]  # nas not under ftp://
    assert resolve("smb://nas").ok
    # the union doorway shows it under its honest scheme
    union = {n.name: n.address for n in vfs_list("remote://")}
    assert union["nas"] == "smb://nas"


# --- the CLI flag form (xlii remote add --protocol smb --share --domain) -------------


def test_cli_add_parser_accepts_smb_flags():
    from xlii.cli import build_parser

    args = build_parser().parse_args(
        ["remote", "add", "nas", "--host", "nas.local", "--protocol", "smb",
         "--share", "media", "--domain", "CORP", "--user", "bob"])
    assert args.protocol == "smb"
    assert args.share == "media" and args.domain == "CORP"


def test_cli_cmd_add_passes_smb_fields(monkeypatch):
    import xlii.remotefs as RF
    from xlii.cli import build_parser

    got = {}

    def _fake_add(name, **kw):
        got.update(name=name, **kw)
        return {"protocol": kw.get("protocol"), "host": kw.get("host")}

    monkeypatch.setattr(RF, "add_connection", _fake_add)
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "s3cret-smb")
    args = build_parser().parse_args(
        ["remote", "add", "nas", "--host", "nas.local", "--protocol", "smb",
         "--share", "media", "--domain", "CORP"])
    assert args.func(args) == 0
    assert got["name"] == "nas" and got["protocol"] == "smb"
    assert got["share"] == "media" and got["domain"] == "CORP"
    assert got["secret"] == "s3cret-smb"
