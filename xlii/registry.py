"""Global registry of XLI-initialized projects.

We need this because the xAI Collections API has no idea what's a "live"
project vs an orphaned one. Each `xlii init` records (path, collection_id, name)
here so `xli gc` can cross-reference with the cloud and offer to clean up
collections whose local project has been deleted.

Stored at ~/.config/xlii/projects.json.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

from xlii.atomicio import write_text_atomic
from xlii.config import GLOBAL_CONFIG_DIR

REGISTRY_FILE = GLOBAL_CONFIG_DIR / "projects.json"


@dataclass
class RegistryEntry:
    path: str           # absolute project root path at registration time
    collection_id: str
    name: str
    created_at: str
    # Fabric body this row is attributed to. Empty = this box (or adopted here).
    node: str = ""
    # Path on that node, when known. Informational — switch uses `path`.
    remote_path: str = ""
    # Public git remote (https://… or git@host:path). Never a password, never
    # a vault ref. Nodes may share this on the project card.
    repo: str = ""


_ENTRY_FIELDS = {f.name for f in fields(RegistryEntry)}


def _entry_from_dict(raw: dict) -> RegistryEntry:
    """Load a row. Unknown keys dropped so older/newer files both open."""
    if not isinstance(raw, dict):
        raise TypeError("registry entry must be an object")
    return RegistryEntry(**{k: v for k, v in raw.items() if k in _ENTRY_FIELDS})


@dataclass
class Registry:
    entries: list[RegistryEntry] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Registry":
        if not REGISTRY_FILE.exists():
            return cls()
        try:
            data = json.loads(REGISTRY_FILE.read_text())
            return cls(entries=[_entry_from_dict(e) for e in data.get("entries", [])])
        except (json.JSONDecodeError, TypeError, OSError) as e:
            # Corrupt registry must not take every command down. Preserve the
            # evidence, warn, start empty — projects re-register on next use.
            backup = REGISTRY_FILE.with_suffix(".json.corrupt")
            try:
                REGISTRY_FILE.replace(backup)
            except OSError:
                backup = REGISTRY_FILE
            import sys
            print(
                f"xlii: project registry was corrupt ({e}) — saved as "
                f"{backup.name}, starting with an empty registry",
                file=sys.stderr,
            )
            return cls()

    def save(self) -> None:
        # Atomic + fsync: a crash mid-write must never leave a half-written
        # registry (the prior hand-rolled tmp+replace skipped the fsync).
        write_text_atomic(
            REGISTRY_FILE,
            json.dumps(
                {"entries": [asdict(e) for e in self.entries]},
                indent=2,
                sort_keys=True,
            ),
            mode=0o600,
        )

    def find_by_path(self, path: Path | str) -> Optional[RegistryEntry]:
        p = str(Path(path).resolve())
        return next((e for e in self.entries if e.path == p), None)

    def find_by_collection(self, collection_id: str) -> Optional[RegistryEntry]:
        # Empty collection_id is the sentinel for local-only projects; matching
        # on it would collide every local-only entry into one registry slot.
        if not collection_id:
            return None
        return next((e for e in self.entries if e.collection_id == collection_id), None)

    def upsert(self, entry: RegistryEntry) -> None:
        existing = self.find_by_path(entry.path) or self.find_by_collection(
            entry.collection_id
        )
        if existing:
            self.entries.remove(existing)
        self.entries.append(entry)

    def remove(self, collection_id: str) -> bool:
        before = len(self.entries)
        self.entries = [e for e in self.entries if e.collection_id != collection_id]
        return len(self.entries) != before

    def remove_by_path(self, path: "Path | str") -> bool:
        """Drop the row for this exact path (ghosts, duplicate names)."""
        try:
            target = str(Path(path).expanduser().resolve())
        except OSError:
            target = str(path)
        before = len(self.entries)
        self.entries = [
            e for e in self.entries
            if str(Path(e.path).expanduser()) != target
            and e.path != str(path)
        ]
        # Also match unresolved stored path (dead /tmp rows).
        if len(self.entries) == before:
            self.entries = [e for e in self.entries if e.path != str(path)]
        return len(self.entries) != before

    def prune_dead(self) -> list[RegistryEntry]:
        """Drop rows whose tree no longer has a project.json. Disk is untouched."""
        from xlii.project_resolver import project_is_alive

        dead = [e for e in self.entries if not project_is_alive(e)]
        if not dead:
            return []
        drop = {id(e) for e in dead}
        self.entries = [e for e in self.entries if id(e) not in drop]
        return dead
