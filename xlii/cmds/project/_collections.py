"""Shared cloud collection helpers for gc and rm."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from xlii.config import JOURNAL_COLLECTION_PREFIX
from xlii.registry import Registry


def _list_cloud_collections(clients) -> dict[str, str]:
    """Every cloud collection as {id: name}, paging until exhausted.

    Shared by `gc` and `project rm`. The paging loop lives in CollectionsBackend
    (the vendor client's one home); this stays the shared entry point."""
    from xlii.storage_backend import CollectionsBackend

    return CollectionsBackend.list_collections(clients)


def _claimed_collection(path: Path) -> Optional[str]:
    """collection_id from ``.xlii/project.json`` at `path`, or None."""
    pj = Path(path) / ".xlii" / "project.json"
    if not pj.is_file():
        return None
    try:
        return json.loads(pj.read_text()).get("collection_id")
    except (json.JSONDecodeError, OSError):
        return None


def _claimed_journal_collection(path: Path) -> Optional[str]:
    """journal_collection_id from ``.xlii/project.json`` at `path`, or None."""
    pj = Path(path) / ".xlii" / "project.json"
    if not pj.is_file():
        return None
    try:
        return json.loads(pj.read_text()).get("journal_collection_id")
    except (json.JSONDecodeError, OSError):
        return None


def _count_collection_docs(clients, collection_id: str) -> Optional[int]:
    """Best-effort document count for the fat-collection delete guard.

    Returns None when it can't be determined (a count failure must never block
    the delete — only inform the confirmation)."""
    try:
        from xlii.client import iter_collection_documents
        return sum(1 for _ in iter_collection_documents(clients.xai, collection_id))
    except Exception:
        return None


def _orphan_journal_collections(
    cloud: dict[str, str], registry: Registry, *, exclude: set[str], project_name: str
) -> list[tuple[str, str]]:
    """Journal Collections for `project_name` that no registered project claims
    via journal_collection_id — the Shadow-aware orphan sweep (OQ3).

    Scoped to ``JOURNAL_COLLECTION_PREFIX + project_name`` so removing one
    project never sweeps another project's journal data on a shared account.
    `exclude` holds ids already in the teardown plan (the project being removed),
    so they're not double-counted as orphans."""
    expected = JOURNAL_COLLECTION_PREFIX + project_name
    claimed: set[str] = set(exclude)
    for entry in registry.entries:
        jid = _claimed_journal_collection(Path(entry.path))
        if jid:
            claimed.add(jid)
    return [
        (cid, name)
        for cid, name in cloud.items()
        if name == expected and cid not in claimed
    ]



