"""Appwrite stock plugin — parse, install-stock, mocked plugin_call.

No live Appwrite. If parse_manifest cannot see the stock file, these
tests fail closed (that is a loader bug, not something to paper over).
"""

from __future__ import annotations

import json
from pathlib import Path

import xlii
from xlii.plugin_call import invoke_action, render_schema_output
from xlii.plugin_manifest import parse_manifest


STOCK = Path(xlii.__file__).parent / "stock_plugins" / "appwrite.md"

_ENV = {
    "APPWRITE_ENDPOINT": "https://appwrite.example.test/v1",
    "APPWRITE_PROJECT": "proj_demo",
    "APPWRITE_API_KEY": "key_demo",
}

_OFFICIAL_HEADERS = ("X-Appwrite-Project", "X-Appwrite-Key")


class _FakeResponse:
    status = 200

    def __init__(self, body: str = "{}") -> None:
        self._body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self) -> bytes:
        return self._body


def _raw() -> str:
    return STOCK.read_text(encoding="utf-8")


def _manifest():
    m = parse_manifest(_raw())
    assert m is not None, (
        "plugin_call cannot see appwrite — parse_manifest returned None. "
        "Do not invent a loader."
    )
    return m


_SERVER_READS = ("health", "databases", "buckets", "users", "functions")
_P1_READS = ("collections", "documents", "files")
_P1_WRITES = ("create_database", "create_collection", "create_bucket")


def test_stock_file_is_well_formed():
    raw = _raw()
    m = _manifest()
    assert m.plugin_id == "appwrite"
    assert m.effect == "external-write"
    assert m.trust == "subscription"
    assert {a.id for a in m.actions} >= {
        "health", "databases", "buckets", "account", "set",
        "collections", "documents", "files", "functions", "users",
        "create_database", "create_collection", "create_bucket",
    }
    for aid in _SERVER_READS:
        action = m.get_action(aid)
        assert action is not None
        assert action.method == "GET"
        assert action.url.startswith("${APPWRITE_ENDPOINT}/")
        for hdr in _OFFICIAL_HEADERS:
            assert hdr in action.headers
        assert action.headers["X-Appwrite-Project"] == "${APPWRITE_PROJECT}"
        assert action.headers["X-Appwrite-Key"] == "${APPWRITE_API_KEY}"
    account = m.get_action("account")
    assert account is not None
    assert account.method == "GET"
    assert "X-Appwrite-Key" not in account.headers
    assert account.headers["X-Appwrite-JWT"] == "${APPWRITE_JWT}"
    for aid in _P1_WRITES:
        action = m.get_action(aid)
        assert action is not None
        assert action.method == "POST"
        assert "application/json" in action.headers.get("Content-Type", "")
        assert action.headers["X-Appwrite-Key"] == "${APPWRITE_API_KEY}"
    collections = m.get_action("collections")
    assert "{databaseId}" in (collections.url or "")
    files = m.get_action("files")
    assert "{bucketId}" in (files.url or "")
    assert "cloud.appwrite.io" not in (m.get_action("health").url or "")
    assert "${APPWRITE_API_KEY}" in raw
    assert "${APPWRITE_JWT}" in raw
    # Names only — never a literal key.
    assert "key_demo" not in raw
    assert "sk-" not in raw.lower()
    status = (((account.output_schema or {}).get("properties") or {}).get("status") or {})
    assert status.get("type") == "string"


def test_list_stock_plugins_includes_appwrite():
    from xlii.plugin import list_stock_plugins, stock_markdown

    ids = [pid for pid, _text in list_stock_plugins()]
    assert "appwrite" in ids
    text = stock_markdown("appwrite")
    assert text is not None
    assert parse_manifest(text) is not None


def test_install_stock_writes_appwrite(tmp_path, monkeypatch):
    import xlii.plugin as plugin_mod

    dest = tmp_path / "plugins"
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", dest)
    plugin_mod._STOCK_TEXT = None
    installed, _skipped = plugin_mod.install_stock_plugins()
    assert "appwrite" in installed
    written = dest / "appwrite.md"
    assert written.is_file()
    m = parse_manifest(written.read_text(encoding="utf-8"))
    assert m is not None
    assert m.plugin_id == "appwrite"
    listed = {p.id for p in plugin_mod.list_plugins()}
    assert "appwrite" in listed
    live = plugin_mod.Plugin(id="appwrite")
    assert live.manifest() is not None
    assert live.manifest().get_action("health") is not None


def test_plugin_call_health_injects_official_headers(monkeypatch):
    raw = _raw()
    assert parse_manifest(raw) is not None
    seen: dict = {}

    def fake_urlopen(req, timeout, *, check_host=True):
        seen["url"] = req.full_url
        seen["check_host"] = check_host
        seen["project"] = req.get_header("X-appwrite-project")
        seen["key"] = req.get_header("X-appwrite-key")
        return _FakeResponse(json.dumps({"status": "pass", "ping": 12}))

    monkeypatch.setattr("xlii.plugin_call._open_plugin_url", fake_urlopen)
    result = invoke_action("appwrite", raw, "health", {}, env=_ENV)
    assert result.ok, result.error or result.body
    assert seen["url"] == "https://appwrite.example.test/v1/health"
    assert seen["project"] == "proj_demo"
    assert seen["key"] == "key_demo"
    assert seen["check_host"] is False  # ${APPWRITE_ENDPOINT} is vault-controlled
    assert "pass" in result.user_text
    assert "12" in result.user_text


def test_plugin_call_lists_and_whoami(monkeypatch):
    raw = _raw()
    assert parse_manifest(raw) is not None
    payloads = {
        "/databases": {"total": 1, "databases": [
            {"$id": "main", "name": "Main", "enabled": True},
        ]},
        "/storage/buckets": {"total": 1, "buckets": [
            {"$id": "files", "name": "Files", "enabled": True},
        ]},
        "/account": {"$id": "user1", "name": "Ada", "email": "ada@example.test", "status": True},
    }
    seen: list[str] = []

    def fake_urlopen(req, timeout, *, check_host=True):
        seen.append(req.full_url)
        path = req.full_url.split("example.test/v1", 1)[-1]
        body = payloads.get(path)
        assert body is not None, path
        return _FakeResponse(json.dumps(body))

    monkeypatch.setattr("xlii.plugin_call._open_plugin_url", fake_urlopen)

    dbs = invoke_action("appwrite", raw, "databases", {}, env=_ENV)
    buckets = invoke_action("appwrite", raw, "buckets", {}, env=_ENV)
    who = invoke_action("appwrite", raw, "account", {}, env=_ENV)
    assert dbs.ok and buckets.ok and who.ok
    assert "main" in dbs.user_text and "Main" in dbs.user_text
    assert "files" in buckets.user_text and "Files" in buckets.user_text
    assert "Ada" in who.user_text and "user1" in who.user_text
    assert seen == [
        "https://appwrite.example.test/v1/databases",
        "https://appwrite.example.test/v1/storage/buckets",
        "https://appwrite.example.test/v1/account",
    ]


def test_plugin_call_nested_reads_and_users(monkeypatch):
    raw = _raw()
    payloads = {
        "/databases/main/collections": {"total": 1, "collections": [
            {"$id": "notes", "name": "Notes", "enabled": True},
        ]},
        "/databases/main/collections/notes/documents": {"total": 1, "documents": [
            {"$id": "doc1", "title": "hello"},
        ]},
        "/storage/buckets/files/files": {"total": 1, "files": [
            {"$id": "f1", "name": "a.txt", "sizeOriginal": 12, "mimeType": "text/plain"},
        ]},
        "/functions": {"total": 1, "functions": [
            {"$id": "fn1", "name": "ping", "enabled": True, "runtime": "python-3.11"},
        ]},
        "/users": {"total": 1, "users": [
            {"$id": "u1", "name": "Ada", "email": "ada@example.test", "status": True},
        ]},
    }
    seen: list[str] = []

    def fake_urlopen(req, timeout, *, check_host=True):
        seen.append(req.full_url)
        path = req.full_url.split("example.test/v1", 1)[-1]
        body = payloads.get(path)
        assert body is not None, path
        return _FakeResponse(json.dumps(body))

    monkeypatch.setattr("xlii.plugin_call._open_plugin_url", fake_urlopen)
    cols = invoke_action("appwrite", raw, "collections", {"databaseId": "main"}, env=_ENV)
    docs = invoke_action(
        "appwrite", raw, "documents",
        {"databaseId": "main", "collectionId": "notes"}, env=_ENV,
    )
    files = invoke_action("appwrite", raw, "files", {"bucketId": "files"}, env=_ENV)
    fns = invoke_action("appwrite", raw, "functions", {}, env=_ENV)
    users = invoke_action("appwrite", raw, "users", {}, env=_ENV)
    assert cols.ok and docs.ok and files.ok and fns.ok and users.ok, (
        cols.error, docs.error, files.error, fns.error, users.error
    )
    assert "notes" in cols.user_text
    assert "doc1" in docs.user_text
    assert "a.txt" in files.user_text
    assert "ping" in fns.user_text
    assert "Ada" in users.user_text
    assert seen == [
        "https://appwrite.example.test/v1/databases/main/collections",
        "https://appwrite.example.test/v1/databases/main/collections/notes/documents",
        "https://appwrite.example.test/v1/storage/buckets/files/files",
        "https://appwrite.example.test/v1/functions",
        "https://appwrite.example.test/v1/users",
    ]


def test_plugin_call_account_sends_jwt_not_key(monkeypatch):
    raw = _raw()
    seen: dict = {}

    def fake_urlopen(req, timeout, *, check_host=True):
        seen["url"] = req.full_url
        seen["jwt"] = req.get_header("X-appwrite-jwt")
        seen["key"] = req.get_header("X-appwrite-key")
        return _FakeResponse(json.dumps({
            "$id": "user1", "name": "Ada", "email": "ada@example.test", "status": True,
        }))

    monkeypatch.setattr("xlii.plugin_call._open_plugin_url", fake_urlopen)
    env = {**_ENV, "APPWRITE_JWT": "jwt_demo"}
    who = invoke_action("appwrite", raw, "account", {}, env=env)
    assert who.ok, who.error or who.body
    assert seen["url"] == "https://appwrite.example.test/v1/account"
    assert seen["jwt"] == "jwt_demo"
    assert seen["key"] in (None, "")


def test_plugin_call_json_creates(monkeypatch):
    raw = _raw()
    seen: list[tuple[str, str, dict]] = []

    def fake_urlopen(req, timeout, *, check_host=True):
        body = req.data.decode("utf-8") if req.data else "{}"
        payload = json.loads(body)
        seen.append((req.get_method(), req.full_url, payload))
        minted = payload.get("databaseId") or payload.get("collectionId") or payload.get("bucketId")
        name = payload.get("name")
        return _FakeResponse(json.dumps({"$id": minted, "name": name, "enabled": True}))

    monkeypatch.setattr("xlii.plugin_call._open_plugin_url", fake_urlopen)
    db = invoke_action("appwrite", raw, "create_database", {"name": "lab"}, env=_ENV)
    col = invoke_action(
        "appwrite", raw, "create_collection",
        {"databaseId": "main", "name": "notes"}, env=_ENV,
    )
    buck = invoke_action("appwrite", raw, "create_bucket", {"name": "files"}, env=_ENV)
    assert db.ok and col.ok and buck.ok, (db.error, col.error, buck.error)
    assert "lab" in db.user_text and "unique()" in db.user_text
    assert "notes" in col.user_text
    assert "files" in buck.user_text
    methods, urls, bodies = zip(*seen)
    assert methods == ("POST", "POST", "POST")
    assert urls == (
        "https://appwrite.example.test/v1/databases",
        "https://appwrite.example.test/v1/databases/main/collections",
        "https://appwrite.example.test/v1/storage/buckets",
    )
    assert bodies[0]["databaseId"] == "unique()" and bodies[0]["name"] == "lab"
    assert bodies[1]["collectionId"] == "unique()" and bodies[1]["name"] == "notes"
    assert "databaseId" not in bodies[1]
    assert bodies[2]["bucketId"] == "unique()" and bodies[2]["name"] == "files"


def test_schema_renders_appwrite_lists():
    m = _manifest()
    dbs = m.get_action("databases")
    out = render_schema_output(dbs, json.dumps({
        "total": 1,
        "databases": [{"$id": "main", "name": "Main", "enabled": True}],
    }))
    assert "main" in out and "Main" in out

    buckets = m.get_action("buckets")
    out = render_schema_output(buckets, json.dumps({
        "total": 1,
        "buckets": [{"$id": "files", "name": "Files", "enabled": False}],
    }))
    assert "files" in out and "Files" in out
