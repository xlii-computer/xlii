"""Mojo-keeper K5 — per-turn bearings: body · surface · desk · reach · delta.

A pure fact sheet prepended to conversational ambient. Every source is guarded;
``compute`` / ``render`` never raise into the turn. The only new write is the
``.last_turn.json`` sidecar next to the persona turn store (per-body; ignored
by sync).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

LAST_TURN_FILENAME = ".last_turn.json"

SURFACE_REPL = "repl"
SURFACE_DESK_FACE = "desk face"
SURFACE_PHONE_GLASS = "phone glass"
SURFACE_XMPP = "xmpp door"

_CHANGED_ORDER = ("body", "surface", "desk")


@dataclass
class Bearings:
    """One turn's orientation. Every field is already a display string."""

    body: str = "unknown"
    surface: str = "unknown"
    desk: str = "unknown"
    reach: str = "unknown"
    hire: str = "unknown"
    memory: str = "unknown"
    last_turn: str = "unknown"
    changed: tuple[str, ...] = ()
    confirm: str = ""
    # ids used for the sidecar / CHANGED compare (not shown as their own lines)
    body_id: str = ""
    surface_id: str = ""
    desk_id: Optional[str] = None


def face_surface(server: Any) -> str:
    """Desk face vs phone glass, from the live Face server. Never raises."""
    try:
        fn = getattr(server, "_phone_glass", None)
        if callable(fn) and fn():
            return SURFACE_PHONE_GLASS
    except Exception:
        pass
    return SURFACE_DESK_FACE


def sidecar_path(turns_dir: Any) -> Optional[Path]:
    if turns_dir is None:
        return None
    try:
        return Path(turns_dir) / LAST_TURN_FILENAME
    except (TypeError, ValueError):
        return None


def turns_dir_of(persona_project: Any) -> Optional[Path]:
    """Persona turn store: ``Persona.turns_dir`` or ``<project_root>/turns``."""
    if persona_project is None:
        return None
    try:
        td = getattr(persona_project, "turns_dir", None)
        if td is not None:
            return Path(td)
        root = getattr(persona_project, "project_root", None)
        if root is not None:
            return Path(root) / "turns"
        xli = getattr(persona_project, "xli_dir", None)
        if xli is not None:
            return Path(xli) / "turns"
    except (TypeError, ValueError):
        return None
    return None


def write_last_turn_sidecar(
    turns_dir: Any,
    *,
    body: str,
    surface: str,
    desk: Optional[str],
    at: Optional[str] = None,
) -> None:
    """Overwrite ``<turns>/.last_turn.json``. Best-effort; never raises."""
    path = sidecar_path(turns_dir)
    if path is None:
        return
    try:
        stamp = at or _now_iso()
        payload = {
            "at": stamp,
            "body": body or "",
            "surface": surface or "",
            "desk": desk,
        }
        from xlii.atomicio import write_text_atomic

        write_text_atomic(path, json.dumps(payload, separators=(",", ":")))
    except Exception:
        return


def read_last_turn_sidecar(turns_dir: Any) -> Optional[dict[str, Any]]:
    """Sidecar dict, or None when missing / unreadable. Never raises."""
    path = sidecar_path(turns_dir)
    if path is None:
        return None
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def stamp_last_turn(
    turns_dir: Any,
    *,
    cfg: Any = None,
    surface: str = SURFACE_REPL,
    desk: Any = None,
) -> None:
    """Persist-path helper: body/surface/desk as the sidecar knows them."""
    try:
        body_id, _ann = _body_ids(cfg)
        desk_id = _desk_id(desk)
        write_last_turn_sidecar(
            turns_dir, body=body_id, surface=surface or SURFACE_REPL, desk=desk_id,
        )
    except Exception:
        return


def compute(
    state: Any,
    *,
    surface: str = SURFACE_REPL,
    cfg: Any = None,
    persona_project: Any = None,
) -> Bearings:
    """Live bearings. Every source is independently guarded."""
    if cfg is None:
        cfg = getattr(state, "cfg", None)
    b = Bearings()
    b.surface_id = (surface or SURFACE_REPL).strip() or SURFACE_REPL
    b.surface = _safe(lambda: b.surface_id)
    b.body_id, body_line = _safe_pair(lambda: _body_ids(cfg), ("unknown", "unknown"))
    b.body = body_line
    desk_line, desk_id = _safe_pair(lambda: _desk_ids(state), ("unknown", None))
    b.desk = desk_line
    b.desk_id = desk_id
    b.reach = _safe(lambda: _reach_line(cfg, persona_project, is_throne=_is_throne(cfg)))
    b.hire = _safe(lambda: _hire_line(state, desk_id))
    b.memory = _safe(lambda: _memory_line(state))
    last_line, changed = _safe_pair(
        lambda: _last_turn_delta(
            persona_project,
            body_id=b.body_id,
            surface_id=b.surface_id,
            desk_id=b.desk_id,
        ),
        ("unknown", ()),
    )
    b.last_turn = last_line
    b.changed = tuple(c for c in changed if c in _CHANGED_ORDER)
    if b.surface_id in (SURFACE_PHONE_GLASS, SURFACE_XMPP):
        b.confirm = "desk modal"
    return b


def render(b: Bearings) -> str:
    """``[bearings]``-fenced paragraph. Never raises."""
    try:
        lines = [
            "[bearings]",
            f"body: {b.body}",
            f"surface: {b.surface}",
            f"desk: {b.desk}",
            f"reach: {b.reach}",
            f"hire: {b.hire}",
            f"memory: {b.memory}",
            f"last turn: {b.last_turn}",
        ]
        if b.changed:
            lines.append("CHANGED since last turn: " + ", ".join(b.changed))
        if b.confirm:
            lines.append(f"confirm: {b.confirm}")
        lines.append("[/bearings]")
        return "\n".join(lines) + "\n"
    except Exception:
        return "[bearings]\nunknown\n[/bearings]\n"


def bearings_block(
    state: Any,
    *,
    surface: str = SURFACE_REPL,
    cfg: Any = None,
    persona_project: Any = None,
) -> str:
    """``render(compute(...))`` with a last-ditch guard around the whole block."""
    try:
        return render(compute(state, surface=surface, cfg=cfg, persona_project=persona_project))
    except Exception:
        return "[bearings]\nunknown\n[/bearings]\n"


# --------------------------------------------------------------------------- #
#  internals — each helper is allowed to raise; callers wrap with _safe
# --------------------------------------------------------------------------- #


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe(fn, fallback: str = "unknown") -> str:
    try:
        val = fn()
        return fallback if val is None else str(val)
    except Exception:
        return fallback


def _safe_pair(fn, fallback):
    try:
        return fn()
    except Exception:
        return fallback


def _is_throne(cfg: Any) -> bool:
    return bool((getattr(cfg, "management_api_key", None) or "").strip())


def _body_ids(cfg: Any) -> tuple[str, str]:
    node = ""
    try:
        from xlii.farm import jobs_cfg

        node = str((jobs_cfg(cfg).get("node") or "") or "").strip()
    except Exception:
        node = ""
    if not node:
        node = str(getattr(cfg, "node_name", "") or "").strip()
    throne = _is_throne(cfg)
    if throne:
        body_id = node or "throne"
        return body_id, f"{body_id} (this is the throne)"
    body_id = node or "limb"
    return body_id, f"{body_id} (limb; throne = throne)"


def _desk_id(desk: Any) -> Optional[str]:
    if desk is None:
        return None
    try:
        from xlii.project_paths import is_home_desk_project

        if is_home_desk_project(desk):
            return None
    except Exception:
        pass
    name = (getattr(desk, "name", "") or "").strip()
    return name or None


def _desk_ids(state: Any) -> tuple[str, Optional[str]]:
    proj = getattr(state, "project", None)
    if proj is None:
        return "none (Home / scratch)", None
    from xlii.project_paths import is_home_desk_project

    if is_home_desk_project(proj):
        return "none (Home / scratch)", None
    name = (getattr(proj, "name", "") or "").strip() or "desk"
    kind = ""
    try:
        from xlii.config import project_kind

        kind = (project_kind(proj) or "").strip()
    except Exception:
        kind = ""
    root = getattr(proj, "project_root", None)
    bits = [name]
    if kind:
        bits.append(kind)
    if root:
        bits.append(str(root))
    return " · ".join(bits), name


def _hire_line(state: Any, desk_id: Optional[str]) -> str:
    hire = getattr(state, "hire", None)
    if hire not in ("read", "write", "none"):
        try:
            hire = getattr(state.agent.session, "hire", None)
        except Exception:
            hire = None
    if hire not in ("read", "write", "none"):
        hire = "read" if desk_id is None else "write"
    if hire == "none":
        return "none (memory off)"
    if hire == "write" and desk_id is not None:
        return "read+write on this desk"
    return "read only (no desk)"


def _memory_line(state: Any) -> str:
    scratch = bool(getattr(state, "scratch", False))
    no_sync = bool(getattr(state, "no_sync", False))
    journal_mute = bool(getattr(state, "journal_mute", False))
    if scratch:
        return "off (never-sync)"
    if no_sync or journal_mute:
        return "off (sync + journal muted this sitting)"
    return "on"


def _reach_line(cfg: Any, persona_project: Any, *, is_throne: bool) -> str:
    if is_throne:
        return "n/a (I am the throne)"
    from xlii.fabric import last_sync_stamp

    stamp = last_sync_stamp()
    unsynced = _unsynced_local_turns(turns_dir_of(persona_project), stamp)
    if stamp is None:
        return f"throne unreachable · last pull never · {unsynced} turns here not yet home"
    age = format_age(_now_epoch() - float(stamp))
    return f"throne ok · last pull {age} · {unsynced} unsynced turns here"


def _unsynced_local_turns(turns_dir: Optional[Path], since: Optional[float]) -> int:
    if turns_dir is None or not turns_dir.is_dir():
        return 0
    from xlii.fabric import source_from_name

    n = 0
    for p in turns_dir.glob("*.md"):
        try:
            if source_from_name(p.name) is not None:
                continue
            if since is None or p.stat().st_mtime > since:
                n += 1
        except OSError:
            continue
    return n


def _last_turn_delta(
    persona_project: Any,
    *,
    body_id: str,
    surface_id: str,
    desk_id: Optional[str],
) -> tuple[str, tuple[str, ...]]:
    side = read_last_turn_sidecar(turns_dir_of(persona_project))
    if not side:
        return "none recorded", ()
    prev_body = str(side.get("body") or "")
    prev_surface = str(side.get("surface") or "")
    prev_desk = side.get("desk")
    if prev_desk == "":
        prev_desk = None
    elif prev_desk is not None:
        prev_desk = str(prev_desk)
    age = _age_from_at(side.get("at"))
    desk_show = prev_desk if prev_desk is not None else "none"
    line = (
        f"{age} · body {prev_body or 'unknown'} · surface {prev_surface or 'unknown'} "
        f"· desk {desk_show}"
    )
    changed: list[str] = []
    if prev_body != (body_id or ""):
        changed.append("body")
    if prev_surface != (surface_id or ""):
        changed.append("surface")
    if prev_desk != desk_id:
        changed.append("desk")
    return line, tuple(changed)


def _age_from_at(raw: Any) -> str:
    epoch = _parse_at(raw)
    if epoch is None:
        return "unknown ago"
    return format_age(_now_epoch() - epoch)


def _parse_at(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        pass
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def _now_epoch() -> float:
    import time

    return time.time()


def format_age(seconds: float) -> str:
    s = int(seconds)
    if s < 0:
        s = 0
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"
