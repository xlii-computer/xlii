"""Storage-backend conformance suite (Vector E of ``open-substrate-plan``).

The substrate's durable value — what ``search_project`` can recall — must never
be trapped in one vendor API. ``open-substrate-plan`` extracts a five-method
``StorageBackend`` protocol so a third-party memory backend (Pinecone, pgvector,
Chroma, …) can be dropped in. This file is the *proof of conformance*: an adapter
author implements the five methods, runs this suite against their backend, and —
if it passes — knows they satisfy the contract without ever reading our internals.

CONTRACT-FIRST
--------------
The source of truth is the protocol block in ``proposals/open-substrate-plan.md``
(Vector D), **not** the in-progress backend code. Until that code lands, this file
self-supplies a faithful reference ``LocalBackend`` (SQLite FTS5, mirroring
``xlii.storage.LocalIndex``) so the suite has a conforming backend to prove green
against. See the RECONCILIATION SEAM below — when Vector D lands
``xlii.storage_backend``, its real ``LocalBackend`` / ``CollectionsBackend`` are
picked up automatically and this reference steps aside.

RUNNING IT
----------
    pytest tests/test_storage_conformance.py                       # -> local (reference)
    pytest tests/test_storage_conformance.py --backend=local       # explicit
    pytest tests/test_storage_conformance.py --backend=collections # env-gated
    pytest tests/test_storage_conformance.py --backend=mybackend   # register yours below

``--backend`` is repeatable. With no flag the suite runs every conforming backend
that is available here (always ``local``; ``collections`` too once its class exists
and ``XLII_CONFORMANCE_COLLECTION_ID`` names a disposable collection). See
``docs/help/topics/storage-backends.md`` for the author on-ramp.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import time
from dataclasses import dataclass

import pytest

from xlii.config import GlobalConfig

# ===========================================================================
# RECONCILIATION SEAM  (Vector E <-> Vector D — reconcile at integrator review)
# ---------------------------------------------------------------------------
# Vector D promotes xlii.storage.LocalIndex to a first-class LocalBackend and
# wraps xai_sdk in a CollectionsBackend, both in a new module. We do not know its
# final import path or constructor signatures, so we degrade gracefully: if the
# module is importable we prefer its classes (see _make_backend); otherwise we run
# the reference LocalBackend defined in this file. The integrator wires the two
# real backends into the two INTEGRATOR HOOK points below.
# ===========================================================================
try:  # Vector D artifact — may not exist on this branch yet.
    import xlii.storage_backend as _real_backends
except Exception:  # pragma: no cover - absence is the contract-first default
    _real_backends = None


# ===========================================================================
# Reference implementations (fixtures, not shipped code)
# ===========================================================================
@dataclass(frozen=True)
class SearchHit:
    """The minimum SearchHit shape the protocol promises: (text, source, score).

    Conformance assertions duck-type these three attributes, so a real backend is
    free to return its own richer SearchHit type.
    """

    text: str
    source: str
    score: float


class ReferenceLocalBackend:
    """A faithful, minimal ``StorageBackend`` over SQLite FTS5.

    This mirrors ``xlii.storage.LocalIndex`` semantics (BM25, query sanitization)
    but exposes the protocol's five methods. It exists so the conformance suite
    has an always-available conforming backend to prove green against while Vector
    D is in flight; Vector D's real ``LocalBackend`` replaces it at integration.
    """

    def __init__(self, db_path):
        self.path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5("
            "doc_id UNINDEXED, relpath UNINDEXED, name UNINDEXED, "
            "sha256 UNINDEXED, content)"
        )
        return con

    @staticmethod
    def _doc_id(relpath: str) -> str:
        # Stable per relpath -> re-upload of the same relpath is idempotent.
        return "local-" + hashlib.sha1(relpath.encode("utf-8")).hexdigest()[:16]

    def _write(self, doc_id: str, relpath: str, data: bytes, fields: dict) -> str:
        content = data.decode("utf-8", errors="replace")
        name = fields.get("name") or relpath          # name: taken from caller fields
        sha = hashlib.sha256(data).hexdigest()        # sha256: computed from content
        con = self._connect()
        try:
            with con:
                con.execute("DELETE FROM docs WHERE doc_id = ? OR relpath = ?", (doc_id, relpath))
                con.execute(
                    "INSERT INTO docs(doc_id, relpath, name, sha256, content) VALUES (?,?,?,?,?)",
                    (doc_id, relpath, name, sha, content),
                )
        finally:
            con.close()
        return doc_id

    def upload(self, relpath: str, data: bytes, fields: dict) -> str:
        return self._write(self._doc_id(relpath), relpath, data, fields)

    def update(self, doc_id: str, relpath: str, data: bytes, fields: dict) -> str:
        return self._write(doc_id, relpath, data, fields)

    def delete(self, doc_id: str) -> None:
        con = self._connect()
        try:
            with con:
                con.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
        finally:
            con.close()

    def list(self) -> dict:
        con = self._connect()
        try:
            cur = con.execute("SELECT relpath, doc_id, sha256, name FROM docs")
            return {
                rel: {"doc_id": did, "sha256": sha, "name": name}
                for rel, did, sha, name in cur.fetchall()
            }
        finally:
            con.close()

    def search(self, query: str, *, limit: int = 10, retrieval_mode=None) -> list:
        # retrieval_mode is advisory: FTS has a single mode, so we accept it and
        # ignore it (the contract forbids raising on an unknown mode).
        terms = re.findall(r"[^\W_]+", query, flags=re.UNICODE)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        con = self._connect()
        try:
            cur = con.execute(
                "SELECT relpath, snippet(docs, 4, '', '', ' … ', 20), bm25(docs) "
                "FROM docs WHERE docs MATCH ? ORDER BY bm25(docs) LIMIT ?",
                (match, limit),
            )
            # bm25() is a cost (lower = better); negate so higher = better.
            return [SearchHit(text=snip, source=rel, score=-score) for rel, snip, score in cur.fetchall()]
        except sqlite3.OperationalError:
            return []
        finally:
            con.close()


class NonconformingBackend:
    """Deliberately broken: it *accepts* every call but persists nothing.

    The suite MUST reject this — that is the proof the conformance checks have
    teeth. It even gets the trivial cases right (an empty index searches clean),
    so the suite fails it for LOSING DATA, not indiscriminately.
    """

    def upload(self, relpath, data, fields):
        return "nonconforming-doc-id"  # a plausible id, but ...

    def update(self, doc_id, relpath, data, fields):
        return doc_id

    def delete(self, doc_id):
        pass

    def list(self):
        return {}  # ... nothing is ever stored ...

    def search(self, query, *, limit=10, retrieval_mode=None):
        return []  # ... and nothing is ever found.


# ===========================================================================
# Backend selection / parametrization
# ===========================================================================
# The `backend` fixture is indirectly parametrized: `request.param` is a backend
# NAME resolved through _make_backend. `pytest_generate_tests` decides which names
# run (from --backend, or the default set). The reference LocalBackend always
# runs; credentialed backends skip cleanly when their creds/classes are absent.


def _creds_available() -> bool:
    """Collections is env-gated on a disposable collection id the operator opts in."""
    return bool(os.environ.get("XLII_CONFORMANCE_COLLECTION_ID"))


def _make_backend(name: str, tmp_path):
    if name == "local":
        # Vector D's real LocalBackend (wired at integration); the reference below
        # only serves branches where xlii.storage_backend has not landed.
        if _real_backends is not None and hasattr(_real_backends, "LocalBackend"):
            return _build_real_local(_real_backends.LocalBackend, tmp_path)
        return ReferenceLocalBackend(tmp_path / "conformance_local.db")

    if name == "collections":
        cls = getattr(_real_backends, "CollectionsBackend", None) if _real_backends else None
        if cls is None:
            pytest.skip("CollectionsBackend not integrated yet (Vector D pending)")
        if not _creds_available():
            pytest.skip("set XLII_CONFORMANCE_COLLECTION_ID (a disposable collection) + xAI key")
        return _build_real_collections(cls, os.environ["XLII_CONFORMANCE_COLLECTION_ID"], tmp_path)

    raise pytest.UsageError(
        f"unknown --backend {name!r}; register it in tests/test_storage_conformance.py "
        f"(_make_backend) — see docs/help/topics/storage-backends.md"
    )


def _build_real_local(cls, tmp_path):
    # Vector D's LocalBackend takes a ProjectConfig (its db lands in project.xli_dir);
    # build a tmp-rooted local-only project, the tests/test_storage.py _project shape.
    import json

    from xlii.config import ProjectConfig

    d = tmp_path / ".xlii"
    d.mkdir(exist_ok=True)
    (d / "project.json").write_text(json.dumps({
        "name": "conformance", "root": str(tmp_path.resolve()), "collection_id": "",
        "created_at": "2026-01-01", "conversation_id": "x", "local_only": True,
    }))
    return cls(ProjectConfig.load(tmp_path))


def _build_real_collections(cls, collection_id, tmp_path):
    # The real CollectionsBackend against the operator's disposable collection.
    # Requires a configured xAI key pair (management key included) on top of the
    # XLII_CONFORMANCE_COLLECTION_ID opt-in; skips loudly when either is absent.
    from xlii.client import Clients, MissingCredentials
    from xlii.config import GlobalConfig

    try:
        clients = Clients.from_config(GlobalConfig.load(), require_management=True)
    except MissingCredentials as e:
        pytest.skip(f"collections conformance needs configured xAI credentials: {e}")
    backend = cls(clients, [collection_id])
    # Collections chunks/embeds server-side — poll past the indexing lag.
    backend.conformance_search_retries = (12, 2.5)
    return backend


def _default_backend_names() -> list:
    names = ["local"]
    if _real_backends is not None and hasattr(_real_backends, "CollectionsBackend") and _creds_available():
        names.append("collections")
    return names


def pytest_generate_tests(metafunc):
    if "backend" not in metafunc.fixturenames:
        return
    selected = metafunc.config.getoption("backend", default=None)
    names = list(selected) if selected else _default_backend_names()
    metafunc.parametrize("backend", names, indirect=True, ids=names)


@pytest.fixture
def backend(request, tmp_path):
    return _make_backend(request.param, tmp_path)


# ===========================================================================
# Eventual-consistency helper
# ===========================================================================
# LocalBackend is synchronous: a search right after an upload sees it. A managed
# backend (Collections) chunks/embeds server-side, so a hit can lag. A backend may
# advertise `conformance_search_retries = (tries, delay_seconds)`; synchronous
# backends leave it unset -> a single immediate attempt (zero added latency).


def _search_until(backend, query, predicate, *, retrieval_mode=None, limit=10):
    tries, delay = getattr(backend, "conformance_search_retries", (1, 0.0))
    hits: list = []
    for i in range(max(1, tries)):
        hits = backend.search(query, limit=limit, retrieval_mode=retrieval_mode)
        if predicate(hits):
            return hits
        if i + 1 < tries:
            time.sleep(delay)
    return hits


# ===========================================================================
# Contract checks (shared by the parametrized tests and the teeth test)
# ===========================================================================
def _fields(relpath: str, data: bytes) -> dict:
    """Protocol-normalized document metadata.

    The protocol passes `fields: dict[str, str]` opaquely. By convention the suite
    supplies `relpath`, `name`, and a true `sha256`: a backend that derives
    `list()`'s name/sha256 from caller fields reads these keys; one that computes
    them itself may ignore them. (CollectionsBackend maps these to its `xli_*`
    field schema internally — see the Vector E report's contract findings.)
    """
    return {"relpath": relpath, "name": relpath, "sha256": hashlib.sha256(data).hexdigest()}


def _sources(hits) -> list:
    return [getattr(h, "source", "") or "" for h in hits]


def _assert_hit_shape(hits) -> None:
    for h in hits:
        assert hasattr(h, "text"), "SearchHit must carry .text"
        assert hasattr(h, "source"), "SearchHit must carry .source"
        assert hasattr(h, "score"), "SearchHit must carry .score"


def check_upload_search_list(backend) -> str:
    data = b"def verify_password(pw):\n    return check(pw)  # UNIQUETOKENALPHA\n"
    doc_id = backend.upload("svc/auth.py", data, _fields("svc/auth.py", data))
    assert doc_id, "upload must return a non-empty doc_id"

    listing = backend.list()
    assert "svc/auth.py" in listing, "list() must report an uploaded relpath"
    entry = listing["svc/auth.py"]
    assert entry["doc_id"] == doc_id, "list() doc_id must match upload()'s return value"
    assert entry.get("sha256"), "list() entry must carry a sha256 fingerprint"
    assert entry.get("name"), "list() entry must carry a name"

    hits = _search_until(backend, "UNIQUETOKENALPHA", lambda hs: any("svc/auth.py" in s for s in _sources(hs)))
    _assert_hit_shape(hits)
    assert any("svc/auth.py" in s for s in _sources(hits)), "search must find uploaded content by its source"
    return doc_id


def check_update_reflected(backend) -> None:
    old = b"the OLDWORDONLY marker content here\n"
    doc_id = backend.upload("note.md", old, _fields("note.md", old))
    old_sha = backend.list()["note.md"]["sha256"]

    new = b"the NEWWORDONLY marker content here\n"
    returned = backend.update(doc_id, "note.md", new, _fields("note.md", new))

    listing = backend.list()
    assert "note.md" in listing, "updated relpath must still be listed"
    assert listing["note.md"]["doc_id"] == returned, "list() must report the doc_id update() returned"
    assert listing["note.md"]["sha256"] != old_sha, "list() sha256 must change when content changes"

    fresh = _search_until(backend, "NEWWORDONLY", lambda hs: any("note.md" in s for s in _sources(hs)))
    assert any("note.md" in s for s in _sources(fresh)), "search must reflect the updated content"
    stale = _search_until(backend, "OLDWORDONLY", lambda hs: not any("note.md" in s for s in _sources(hs)))
    assert not any("note.md" in s for s in _sources(stale)), "search must not still return the pre-update content"


def check_delete_removes(backend) -> None:
    data = b"DELETABLEMARKER content payload\n"
    doc_id = backend.upload("tmp/del.txt", data, _fields("tmp/del.txt", data))
    assert "tmp/del.txt" in backend.list()

    backend.delete(doc_id)
    assert "tmp/del.txt" not in backend.list(), "delete must remove the relpath from list()"

    hits = _search_until(backend, "DELETABLEMARKER", lambda hs: not any("tmp/del.txt" in s for s in _sources(hs)))
    assert not any("tmp/del.txt" in s for s in _sources(hits)), "delete must make search miss the removed content"


def check_reupload_idempotent(backend) -> None:
    data = b"IDEMPOTENTMARKER payload token\n"
    backend.upload("dup.py", data, _fields("dup.py", data))
    second = backend.upload("dup.py", data, _fields("dup.py", data))

    listing = backend.list()
    assert list(listing).count("dup.py") == 1, "re-uploading a relpath must not create a duplicate entry"
    # End-state is a single live document whose id list() reflects (id-stability
    # itself is backend-defined — see the report's idempotency contract finding).
    assert listing["dup.py"]["doc_id"] == second, "list() must reflect the most recent upload of a relpath"

    hits = _search_until(backend, "IDEMPOTENTMARKER", lambda hs: any("dup.py" in s for s in _sources(hs)))
    assert any("dup.py" in s for s in _sources(hits)), "re-uploaded content must still be searchable"


def check_empty_index_search_clean(backend) -> None:
    hits = backend.search("nothinghasbeenuploadedyet")
    assert isinstance(hits, list), "search on an empty index must return a list"
    assert hits == [], "search on an empty index must return no hits (and must not raise)"


def check_unicode_content(backend) -> None:
    data = "Café serveur — 日本語 σύστημα UNICODEMARKER résumé\n".encode("utf-8")
    backend.upload("i18n/notes.md", data, _fields("i18n/notes.md", data))

    hits = _search_until(backend, "UNICODEMARKER", lambda hs: any("i18n/notes.md" in s for s in _sources(hs)))
    assert any("i18n/notes.md" in s for s in _sources(hits)), "unicode content must upload and be searchable"
    # A multibyte query term must not raise, whatever the tokenizer does with it.
    backend.search("日本語")
    backend.search("café")


def check_max_file_bytes_boundary(backend) -> None:
    limit = GlobalConfig().max_file_bytes
    token = b"BOUNDARYMARKER "
    filler = b"filler "
    body = token + filler * ((limit - len(token)) // len(filler))
    body = body + b"z" * (limit - len(body))
    assert len(body) == limit, "test setup: body must be exactly max_file_bytes"

    backend.upload("big.txt", body, _fields("big.txt", body))
    assert "big.txt" in backend.list(), "a boundary-sized document must be listed"

    hits = _search_until(backend, "BOUNDARYMARKER", lambda hs: any("big.txt" in s for s in _sources(hs)))
    assert any("big.txt" in s for s in _sources(hits)), "a max_file_bytes-boundary document must be findable"


def check_unknown_retrieval_mode_ok(backend) -> None:
    data = b"MODEPROBE searchable content\n"
    backend.upload("m.txt", data, _fields("m.txt", data))
    # retrieval_mode is advisory: an unknown mode must be accepted, never raised on.
    backend.search("MODEPROBE", retrieval_mode="totally-unknown-mode-xyz")
    backend.search("MODEPROBE", retrieval_mode=None)


# ===========================================================================
# The parametrized conformance suite
# ===========================================================================
def test_upload_then_search_and_list(backend):
    check_upload_search_list(backend)


def test_update_is_reflected(backend):
    check_update_reflected(backend)


def test_delete_removes_from_search_and_list(backend):
    check_delete_removes(backend)


def test_reupload_same_relpath_idempotent(backend):
    check_reupload_idempotent(backend)


def test_empty_index_search_is_clean(backend):
    check_empty_index_search_clean(backend)


def test_unicode_content(backend):
    check_unicode_content(backend)


def test_max_file_bytes_boundary(backend):
    check_max_file_bytes_boundary(backend)


def test_unknown_retrieval_mode_does_not_raise(backend):
    check_unknown_retrieval_mode_ok(backend)


# ===========================================================================
# Teeth: the suite must reject a broken backend
# ===========================================================================
def test_conformance_suite_has_teeth(tmp_path):
    """A broken backend must fail loudly; a conforming one must pass the same check."""
    # Positive control: the reference backend passes the core invariant.
    check_upload_search_list(ReferenceLocalBackend(tmp_path / "teeth_ok.db"))

    broken = NonconformingBackend()
    # It gets the trivial case right (empty search is clean) ...
    check_empty_index_search_clean(broken)
    # ... but the suite fails it the moment it loses an uploaded document,
    # on BOTH the list() and the search() invariants.
    with pytest.raises(AssertionError):
        check_upload_search_list(broken)
    with pytest.raises(AssertionError):
        check_delete_removes(broken)
