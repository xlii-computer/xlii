# Writing a storage backend

xlii's durable value is what `search_project` can recall — a project's memory.
That memory must never be trapped in one vendor's API. So the persistence layer
is a small **protocol** with two shipped implementations (a managed remote and a
local file), and a **conformance suite** any third party can run against a new
backend. The vendor provides primitives; you compose the system.

This page is the on-ramp: implement five methods, run the suite against your
backend, done. You never read xlii's internals to do it.

## The protocol

A storage backend is any object with these five methods:

```python
class StorageBackend(Protocol):
    def upload(self, relpath: str, data: bytes, fields: dict[str, str]) -> str: ...
    def update(self, doc_id: str, relpath: str, data: bytes, fields: dict[str, str]) -> str: ...
    def delete(self, doc_id: str) -> None: ...
    def list(self) -> dict[str, dict]: ...   # relpath -> {doc_id, sha256, name}
    def search(self, query: str, *, limit: int = 10,
               retrieval_mode: str | None = None) -> list[SearchHit]: ...
```

- **`upload(relpath, data, fields)` → `doc_id`.** Store `data` under `relpath` and
  return a non-empty document id. After it returns, `list()` reports `relpath` with
  that id.
- **`update(doc_id, relpath, data, fields)` → `doc_id`.** Replace the document's
  content. Return the document's id (stable across the update — same-document
  semantics are yours to define, but `list()` must then report whatever you
  return).
- **`delete(doc_id)`.** Remove the document. Afterward it is absent from both
  `list()` and `search()`.
- **`list()` → `{relpath: {doc_id, sha256, name}}`.** The current index state,
  keyed by relpath. `sha256` is a content fingerprint (it must change when content
  changes — sync uses it to skip unchanged files); `name` is a human-facing label.
- **`search(query, *, limit, retrieval_mode)` → `list[SearchHit]`.** Rank documents
  by relevance to `query`. On an empty index, return `[]` — never raise.

### `SearchHit`

A `SearchHit` carries at minimum three attributes, shaped to render in a search
result block unchanged:

| attribute | meaning |
| --- | --- |
| `text` | the matched snippet / chunk text |
| `source` | what to cite — the document's relpath or name |
| `score` | relevance, **higher = better** (normalize if your engine is a cost) |

Return your own type; the suite duck-types these three attributes.

### `retrieval_mode` is advisory

`retrieval_mode` is a *hint*, not a command. A managed backend may honor named
modes (keyword / hybrid / vector); a plain full-text backend has one mode and
ignores it. **Whatever you do, an unknown mode must not raise** — accept it and
fall back to your default. The suite asserts exactly this.

### The `fields` metadata

`fields` is opaque `dict[str, str]` document metadata. By convention the suite
supplies `relpath`, `name`, and a true `sha256`. Populate `list()`'s `name` /
`sha256` from these keys if your store keeps arbitrary metadata, or compute them
yourself (e.g. hash `data`) if it doesn't. Either satisfies the contract, because
the contract is on the *observable* fingerprint, not on where it comes from. (The
shipped Collections backend maps these keys onto its own declared field schema
internally — your backend need not.)

## Run the conformance suite

The suite lives at `tests/test_storage_conformance.py`. It is parametrized over a
`backend` fixture and selected with `--backend`:

```
pytest tests/test_storage_conformance.py                        # local reference backend
pytest tests/test_storage_conformance.py --backend=local        # the same, explicit
pytest tests/test_storage_conformance.py --backend=collections  # the remote backend (env-gated)
pytest tests/test_storage_conformance.py --backend=mybackend    # yours
```

To wire your backend in, add a branch to `_make_backend(name, tmp_path)` in that
file that constructs your backend against a throwaway location under `tmp_path`,
then run with `--backend=<yourname>`. If your backend indexes asynchronously (a
managed service with server-side embedding), set
`backend.conformance_search_retries = (tries, delay_seconds)` so the suite polls
past indexing lag instead of racing it — synchronous backends leave it unset and
pay no delay.

The cases (each must pass):

- upload → search finds it; `list()` agrees
- update → search reflects the new content; `list()` reports the new fingerprint
- delete → search misses; `list()` agrees
- re-upload of the same relpath is idempotent (one entry, no orphan)
- empty-index search returns `[]` cleanly
- unicode content round-trips and is searchable
- a document at the `max_file_bytes` boundary uploads and is findable
- an unknown `retrieval_mode` does not raise

### The suite has teeth

Alongside the real backends the suite runs a `NonconformingBackend` that accepts
every call but persists nothing, and asserts the checks **fail** against it. That
is the proof the suite is not vacuously green: a backend that silently loses your
memory is rejected, loudly. If your adapter has a hole, you will see the same
failure the broken reference does.

## What Collections gives you that a bare vector DB does not

Two backends ship: `LocalBackend` (SQLite FTS5 — the offline / local-only floor)
and `CollectionsBackend` (xAI Collections — a **managed RAG pipeline**). Before you
wrap a bare vector index (Pinecone, pgvector, Chroma), budget for what Collections
does *server-side* that a raw index leaves to you:

- **Chunking.** Collections splits documents into passages. A bare index stores
  whatever vector you hand it — you must chunk first, and chunk well.
- **Embedding.** Collections embeds text on upload and at query time. A bare index
  wants vectors, not text — you own an embedding provider, its cost, and its
  versioning.
- **Retrieval modes.** Collections offers keyword / hybrid / vector retrieval
  behind `retrieval_mode`. A bare vector index gives you nearest-neighbor only;
  hybrid ranking is yours to build.

In other words: `upload(relpath, data, fields)` takes **bytes**. If your backend
speaks vectors, the chunk-and-embed step lives inside your `upload` and `search`.
That is the real work of a vector-DB adapter — the five methods are the easy part.

## Where selection happens

A project chooses its backend by today's rule: a provisioned remote collection
uses `CollectionsBackend`; a local-only project (`xlii init --local`) uses
`LocalBackend`. `xlii sync` maintains the index; `/sync` does the same from inside
a session. There is no per-backend command surface — selection is configuration,
not a verb.

## See also

- `/howto plugins` — the other extension seam (markdown capabilities)
- `/howto knowledge` — how memory is attached and searched at the user layer
- `/howto bridges` — exposing xlii's context to *other* agents
