"""Shared helpers for the input-line history panel (Track F).

Reads the same ``.xlii/repl_history`` file the inline REPL and TUI share
(prompt_toolkit ``FileHistory`` format). Newest-first for visual browse.
"""

from __future__ import annotations

from pathlib import Path


def load_repl_history_strings(xli_dir: Path | str) -> list[str]:
    """Return submitted lines, newest first. Empty when unreadable."""
    try:
        from prompt_toolkit.history import FileHistory

        path = Path(xli_dir) / "repl_history"
        if not path.exists():
            return []
        hist = FileHistory(str(path))
        return list(hist.load_history_strings())
    except Exception:
        return []


def clear_repl_history(xli_dir: Path | str) -> bool:
    """Wipe ``.xlii/repl_history``. True when the file is gone afterwards.

    Does not touch working talk (``/reset``), journal, or wiki. Face ↑/↓ is a
    separate localStorage pile — the surface that runs this also clears that.
    """
    path = Path(xli_dir) / "repl_history"
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return not path.exists()
    return not path.exists()


def history_line_kind(line: str) -> str:
    """Classify a stored input line: slash / ask / shell / text."""
    s = (line or "").lstrip()
    if s.startswith("/"):
        return "slash"
    if s.startswith("?"):
        return "ask"
    if s.startswith("!"):
        return "shell"
    return "text"


def abbreviate_history_line(line: str, *, max_chars: int = 72) -> str:
    """One-line preview for the panel list."""
    text = (line or "").replace("\n", " ").strip()
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def format_history_row(line: str, *, max_chars: int = 72) -> str:
    kind = history_line_kind(line)
    body = abbreviate_history_line(line, max_chars=max_chars)
    return f"[{kind}] {body}"
