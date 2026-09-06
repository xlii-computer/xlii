"""Local file manifest: relpath -> sha256/mtime/file_id mapping."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from xlii.atomicio import write_text_atomic


@dataclass
class FileEntry:
    sha256: str
    size: int
    mtime: float
    file_id: Optional[str] = None
    last_synced: Optional[float] = None


@dataclass
class Manifest:
    path: Path
    entries: dict[str, FileEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not path.exists():
            return cls(path=path)
        data = json.loads(path.read_text())
        entries = {k: FileEntry(**v) for k, v in data.get("entries", {}).items()}
        return cls(path=path, entries=entries)

    def save(self) -> None:
        write_text_atomic(
            self.path,
            json.dumps(
                {"entries": {k: asdict(v) for k, v in self.entries.items()}},
                indent=2,
                sort_keys=True,
            ),
            mode=0o644,
        )

    def get(self, relpath: str) -> Optional[FileEntry]:
        return self.entries.get(relpath)

    def set(self, relpath: str, entry: FileEntry) -> None:
        self.entries[relpath] = entry

    def remove(self, relpath: str) -> None:
        self.entries.pop(relpath, None)


def hash_file(path: Path, chunk: int = 65536) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()
