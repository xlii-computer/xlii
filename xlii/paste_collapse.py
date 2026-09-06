"""Collapse large clipboard pastes so they don't flood the terminal UI.

When a user pastes a big traceback / log / code dump into the TUI or inline
REPL, inserting every line into the editable buffer pushes prior context
(assistant answers, status) off-screen. Instead we:

1. Stash the full paste under a small integer id.
2. Insert a one-line placeholder: ``(pasted N lines · #id)``.
3. On submit, expand placeholders back to the full text for the agent.
4. Echo the placeholder (or a display summary) in the transcript.

Thresholds are env-overridable; small pastes pass through unchanged.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


# Visible placeholder — keep the shape stable; the id disambiguates multi-paste.
# Example: (pasted 847 lines · #3)
PLACEHOLDER_RE = re.compile(r"\(pasted (\d+) lines · #(\d+)\)")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


def line_threshold() -> int:
    """Collapse pastes with at least this many lines (default 8)."""
    return _env_int("XLII_PASTE_COLLAPSE_LINES", 8)


def char_threshold() -> int:
    """Collapse pastes with at least this many characters (default 600)."""
    return _env_int("XLII_PASTE_COLLAPSE_CHARS", 600)


def line_count(text: str) -> int:
    if not text:
        return 0
    # Trailing newline does not add an extra empty "line" for the count users expect.
    if text.endswith("\n"):
        return text.count("\n") or 1
    return text.count("\n") + 1


def should_collapse(text: str) -> bool:
    """True when a paste is large enough to replace with a placeholder."""
    if not text:
        return False
    return line_count(text) >= line_threshold() or len(text) >= char_threshold()


def make_placeholder(seq: int, text: str) -> str:
    return f"(pasted {line_count(text)} lines · #{seq})"


def expand_placeholders(text: str, store: dict[int, str]) -> str:
    """Replace ``(pasted N lines · #id)`` with stashed bodies. Unknown ids stay as-is."""

    def repl(m: re.Match[str]) -> str:
        seq = int(m.group(2))
        return store.get(seq, m.group(0))

    return PLACEHOLDER_RE.sub(repl, text)


def collapse_for_display(text: str) -> str:
    """Summarize a huge raw string for transcript/UI when no placeholder is present.

    If the text already contains our placeholders, leave it alone (caller already
    collapsed at paste time). Used as a safety net for non-paste large submits.
    """
    if not text or PLACEHOLDER_RE.search(text):
        return text
    if not should_collapse(text):
        return text
    n = line_count(text)
    kb = max(1, len(text) // 1024)
    return f"(pasted {n} lines · ~{kb}KB)"


@dataclass
class PasteStore:
    """Per-input stash of collapsed pastes (id → full text)."""

    _items: dict[int, str] = field(default_factory=dict)
    _next: int = 1

    def stash(self, text: str) -> str:
        """Store ``text`` and return the placeholder to insert into the buffer."""
        seq = self._next
        self._next = seq + 1
        self._items[seq] = text
        return make_placeholder(seq, text)

    def expand(self, text: str) -> str:
        return expand_placeholders(text, self._items)

    def clear(self) -> None:
        self._items.clear()

    def maybe_collapse_insert(self, text: str) -> str:
        """Return placeholder if large, else the original text (not stashed)."""
        if should_collapse(text):
            return self.stash(text)
        return text


def normalize_paste(text: str) -> str:
    """Normalize common clipboard line endings to ``\\n``."""
    if "\r\n" in text:
        return text.replace("\r\n", "\n")
    if "\r" in text:
        return text.replace("\r", "\n")
    return text


def attach_prompt_toolkit_paste_collapse(session: object, store: PasteStore | None = None) -> PasteStore:
    """Install a bracketed-paste filter on a ``PromptSession`` (inline REPL).

    Returns the ``PasteStore`` used (create one if not provided). Call
    ``store.expand(line)`` on each submitted line before handing it to the agent.
    """
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.key_binding.key_bindings import merge_key_bindings
    from prompt_toolkit.keys import Keys

    store = store if store is not None else PasteStore()
    kb = KeyBindings()

    @kb.add(Keys.BracketedPaste, eager=True)
    def _collapsed_paste(event) -> None:  # type: ignore[no-untyped-def]
        data = event.data
        if not isinstance(data, str):
            data = str(data or "")
        data = normalize_paste(data)
        insert = store.maybe_collapse_insert(data)
        event.current_buffer.insert_text(insert)

    # Prepend so we win over prompt_toolkit's default bracketed-paste handler.
    existing = getattr(session, "key_bindings", None)
    if existing is not None:
        session.key_bindings = merge_key_bindings([kb, existing])  # type: ignore[attr-defined]
    else:
        session.key_bindings = kb  # type: ignore[attr-defined]
    session._xlii_paste_store = store  # type: ignore[attr-defined]
    return store
