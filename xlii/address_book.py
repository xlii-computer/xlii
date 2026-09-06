"""Address book vs project card.

The project card (``.xlii/project.json`` + a catalog row) may carry *public*
identity: name, path, kind, a git ``repo`` URL. Nodes can share that.

The address book (named remotes, vault secrets, node roster, JIDs, SSH keys)
is this box's. A catalog row cannot mint a connection, cannot carry a
password, and cannot be a way in.

Known connections are only those already on *this* desk (``xlii remote add``).
A node announcing ``sftp://newbox/…`` does not add ``newbox``.
"""

from __future__ import annotations

import re
from typing import Any, Optional
from urllib.parse import urlsplit

# Keys that must never appear on a shared catalog row or ride a project card
# between boxes. Extra keys on a foreign registry are dropped, not stored.
FORBIDDEN_CATALOG_KEYS = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "vault_ref", "key_path", "key_filename", "private_key", "privatekey",
    "identity_file", "pem", "ftp_connections", "fabric_nodes",
    "management_api_key", "jid", "xmpp_password", "xmpp",
})

# git@host:path/repo.git  or  ssh://git@host/path
_GIT_SCP = re.compile(
    r"^(?:git@|org-\d+@)?[A-Za-z0-9._-]+(?:\.[A-Za-z0-9._-]+)+"
    r":[A-Za-z0-9._/~+-]+(?:\.git)?$"
)
_TOKENISH = re.compile(
    r"^(ghp_|github_pat_|gho_|ghu_|xox[baprs]-|sk-|xai-)",
    re.I,
)


class CatalogUnsafe(ValueError):
    """A catalog row tried to carry address-book material."""


def known_connections(cfg: Optional[object] = None) -> frozenset[str]:
    """Connection names this desk already has. Foreign names are not added."""
    if cfg is None:
        try:
            from xlii.config import GlobalConfig

            cfg = GlobalConfig.load()
        except Exception:
            return frozenset()
    conns = getattr(cfg, "ftp_connections", None) or {}
    if not isinstance(conns, dict):
        return frozenset()
    return frozenset(str(k) for k in conns if str(k).strip())


def is_known_connection(name: str, *, cfg: Optional[object] = None) -> bool:
    return bool(name) and name in known_connections(cfg)


def sanitize_repo(raw: str) -> str:
    """A public git URL, or ``""``. Credentials in the URL are refused."""
    text = (raw or "").strip()
    if not text or len(text) > 512:
        return ""
    if any(k in text.lower() for k in ("begin private", "-----begin", "vault_ref")):
        return ""
    if _GIT_SCP.match(text):
        return text
    parts = urlsplit(text)
    if parts.scheme not in {"https", "http", "ssh", "git"}:
        return ""
    if parts.password:
        return ""
    if parts.username and _TOKENISH.match(parts.username):
        return ""
    host = (parts.hostname or "").strip()
    if not host:
        return ""
    return text


def files_root_if_known(raw: str, *, cfg: Optional[object] = None) -> str:
    """Keep ``sftp://<already-known-conn>/path``. Unknown names drop to ``""``."""
    from xlii.addressing import Address
    from xlii.desk_files import is_remote_files_address, normalize_files_root

    text = (raw or "").strip()
    if not is_remote_files_address(text):
        return ""
    try:
        parsed = Address.parse(text)
    except Exception:
        return ""
    if not is_known_connection(parsed.key, cfg=cfg):
        return ""
    return normalize_files_root(text)


def scrub_catalog_row(raw: dict, *, cfg: Optional[object] = None) -> dict:
    """Public card only. Forbidden keys dropped. ``repo`` sanitized."""
    if not isinstance(raw, dict):
        raise TypeError("catalog row must be an object")
    hit = FORBIDDEN_CATALOG_KEYS.intersection(raw)
    if hit:
        # Drop, do not store, do not raise on ingest — a hostile node
        # must not crash the merge. The keys never land.
        raw = {k: v for k, v in raw.items() if k not in FORBIDDEN_CATALOG_KEYS}
    repo = sanitize_repo(str(raw.get("repo") or ""))
    files = files_root_if_known(str(raw.get("files_root") or ""), cfg=cfg)
    return {
        "name": str(raw.get("name") or ""),
        "path": str(raw.get("path") or ""),
        "collection_id": str(raw.get("collection_id") or ""),
        "created_at": str(raw.get("created_at") or ""),
        "node": str(raw.get("node") or ""),
        "remote_path": str(raw.get("remote_path") or raw.get("path") or ""),
        "repo": repo,
        "files_root": files,
    }


def public_catalog_row(entry: Any, *, repo: str = "", cfg: Optional[object] = None) -> dict:
    """What this desk may *send*. No vault, no roster, no passwords."""
    raw = {
        "name": getattr(entry, "name", "") or "",
        "path": getattr(entry, "remote_path", None) or getattr(entry, "path", "") or "",
        "collection_id": getattr(entry, "collection_id", "") or "",
        "created_at": getattr(entry, "created_at", "") or "",
        "node": getattr(entry, "node", "") or "",
        "remote_path": getattr(entry, "remote_path", "") or "",
        "repo": repo or getattr(entry, "repo", "") or "",
    }
    return scrub_catalog_row(raw, cfg=cfg)
