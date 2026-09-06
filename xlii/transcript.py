"""Per-turn conversation persistence for `xlii chat`.

Each turn is written as a single markdown file under `<project_root>/turns/`,
named with a sortable timestamp. The file contains the user's message and the
assistant's final reply (no tool-call noise — that's investigation detail, not
memory worth preserving). Files sync to the persona's Collection like any
other project file, becoming searchable via `search_project`.

The "last N turns" loaded as inline history at chat-start come from these
same files — read in order, parse out the user/assistant blocks, prepend after
the system prompt so the conversation feels continuous across sessions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Each turn renders as two markdown blocks. The block headers are stable so
# we can parse them back at load-time. Don't change without a migration.
_USER_HEADER = "## user"
_ASSISTANT_HEADER = "## assistant"
_BLOCK_RX = re.compile(
    rf"^{re.escape(_USER_HEADER)}\s*$\n(.+?)\n^{re.escape(_ASSISTANT_HEADER)}\s*$\n(.+)\Z",
    re.DOTALL | re.MULTILINE,
)


# A mark bullet may carry a positional recall window: `- name (window: N)`
# (RP6). N adjacent *preceding* turns + the marked turn travel together as one
# idea-unit. The name capture is non-greedy so the suffix is stripped cleanly.
_WINDOW_RX = re.compile(r"^(?P<name>.*?)\s*\(window:\s*(?P<n>\d+)\)\s*$")


def _parse_marks_full(lines: list[str]) -> list[tuple[str, int]]:
    """Pull `(name, window)` for each mark in a turn file's optional `## marks`
    section. A bare `- name` parses to window 0 (just the turn — the default).

        ## marks
        - foo
        - bar (window: 4)
    """
    out: list[tuple[str, int]] = []
    in_marks = False
    for line in lines:
        s = line.strip()
        if s.lower() == "## marks":
            in_marks = True
            continue
        if in_marks:
            if s.startswith("## "):  # next section ends the marks block
                break
            if s.startswith("- "):
                body = s[2:].strip()
                m = _WINDOW_RX.match(body)
                if m:
                    out.append((m.group("name").strip(), int(m.group("n"))))
                else:
                    out.append((body, 0))
    return out


def _parse_marks(lines: list[str]) -> list[str]:
    """The mark names only (back-compat shape for list_marks / history load)."""
    return [name for name, _ in _parse_marks_full(lines)]


def _recover_timestamp(text: str, fallback: str) -> str:
    """Pull the timestamp back out of the `# turn — <ts>` first line,
    falling back (typically to the file stem) when it isn't present."""
    first = text.splitlines()[0] if text else ""
    return first.split("—", 1)[1].strip() if "—" in first else fallback


def _warn_unparsable(name: str) -> None:
    """Log a turn file that doesn't parse as a turn — once, in one place."""
    from loguru import logger

    logger.warning(
        f"persona memory: {name} does not parse as a turn file — "
        "skipped from inline context (still searchable via sync); "
        "fix or delete it"
    )


@dataclass
class Turn:
    timestamp: str  # ISO-ish, sortable
    user: str
    assistant: str
    marks: list[str] = field(default_factory=list)
    # RP6: per-mark recall window (name → N preceding turns). Names absent here
    # default to 0 (just the turn). Carried alongside `marks` so re-marking a
    # turn preserves the windows of marks already on it.
    mark_windows: dict[str, int] = field(default_factory=dict)
    # Fabric body this turn was said on. Set from the filename at load
    # (``TS-<node>-NNNN.md``). None = written on this box. Not persisted in
    # the markdown — the name is the provenance.
    source: Optional[str] = None

    def to_markdown(self) -> str:
        header = f"# turn — {self.timestamp}\n\n"
        if self.marks:
            def _bullet(m: str) -> str:
                w = self.mark_windows.get(m, 0)
                return f"- {m} (window: {w})" if w else f"- {m}"
            marks_block = "\n".join(_bullet(m) for m in self.marks)
            header += f"## marks\n{marks_block}\n\n"
        header += (
            f"{_USER_HEADER}\n{self.user}\n\n"
            f"{_ASSISTANT_HEADER}\n{self.assistant}\n"
        )
        return header

    def as_history_pair(self) -> list[dict]:
        """Two history entries (user + assistant) in the chat-completions shape."""
        user = self.user
        if self.source:
            from xlii.fabric import via_tag

            tag = via_tag(self.source)
            if tag:
                user = f"{tag}\n{user}"
        return [
            {"role": "user", "content": user},
            {"role": "assistant", "content": self.assistant},
        ]


def _attach_source(turn: "Turn", filename: str) -> "Turn":
    """Stamp ``turn.source`` from a pulled filename. Local writes stay None."""
    from xlii.fabric import source_from_name

    turn.source = source_from_name(filename)
    return turn


def parse_turn_markdown(text: str, *, fallback_ts: str = "") -> Optional["Turn"]:
    """Parse one turn file's markdown into a :class:`Turn`, or ``None`` if it doesn't parse.

    The single home for "turn text → Turn" (the VFS-backed transcript pane reads bytes through
    ``conv://`` and parses here, exactly as :func:`load_recent_turns` parses files)."""
    m = _BLOCK_RX.search(text)
    if not m:
        return None
    pairs = _parse_marks_full(text.splitlines())
    return Turn(
        timestamp=_recover_timestamp(text, fallback_ts),
        user=m.group(1).strip(),
        assistant=m.group(2).strip(),
        marks=[n for n, _ in pairs],
        mark_windows={n: w for n, w in pairs if w},
    )


def turn_filename(ts: datetime, n: int) -> str:
    """Sortable + readable: 20260429T123456Z-042.md."""
    return ts.strftime("%Y%m%dT%H%M%SZ") + f"-{n:04d}.md"


def write_turn(turns_dir: Path, user: str, assistant: str, marks: list[str] | None = None) -> Path:
    """Append a new turn file. Returns the file path so the caller can mark
    it dirty for sync. Numbering is "next free slot" — counts existing files.

    Creation is exclusive (`open(…, "x")`) with a bump-and-retry: two concurrent
    writers on the same dir (e.g. an `ask --session` double-send from the phone)
    count the same slot in the same second and would otherwise silently
    overwrite each other's turn."""
    turns_dir.mkdir(parents=True, exist_ok=True)
    n = sum(1 for _ in turns_dir.glob("*.md")) + 1
    ts = datetime.now(timezone.utc)
    iso = ts.strftime("%Y-%m-%d %H:%M:%S UTC")
    turn = Turn(timestamp=iso, user=user.strip(), assistant=assistant.strip(), marks=marks or [])
    while True:
        path = turns_dir / turn_filename(ts, n)
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(turn.to_markdown())
            return path
        except FileExistsError:
            n += 1


def mark_last_turn(turns_dir: Path, mark_name: str, window: int = 0) -> bool:
    """Add a mark to the most recent turn file (if any), with an optional recall
    `window` (N preceding turns travel with it). Rewrites the file, preserving
    any marks/windows already on the turn. Returns True on success."""
    if not turns_dir.exists():
        return False
    files = sorted(turns_dir.glob("*.md"))
    if not files:
        return False
    latest = files[-1]
    try:
        text = latest.read_text()
    except OSError:
        return False

    # Parse existing turn (reuse load logic but for single file)
    m = _BLOCK_RX.search(text)
    if not m:
        return False

    ts = _recover_timestamp(text, latest.stem)
    pairs = _parse_marks_full(text.splitlines())
    marks = [n for n, _ in pairs]
    windows = {n: w for n, w in pairs if w}

    if mark_name in marks:
        # Idempotent on the name; a new positive window updates the stored span.
        if window and windows.get(mark_name) != window:
            windows[mark_name] = window
            latest.write_text(Turn(timestamp=ts, user=m.group(1).strip(),
                                   assistant=m.group(2).strip(), marks=marks,
                                   mark_windows=windows).to_markdown())
        return True

    marks.append(mark_name)
    if window:
        windows[mark_name] = window

    # Rebuild the turn and rewrite
    turn = Turn(timestamp=ts, user=m.group(1).strip(), assistant=m.group(2).strip(),
                marks=marks, mark_windows=windows)
    latest.write_text(turn.to_markdown())
    return True


def get_marked_turn(turns_dir: Path, mark_name: str) -> Optional[Turn]:
    """Return the Turn that has the given mark_name, or None if not found.
    Scans turn files (newest first) for efficiency on typical usage.
    Phase 2 point retrieval primitive.
    """
    if not turns_dir or not turns_dir.exists():
        return None
    files = sorted(turns_dir.glob("*.md"), reverse=True)  # newest first
    for f in files:
        try:
            text = f.read_text()
        except OSError:
            continue
        pairs = _parse_marks_full(text.splitlines())
        marks = [n for n, _ in pairs]
        if mark_name not in marks:
            continue

        m = _BLOCK_RX.search(text)
        if not m:
            _warn_unparsable(f.name)
            continue

        return _attach_source(Turn(
            timestamp=_recover_timestamp(text, f.stem),
            user=m.group(1).strip(),
            assistant=m.group(2).strip(),
            marks=marks,
            mark_windows={n: w for n, w in pairs if w},
        ), f.name)
    return None


def get_marked_span(turns_dir: Path, mark_name: str, *,
                    window_override: Optional[int] = None) -> Optional[list[Turn]]:
    """Return the marked turn plus its recall window — N *preceding* turns + the
    marked turn, in chronological order (RP6). N is `window_override` if given,
    else the window stored on the mark, else 0 (just the marked turn). Returns
    None if no turn carries `mark_name`. Lazy: gathers adjacent turn files at
    recall time (reflects live edits; no snapshot)."""
    if not turns_dir or not turns_dir.exists():
        return None
    files = sorted(turns_dir.glob("*.md"))  # chronological
    for idx in range(len(files) - 1, -1, -1):  # newest-first scan for the mark
        f = files[idx]
        try:
            text = f.read_text()
        except OSError:
            continue
        pairs = _parse_marks_full(text.splitlines())
        names = [n for n, _ in pairs]
        if mark_name not in names:
            continue
        stored = next((w for n, w in pairs if n == mark_name), 0)
        window = window_override if window_override is not None else stored
        window = max(0, window)
        start = max(0, idx - window)
        span: list[Turn] = []
        for g in files[start:idx + 1]:
            try:
                gtext = g.read_text()
            except OSError:
                continue
            gm = _BLOCK_RX.search(gtext)
            if not gm:
                _warn_unparsable(g.name)
                continue
            gpairs = _parse_marks_full(gtext.splitlines())
            span.append(_attach_source(Turn(
                timestamp=_recover_timestamp(gtext, g.stem),
                user=gm.group(1).strip(),
                assistant=gm.group(2).strip(),
                marks=[n for n, _ in gpairs],
                mark_windows={n: w for n, w in gpairs if w},
            ), g.name))
        return span or None
    return None


def load_recent_turns(turns_dir: Path, limit: int) -> list[Turn]:
    """Read the most recent `limit` turns from disk, in chronological order."""
    if not turns_dir.exists():
        return []
    files = sorted(turns_dir.glob("*.md"))
    if limit > 0:
        files = files[-limit:]
    out: list[Turn] = []
    for f in files:
        try:
            text = f.read_text()
        except OSError:
            continue
        turn = parse_turn_markdown(text, fallback_ts=f.stem)
        if turn is None:
            _warn_unparsable(f.name)
            continue
        out.append(_attach_source(turn, f.name))
    return out


def turns_to_history(turns: list[Turn]) -> list[dict]:
    """Flatten a list of turns into chat-completions history entries."""
    history: list[dict] = []
    for t in turns:
        history.extend(t.as_history_pair())
    return history


def count_turns(turns_dir: Path) -> int:
    return sum(1 for _ in turns_dir.glob("*.md")) if turns_dir.exists() else 0


def clear_turns(turns_dir: Path) -> int:
    """Delete every turn file. Returns count removed. Caller should also
    sync afterward to propagate the deletes to the Collection."""
    if not turns_dir.exists():
        return 0
    n = 0
    for f in turns_dir.glob("*.md"):
        try:
            f.unlink()
            n += 1
        except OSError:
            continue
    return n


def list_marks(turns_dir: Path) -> list[tuple[str, str]]:
    """Return (mark_name, turn_timestamp) for every mark across all turn files,
    newest turn first. Companion to mark_last_turn / get_marked_turn."""
    out: list[tuple[str, str]] = []
    if not turns_dir or not turns_dir.exists():
        return out
    for f in sorted(turns_dir.glob("*.md"), reverse=True):
        try:
            text = f.read_text()
        except OSError:
            continue
        ts = _recover_timestamp(text, f.stem)
        for mark in _parse_marks(text.splitlines()):
            out.append((mark, ts))
    return out


def get_last_turn_content(turns_dir: Path) -> Optional[dict]:
    """Return {'user': ..., 'assistant': ...} from the newest turn file, or None."""
    if not turns_dir or not turns_dir.exists():
        return None
    files = sorted(turns_dir.glob("*.md"), reverse=True)
    if not files:
        return None
    try:
        text = files[0].read_text()
    except OSError:
        return None
    m = _BLOCK_RX.search(text)
    if not m:
        return None
    return {
        "user": m.group(1).strip(),
        "assistant": m.group(2).strip(),
    }
