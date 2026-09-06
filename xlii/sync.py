"""Sync engine — make the xAI Collection mirror the local project."""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

from xlii.client import Clients
from xlii.config import (
    PROJECT_KIND_CODE,
    PROJECT_KIND_COLLECTION,
    GlobalConfig,
    ProjectConfig,
    normalize_project_kind,
)
from xlii.ignore import DEFAULT_IGNORES, load_ignore_spec, walk_paths_only, walk_project
from xlii.manifest import FileEntry, Manifest, hash_file

# Per-op retry on rate limiting. Same policy as bootstrap.create_api_key —
# exponential backoff, give up after a handful of tries.
MAX_RATE_LIMIT_RETRIES = 5

# xAI metadata fields are dict[str, str]; we use these keys.
# These must be declared in the collection's `field_definitions` at create time
# (xAI rejects undeclared fields on update_document — see init_project below).
META_RELPATH = "xli_relpath"
META_SHA256 = "xli_sha256"

# Field schema declared when creating a new collection, so update_document(fields=...)
# is accepted later on. Existing collections that were created without these
# fields fall back to a no-fields update via _is_unknown_field_error.
FIELD_DEFINITIONS = [
    {
        "key": META_RELPATH,
        "required": False,
        "inject_into_chunk": False,
        "unique": False,
        "description": "XLI project-relative path",
    },
    {
        "key": META_SHA256,
        "required": False,
        "inject_into_chunk": False,
        "unique": False,
        "description": "XLI sha256 of file contents",
    },
]


def _is_unknown_field_error(exc: Exception) -> bool:
    """Detect the xAI 'Unknown field' / 'field_definitions' rejection."""
    s = str(exc)
    return "Unknown field" in s or "field_definitions" in s


def _short_error(exc: Exception) -> str:
    """Trim verbose multi-line gRPC errors down to a one-liner.

    xai-sdk surfaces _InactiveRpcError with a 5-line `repr` that includes
    duplicated debug strings. We extract just `details = "..."` (the
    grpc_message) when present, falling back to the first non-empty line.
    """
    s = str(exc)
    # Single-line already? Pass through.
    if "\n" not in s:
        return s
    m = re.search(r'details\s*=\s*"([^"]+)"', s)
    if m:
        # Surface the gRPC status code too if present, for triage.
        status = re.search(r'status\s*=\s*StatusCode\.(\w+)', s)
        if status:
            return f"{status.group(1)}: {m.group(1)}"
        return m.group(1)
    # Fallback: first non-empty line.
    for line in s.splitlines():
        line = line.strip()
        if line:
            return line
    return s


def _is_rate_limited(exc: Exception) -> bool:
    """Detect a 429 / rate-limit error from xai-sdk.

    The SDK doesn't expose a typed exception for this, so we sniff the message.
    """
    s = str(exc).lower()
    return "429" in s or ("rate" in s and "limit" in s) or "resource_exhausted" in s


def _is_transient_server_error(exc: Exception) -> bool:
    """Detect retryable server-side failures from the Collections API.

    The beta backend intermittently returns gRPC INTERNAL/UNAVAILABLE (e.g.
    'INTERNAL: Failed to create storage ledger file') on individual document
    ops. These are not the caller's fault and usually succeed on retry.
    """
    s = str(exc).lower()
    return (
        "internal" in s
        or "unavailable" in s
        or "deadline_exceeded" in s
        or "storage ledger" in s
    )


def _is_retryable(exc: Exception) -> bool:
    return _is_rate_limited(exc) or _is_transient_server_error(exc)


# Injectable sleep — tests swap this out so the 429 backoff path runs in
# milliseconds instead of 31 real seconds.
_sleep: Callable[[float], None] = time.sleep


def _with_rate_limit_retry(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run fn(*args, **kwargs); on 429 sleep + retry up to MAX_RATE_LIMIT_RETRIES."""
    for attempt in range(MAX_RATE_LIMIT_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if _is_retryable(e) and attempt < MAX_RATE_LIMIT_RETRIES - 1:
                wait = 2 ** attempt + 1
                kind = "rate-limited" if _is_rate_limited(e) else "transient server error"
                logger.warning(f"{kind}; backing off {wait}s (attempt {attempt+1})")
                _sleep(wait)
                continue
            raise


# The document CRUD + the undeclared-fields fallback moved into
# CollectionsBackend (xlii.storage_backend) — the single home for the vendor
# collections client. `_is_unknown_field_error`, `_with_rate_limit_retry`, and
# the error sniffers above stay here: they are the patch targets
# tests/test_sync_planner.py pins, and the backend imports them.


@dataclass
class SyncStats:
    uploaded: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    # Exact relpaths per planned operation — always populated, so callers
    # (and `xlii sync --dry-run`) can show precisely what will/would happen.
    planned: dict[str, list[str]] = field(
        default_factory=lambda: {"upload": [], "update": [], "delete": []}
    )

    def summary(self) -> str:
        return (
            f"uploaded={self.uploaded} updated={self.updated} "
            f"deleted={self.deleted} unchanged={self.unchanged} failed={self.failed}"
        )


def scan_local(project: ProjectConfig, cfg: GlobalConfig) -> dict[str, FileEntry]:
    """Walk the project, hashing each tracked file."""
    spec = load_ignore_spec(project.project_root, project.extra_ignores)
    out: dict[str, FileEntry] = {}
    for path in walk_project(project.project_root, spec, max_bytes=cfg.max_file_bytes):
        rel = path.relative_to(project.project_root).as_posix()
        try:
            stat = path.stat()
            out[rel] = FileEntry(
                sha256=hash_file(path),
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
        except OSError as e:
            logger.warning(f"skip {rel}: {e}")
    return out


def _files_root_mount(project: ProjectConfig):
    """``(conn, base_path)`` when Files points at a remote tree; else ``(None, "")``."""
    from xlii.addressing import Address
    from xlii.desk_files import files_mount_address

    addr = files_mount_address(project)
    if not addr:
        return None, ""
    parsed = Address.parse(addr)
    from xlii.remotefs import manager

    return manager.get(parsed.key), (parsed.subpath or "").strip("/")


def _remote_ignored(rel: str) -> bool:
    from pathspec import PathSpec

    spec = PathSpec.from_lines("gitwildmatch", DEFAULT_IGNORES)
    return spec.match_file(rel) or spec.match_file(rel.rstrip("/") + "/")


def scan_remote(conn: Any, base: str, cfg: GlobalConfig) -> dict[str, FileEntry]:
    """Walk a remote Files mount and hash each tracked file."""
    import hashlib

    out: dict[str, FileEntry] = {}
    max_bytes = int(getattr(cfg, "max_file_bytes", 0) or 0)

    def walk(prefix: str, rel_prefix: str) -> None:
        try:
            listing = conn.listdir(prefix or ".")
        except Exception as e:
            logger.warning(f"skip remote {prefix or '.'}: {e}")
            return
        for name, is_dir, size in listing:
            rel = f"{rel_prefix}/{name}" if rel_prefix else name
            if _remote_ignored(rel) or _remote_ignored(rel + "/"):
                continue
            child = f"{prefix}/{name}" if prefix else name
            if is_dir:
                walk(child, rel)
                continue
            if max_bytes and size is not None and size > max_bytes:
                continue
            try:
                data = conn.read(child)
            except Exception as e:
                logger.warning(f"skip {rel}: {e}")
                continue
            out[rel] = FileEntry(
                sha256=hashlib.sha256(data).hexdigest(),
                size=len(data),
                mtime=0.0,
            )

    walk(base, "")
    return out


def scan_tracked(project: ProjectConfig, cfg: GlobalConfig) -> dict[str, FileEntry]:
    """Local tree, or the remote Files mount when ``files_root`` is set."""
    try:
        conn, base = _files_root_mount(project)
    except Exception as e:
        logger.warning(f"files_root unavailable: {e}")
        conn, base = None, ""
    if conn is not None:
        return scan_remote(conn, base, cfg)
    return scan_local(project, cfg)


def read_tracked_bytes(project: ProjectConfig, rel: str) -> bytes:
    conn, base = _files_root_mount(project)
    if conn is not None:
        path = f"{base}/{rel}" if base else rel
        return conn.read(path)
    return (project.project_root / rel).read_bytes()


def fetch_collection_state(clients: Clients, collection_id: str) -> dict[str, dict]:
    """Pull every doc in the collection. Returns relpath -> {file_id, sha256, name}.

    Runs through the protocol (CollectionsBackend.list) and re-keys ``doc_id`` to
    the ``file_id`` the sync algorithm and its callers (diag, the manifest) speak.
    Falls back to keying by document name if our metadata fields are missing.
    """
    from xlii.storage_backend import CollectionsBackend

    raw = CollectionsBackend(clients, [collection_id]).list()
    return {
        rel: {"file_id": d["doc_id"], "sha256": d["sha256"], "name": d["name"]}
        for rel, d in raw.items()
    }


def _do_upload(clients, collection_id: str, project: ProjectConfig, rel: str, fields: dict) -> str:
    from xlii.storage_backend import CollectionsBackend

    data = read_tracked_bytes(project, rel)
    return CollectionsBackend(clients, [collection_id]).upload(rel, data, fields)


def _do_update(clients, collection_id: str, file_id: str, project: ProjectConfig, rel: str, fields: dict) -> str:
    from xlii.storage_backend import CollectionsBackend

    data = read_tracked_bytes(project, rel)
    return CollectionsBackend(clients, [collection_id]).update(file_id, rel, data, fields)


def _do_delete(clients, collection_id: str, file_id: str) -> None:
    from xlii.storage_backend import CollectionsBackend

    CollectionsBackend(clients, [collection_id]).delete(file_id)


# A sync turn that wants to delete more docs than this needs explicit
# confirmation — a scan hiccup or an ignore-filter change must not be allowed
# to silently bulk-delete the remote Collection.
DELETE_GUARD_THRESHOLD = 10


def _refresh_local_index(project: ProjectConfig, cfg: GlobalConfig) -> None:
    """Rebuild the local FTS index (offline search floor). Never fatal."""
    try:
        from xlii.storage import LocalIndex
        LocalIndex(project).rebuild(cfg)
    except Exception as e:
        logger.warning(f"local index rebuild failed: {e}")


def _sync_local_backend(project: ProjectConfig, cfg: GlobalConfig) -> None:
    """Populate the LocalBackend document store from the local tree (OQ 4).

    Local-only projects persist uploads through the StorageBackend protocol
    instead of only rebuilding the read-only LocalIndex floor. Never fatal —
    the LocalIndex rebuild remains the search floor if this fails.
    """
    try:
        import hashlib

        from xlii.ignore import load_ignore_spec, walk_project
        from xlii.storage_backend import LocalBackend

        backend = LocalBackend(project, cfg=cfg)
        spec = load_ignore_spec(project.project_root, project.extra_ignores)
        live: dict[str, bytes] = {}
        for path in walk_project(
            project.project_root, spec, max_bytes=cfg.max_file_bytes
        ):
            rel = path.relative_to(project.project_root).as_posix()
            try:
                live[rel] = path.read_bytes()
            except OSError:
                continue
        remote = backend.list()
        for rel, meta in list(remote.items()):
            if rel not in live:
                try:
                    backend.delete(meta["doc_id"])
                except Exception:
                    # A remote doc that won't delete is left in place; the next sync sees it again and retries.
                    pass
        for rel, data in live.items():
            sha = hashlib.sha256(data).hexdigest()
            fields = {META_RELPATH: rel, META_SHA256: sha, "sha256": sha}
            existing = remote.get(rel)
            if existing and existing.get("sha256") == sha:
                continue
            if existing:
                backend.update(existing["doc_id"], rel, data, fields)
            else:
                backend.upload(rel, data, fields)
    except Exception as e:
        logger.warning(f"local backend sync failed: {e}")


def sync_project(
    clients: Optional[Clients],
    project: ProjectConfig,
    cfg: GlobalConfig,
    *,
    dry_run: bool = False,
    confirm_deletes: Optional[Callable[[list[str]], bool]] = None,
) -> SyncStats:
    """Reconcile the collection to match local files.

    Algorithm: for each local file, if the remote sha256 matches we skip;
    if it differs we update; if remote is missing we upload. Anything in the
    collection that has no local counterpart is removed. Mutating operations
    fan out across cfg.max_parallel_workers threads with 429 backoff per op.

    Local-only projects skip the network entirely and rebuild the optional
    file index in-place — the source of truth is always the local tree.
    """
    if project.local_only:
        if (project.xli_dir / "index.txt").exists() and not dry_run:
            write_file_index(project, cfg)
        if not dry_run:
            _refresh_local_index(project, cfg)
            _sync_local_backend(project, cfg)
        return SyncStats()
    stats = SyncStats()
    manifest = Manifest.load(project.manifest_path)
    local = scan_tracked(project, cfg)
    remote = fetch_collection_state(clients, project.collection_id)

    # Phase 1: classify each local file against remote state. File bytes are
    # NOT read here — each upload/update task reads its own file inside the
    # thread pool so peak memory stays bounded to max_workers * max_file_bytes
    # rather than total_changed_files * max_file_bytes.
    uploads: list[tuple[str, FileEntry, dict]] = []
    updates: list[tuple[str, FileEntry, str, dict]] = []

    for rel, entry in local.items():
        remote_doc = remote.get(rel)
        if remote_doc and remote_doc["sha256"] == entry.sha256:
            entry.file_id = remote_doc["file_id"]
            entry.last_synced = time.time()
            manifest.set(rel, entry)
            stats.unchanged += 1
            continue

        if dry_run:
            if remote_doc:
                stats.updated += 1
                stats.planned["update"].append(rel)
            else:
                stats.uploaded += 1
                stats.planned["upload"].append(rel)
            continue

        fields = {META_RELPATH: rel, META_SHA256: entry.sha256}
        if remote_doc:
            updates.append((rel, entry, remote_doc["file_id"], fields))
            stats.planned["update"].append(rel)
        else:
            uploads.append((rel, entry, fields))
            stats.planned["upload"].append(rel)

    deletes: list[tuple[str, str]] = [
        (rel, doc["file_id"]) for rel, doc in remote.items() if rel not in local
    ]
    stats.planned["delete"] = [rel for rel, _ in deletes]

    if dry_run:
        stats.deleted = len(deletes)
        return stats

    # User pre-sync hooks observe the plan before any mutation runs.
    try:
        from xlii.hooks import run_hooks
        run_hooks(project.xli_dir, "pre-sync", stats.planned)
    except Exception:
        # Observer hooks must not veto the sync -- the plan above is already computed.
        pass

    # Deletion guard: never convert local confusion into remote destruction.
    # An empty local scan against a non-empty collection is almost certainly a
    # scan/filter failure, not the user deleting every file — block outright.
    # Above-threshold deletions require explicit confirmation; with no
    # confirmer available (e.g. silent startup sync) they are skipped.
    if deletes and not local:
        stats.errors.append(
            f"delete-guard: local scan found 0 files but the collection has "
            f"{len(deletes)} — refusing to delete anything (run `xlii sync` "
            f"manually if this is intentional)"
        )
        deletes = []
        stats.planned["delete"] = []
    elif len(deletes) > DELETE_GUARD_THRESHOLD:
        doomed = [rel for rel, _ in deletes]
        if confirm_deletes is not None and confirm_deletes(doomed):
            pass  # user approved
        else:
            stats.errors.append(
                f"delete-guard: {len(deletes)} remote docs have no local "
                f"counterpart — skipped (run `xlii sync` to review and confirm)"
            )
            deletes = []
            stats.planned["delete"] = []

    # Phase 2: fan out mutating operations across a thread pool. Each future
    # returns enough metadata for the main thread to update the manifest and
    # stats — no shared-state locking needed.
    total_ops = len(uploads) + len(updates) + len(deletes)
    if total_ops == 0:
        manifest.save()
        _refresh_local_index(project, cfg)
        return stats

    max_workers = max(1, min(cfg.max_parallel_workers, total_ops))
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futs: dict[Any, tuple[str, str, Optional[FileEntry]]] = {}
        for rel, entry, fields in uploads:
            f = ex.submit(_do_upload, clients, project.collection_id, project, rel, fields)
            futs[f] = ("upload", rel, entry)
        for rel, entry, fid, fields in updates:
            f = ex.submit(_do_update, clients, project.collection_id, fid, project, rel, fields)
            futs[f] = ("update", rel, entry)
        for rel, fid in deletes:
            f = ex.submit(_do_delete, clients, project.collection_id, fid)
            futs[f] = ("delete", rel, None)

        for fut in as_completed(futs):
            op, rel, entry = futs[fut]
            try:
                result = fut.result()
            except Exception as e:
                stats.failed += 1
                short = _short_error(e)
                stats.errors.append(f"{op} {rel}: {short}")
                logger.error(f"{op} {rel}: {short}")
                continue

            if op == "upload" and entry is not None:
                entry.file_id = result  # file_id from upload response
                entry.last_synced = time.time()
                manifest.set(rel, entry)
                stats.uploaded += 1
            elif op == "update" and entry is not None:
                entry.file_id = result  # same file_id as before
                entry.last_synced = time.time()
                manifest.set(rel, entry)
                stats.updated += 1
            elif op == "delete":
                manifest.remove(rel)
                stats.deleted += 1

    manifest.save()
    _refresh_local_index(project, cfg)
    return stats


def end_of_turn_sync(state, dirty: set[str]) -> None:
    """Mirror this turn's file changes to the Collection (the system prompt's
    'changes are mirrored automatically' claim). No-op for local-only
    projects, --no-sync sessions, or turns that touched nothing.

    Kernel home of the old cmds/sessions/nesting.py gate (godzilla-mothra B2);
    ``state`` is the REPL/agent session duck-type (``.project``, ``.cfg``,
    ``.pool``, ``.console``, optional ``.no_sync``)."""
    if not dirty or state.project.local_only or getattr(state, "no_sync", False):
        return
    try:
        with state.console.status("[dim]mirroring changes…[/dim]"):
            stats = sync_project(state.pool.primary(), state.project, state.cfg)
        if stats.uploaded or stats.updated or stats.deleted:
            state.console.print(f"[dim]sync: {stats.summary()}[/dim]")
    except Exception as e:
        state.console.print(
            f"[yellow]end-of-turn sync failed ({type(e).__name__}: {e}) — "
            "changes are local only; /sync to retry[/yellow]"
        )


def write_file_index(
    project: ProjectConfig,
    cfg: GlobalConfig,
    *,
    on_progress: Optional[Callable[[int, str], None]] = None,
) -> int:
    """Walk the project tree (respecting ignores) and write
    `<root>/.xlii/index.txt` containing every tracked relpath plus its byte size,
    one per line: `<size>\\t<relpath>`. Returns the count.

    Uses `walk_paths_only` — NO content sniffing, NO size cap, NO binary skip.
    A 150k-file NAS over the network can be indexed in ~30 seconds because we
    only stat() each file (one network round-trip per file's metadata, vs the
    8KB read per file that walk_project would do).

    `on_progress(count, last_relpath)` fires every 1000 files so the caller
    can show a live counter — without it, large trees look like the process
    is hung.
    """
    spec = load_ignore_spec(project.project_root, project.extra_ignores)
    rows: list[str] = []
    n = 0
    last_rel = ""
    for path in walk_paths_only(project.project_root, spec):
        rel = path.relative_to(project.project_root).as_posix()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        rows.append(f"{size}\t{rel}")
        n += 1
        last_rel = rel
        if on_progress is not None and n % 1000 == 0:
            on_progress(n, rel)
    if on_progress is not None and n % 1000 != 0:
        on_progress(n, last_rel)
    project.xli_dir.mkdir(parents=True, exist_ok=True)
    (project.xli_dir / "index.txt").write_text("\n".join(rows) + "\n")
    return len(rows)


def _stamp_kind(kind: Optional[str], name: str) -> str:
    stamped = normalize_project_kind(kind)
    if stamped is not None:
        return stamped
    if name.startswith("scratch/") and name != "scratch/home":
        return PROJECT_KIND_COLLECTION
    return PROJECT_KIND_CODE


def init_project(
    clients: Clients,
    project_root: Path,
    *,
    name: Optional[str] = None,
    existing_collection_id: Optional[str] = None,
    local_only: bool = False,
    snapshot: bool = False,
    kind: Optional[str] = None,
) -> ProjectConfig:
    """Create the .xlii/ directory, create or reuse a collection, register it.

    `local_only=True` skips Collection provisioning entirely — for ad-hoc /
    file-management workflows where you don't want any content uploaded.
    `snapshot=True` writes a paths+sizes index at .xlii/index.txt for fast
    grep-based structural search (most useful in local-only mode on big trees).
    """
    import uuid
    from datetime import datetime, timezone

    from xlii.registry import Registry, RegistryEntry

    project_root = project_root.resolve()
    name = name or project_root.name

    if local_only:
        coll_id = ""
    elif existing_collection_id:
        coll_id = existing_collection_id
    else:
        from xlii.storage_backend import CollectionsBackend

        meta = CollectionsBackend.create_collection(
            clients, f"xlii/{name}", FIELD_DEFINITIONS,
        )
        coll_id = meta.collection_id

    created_at = datetime.now(timezone.utc).isoformat()
    project = ProjectConfig(
        project_root=project_root,
        name=name,
        collection_id=coll_id,
        created_at=created_at,
        conversation_id=uuid.uuid4().hex,  # stable per-project cache key
        local_only=local_only,
        kind=_stamp_kind(kind, name),
    )
    project.save()

    # Note: snapshot index is written by the caller (cmd_init) so the walk
    # can be wrapped in a live progress widget — on a 150k-file NAS the walk
    # takes long enough that silent execution looks like a hang.
    _ = snapshot  # kept in signature for backward compat; no-op here

    registry = Registry.load()
    registry.upsert(
        RegistryEntry(
            path=str(project_root),
            collection_id=coll_id,
            name=name,
            created_at=created_at,
        )
    )
    registry.save()

    return project


def provision_collection(clients: Clients, project: ProjectConfig) -> str:
    """Throne opt-in: attach a Collection to a local-only desk and persist it.

    Nodes without the management key never reach this. The tree (local or
    ``files_root``) is unchanged — only searchable memory is added.
    """
    from xlii.registry import Registry, RegistryEntry
    from xlii.storage_backend import CollectionsBackend

    meta = CollectionsBackend.create_collection(
        clients, f"xlii/{project.name}", FIELD_DEFINITIONS,
    )
    cid = meta.collection_id
    project.collection_id = cid
    project.local_only = False
    project.save()
    registry = Registry.load()
    existing = registry.find_by_path(project.project_root)
    if existing is not None:
        existing.collection_id = cid
        registry.save()
    else:
        registry.upsert(
            RegistryEntry(
                path=str(project.project_root),
                collection_id=cid,
                name=project.name,
                created_at=project.created_at,
            )
        )
        registry.save()
    return cid


def make_preview_project(root: Path) -> ProjectConfig:
    """A throwaway local-only project for `xlii code` preview mode.

    Opens the code REPL in `root` WITHOUT writing a .xlii/ there: all session
    state (repl_history, turns/, session.json) is redirected to a fresh temp
    dir via state_dir_override and discarded when the session ends. No
    snapshot, no sync, no Collection — the agent explores the tree live with
    list_dir/glob/grep. Never saved or registered, so it leaves no trace.

    Kernel home since godzilla-mothra B4 (was cmds/sessions/code.py's
    ``_ephemeral_preview_project``; the cmd keeps a façade alias).
    """
    import tempfile
    import uuid
    from datetime import datetime, timezone

    state_dir = Path(tempfile.mkdtemp(prefix="xlii-preview-"))
    return ProjectConfig(
        project_root=root.resolve(),
        name=f"preview/{root.name or root}",
        collection_id="",
        created_at=datetime.now(timezone.utc).isoformat(),
        conversation_id=uuid.uuid4().hex,
        local_only=True,
        state_dir_override=state_dir,
    )


@dataclass
class SyncOutcome:
    """The result of a startup sync, packaged for a face to render (D8).

    ``status`` is "ok" | "failed" | "skipped"; ``stats`` is set on "ok";
    ``error`` on "failed". The caller owns all printing (spinner, warnings),
    so a headless body can consume the outcome without a console."""

    status: str
    stats: Optional[SyncStats] = None
    error: Optional[Exception] = None


def startup_sync(client, project: ProjectConfig, cfg: GlobalConfig, *, skip: bool = False) -> SyncOutcome:
    """The session-start sync policy, one owner for both session doors (D8).

    Runs ``sync_project`` and packages the result; a failure degrades to
    "failed" (never raises) so the session continues in degraded local mode —
    the exact policy cmds/sessions/{code,chat} each hand-rolled, already
    divergent. The CALLER decides ``skip`` (preview / --no-sync / local_only
    for code) and renders the outcome (spinner text, warning wording).
    """
    if skip:
        return SyncOutcome("skipped")
    try:
        stats = sync_project(client, project, cfg)
        return SyncOutcome("ok", stats=stats)
    except Exception as e:
        return SyncOutcome("failed", error=e)
