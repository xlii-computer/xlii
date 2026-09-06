"""Exclusive input occupancy — one mouth, Face lock, remote-lab sitting.

Memory bus is fabric; this file is input exclusivity only.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

Mouth = Literal["desk", "me", "none"]
GlassLevel = Literal["home", "cafe", "black"]

LOCK_VERBS = frozenset({"lock", "unlock"})

# Sitting and phone /xsu share this idle: 5 minutes with no activity autolocks.
REMOTE_LAB_IDLE_S = 300.0
SITTING_TIER_DOOR = "door"
SITTING_TIER_GLASS = "glass"
_SITTING_TIERS = frozenset({SITTING_TIER_DOOR, SITTING_TIER_GLASS})

_DEFAULT_GLASS = {
    "level": "home",
    "black": False,
    "idle_s": 0,
    "remote_lab_idle_s": REMOTE_LAB_IDLE_S,
}


@dataclass
class FaceLock:
    locked: bool = False
    level: GlassLevel = "home"
    black: bool = False
    fail_count: int = 0  # Face only — separate from daemon `/xsu` elevation counter.


@dataclass
class RemoteLabSitting:
    open: bool = False
    locked: bool = False
    opened_at: float = 0.0
    last_agent_at: float = 0.0
    idle_s: float = REMOTE_LAB_IDLE_S
    # "face" (desk /remote-control open) or "phone" (daemon /xsu then
    # /remote-control). Same 5-minute idle either way.
    source: str = ""
    # Tailnet node name this sitting is bound to (empty = any allowlisted device).
    device: str = ""
    # "door" = /remote-turn only (today). "glass" = assets + WS on the
    # sitting-scoped TailnetDoor (`/remote-control open --glass`).
    tier: str = SITTING_TIER_DOOR
    # Minted on open; glass grants name this so drop/re-open invalidates them.
    id: str = ""


@dataclass
class Occupancy:
    mouth: Mouth = "desk"
    # Which fabric node currently holds me@ (empty = this glass / unknown).
    via: str = ""
    face: FaceLock = field(default_factory=FaceLock)
    remote_lab: RemoteLabSitting = field(default_factory=RemoteLabSitting)

    def take_mouth(self, who: Mouth, *, via: str = "") -> None:
        self.mouth = who
        if who == "desk":
            self.via = ""
        elif via:
            self.via = via

    def lock_face(self) -> None:
        self.face.locked = True

    def unlock_face(self) -> None:
        self.face.locked = False

    def note_face_unlock_fail(self) -> None:
        """Increment Face unlock failures — never the daemon elevation counter."""
        self.face.fail_count += 1

    def face_accepts_input(self, text: str) -> bool:
        """False when locked or mouth != desk, except the lock/unlock verbs."""
        if self.face.locked or self.mouth != "desk":
            return _face_verb(text) in LOCK_VERBS
        return True

    def allowed_face_verbs(self) -> frozenset[str]:
        """``{'lock','unlock'}`` when occupied by me@ or locked; empty = all."""
        if self.face.locked or self.mouth != "desk":
            return LOCK_VERBS
        return frozenset()

    def open_remote_lab(
        self, *, now: float, source: str = "face", device: str = "",
        tier: str = SITTING_TIER_DOOR,
    ) -> None:
        """Open sitting. Face skips /xsu; phone mints only after /xsu."""
        self.remote_lab.open = True
        self.remote_lab.locked = False
        self.remote_lab.opened_at = now
        self.remote_lab.last_agent_at = now
        src = (source or "").strip().lower()
        self.remote_lab.source = src if src in ("face", "phone") else "face"
        self.remote_lab.device = (device or "").strip()
        t = (tier or SITTING_TIER_DOOR).strip().lower()
        self.remote_lab.tier = t if t in _SITTING_TIERS else SITTING_TIER_DOOR
        self.remote_lab.id = secrets.token_hex(8)

    def lock_remote_lab(self) -> None:
        self.remote_lab.locked = True

    def drop_remote_lab(self) -> None:
        self.remote_lab.open = False
        self.remote_lab.locked = False
        self.remote_lab.source = ""
        self.remote_lab.device = ""
        self.remote_lab.tier = SITTING_TIER_DOOR
        self.remote_lab.id = ""

    def remote_lab_allows_dollar(self, *, now: float) -> bool:
        """True only if open, unlocked, and idle < idle_s."""
        rl = self.remote_lab
        if not rl.open or rl.locked:
            return False
        ref = rl.last_agent_at if rl.last_agent_at > rl.opened_at else rl.opened_at
        if now - ref >= rl.idle_s:
            return False
        return True

    def record_remote_lab_agent(self, *, now: float) -> None:
        """Reset remote-lab idle clock after agent or ``$`` work."""
        if self.remote_lab.open:
            self.remote_lab.last_agent_at = now

    def tick(self, *, now: float) -> None:
        """Autolock remote-lab when idle. Does not change mouth."""
        rl = self.remote_lab
        if not rl.open or rl.locked:
            return
        ref = rl.last_agent_at if rl.last_agent_at > rl.opened_at else rl.opened_at
        if now - ref >= rl.idle_s:
            rl.locked = True


def _face_toml_path() -> Path:
    override = os.environ.get("XLII_FACE_TOML")
    if override:
        return Path(override)
    from xlii.config import global_config_dir

    return global_config_dir() / "face.toml"


def _read_glass_section(path: Path | None = None) -> dict:
    defaults = dict(_DEFAULT_GLASS)
    cfg_path = path or _face_toml_path()
    if not cfg_path.is_file():
        return defaults
    try:
        raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return defaults
    glass = raw.get("glass")
    if not isinstance(glass, dict):
        return defaults
    level = glass.get("level", defaults["level"])
    if level not in ("home", "cafe", "black"):
        level = defaults["level"]
    out = {
        "level": level,
        "black": bool(glass.get("black", defaults["black"])),
        "idle_s": glass.get("idle_s", defaults["idle_s"]),
        "remote_lab_idle_s": glass.get(
            "remote_lab_idle_s", defaults["remote_lab_idle_s"]
        ),
    }
    try:
        out["idle_s"] = float(out["idle_s"])
    except (TypeError, ValueError):
        out["idle_s"] = defaults["idle_s"]
    try:
        out["remote_lab_idle_s"] = float(out["remote_lab_idle_s"])
    except (TypeError, ValueError):
        out["remote_lab_idle_s"] = defaults["remote_lab_idle_s"]
    return out


def load_glass_config(path: Path | None = None) -> FaceLock:
    """Load ``[glass]`` from face.toml — never writes the file."""
    glass = _read_glass_section(path)
    return FaceLock(
        locked=False,
        level=glass["level"],
        black=glass["black"],
        fail_count=0,
    )


def default_occupancy() -> Occupancy:
    glass = _read_glass_section()
    occ = Occupancy(face=load_glass_config())
    occ.remote_lab.idle_s = glass["remote_lab_idle_s"]
    return occ


def _face_verb(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("/"):
        parts = stripped[1:].split()
        return parts[0].lower() if parts else None
    if stripped.split()[0].lower() in LOCK_VERBS:
        return stripped.split()[0].lower()
    return None
