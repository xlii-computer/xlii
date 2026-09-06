"""One-shot legacy path migrations (.xli → .xlii, global loadouts, ignore files)."""

from __future__ import annotations

import shutil
from pathlib import Path

from xlii.config import PROJECT_DIR_NAME, ProjectConfig
from xlii.context import migrate_global_contexts
from xlii.loadout_paths import LEGACY_GLOBAL_LOADOUTS_DIR, migrate_global_loadouts


def migrate_project_state(project_root: Path, *, dry_run: bool = False) -> list[str]:
    """Move ``.xli/`` project state to ``.xlii/`` when only the legacy dir exists."""
    lines: list[str] = []
    root = project_root.resolve()
    legacy = root / ".xli"
    modern = root / PROJECT_DIR_NAME
    if not legacy.is_dir() or modern.exists():
        return lines
    if dry_run:
        lines.append(f"would move {legacy} → {modern}")
        return lines
    shutil.move(str(legacy), str(modern))
    lines.append(f"moved {legacy} → {modern}")
    return lines


def migrate_ignore_file(project_root: Path, *, dry_run: bool = False) -> list[str]:
    lines: list[str] = []
    legacy = project_root / ".xliignore"
    modern = project_root / ".xliiignore"
    if not legacy.is_file() or modern.exists():
        return lines
    if dry_run:
        lines.append(f"would rename {legacy.name} → {modern.name}")
        return lines
    legacy.rename(modern)
    lines.append(f"renamed {legacy.name} → {modern.name}")
    return lines


def migrate_all(*, project_root: Path | None = None, dry_run: bool = False) -> list[str]:
    """Run all safe legacy migrations. Returns log lines."""
    lines: list[str] = []
    lines.extend(migrate_global_loadouts(dry_run=dry_run))
    lines.extend(migrate_global_contexts(dry_run=dry_run))
    if project_root is not None:
        lines.extend(migrate_project_state(project_root, dry_run=dry_run))
        lines.extend(migrate_ignore_file(project_root, dry_run=dry_run))
    elif LEGACY_GLOBAL_LOADOUTS_DIR.is_dir():
        pass
    cwd = Path(".").resolve()
    proj = ProjectConfig.load(cwd)
    if proj is not None and project_root is None:
        lines.extend(migrate_project_state(proj.project_root, dry_run=dry_run))
        lines.extend(migrate_ignore_file(proj.project_root, dry_run=dry_run))
    return lines
