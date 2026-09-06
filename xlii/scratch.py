"""Spill large tool output to disk — dynamic context (cursor-workflows.md A2).

When a tool's output exceeds the configured threshold, the FULL output is written
under ``<project>/.xlii/scratch/tool-output/`` and the tool returns a head+tail
preview plus the project-relative path. The agent reads a slice with
``read_file(path=..., offset=N, limit=M)`` instead of paying the whole payload's
tokens every turn — and nothing is lost (the old behavior truncated the middle
irrecoverably).

The directory lives under ``.xlii/`` so it is ignored everywhere (sync, search,
``/browse``). It is distinct from the ephemeral-project scratch under
``~/.xlii/scratch/<name>/`` (that's a home-dir project store, not tool output).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from xlii.atomicio import write_text_atomic

# Cap files kept in the spill dir — a cheap bound so long sessions don't grow it
# without limit. A full GC tie-in (`xlii gc`) is deferred (proposal open question).
_KEEP = 200


def spill_dir(project_root: Path) -> Path:
    return Path(project_root) / ".xlii" / "scratch" / "tool-output"


def _prune(d: Path, keep: int = _KEEP, *, protect: Path | None = None) -> None:
    try:
        files = sorted(d.glob("*.txt"), key=lambda p: p.stat().st_mtime)
    except OSError:
        return
    # Never prune the file we just wrote — on coarse-mtime filesystems a burst of
    # spills can tie on st_mtime, and we must not delete the path we just returned.
    candidates = [f for f in files if f != protect]
    for old in candidates[: max(0, len(files) - keep)]:
        try:
            old.unlink()
        except OSError:
            # A scratch file that won't delete is skipped; pruning is opportunistic.
            pass


def write_spill(project_root: Path, text: str, *, seq: int = 0, token: str | None = None) -> str:
    """Write `text` to the spill dir; return its PROJECT-RELATIVE posix path.

    Raises (OSError) if the tree is not writable — callers fall back to inline
    truncation so a read-only project never breaks a turn.
    """
    root = Path(project_root)
    d = spill_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    name = f"{seq:03d}-{token or uuid.uuid4().hex[:8]}.txt"
    path = d / name
    write_text_atomic(path, text, mode=0o644)
    _prune(d, protect=path)  # cap the dir, but never delete what we just wrote
    return path.relative_to(root).as_posix()
