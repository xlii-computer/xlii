"""Vector D — the StorageBackend seam (two backends, one protocol).

Unit coverage for the seam itself: LocalBackend document CRUD, CollectionsBackend
routing through the vendor client (retry + unknown-field fallback + lifecycle),
the selection factory, and the offline degrade *at the t_search_project call
site*. The cross-backend conformance suite lives in Vector E
(tests/test_storage_conformance.py) — this file guards the implementation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.config import GlobalConfig
from xlii.storage import LocalIndex, local_search_text
from xlii.storage_backend import (
    CollectionsBackend,
    LocalBackend,
    SearchHit,
    StorageBackend,
    get_backend,
)
from tests.helpers import make_project


# --------------------------------------------------------------------------- #
#  LocalBackend — a first-class SQLite FTS5 document store
# --------------------------------------------------------------------------- #

def _local(tmp_path):
    proj = make_project(tmp_path, local_only=True)
    return LocalBackend(proj, cfg=GlobalConfig())


def test_local_upload_search_and_list(tmp_path):
    be = _local(tmp_path)
    did = be.upload("auth.py", b"def verify_password(): return bcrypt.checkpw()", {"xli_sha256": "abc"})
    assert did
    hits = be.search("password bcrypt")
    assert hits and hits[0].source == "auth.py"
    assert isinstance(hits[0], SearchHit) and "bcrypt" in hits[0].text
    listing = be.list()
    assert listing == {"auth.py": {"doc_id": did, "sha256": "abc", "name": "auth.py"}}


def test_local_update_reflects_new_content_stable_id(tmp_path):
    be = _local(tmp_path)
    did = be.upload("m.py", b"alpha content here", {"xli_sha256": "1"})
    same = be.update(did, "m.py", b"beta replaced text", {"xli_sha256": "2"})
    assert same == did                              # doc_id stable across update
    assert be.search("alpha") == []                 # old content gone
    assert be.search("beta")[0].source == "m.py"    # new content searchable
    assert be.list()["m.py"]["sha256"] == "2"


def test_local_delete_removes_from_search_and_list(tmp_path):
    be = _local(tmp_path)
    did = be.upload("gone.py", b"temporary noodles", {})
    be.delete(did)
    assert be.search("noodles") == []
    assert be.list() == {}


def test_local_reupload_same_relpath_is_idempotent(tmp_path):
    be = _local(tmp_path)
    first = be.upload("dup.py", b"one", {"xli_sha256": "s1"})
    second = be.upload("dup.py", b"two updated", {"xli_sha256": "s2"})
    assert first == second                          # same relpath -> same doc
    assert len(be.list()) == 1
    assert be.list()["dup.py"]["sha256"] == "s2"


def test_local_empty_index_and_unknown_mode(tmp_path):
    be = _local(tmp_path)
    assert be.search("anything") == []              # empty store searches cleanly
    be.upload("u.py", "café — naïve — 日本語 — payload".encode("utf-8"), {})
    # unicode round-trips through storage; unknown retrieval_mode must not raise
    hit = be.search("payload", retrieval_mode="dense-nonsense")[0]
    assert hit.source == "u.py" and "日本語" in hit.text


def test_local_backend_satisfies_protocol(tmp_path):
    assert isinstance(_local(tmp_path), StorageBackend)


# --------------------------------------------------------------------------- #
#  CollectionsBackend — routes through the vendor client (fake)
# --------------------------------------------------------------------------- #

class _FakeColls:
    def __init__(self):
        self.uploaded = []
        self.updated = []
        self.removed = []
        self.searched = []
        self.created = []
        self.deleted = []
        self.upload_unknown_field_once = False
        self.transient_once = False

    def upload_document(self, collection_id, name, data, fields=None):
        if self.upload_unknown_field_once and fields is not None:
            self.upload_unknown_field_once = False
            raise RuntimeError("Unknown field xli_relpath in field_definitions")
        if self.transient_once:
            self.transient_once = False
            raise RuntimeError("INTERNAL: Failed to create storage ledger file")
        self.uploaded.append((collection_id, name, fields))
        return SimpleNamespace(file_metadata=SimpleNamespace(file_id=f"f{len(self.uploaded)}"))

    def update_document(self, collection_id, file_id, name, data, fields=None):
        self.updated.append((collection_id, file_id, name, fields))
        return SimpleNamespace(file_metadata=SimpleNamespace(file_id=file_id))

    def remove_document(self, collection_id, file_id):
        self.removed.append((collection_id, file_id))

    def search(self, query, collection_ids, limit, retrieval_mode):
        self.searched.append((query, list(collection_ids), limit, retrieval_mode))
        return SimpleNamespace(results=[
            SimpleNamespace(text="hit body", score=0.9,
                            file_metadata=SimpleNamespace(name="doc.md")),
        ])

    def create(self, name, field_definitions=None):
        cid = f"coll-{len(self.created)}"
        self.created.append((name, field_definitions))
        return SimpleNamespace(collection_id=cid)

    def delete(self, collection_id):
        self.deleted.append(collection_id)

    def list(self, limit=500, pagination_token=None):
        cols = [SimpleNamespace(collection_id="c1", collection_name="one"),
                SimpleNamespace(collection_id="c2", collection_name="two")]
        return SimpleNamespace(collections=cols, pagination_token=None)


def _clients(colls):
    return SimpleNamespace(xai=SimpleNamespace(collections=colls))


def test_collections_upload_update_delete_route(tmp_path):
    colls = _FakeColls()
    be = CollectionsBackend(_clients(colls), ["cX"])
    did = be.upload("a.py", b"x", {"xli_relpath": "a.py"})
    assert did == "f1" and colls.uploaded[0][0] == "cX"
    assert be.update("f1", "a.py", b"y", {}) == "f1"
    assert colls.updated[0][:2] == ("cX", "f1")
    be.delete("f1")
    assert colls.removed == [("cX", "f1")]


def test_collections_search_returns_searchhits_over_all_ids(tmp_path):
    colls = _FakeColls()
    be = CollectionsBackend(_clients(colls), ["cA", "cB"])
    hits = be.search("q", limit=7, retrieval_mode="hybrid")
    assert colls.searched == [("q", ["cA", "cB"], 7, "hybrid")]
    assert hits == [SearchHit(text="hit body", source="doc.md", score=0.9)]


def test_collections_upload_unknown_field_fallback(tmp_path):
    colls = _FakeColls()
    colls.upload_unknown_field_once = True
    be = CollectionsBackend(_clients(colls), ["cX"])
    did = be.upload("a.py", b"x", {"xli_relpath": "a.py"})
    # retried without fields; the surviving call recorded fields=None
    assert did == "f1" and colls.uploaded[0][2] is None


def test_collections_upload_unknown_field_fallback_logs_relpath(tmp_path, monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr("xlii.storage_backend.logger.warning", lambda msg: messages.append(msg))
    colls = _FakeColls()
    colls.upload_unknown_field_once = True
    be = CollectionsBackend(_clients(colls), ["cX"])
    be.upload("src/a.py", b"x", {"xli_relpath": "src/a.py"})
    assert messages
    assert "uploading src/a.py without metadata fields" in messages[0]
    assert "{name}" not in messages[0]


def test_collections_upload_applies_retry(monkeypatch):
    # The backend absorbs _with_rate_limit_retry: a transient error is retried.
    import xlii.sync as sync_mod
    monkeypatch.setattr(sync_mod, "_sleep", lambda *_a, **_k: None)
    colls = _FakeColls()
    colls.transient_once = True
    be = CollectionsBackend(_clients(colls), ["cX"])
    assert be.upload("a.py", b"x", {}) == "f1"       # succeeded on retry


def test_collections_list_remaps_and_lifecycle(tmp_path):
    colls = _FakeColls()
    clients = _clients(colls)
    # list_collections pages the account
    assert CollectionsBackend.list_collections(clients) == {"c1": "one", "c2": "two"}
    # create/delete lifecycle
    meta = CollectionsBackend.create_collection(clients, "xlii/proj", [{"key": "k"}])
    assert meta.collection_id == "coll-0"
    CollectionsBackend.delete_collection(clients, "coll-0")
    assert colls.deleted == ["coll-0"]


def test_collections_backend_satisfies_protocol(tmp_path):
    assert isinstance(CollectionsBackend(_clients(_FakeColls()), ["c"]), StorageBackend)


# --------------------------------------------------------------------------- #
#  Selection — today's rule in one place
# --------------------------------------------------------------------------- #

def test_get_backend_defaults_by_collection_id(tmp_path):
    proj = make_project(tmp_path)
    proj.collection_id = "c1"
    assert isinstance(get_backend(_clients(_FakeColls()), proj), CollectionsBackend)
    proj.collection_id = ""
    assert isinstance(get_backend(None, proj), LocalBackend)


def test_get_backend_storage_key_overrides(tmp_path):
    proj = make_project(tmp_path)
    proj.collection_id = "c1"
    proj.storage = "local"                           # explicit override wins
    assert isinstance(get_backend(None, proj), LocalBackend)
    proj.collection_id = ""
    proj.storage = "collections"
    with pytest.raises(ValueError, match="requires a non-empty collection_id"):
        get_backend(_clients(_FakeColls()), proj)


# --------------------------------------------------------------------------- #
#  Offline degrade — ComposedBackend + LocalIndex floor (OQ 5)
# --------------------------------------------------------------------------- #

def test_t_search_project_degrades_to_local_when_remote_raises(tmp_path):
    from xlii.tools import ToolContext, t_search_project

    (tmp_path / "readme.md").write_text("This project parses invoices.\n")
    proj = make_project(tmp_path)
    proj.collection_id = "c1"                         # collections-backed -> tries remote
    LocalIndex(proj).rebuild(GlobalConfig())          # the offline floor

    class _Boom:
        def search(self, *a, **k):
            raise RuntimeError("network down")

    clients = SimpleNamespace(xai=SimpleNamespace(collections=_Boom()))
    ctx = ToolContext(project=proj, clients=clients, cfg=GlobalConfig())
    res = t_search_project(ctx, {"query": "invoices"})
    assert res.is_error is False
    assert "degraded to local index" in res.content
    assert "readme.md" in res.content


def test_t_search_project_local_only_unchanged(tmp_path):
    from xlii.tools import ToolContext, t_search_project

    proj = make_project(tmp_path, local_only=True)    # collection_id None -> local floor
    ctx = ToolContext(project=proj, clients=None, cfg=GlobalConfig())
    res = t_search_project(ctx, {"query": "anything"})
    assert res.is_error is True
    assert "no local index yet" in res.content


def test_composed_backend_writes_primary_only(tmp_path):
    from xlii.storage_backend import ComposedBackend, LocalBackend, LocalIndexFloor

    proj = make_project(tmp_path, local_only=True)
    primary = LocalBackend(proj)
    floor = LocalIndexFloor(proj)
    composed = ComposedBackend(primary, floor)
    doc_id = composed.upload("a.md", b"hello world", {"sha256": "x"})
    assert primary.list()["a.md"]["doc_id"] == doc_id
    with pytest.raises(NotImplementedError):
        floor.upload("b.md", b"nope", {})


def test_composed_backend_degrades_search(tmp_path):
    from xlii.storage_backend import ComposedBackend, LocalIndexFloor

    (tmp_path / "n.md").write_text("needle in a haystack\n")
    proj = make_project(tmp_path)
    LocalIndex(proj).rebuild(GlobalConfig())

    class _Boom:
        def search(self, *a, **k):
            raise ConnectionError("offline")
        def upload(self, *a, **k):
            raise AssertionError("write")
        def update(self, *a, **k):
            raise AssertionError("write")
        def delete(self, *a, **k):
            raise AssertionError("write")
        def list(self):
            return {}

    composed = ComposedBackend(_Boom(), LocalIndexFloor(proj))
    hits = composed.search("needle", limit=5)
    assert composed.degraded_from is not None
    assert isinstance(composed.degraded_from, ConnectionError)
    assert hits and any(h.source == "n.md" for h in hits)


def test_local_only_sync_populates_local_backend(tmp_path):
    """OQ 4: local-only sync persists through LocalBackend, not only LocalIndex."""
    from xlii.storage_backend import LocalBackend
    from xlii.sync import sync_project

    (tmp_path / "note.md").write_text("findme UNIQUEKEYBACKEND\n")
    proj = make_project(tmp_path, local_only=True)
    sync_project(None, proj, GlobalConfig())
    backend = LocalBackend(proj)
    hits = backend.search("UNIQUEKEYBACKEND", limit=5)
    assert hits and any("note.md" in h.source for h in hits)
    # Round-trip: LocalIndex floor still rebuilt too.
    text = local_search_text(proj, "UNIQUEKEYBACKEND")
    assert text and "note.md" in text


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
