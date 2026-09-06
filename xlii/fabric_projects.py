"""Fabric project catalog — Collection-first folders across nodes of a throne.

Create mints a Collection; each body that wants it points at that Collection.
No box is the file home until someone adopts. Throne Home (``scratch/home``)
never federates — it is the sacred desk, not a project in the shared menu.

Star: the throne pulls each node's ``projects.json``, merges locally (pointers
for new collection ids), and pushes the federated catalog back so any Face
sees one list.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Protocol, runtime_checkable

from xlii.address_book import public_catalog_row, sanitize_repo, scrub_catalog_row
from xlii.registry import Registry, RegistryEntry, _entry_from_dict

DEFAULT_REMOTE_REGISTRY = ".config/xlii/projects.json"
DEFAULT_REMOTE_POINTER = ".xlii/remote-projects"

_SAFE_SEG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@runtime_checkable
class CatalogConn(Protocol):
    """SFTP slice: read a registry, write pointers + a merged registry."""

    def read(self, path: str) -> bytes:
        raise NotImplementedError

    def write(self, path: str, data: bytes) -> None:
        raise NotImplementedError

    def mkdir(self, path: str) -> None:
        raise NotImplementedError

    def makedirs(self, path: str) -> None:
        raise NotImplementedError


@dataclass
class MergeResult:
    node: str
    added: int = 0
    skipped: int = 0
    hidden: int = 0  # throne Home (and friends) not imported
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{self.node}: +{self.added} skip {self.skipped} "
            f"home-hidden {self.hidden}"
        )


@dataclass
class SyncResult:
    nodes: list[MergeResult] = field(default_factory=list)
    pushed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class CreatedProject:
    name: str
    node: str
    collection_id: str
    path: str
    pushed: bool = False


def is_throne_home_name(name: str) -> bool:
    return (name or "").strip().lower() == "scratch/home"


def is_throne_home_entry(entry: Any) -> bool:
    """True for the sacred desk. Named scratch piles are ordinary folders."""
    name = str(getattr(entry, "name", "") or "")
    if is_throne_home_name(name):
        return True
    path = str(getattr(entry, "path", "") or "").replace("\\", "/").rstrip("/")
    return path.endswith("/scratch/home") or path.endswith("scratch/home")


def federated_visible(entries: list) -> list:
    return [e for e in entries if not is_throne_home_entry(e)]


def _safe_seg(raw: str) -> str:
    s = (raw or "").strip() or "x"
    if not _SAFE_SEG.fullmatch(s):
        s = re.sub(r"[^A-Za-z0-9_.-]+", "-", s).strip(".-") or "x"
    return s[:64]


def pointer_dir(node: str, name: str, *, user_root: Optional[Path] = None) -> Path:
    """Local thin checkout: ``~/.xlii/remote-projects/<node>/<name>``."""
    from xlii.project_paths import xlii_user_root

    base = user_root if user_root is not None else xlii_user_root()
    return base / "remote-projects" / _safe_seg(node or "throne") / _safe_seg(name)


def parse_registry_bytes(raw: bytes) -> list[dict]:
    data = json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw)
    if not isinstance(data, dict):
        raise ValueError("registry root must be an object")
    # A foreign registry is a catalog, not an address book. Extra top-level
    # keys (ftp_connections, keys, …) are ignored.
    rows = data.get("entries") or []
    if not isinstance(rows, list):
        raise ValueError("registry entries must be a list")
    out = []
    for row in rows:
        if isinstance(row, dict):
            out.append(scrub_catalog_row(row))
    return out


def encode_registry(entries: list[dict]) -> bytes:
    return json.dumps({"entries": entries}, indent=2, sort_keys=True).encode("utf-8")


def unique_registry_name(reg: Registry, name: str, node: str) -> str:
    """Keep ``app`` when free; ``node/app`` when the short name is taken."""
    short = (name or "").strip()
    if not short:
        short = "project"
    if not any(e.name == short for e in reg.entries):
        return short
    tagged = f"{_safe_seg(node)}/{short}" if node else short
    if not any(e.name == tagged for e in reg.entries):
        return tagged
    n = 2
    while any(e.name == f"{tagged}-{n}" for e in reg.entries):
        n += 1
    return f"{tagged}-{n}"


def ensure_pointer(
    *,
    name: str,
    collection_id: str,
    node: str,
    kind: Optional[str] = None,
    created_at: Optional[str] = None,
    user_root: Optional[Path] = None,
) -> Path:
    """A live local project.json that points at the Collection. No file tree."""
    from xlii.config import ProjectConfig, normalize_project_kind

    root = pointer_dir(node, name, user_root=user_root)
    root.mkdir(parents=True, exist_ok=True)
    existing = ProjectConfig.load(root)
    if existing is not None:
        return root
    stamped = datetime.now(timezone.utc).isoformat() if not created_at else created_at
    cfg = ProjectConfig(
        project_root=root,
        name=name,
        collection_id=collection_id,
        created_at=stamped,
        conversation_id=uuid.uuid4().hex,
        local_only=not bool(collection_id),
        kind=normalize_project_kind(kind) or "collection",
    )
    cfg.xli_dir.mkdir(parents=True, exist_ok=True)
    cfg.save()
    return root


def absorb_remote_row(
    reg: Registry,
    raw: dict,
    *,
    origin: str,
    user_root: Optional[Path] = None,
) -> str:
    """Merge one remote registry row. Returns 'added' | 'skipped' | 'hidden'.

    Same collection_id already here → skip (adopted tree wins). Home → hidden.
    Local-only folders on a node still appear in the menu.
    """
    try:
        raw = scrub_catalog_row(raw)
        entry = _entry_from_dict(raw)
    except (TypeError, ValueError, KeyError):
        return "skipped"
    if is_throne_home_entry(entry):
        return "hidden"
    cid = (entry.collection_id or "").strip()
    origin_node = (entry.node or origin or "").strip()
    short = (entry.name or "").strip() or "project"
    if cid and reg.find_by_collection(cid) is not None:
        return "skipped"
    if not cid:
        if any(
            (getattr(e, "node", "") or "") == origin_node
            and (e.name == short or (getattr(e, "remote_path", "") or "") == (entry.path or ""))
            for e in reg.entries
        ):
            return "skipped"
    uname = unique_registry_name(reg, short, origin_node)
    path = ensure_pointer(
        name=short,
        collection_id=cid,
        node=origin_node or "node",
        kind=None,
        created_at=entry.created_at,
        user_root=user_root,
    )
    remote_path = (entry.path or entry.remote_path or "").strip()
    repo = sanitize_repo(getattr(entry, "repo", "") or raw.get("repo") or "")
    try:
        from xlii.config import PROJECT_KIND_CODE, ProjectConfig, normalize_project_kind
        from xlii.desk_files import (
            bind_files_root,
            decode_stub_slug,
            files_root_of,
            is_pointer_stub_path,
            pointer_stub_slug,
            sftp_files_address,
        )

        pc = ProjectConfig.load(path)
        if pc is not None:
            dirty = False
            if repo and not (pc.repo or ""):
                pc.repo = repo
                dirty = True
            origin = (origin_node or "").strip()
            if origin and remote_path and not files_root_of(pc):
                if is_pointer_stub_path(remote_path):
                    inner = decode_stub_slug(pointer_stub_slug(remote_path))
                    if inner:
                        try:
                            bind_files_root(pc, inner)
                            dirty = False
                        except ValueError:
                            # The bind was rejected, so dirty stays set and the row is rewritten instead.
                            pass
                        if normalize_project_kind(pc.kind) != PROJECT_KIND_CODE:
                            pc.kind = PROJECT_KIND_CODE
                            dirty = True
                else:
                    addr = sftp_files_address(origin, remote_path)
                    if addr:
                        try:
                            bind_files_root(pc, addr)
                            dirty = False
                        except ValueError:
                            # The sftp bind was rejected too, so dirty stays set and the row is rewritten
                            # instead.
                            pass
            if dirty:
                pc.save()
    except Exception:
        # Stamping repo / Files mount onto the pointer is decoration, not the
        # adoption itself — a missing/unwritable project config must not
        # abort the registry upsert below.
        pass
    reg.upsert(
        RegistryEntry(
            path=str(path.resolve()),
            collection_id=cid,
            name=uname,
            created_at=entry.created_at or datetime.now(timezone.utc).isoformat(),
            node=origin_node,
            remote_path=remote_path,
            repo=repo,
        )
    )
    return "added"


def apply_remote_entries(
    reg: Registry,
    rows: list[dict],
    origin: str,
    *,
    user_root: Optional[Path] = None,
) -> MergeResult:
    result = MergeResult(node=origin)
    for raw in rows:
        try:
            fate = absorb_remote_row(reg, raw, origin=origin, user_root=user_root)
        except Exception as e:  # noqa: BLE001 — one bad row must not abort the node
            result.errors.append(f"{origin}: {type(e).__name__}: {e}")
            result.skipped += 1
            continue
        if fate == "added":
            result.added += 1
        elif fate == "hidden":
            result.hidden += 1
        else:
            result.skipped += 1
    return result


def pull_node_registry(conn: CatalogConn, *, registry_path: str = DEFAULT_REMOTE_REGISTRY) -> list[dict]:
    return parse_registry_bytes(conn.read(registry_path))


def _pointer_project_json(
    *,
    name: str,
    collection_id: str,
    created_at: str,
    kind: str = "collection",
    conversation_id: str = "",
    repo: str = "",
) -> bytes:
    # No ``root`` — the receiving box binds on first load.
    # No address-book fields. ``repo`` is a public git URL only.
    card = {
        "name": name,
        "collection_id": collection_id,
        "created_at": created_at,
        "local_only": not bool(collection_id),
        "kind": kind or "collection",
        "conversation_id": conversation_id or uuid.uuid4().hex,
        "repo": sanitize_repo(repo),
    }
    return json.dumps(card, indent=2).encode("utf-8")


def push_catalog_to_node(
    conn: CatalogConn,
    offer: list[RegistryEntry],
    *,
    origin_label: str,
    registry_path: str = DEFAULT_REMOTE_REGISTRY,
    pointer_root: str = DEFAULT_REMOTE_POINTER,
) -> int:
    """Merge federated (non-Home, with Collection) rows onto a node."""
    try:
        existing = pull_node_registry(conn, registry_path=registry_path)
    except FileNotFoundError:
        existing = []
    except Exception:
        existing = []
    have = {(e.get("collection_id") or "").strip() for e in existing}
    have.discard("")
    added = 0
    for e in federated_visible(offer):
        cid = (e.collection_id or "").strip()
        if not cid or cid in have:
            continue
        node = (e.node or origin_label or "throne").strip()
        short = (e.name or "project").split("/", 1)[-1]
        rdir = f"{pointer_root.rstrip('/')}/{_safe_seg(node)}/{_safe_seg(short)}"
        body = _pointer_project_json(
            name=short,
            collection_id=cid,
            created_at=e.created_at or datetime.now(timezone.utc).isoformat(),
            repo=sanitize_repo(getattr(e, "repo", "") or ""),
        )
        conn.makedirs(f"{rdir}/.xlii")
        conn.write(f"{rdir}/.xlii/project.json", body)
        existing.append(
            public_catalog_row(e, repo=getattr(e, "repo", "") or "")
        )
        have.add(cid)
        added += 1
    if added:
        parent = registry_path.rsplit("/", 1)[0] if "/" in registry_path else ""
        if parent:
            conn.makedirs(parent)
        conn.write(registry_path, encode_registry(existing))
    return added


def sync_fabric_projects(
    roster: dict,
    *,
    connect: Callable[[str], CatalogConn],
    require_remote: Optional[Callable[[str], Optional[str]]] = None,
    this_node: str = "throne",
    user_root: Optional[Path] = None,
    dry_run: bool = False,
) -> SyncResult:
    """Pull every node's registry, merge, push the catalog back. Throne-side."""
    out = SyncResult()
    reg = Registry.load()
    for name in sorted(roster or {}):
        spec = roster[name] or {}
        remote = (spec.get("remote") or "").strip()
        if not remote:
            out.errors.append(f"{name}: no remote")
            continue
        if require_remote is not None:
            err = require_remote(remote)
            if err:
                out.errors.append(f"{name}: {err}")
                continue
        try:
            conn = connect(remote)
        except Exception as e:  # noqa: BLE001
            out.errors.append(f"{name}: connect failed ({type(e).__name__}: {e})")
            continue
        try:
            rows = pull_node_registry(conn)
        except FileNotFoundError:
            rows = []
        except Exception as e:  # noqa: BLE001
            out.errors.append(f"{name}: read registry ({type(e).__name__}: {e})")
            continue
        if dry_run:
            hidden = sum(
                1 for r in rows
                if is_throne_home_name(str(r.get("name") or ""))
            )
            out.nodes.append(MergeResult(
                node=name, added=0, skipped=len(rows) - hidden, hidden=hidden,
            ))
            continue
        merged = apply_remote_entries(reg, rows, name, user_root=user_root)
        out.nodes.append(merged)
    if not dry_run:
        reg.save()
        offer = list(reg.entries)
        for name in sorted(roster or {}):
            spec = roster[name] or {}
            remote = (spec.get("remote") or "").strip()
            if not remote:
                continue
            if require_remote is not None and require_remote(remote):
                continue
            try:
                conn = connect(remote)
                n = push_catalog_to_node(conn, offer, origin_label=this_node or "throne")
                if n:
                    out.pushed.append(f"{name}: +{n}")
            except Exception as e:  # noqa: BLE001
                out.errors.append(f"{name}: push failed ({type(e).__name__}: {e})")
    return out


def create_fabric_project(
    name: str,
    node: str,
    *,
    roster: Optional[dict] = None,
    connect: Optional[Callable[[str], CatalogConn]] = None,
    require_remote: Optional[Callable[[str], Optional[str]]] = None,
    kind: Optional[str] = None,
    user_root: Optional[Path] = None,
    this_node: str = "",
) -> CreatedProject:
    """Mkdir the folder on that node, stub here, Files points at sftp.

    No vendor Collection. ``/sync`` on a desk that has the management key
    is how you opt into searchable memory later.
    """
    from xlii.desk_files import adopt_remote
    from xlii.registry import Registry
    from xlii.tasks import TaskParseError, claim_folder_name

    try:
        safe = claim_folder_name(name)
    except TaskParseError as e:
        raise ValueError(str(e)) from e
    node = (node or "").strip()
    if not node:
        raise ValueError("need a node")
    roster = roster or {}
    if node not in roster:
        raise ValueError(f"no such node: {node}")
    spec = roster.get(node) or {}
    remote = (spec.get("remote") or "").strip()
    if not remote:
        raise ValueError(f"node {node!r} has no remote connection")
    if require_remote is not None:
        err = require_remote(remote)
        if err:
            raise ValueError(err)
    if connect is None:
        raise ValueError("need a remote connection")
    conn = connect(remote)
    conn.makedirs(safe)
    addr = f"sftp://{remote}/{safe}"
    project = adopt_remote(addr, kind=kind, name=safe)
    root = Path(project.project_root)
    stamped = datetime.now(timezone.utc).isoformat()
    reg = Registry.load()
    uname = unique_registry_name(reg, safe, node)
    existing = reg.find_by_path(root)
    if existing is not None:
        existing.node = node
        existing.remote_path = safe
        existing.name = uname
        reg.save()
        path = existing.path
        cid = existing.collection_id
    else:
        entry = RegistryEntry(
            path=str(root.resolve()),
            collection_id=getattr(project, "collection_id", "") or "",
            name=uname,
            created_at=stamped,
            node=node,
            remote_path=safe,
        )
        reg.upsert(entry)
        reg.save()
        path = entry.path
        cid = entry.collection_id
    pushed = True
    _ = this_node
    return CreatedProject(
        name=uname,
        node=node,
        collection_id=cid,
        path=path,
        pushed=pushed,
    )


def as_public_row(entry: RegistryEntry) -> dict:
    """Menu/CLI card. Home is the caller's filter, not this helper's."""
    return {
        "name": entry.name,
        "path": entry.path,
        "collection_id": entry.collection_id,
        "node": entry.node or "",
        "created_at": entry.created_at,
        "home": is_throne_home_entry(entry),
    }
