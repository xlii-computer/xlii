"""StorageBackend seam — the project's memory substrate as a swappable protocol.

Two implementations prove the protocol composes (open-substrate plan, Vector D):

  * ``CollectionsBackend`` — the xAI Collections managed-RAG path (server-side
    chunking, embedding, ``retrieval_mode``). This is the **single home** for the
    vendor ``xai`` collections client: the 429/backoff retry, the
    transient-server-error sniffing, the legacy unknown-field fallback, and the
    collection-lifecycle calls (create / delete / list) all live here now.
  * ``LocalBackend`` — a first-class SQLite FTS5 *document* store, promoting the
    ``xlii.storage`` LocalIndex idea from offline floor to a real backend a
    local-only project (or the conformance suite) can upload / update / delete /
    list / search against.

``retrieval_mode`` is **advisory**: CollectionsBackend passes it through;
LocalBackend accepts and ignores it (FTS has one mode). Neither errors on an
unknown mode.

Selection follows today's rule (``collection_id`` present -> collections;
absent -> local); an explicit project-level ``storage`` key overrides.

``ComposedBackend`` (OQ 5 follow-up) wraps a primary backend with a read-only
``LocalIndexFloor``: writes go to primary only; ``search`` tries primary and
on failure serves the LocalIndex floor (the remote→local degrade that used to
live as a try/except at ``t_search_project``). Exactly two *base* backends
remain — CollectionsBackend and LocalBackend; ComposedBackend is composition,
not a third store.

The retry / error-sniff helpers physically remain in ``xlii.sync`` (they are the
patch targets ``tests/test_sync_planner.py`` pins); CollectionsBackend imports
and applies them, so the *responsibility* for retry lives with the backend even
though the *functions* stay put.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

from loguru import logger

from xlii.client import iter_collection_documents

if TYPE_CHECKING:
    from pathlib import Path

    from xlii.config import GlobalConfig, ProjectConfig


LOCAL_BACKEND_DB = "local_backend.db"


@dataclass
class SearchHit:
    """One retrieval result. ``(text, source, score)`` is the contract minimum,
    shaped to render today's ``t_search_project`` block unchanged: ``source`` is
    the document name / relpath, ``score`` is higher-is-better (or ``None`` when
    the backend supplies no ranking value)."""

    text: str
    source: str
    score: Optional[float] = None


@runtime_checkable
class StorageBackend(Protocol):
    """The five-method memory substrate any backend implements.

    An adapter author implements exactly these and runs the conformance suite —
    never reading xlii internals (open-substrate plan, Vector E)."""

    def upload(self, relpath: str, data: bytes, fields: dict[str, str]) -> str: ...

    def update(self, doc_id: str, relpath: str, data: bytes, fields: dict[str, str]) -> str: ...

    def delete(self, doc_id: str) -> None: ...

    def list(self) -> dict[str, dict]:  # relpath -> {doc_id, sha256, name}
        ...

    def search(
        self, query: str, *, limit: int = 10, retrieval_mode: Optional[str] = None
    ) -> list[SearchHit]: ...


# --------------------------------------------------------------------------- #
#  CollectionsBackend — the xAI managed-RAG path (the vendor client's one home)
# --------------------------------------------------------------------------- #

def _vendor_fields(fields: dict[str, str]) -> Optional[dict[str, str]]:
    """Map protocol-normalized field keys onto the Collections ``xli_*`` schema.

    The conformance suite (Vector E) supplies ``relpath``/``name``/``sha256``;
    the sync algorithm supplies ``xli_relpath``/``xli_sha256`` directly. The
    adapter maps internally (Vector E contract finding b): protocol keys are
    renamed onto the vendor schema, ``name`` is dropped (it rides the document
    ``name=`` parameter), and already-vendor-keyed dicts pass through unchanged.
    """
    from xlii.sync import META_RELPATH, META_SHA256

    if not fields:
        return fields
    out = dict(fields)
    out.pop("name", None)
    for proto, vendor in (("relpath", META_RELPATH), ("sha256", META_SHA256)):
        if proto in out:
            out.setdefault(vendor, out[proto])
            del out[proto]
    return out


class CollectionsBackend:
    """Wraps the ``xai_sdk`` collections client.

    Constructed with one or more collection ids. Mutations (upload / update /
    delete / list) target the **primary** id (the first); search fans across all
    of them, so DeepContext / ``/ref``-attached persona collections join a query
    by riding in the id list — exactly as the old call site did.
    """

    def __init__(self, clients: Any, collection_ids: Any):
        self.clients = clients
        if isinstance(collection_ids, str):
            collection_ids = [collection_ids]
        self.collection_ids: list[str] = list(collection_ids)
        self.collection_id: str = self.collection_ids[0] if self.collection_ids else ""

    # --- document mutations (primary collection) ---------------------------- #
    def upload(self, relpath: str, data: bytes, fields: dict[str, str]) -> str:
        """upload_document with the legacy undeclared-fields fallback + 429/transient
        retry. Returns the new document id."""
        from xlii.sync import _is_unknown_field_error, _with_rate_limit_retry

        fields = _vendor_fields(fields)

        def _do():
            try:
                return self.clients.xai.collections.upload_document(
                    collection_id=self.collection_id, name=relpath, data=data, fields=fields,
                )
            except Exception as e:
                if _is_unknown_field_error(e):
                    logger.warning(
                        f"collection {self.collection_id} predates field_definitions — uploading {relpath} "
                        "without metadata fields. Re-init the project for clean schema-aware sync."
                    )
                    return self.clients.xai.collections.upload_document(
                        collection_id=self.collection_id, name=relpath, data=data, fields=None,
                    )
                raise

        resp = _with_rate_limit_retry(_do)
        return resp.file_metadata.file_id

    def update(self, doc_id: str, relpath: str, data: bytes, fields: dict[str, str]) -> str:
        """update_document with the same fallback + retry. The doc id is stable."""
        from xlii.sync import _is_unknown_field_error, _with_rate_limit_retry

        fields = _vendor_fields(fields)

        def _do():
            try:
                return self.clients.xai.collections.update_document(
                    collection_id=self.collection_id, file_id=doc_id,
                    name=relpath, data=data, fields=fields,
                )
            except Exception as e:
                if _is_unknown_field_error(e):
                    return self.clients.xai.collections.update_document(
                        collection_id=self.collection_id, file_id=doc_id,
                        name=relpath, data=data, fields=None,
                    )
                raise

        _with_rate_limit_retry(_do)
        return doc_id

    def delete(self, doc_id: str) -> None:
        from xlii.sync import _with_rate_limit_retry

        _with_rate_limit_retry(
            self.clients.xai.collections.remove_document,
            collection_id=self.collection_id,
            file_id=doc_id,
        )

    # --- listing (primary collection) --------------------------------------- #
    def list(self) -> dict[str, dict]:
        """Every doc in the primary collection: relpath -> {doc_id, sha256, name}.

        Falls back to keying by document name when our metadata fields are absent
        (docs uploaded by something else)."""
        from xlii.sync import META_RELPATH, META_SHA256

        state: dict[str, dict] = {}
        for doc in iter_collection_documents(self.clients.xai, self.collection_id):
            fm = doc.file_metadata
            meta = dict(doc.fields) if doc.fields else {}
            rel = meta.get(META_RELPATH) or fm.name
            state[rel] = {
                "doc_id": fm.file_id,
                "sha256": meta.get(META_SHA256, ""),
                "name": fm.name,
            }
        return state

    # --- retrieval (all collections) ---------------------------------------- #
    def search(
        self, query: str, *, limit: int = 10, retrieval_mode: Optional[str] = None
    ) -> list[SearchHit]:
        resp = self.clients.xai.collections.search(
            query=query,
            collection_ids=self.collection_ids,
            limit=limit,
            retrieval_mode=retrieval_mode,
        )
        chunks = list(getattr(resp, "results", None) or getattr(resp, "chunks", []) or [])
        hits: list[SearchHit] = []
        for ch in chunks:
            name = getattr(getattr(ch, "file_metadata", None), "name", "?")
            text = (
                getattr(ch, "text", None)
                or getattr(getattr(ch, "chunk", None), "text", "")
                or ""
            )
            score = getattr(ch, "score", None)
            hits.append(SearchHit(text=text, source=name, score=score))
        return hits

    # --- collection lifecycle (account-level; the vendor client's only home) - #
    @staticmethod
    def create_collection(clients: Any, name: str, field_definitions: Any) -> Any:
        """Create a collection; returns the raw create-response (callers read
        ``.collection_id``). Kept a passthrough so each caller keeps its own
        error handling."""
        resp = clients.xai.collections.create(name=name, field_definitions=field_definitions)
        collection_id = getattr(resp, "collection_id", None)
        if collection_id:
            team_id = getattr(clients, "team_id", None) or ""
            from xlii.minted import record_collection_safe

            record_collection_safe(collection_id=collection_id, team_id=team_id, name=name)
        return resp

    @staticmethod
    def delete_collection(clients: Any, collection_id: str) -> None:
        """Delete a whole collection (project teardown / gc)."""
        clients.xai.collections.delete(collection_id)

    @staticmethod
    def list_collections(clients: Any) -> dict[str, str]:
        """Every cloud collection as ``{id: name}``, paging until exhausted."""
        out: dict[str, str] = {}
        token = None
        while True:
            resp = clients.xai.collections.list(limit=500, pagination_token=token)
            for c in resp.collections:
                out[c.collection_id] = c.collection_name
            token = resp.pagination_token or None
            if not token or not resp.collections:
                break
        return out


# --------------------------------------------------------------------------- #
#  LocalBackend — a first-class SQLite FTS5 document store
# --------------------------------------------------------------------------- #

class LocalBackend:
    """A writable document store over SQLite FTS5 — the local twin of a managed
    collection.

    Distinct from ``xlii.storage.LocalIndex`` (which *rebuilds* a read-only search
    floor from the file tree): this store takes per-document upload / update /
    delete, tracks ``(doc_id, sha256, name)`` metadata, and searches its own
    contents. It lives in its own db file (``local_backend.db``) so it never
    collides with the LocalIndex offline floor.
    """

    def __init__(
        self,
        project: "ProjectConfig",
        *,
        cfg: Optional["GlobalConfig"] = None,
        db_path: "Path | None" = None,
    ) -> None:
        self.project = project
        self.cfg = cfg
        # A caller (e.g. the journal) can point the store at its own db file so
        # several LocalBackends under one project never collide; default is the
        # project-level store.
        self.path = db_path if db_path is not None else project.xli_dir / LOCAL_BACKEND_DB

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path)
        # Metadata columns ride the FTS5 table as UNINDEXED so a single table
        # serves both search (rel, content) and CRUD/list (doc_id, sha256, name).
        con.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5("
            "doc_id UNINDEXED, rel, content, sha256 UNINDEXED, name UNINDEXED)"
        )
        return con

    @staticmethod
    def _sha(data: bytes, fields: dict[str, str]) -> str:
        """Caller-supplied fingerprint under the protocol key (``sha256``) or the
        internal sync key (``xli_sha256``); computed from content when absent —
        ``list()`` always reports a true fingerprint (Vector E contract)."""
        f = fields or {}
        return f.get("sha256") or f.get("xli_sha256") or hashlib.sha256(data).hexdigest()

    def upload(self, relpath: str, data: bytes, fields: dict[str, str]) -> str:
        """Insert (or replace, for an idempotent re-upload of the same relpath) a
        document. Returns its stable doc id."""
        text = data.decode("utf-8", errors="replace")
        sha = self._sha(data, fields)
        con = self._connect()
        try:
            with con:
                row = con.execute(
                    "SELECT doc_id FROM docs WHERE rel = ?", (relpath,)
                ).fetchone()
                if row is not None:
                    # Same relpath already present — idempotent replace, id preserved.
                    doc_id = row[0]
                    con.execute(
                        "UPDATE docs SET content = ?, sha256 = ?, name = ? WHERE doc_id = ?",
                        (text, sha, relpath, doc_id),
                    )
                    return doc_id
                doc_id = uuid.uuid4().hex
                con.execute(
                    "INSERT INTO docs(doc_id, rel, content, sha256, name) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, relpath, text, sha, relpath),
                )
                return doc_id
        finally:
            con.close()

    def update(self, doc_id: str, relpath: str, data: bytes, fields: dict[str, str]) -> str:
        text = data.decode("utf-8", errors="replace")
        sha = self._sha(data, fields)
        con = self._connect()
        try:
            with con:
                cur = con.execute(
                    "UPDATE docs SET rel = ?, content = ?, sha256 = ?, name = ? WHERE doc_id = ?",
                    (relpath, text, sha, relpath, doc_id),
                )
                if cur.rowcount == 0:
                    # No row matched — the update silently affected nothing but we'd
                    # have told the caller it succeeded (content dropped). CollectionsBackend
                    # surfaces a missing id as an API error; match that so the two backends
                    # don't diverge on the same input.
                    raise KeyError(f"no local doc with doc_id {doc_id!r} to update")
            return doc_id
        finally:
            con.close()

    def delete(self, doc_id: str) -> None:
        con = self._connect()
        try:
            with con:
                con.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
        finally:
            con.close()

    def list(self) -> dict[str, dict]:
        con = self._connect()
        try:
            cur = con.execute("SELECT rel, doc_id, sha256, name FROM docs")
            return {
                rel: {"doc_id": doc_id, "sha256": sha, "name": name}
                for rel, doc_id, sha, name in cur.fetchall()
            }
        finally:
            con.close()

    def search(
        self, query: str, *, limit: int = 10, retrieval_mode: Optional[str] = None
    ) -> list[SearchHit]:
        """BM25-ranked FTS search. ``retrieval_mode`` is accepted and ignored —
        FTS has a single mode. Model-written FTS operators (NEAR/"/*) are reduced
        to bare OR-ed terms so they never become a syntax error."""
        _ = retrieval_mode  # advisory; FTS has one mode
        terms = re.findall(r"[A-Za-z0-9_]+", query)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"' for t in terms)
        con = self._connect()
        try:
            cur = con.execute(
                "SELECT rel, content, bm25(docs) FROM docs WHERE docs MATCH ? "
                "ORDER BY bm25(docs) LIMIT ?",
                (match, limit),
            )
            # bm25() is a cost (lower = better); negate so higher = better.
            return [SearchHit(text=content, source=rel, score=-score)
                    for rel, content, score in cur.fetchall()]
        except sqlite3.OperationalError:
            return []
        finally:
            con.close()


# --------------------------------------------------------------------------- #
#  LocalIndexFloor + ComposedBackend — OQ 4–5 carried follow-up
# --------------------------------------------------------------------------- #

class LocalIndexFloor:
    """Read-only search floor over :class:`~xlii.storage.LocalIndex`.

    Writes raise — the floor is never the write path. Used as the degrade half
    of :class:`ComposedBackend` (and nowhere else).
    """

    def __init__(self, project: "ProjectConfig") -> None:
        self.project = project

    def upload(self, relpath: str, data: bytes, fields: dict[str, str]) -> str:
        raise NotImplementedError("LocalIndexFloor is read-only")

    def update(self, doc_id: str, relpath: str, data: bytes, fields: dict[str, str]) -> str:
        raise NotImplementedError("LocalIndexFloor is read-only")

    def delete(self, doc_id: str) -> None:
        raise NotImplementedError("LocalIndexFloor is read-only")

    def list(self) -> dict[str, dict]:
        raise NotImplementedError("LocalIndexFloor is read-only")

    def search(
        self, query: str, *, limit: int = 10, retrieval_mode: Optional[str] = None
    ) -> list[SearchHit]:
        _ = retrieval_mode
        from xlii.storage import LocalIndex

        idx = LocalIndex(self.project)
        if not idx.exists():
            return []
        return [
            SearchHit(text=snippet, source=rel, score=score)
            for rel, snippet, score in idx.search(query, limit)
        ]


class ComposedBackend:
    """Primary backend + read-only LocalIndex floor (OQ 5).

    Writes/list go to ``primary`` only. ``search`` tries primary; on any
    exception it sets :attr:`degraded_from` and serves the floor. Exactly two
    base backends remain — this is composition, not a third store.
    """

    def __init__(self, primary: StorageBackend, floor: LocalIndexFloor) -> None:
        self.primary = primary
        self.floor = floor
        self.degraded_from: Optional[BaseException] = None

    def upload(self, relpath: str, data: bytes, fields: dict[str, str]) -> str:
        return self.primary.upload(relpath, data, fields)

    def update(self, doc_id: str, relpath: str, data: bytes, fields: dict[str, str]) -> str:
        return self.primary.update(doc_id, relpath, data, fields)

    def delete(self, doc_id: str) -> None:
        self.primary.delete(doc_id)

    def list(self) -> dict[str, dict]:
        return self.primary.list()

    def search(
        self, query: str, *, limit: int = 10, retrieval_mode: Optional[str] = None
    ) -> list[SearchHit]:
        self.degraded_from = None
        try:
            return self.primary.search(
                query, limit=limit, retrieval_mode=retrieval_mode
            )
        except Exception as e:
            self.degraded_from = e
            return self.floor.search(
                query, limit=limit, retrieval_mode=retrieval_mode
            )


# --------------------------------------------------------------------------- #
#  Selection — today's rule, in one place
# --------------------------------------------------------------------------- #

def get_backend(clients: Any, project: "ProjectConfig", *, cfg: Optional["GlobalConfig"] = None) -> StorageBackend:
    """Resolve the backend for a project.

    Rule (unchanged from today): an explicit project-level ``storage`` key wins
    (``"collections"`` | ``"local"``); otherwise a present ``collection_id`` means
    collections, an absent one means local. No user-facing switch in this vector.
    """
    storage = (getattr(project, "storage", "") or "").strip().lower()
    collection_id = (getattr(project, "collection_id", "") or "").strip()
    if storage == "local":
        return LocalBackend(project, cfg=cfg)
    if storage == "collections":
        if not collection_id:
            raise ValueError(
                "Invalid project configuration: storage='collections' requires a non-empty collection_id."
            )
        return CollectionsBackend(clients, [collection_id])
    if collection_id:
        return CollectionsBackend(clients, [collection_id])
    return LocalBackend(project, cfg=cfg)


def compose_with_local_floor(
    primary: StorageBackend, project: "ProjectConfig"
) -> ComposedBackend:
    """Wrap ``primary`` with the LocalIndex read-only floor for search degrade."""
    return ComposedBackend(primary, LocalIndexFloor(project))
