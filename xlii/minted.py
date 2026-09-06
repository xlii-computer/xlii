"""Ownership manifest: keys and collections minted on this body.

Written at mint time so destroy can revoke by id. Never stores secrets.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xlii.atomicio import write_text_atomic
from xlii.config import JOURNAL_COLLECTION_PREFIX
from xlii.project_paths import user_home

logger = logging.getLogger(__name__)

_VERSION = 1


@dataclass
class MintedKey:
    key_id: str
    team_id: str
    label: str = ""
    minted_at: str = ""  # ISO-8601


@dataclass
class MintedCollection:
    collection_id: str
    team_id: str
    name: str = ""
    kind: str = ""  # "main" | "journal" | ""
    minted_at: str = ""


@dataclass
class Manifest:
    keys: list[MintedKey] = field(default_factory=list)
    collections: list[MintedCollection] = field(default_factory=list)


def minted_path() -> Path:
    override = os.environ.get("XLII_MINTED_PATH")
    if override:
        return Path(override)
    return user_home() / ".local" / "share" / "xlii" / "minted.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_manifest() -> Manifest:
    return Manifest()


def _key_from_dict(d: dict[str, Any]) -> MintedKey | None:
    key_id = d.get("key_id")
    team_id = d.get("team_id")
    if not isinstance(key_id, str) or not key_id:
        return None
    if not isinstance(team_id, str):
        team_id = ""
    return MintedKey(
        key_id=key_id,
        team_id=team_id,
        label=d.get("label") if isinstance(d.get("label"), str) else "",
        minted_at=d.get("minted_at") if isinstance(d.get("minted_at"), str) else "",
    )


def _collection_from_dict(d: dict[str, Any]) -> MintedCollection | None:
    collection_id = d.get("collection_id")
    team_id = d.get("team_id")
    if not isinstance(collection_id, str) or not collection_id:
        return None
    if not isinstance(team_id, str):
        team_id = ""
    return MintedCollection(
        collection_id=collection_id,
        team_id=team_id,
        name=d.get("name") if isinstance(d.get("name"), str) else "",
        kind=d.get("kind") if isinstance(d.get("kind"), str) else "",
        minted_at=d.get("minted_at") if isinstance(d.get("minted_at"), str) else "",
    )


def load() -> Manifest:
    path = minted_path()
    if not path.is_file():
        return _empty_manifest()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("minted.json unreadable (%s): %s", path, e)
        return _empty_manifest()
    if not isinstance(raw, dict):
        logger.warning("minted.json invalid shape (%s): expected object", path)
        return _empty_manifest()
    keys: list[MintedKey] = []
    for item in raw.get("keys") or []:
        if isinstance(item, dict):
            k = _key_from_dict(item)
            if k is not None:
                keys.append(k)
    collections: list[MintedCollection] = []
    for item in raw.get("collections") or []:
        if isinstance(item, dict):
            c = _collection_from_dict(item)
            if c is not None:
                collections.append(c)
    return Manifest(keys=keys, collections=collections)


def save(m: Manifest) -> None:
    payload = {
        "version": _VERSION,
        "keys": [asdict(k) for k in m.keys],
        "collections": [asdict(c) for c in m.collections],
    }
    write_text_atomic(minted_path(), json.dumps(payload, indent=2) + "\n", mode=0o600)


def _infer_collection_kind(name: str) -> str:
    if name.startswith(JOURNAL_COLLECTION_PREFIX):
        return "journal"
    return ""


def record_key_safe(*, key_id: str, team_id: str, label: str = "") -> None:
    """Best-effort ``record_key``; mint paths swallow failures after logging."""
    try:
        record_key(key_id=key_id, team_id=team_id, label=label)
    except Exception as e:
        logger.warning("record_key failed (non-fatal): %s", e)


def record_collection_safe(
    *, collection_id: str, team_id: str, name: str = "", kind: str = ""
) -> None:
    """Best-effort ``record_collection``; mint paths swallow failures after logging."""
    try:
        record_collection(
            collection_id=collection_id, team_id=team_id, name=name, kind=kind
        )
    except Exception as e:
        logger.warning("record_collection failed (non-fatal): %s", e)


def record_key(*, key_id: str, team_id: str, label: str = "") -> None:
    if not key_id:
        return
    m = load()
    for existing in m.keys:
        if existing.key_id == key_id and existing.team_id == team_id:
            return
    m.keys.append(
        MintedKey(key_id=key_id, team_id=team_id, label=label, minted_at=_now_iso())
    )
    save(m)


def record_collection(
    *, collection_id: str, team_id: str, name: str = "", kind: str = ""
) -> None:
    if not collection_id:
        return
    if not kind and name:
        kind = _infer_collection_kind(name)
    m = load()
    for existing in m.collections:
        if existing.collection_id == collection_id and existing.team_id == team_id:
            return
    m.collections.append(
        MintedCollection(
            collection_id=collection_id,
            team_id=team_id,
            name=name,
            kind=kind,
            minted_at=_now_iso(),
        )
    )
    save(m)


def ids_for_team(team_id: str) -> tuple[list[str], list[str]]:
    """Return (key_ids, collection_ids) recorded for ``team_id``."""
    m = load()
    key_ids = [k.key_id for k in m.keys if k.team_id == team_id]
    collection_ids = [c.collection_id for c in m.collections if c.team_id == team_id]
    return key_ids, collection_ids
