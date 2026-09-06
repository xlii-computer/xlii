"""Live occupancy across Face and daemon.

In-memory Occupancy is the state machine. This file is the *sitting copy*
on ``XDG_RUNTIME_DIR`` (or ``XLII_OCCUPANCY_PATH``): another process can
see who has the mouth. Reboot drops the runtime dir — lock does not hostage
the next boot.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from xlii.atomicio import write_text_atomic
from xlii.occupancy import (
    FaceLock,
    Occupancy,
    REMOTE_LAB_IDLE_S,
    RemoteLabSitting,
    SITTING_TIER_DOOR,
    SITTING_TIER_GLASS,
    default_occupancy,
    load_glass_config,
)

_MOUTHS = frozenset({"desk", "me", "none"})


def _sitting_tier(raw: Any) -> str:
    t = str(raw or "").strip().lower()
    return t if t == SITTING_TIER_GLASS else SITTING_TIER_DOOR
_LEVELS = frozenset({"home", "cafe", "black"})
_T = TypeVar("_T")


@contextmanager
def _occupancy_lock(path: Path):
    """Serialize read-modify-write cycles across Face and daemon processes."""
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def occupancy_path() -> Path:
    override = (os.environ.get("XLII_OCCUPANCY_PATH") or "").strip()
    if override:
        return Path(override)
    runtime = (os.environ.get("XDG_RUNTIME_DIR") or "").strip()
    if runtime:
        return Path(runtime) / "xlii-occupancy.json"
    return Path(tempfile.gettempdir()) / f"xlii-{os.getuid()}-occupancy.json"


def _occ_from_dict(raw: dict[str, Any]) -> Occupancy:
    face_raw = raw.get("face") if isinstance(raw.get("face"), dict) else {}
    rl_raw = raw.get("remote_lab") if isinstance(raw.get("remote_lab"), dict) else {}
    mouth = raw.get("mouth") if raw.get("mouth") in _MOUTHS else "desk"
    level = face_raw.get("level") if face_raw.get("level") in _LEVELS else "home"
    cfg = load_glass_config()
    via = str(raw.get("via") or "").strip()
    occ = Occupancy(
        mouth=mouth,  # type: ignore[arg-type]
        via=via,
        face=FaceLock(
            locked=bool(face_raw.get("locked")),
            level=level,  # type: ignore[arg-type]
            black=bool(face_raw.get("black", cfg.black)),
            fail_count=int(face_raw.get("fail_count") or 0),
        ),
        remote_lab=RemoteLabSitting(
            open=bool(rl_raw.get("open")),
            locked=bool(rl_raw.get("locked")),
            opened_at=float(rl_raw.get("opened_at") or 0.0),
            last_agent_at=float(rl_raw.get("last_agent_at") or 0.0),
            idle_s=float(rl_raw.get("idle_s") or REMOTE_LAB_IDLE_S),
            source=str(rl_raw.get("source") or ""),
            device=str(rl_raw.get("device") or ""),
            tier=_sitting_tier(rl_raw.get("tier")),
            id=str(rl_raw.get("id") or ""),
        ),
    )
    return occ


def _occ_to_dict(occ: Occupancy, *, face_last_input_at: float) -> dict[str, Any]:
    return {
        "mouth": occ.mouth,
        "via": getattr(occ, "via", "") or "",
        "face": {
            "locked": occ.face.locked,
            "level": occ.face.level,
            "black": occ.face.black,
            "fail_count": occ.face.fail_count,
        },
        "remote_lab": {
            "open": occ.remote_lab.open,
            "locked": occ.remote_lab.locked,
            "opened_at": occ.remote_lab.opened_at,
            "last_agent_at": occ.remote_lab.last_agent_at,
            "idle_s": occ.remote_lab.idle_s,
            "source": getattr(occ.remote_lab, "source", "") or "",
            "device": getattr(occ.remote_lab, "device", "") or "",
            "tier": _sitting_tier(getattr(occ.remote_lab, "tier", "")),
            "id": getattr(occ.remote_lab, "id", "") or "",
        },
        "face_last_input_at": face_last_input_at,
    }


def _read_raw_bundle_dict(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def _load_bundle_unlocked(*, now: Optional[float] = None) -> tuple[Occupancy, float]:
    """Return (occupancy, face_last_input_at). Missing/corrupt → defaults."""
    path = occupancy_path()
    raw = _read_raw_bundle_dict(path)
    if raw is None:
        occ = default_occupancy()
        return occ, 0.0
    try:
        occ = _occ_from_dict(raw)
    except (TypeError, ValueError):
        return default_occupancy(), 0.0
    try:
        last = float(raw.get("face_last_input_at") or 0.0)
    except (TypeError, ValueError):
        last = 0.0
    clock = time.time() if now is None else now
    occ.tick(now=clock)
    return occ, last


def load_bundle(*, now: Optional[float] = None) -> tuple[Occupancy, float]:
    """Return (occupancy, face_last_input_at). Missing/corrupt → defaults."""
    path = occupancy_path()
    with _occupancy_lock(path):
        return _load_bundle_unlocked(now=now)


def load_live(*, now: Optional[float] = None) -> Occupancy:
    occ, _ = load_bundle(now=now)
    return occ


def _read_last_input_unlocked() -> float:
    path = occupancy_path()
    raw = _read_raw_bundle_dict(path)
    if raw is None:
        return 0.0
    try:
        return float(raw.get("face_last_input_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _save_live_unlocked(occ: Occupancy, *, face_last_input_at: Optional[float] = None) -> None:
    last = (
        _read_last_input_unlocked() if face_last_input_at is None else face_last_input_at
    )
    write_text_atomic(
        occupancy_path(),
        json.dumps(_occ_to_dict(occ, face_last_input_at=last), indent=2) + "\n",
        mode=0o600,
    )


def save_live(occ: Occupancy, *, face_last_input_at: Optional[float] = None) -> None:
    path = occupancy_path()
    with _occupancy_lock(path):
        _save_live_unlocked(occ, face_last_input_at=face_last_input_at)


def mutate(
    fn: Callable[[Occupancy], Any],
    *,
    now: Optional[float] = None,
    touch_face_input: bool = False,
) -> Occupancy:
    """Load, tick, run *fn*, save. ``fn`` may mutate the occupancy in place."""
    clock = time.time() if now is None else now
    path = occupancy_path()
    with _occupancy_lock(path):
        occ, last = _load_bundle_unlocked(now=clock)
        fn(occ)
        if touch_face_input:
            last = clock
        _save_live_unlocked(occ, face_last_input_at=last)
        return occ


def mutate_bundle(
    fn: Callable[[Occupancy, float], tuple[Optional[float], _T]],
    *,
    now: Optional[float] = None,
) -> _T:
    """Load (occ, last), run *fn*, save under one lock. *fn* returns (new_last|None, result)."""
    clock = time.time() if now is None else now
    path = occupancy_path()
    with _occupancy_lock(path):
        occ, last = _load_bundle_unlocked(now=clock)
        new_last, result = fn(occ, last)
        if new_last is not None:
            last = new_last
        _save_live_unlocked(occ, face_last_input_at=last)
        return result


def apply_face_idle(occ: Occupancy, last_input_at: float, *, now: float) -> bool:
    """Lock the Face if ``[glass] idle_s`` elapsed with no input. True if locked now."""
    from xlii.occupancy import _read_glass_section

    idle = float(_read_glass_section().get("idle_s") or 0)
    if idle <= 0 or last_input_at <= 0:
        return occ.face.locked
    if now - last_input_at >= idle and not occ.face.locked:
        occ.lock_face()
    return occ.face.locked


def glass_wire(occ: Occupancy) -> str:
    """Chrome ``glass`` field: ``""`` | ``locked`` | ``black``."""
    if not occ.face.locked:
        return ""
    if occ.face.black or occ.face.level == "black":
        return "black"
    return "locked"
