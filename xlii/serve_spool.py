"""Daemon ↔ serve grant spool — ``<state_dir>/serve-grants.json`` (serve-public P0).

The fabric daemon mints pairing codes into this file; the serve process drains
them and owns them from pairing onward (D6). Stdlib + ``atomicio`` only — keeps
the daemon free of the ``[web]`` extra.

Schema::

    {"version": 1, "pending": [
        {"code": "X7K2M9Q4", "minted_at": 1721..., "ttl_s": 300, "mode": "full"}
    ]}

Codes are stored in canonical 8-char form (no grouping). Corrupt or missing
files degrade to empty — serve must never crash on a bad spool.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from xlii.atomicio import write_text_atomic
from xlii.serve_gate import is_valid_format, normalize_code

SPOOL_NAME = "serve-grants.json"
SPOOL_VERSION = 1
_VALID_MODES = frozenset({"full", "preview"})


@contextmanager
def _spool_lock(path: Path):
    """Serialize read-modify-write cycles across processes.

    Three writers share this file (fabric daemon mint, CLI mint, serve drain);
    ``write_text_atomic`` makes each *write* safe but not the RMW cycle — two
    interleaved cycles can lose an append, or worse, write drained codes back
    into the spool, where the next drain would re-register a code that may
    already have been paired (breaking single-use). flock is advisory but every
    writer goes through this module, and closing the fd releases the lock even
    if the holder dies."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def spool_path(state_dir: Path) -> Path:
    """Absolute path of the grant spool under ``state_dir``."""
    return Path(state_dir) / SPOOL_NAME


def _empty() -> dict[str, Any]:
    return {"version": SPOOL_VERSION, "pending": []}


def _read(path: Path) -> dict[str, Any]:
    """Load the spool; corrupt / missing / wrong shape → empty document."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _empty()
    except OSError:
        return _empty()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    pending = data.get("pending")
    if not isinstance(pending, list):
        return _empty()
    # Keep only well-shaped entries; drop junk silently.
    clean: list[dict[str, Any]] = []
    for item in pending:
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        minted_at = item.get("minted_at")
        ttl_s = item.get("ttl_s")
        mode = item.get("mode")
        if not isinstance(code, str) or not is_valid_format(code):
            continue
        if not isinstance(minted_at, (int, float)):
            continue
        if not isinstance(ttl_s, int) or ttl_s <= 0:
            continue
        if mode not in _VALID_MODES:
            continue
        clean.append({
            "code": normalize_code(code),
            "minted_at": float(minted_at),
            "ttl_s": int(ttl_s),
            "mode": mode,
        })
    return {"version": SPOOL_VERSION, "pending": clean}


def _write(path: Path, data: dict[str, Any]) -> None:
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    write_text_atomic(path, text, mode=0o600)


def append_pending(
    state_dir: Path,
    code: str,
    *,
    ttl_s: int,
    mode: str,
    now: float,
) -> None:
    """Daemon side: append one pending code (atomic read-modify-write, 0600)."""
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(_VALID_MODES)}; got {mode!r}")
    if ttl_s <= 0:
        raise ValueError(f"ttl_s must be positive; got {ttl_s}")
    canon = normalize_code(code)
    if not is_valid_format(canon):
        raise ValueError(f"invalid pairing code format: {code!r}")

    path = spool_path(state_dir)
    with _spool_lock(path):
        data = _read(path)
        # Replace same code if re-minted; otherwise append.
        data["pending"] = [p for p in data["pending"] if p["code"] != canon]
        data["pending"].append({
            "code": canon,
            "minted_at": float(now),
            "ttl_s": int(ttl_s),
            "mode": mode,
        })
        _write(path, data)


def take_pending(state_dir: Path, code: str, *, now: float) -> dict | None:
    """Atomically remove one live pending code. ``None`` if missing or expired.

    Other codes stay in the spool — the tailnet glass spends one webcode
    without draining the public-serve queue.
    """
    canon = normalize_code(code)
    if not is_valid_format(canon):
        return None
    path = spool_path(state_dir)
    with _spool_lock(path):
        data = _read(path)
        found: dict | None = None
        hit = False
        kept: list[dict] = []
        for item in data["pending"]:
            if item["code"] == canon:
                hit = True
                if now < item["minted_at"] + item["ttl_s"]:
                    found = item
                continue
            kept.append(item)
        if hit:
            data["pending"] = kept
            _write(path, data)
        return found


def drain_pending(state_dir: Path) -> list[dict]:
    """Serve side: atomically take all pending entries and clear the spool.

    Returns the list of pending dicts (may be empty). After this returns, serve
    owns those grants — the daemon must mint again for new codes.
    """
    path = spool_path(state_dir)
    with _spool_lock(path):
        data = _read(path)
        pending = list(data["pending"])
        if pending or path.exists():
            _write(path, _empty())
    return pending


def default_state_dir() -> Path:
    """Canonical root for the serve-public handshake files (body contract #1).

    Every producer/consumer of the grant spool, session mirror, and revoke
    queue — fabric-daemon mint, ``xlii serve mint``, serve's drain — resolves
    the SAME directory through this one accessor. A mismatch is silent: nothing
    errors, codes just never arrive (#283). Delegates to the daemon runtime dir,
    so ``XLII_STATE_DIR`` overrides for tests and relocated installs.
    """
    from xlii.journal_daemon import runtime_dir
    return runtime_dir()


# ── Revoke queue — <state_dir>/serve-revokes.json ────────────────────────────
#
# Producers: `xlii serve revoke` (V3) and the fabric `webcode kill` verb (V5).
# Consumer: the serve process drains on its sweep tick and kills the sessions.
# Shape: {"version": 1, "pending": ["<sid>" | "all", ...]} — "all" is the
# kill-everything sentinel. Same atomicio + flock discipline as the grant spool.

REVOKES_NAME = "serve-revokes.json"
_REVOKE_TARGET_MAX = 128  # sids are ~22 chars; bound what lands in file + audit


def revokes_path(state_dir: Path) -> Path:
    """Absolute path of the revoke queue under ``state_dir``."""
    return Path(state_dir) / REVOKES_NAME


def _read_revokes(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return []
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    if not isinstance(data, dict) or not isinstance(data.get("pending"), list):
        return []
    return [
        t for t in data["pending"]
        if isinstance(t, str) and 0 < len(t) <= _REVOKE_TARGET_MAX
    ]


def append_revoke(state_dir: Path, target: str) -> None:
    """Queue a session id (or the ``"all"`` sentinel) for the serve process to
    kill on its next sweep. Deduplicates; atomic read-modify-write under flock
    (two producers share this file)."""
    target = (target or "").strip()
    if not target or len(target) > _REVOKE_TARGET_MAX:
        raise ValueError(f"invalid revoke target: {target[:32]!r}...")
    path = revokes_path(state_dir)
    with _spool_lock(path):
        pending = _read_revokes(path)
        if target not in pending:
            pending.append(target)
            _write(path, {"version": SPOOL_VERSION, "pending": pending})


def drain_revokes(state_dir: Path) -> list[str]:
    """Serve side: atomically take all queued revoke targets and clear the
    queue. Corrupt or missing file degrades to empty."""
    path = revokes_path(state_dir)
    with _spool_lock(path):
        pending = _read_revokes(path)
        if pending or path.exists():
            _write(path, {"version": SPOOL_VERSION, "pending": []})
    return pending
