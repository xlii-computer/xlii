"""Most-recent desks — the Xlii menu's way back after Home.

Home is a blank slate. Recents are the last few *folders* you opened
(lab code trees and talk collections / chat islands), newest first.
Home itself is never stored.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xlii.atomicio import write_text_atomic
from xlii.config import GLOBAL_CONFIG_DIR

RECENT_FILE = GLOBAL_CONFIG_DIR / "recent_desks.json"
SHOW = 5
STORE = 12


def _kind_of(project: Any) -> str:
    """Public desk word: lab | talk."""
    from xlii.config import PROJECT_KIND_COLLECTION, project_kind, project_landing_posture

    try:
        if project_kind(project) == PROJECT_KIND_COLLECTION:
            return "talk"
        if project_landing_posture(project) == "chat":
            return "talk"
    except Exception:
        # Unreadable project config -- fall through to the default kind below.
        pass
    name = (getattr(project, "name", "") or "").strip()
    if name.startswith("chat/"):
        return "talk"
    return "lab"


def _label(name: str) -> str:
    n = (name or "").strip()
    if n.startswith("chat/"):
        return n.split("/", 1)[-1] or n
    return n


def _is_home(project: Any) -> bool:
    from xlii.project_paths import is_home_desk_project

    try:
        return bool(is_home_desk_project(project))
    except Exception:
        return False


def _skip_name(name: str) -> bool:
    n = (name or "").strip().lower()
    return (not n) or n == "scratch/home" or n.startswith("scratch/")


def _alive(path: str) -> bool:
    try:
        from xlii.project_resolver import project_is_alive
        from xlii.registry import RegistryEntry

        return bool(project_is_alive(RegistryEntry(
            path=path, collection_id="", name="", created_at="",
        )))
    except Exception:
        return Path(path).expanduser().joinpath(".xlii", "project.json").is_file()


def _load() -> list[dict[str, str]]:
    if not RECENT_FILE.exists():
        return []
    try:
        data = json.loads(RECENT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("desks") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path") or "").strip()
        name = str(raw.get("name") or "").strip()
        if not path or path in seen or _skip_name(name):
            continue
        seen.add(path)
        kind = str(raw.get("kind") or "lab").strip()
        if kind not in ("lab", "talk"):
            kind = "lab"
        out.append({"name": name, "path": path, "kind": kind})
    return out


def _save(rows: list[dict[str, str]]) -> None:
    RECENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        RECENT_FILE,
        json.dumps({"desks": rows[:STORE]}, indent=2, sort_keys=True),
        mode=0o600,
    )


def touch_desk(project: Any) -> None:
    """Record that this folder was opened. No-op for Home / unnamed scratch."""
    if project is None or _is_home(project):
        return
    name = (getattr(project, "name", "") or "").strip()
    if _skip_name(name):
        return
    root = getattr(project, "project_root", None)
    if root is None:
        return
    try:
        path = str(Path(root).expanduser().resolve())
    except OSError:
        path = str(root)
    row = {"name": name, "path": path, "kind": _kind_of(project)}
    rest = [r for r in _load() if r.get("path") != path]
    try:
        _save([row] + rest)
    except OSError:
        # The recent-desks list is a convenience; a failed write only loses this touch.
        pass


def list_recent(*, limit: int | None = None) -> list[dict[str, str]]:
    """Newest-first live desks. Dead paths are dropped from the file."""
    cap = SHOW if limit is None else int(limit)
    live: list[dict[str, str]] = []
    dropped = False
    for row in _load():
        if not _alive(row["path"]):
            dropped = True
            continue
        live.append(row)
    if dropped:
        try:
            _save(live)
        except OSError:
            # Pruning is opportunistic -- the dead rows are already filtered out of the returned list.
            pass
    out: list[dict[str, str]] = []
    for row in live[: max(1, cap)]:
        item = dict(row)
        item["label"] = _label(row["name"])
        out.append(item)
    return out
