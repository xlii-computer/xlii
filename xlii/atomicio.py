"""Atomic file writes: write a temp sibling, fsync, then os.replace.

A crash (or kill -9) mid-write must never leave a half-written file the reader
chokes on. `os.replace` is atomic on POSIX, and the fsync makes the bytes
durable before the rename — important for state xlii reads back, especially the
daemon's OMEMO crypto ledger, where a truncated file can drop you to an
unrecoverable session (ROADMAP principle 3).

Dependency-free on purpose so it's unit-testable without the [daemon] extra.
"""

from __future__ import annotations

import itertools
import os
from pathlib import Path

# Per-write unique suffix so two concurrent writers to the same path never share
# one temp file (which would let a failing writer's `tmp.unlink()` erase the other
# writer's in-flight temp, or interleave into a corrupt intermediate).
_tmp_counter = itertools.count()


def _tmp_sibling(path: Path) -> Path:
    return path.with_name(f".{path.name}.{os.getpid()}.{next(_tmp_counter)}.tmp")


def write_text_atomic(path, text: str, *, mode: int = 0o600, encoding: str = "utf-8") -> None:
    """Atomically write `text` to `path` (0600 by default).

    Writes to a unique `<path>` sibling, flushes + fsyncs it, then renames over
    `path`. On any failure the original `path` is left untouched and the temp file
    is removed.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_sibling(path)
    try:
        with open(tmp, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            # The replace already failed; a leftover temp file must not mask that error.
            pass
        raise


def write_bytes_atomic(path, data: bytes, *, mode: int = 0o600) -> None:
    """Atomically write ``data`` to ``path`` and set ``mode`` on the result."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_sibling(path)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except OSError:
            # The replace already failed; a leftover temp file must not mask that error.
            pass
        raise
