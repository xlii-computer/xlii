"""Tools → Shell tools… menu bridge (Track J) — grouped lint/format recipes, prefill only."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from xlii.xtool_catalog import (
    GROUP_LABELS,
    XToolEntry,
    entry_available,
    entries_in_group,
    format_argv,
    lookup,
    ordered_groups,
    project_fingerprints,
)


def dock_path_from_address(address: Optional[str]) -> Optional[Path]:
    if not address or not address.startswith("file://"):
        return None
    return Path(address[len("file://"):]).expanduser()


def group_menu_items(fingerprints: Iterable[str] | None = None) -> list[tuple[str, str, bool]]:
    items: list[tuple[str, str, bool]] = []
    for group in ordered_groups(fingerprints):
        if not entries_in_group(group, legacy=None):
            continue
        label = GROUP_LABELS.get(group, group)
        has_any = any(entry_available(e) for e in entries_in_group(group, legacy=None))
        items.append((f"xtgrp:{group}", f"  {label}…", has_any))
    return items


def _entry_row(entry: XToolEntry) -> tuple[str, str, bool]:
    enabled = entry_available(entry)
    mark = "  " if enabled else "  (missing) "
    return (f"xtool:{entry.id}", f"{mark}{entry.label}", enabled)


def tool_menu_items(group: str, *, legacy: bool = False) -> list[tuple[str, str, bool]]:
    return [_entry_row(e) for e in entries_in_group(group, legacy=legacy)]


def group_has_legacy(group: str) -> bool:
    return bool(entries_in_group(group, legacy=True))


def prefill_line(entry_id: str, *, dock_path: Optional[Path] = None) -> Optional[str]:
    entry = lookup(entry_id)
    if entry is None or not entry_available(entry):
        return None
    return f"!{format_argv(entry, dock_path=dock_path)}"


def fingerprints_for_state(state) -> list[str]:
    root = getattr(getattr(state, "project", None), "project_root", None)
    return project_fingerprints(Path(str(root)) if root else None)
