"""Global loadout export paths — canonical location + one-time legacy migration.

A *loadout* is a saved bundle of attachments (docs, locker files, model/temp)
exportable across projects. Previously called "workspace" and stored under
``~/.xli/workspaces/``; canonical path is now ``~/.config/xlii/loadouts/``. A legacy
tree is copied into the canonical dir once on first read, then it's canonical-only.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from xlii.config import GLOBAL_CONFIG_DIR

GLOBAL_LOADOUTS_DIR = GLOBAL_CONFIG_DIR / "loadouts"
LEGACY_GLOBAL_LOADOUTS_DIR = Path.home() / ".xli" / "workspaces"

# Back-compat alias for tests and callers that still monkeypatch the old name.
GLOBAL_WORKSPACES_DIR = GLOBAL_LOADOUTS_DIR

def _auto_migrate_legacy() -> None:
    """Best-effort one-way DRAIN of legacy ``~/.xli/workspaces`` loadouts into the
    canonical dir: each legacy file is moved over (canonical wins on a name clash,
    dropping the shadow), then the emptied legacy dir is removed. Draining — rather
    than copying — is what lets a deleted loadout stay deleted (a lingering legacy
    copy would otherwise resurface on the next read). A no-op (one stat) when there
    is no legacy dir, so it's safe to call on every read."""
    src = LEGACY_GLOBAL_LOADOUTS_DIR
    if not src.is_dir():
        return
    try:
        GLOBAL_LOADOUTS_DIR.mkdir(parents=True, exist_ok=True)
        for path in src.glob("*.json"):
            target = GLOBAL_LOADOUTS_DIR / path.name
            if target.exists():
                path.unlink()                        # canonical wins — drop shadow
            else:
                shutil.move(str(path), str(target))  # absorb legacy-only name
        try:
            src.rmdir()                              # remove now-empty legacy dir
        except OSError:
            # The legacy dir isn't empty (or won't go) -- the files above were still migrated.
            pass
    except Exception:
        # Auto-migration is opportunistic: a partial or failed migration must not block resolving the loadout
        # dir.
        pass


def resolve_global_loadouts_dir(*, for_write: bool = False) -> Path:
    """Return the canonical directory for global loadout JSON files."""
    if for_write:
        GLOBAL_LOADOUTS_DIR.mkdir(parents=True, exist_ok=True)
    else:
        _auto_migrate_legacy()
    return GLOBAL_LOADOUTS_DIR


def global_loadout_read_dirs() -> list[Path]:
    """Return the global loadout directory (canonical only). Legacy loadouts are
    auto-migrated into it on read."""
    _auto_migrate_legacy()
    return [GLOBAL_LOADOUTS_DIR]


def find_global_loadout_path(name: str) -> Path | None:
    """Find a global loadout JSON by name in the canonical location."""
    for root in global_loadout_read_dirs():
        path = root / f"{name}.json"
        if path.exists():
            return path
    return None


def migrate_global_loadouts(*, dry_run: bool = False) -> list[str]:
    """Copy legacy ``~/.xli/workspaces/*.json`` into ``~/.config/xlii/loadouts/``.

    Returns human-readable log lines. Skips names that already exist in the target.
    """
    lines: list[str] = []
    src = LEGACY_GLOBAL_LOADOUTS_DIR
    if not src.is_dir():
        return lines
    dst = GLOBAL_LOADOUTS_DIR
    if not dry_run:
        dst.mkdir(parents=True, exist_ok=True)
    for path in sorted(src.glob("*.json")):
        target = dst / path.name
        if target.exists():
            lines.append(f"skip {path.name} (already in {dst})")
            continue
        if dry_run:
            lines.append(f"would migrate {path} → {target}")
        else:
            shutil.copy2(path, target)
            lines.append(f"migrated {path.name} → {dst}/")
    return lines
