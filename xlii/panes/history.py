"""``HistoryPane`` — input-line scrollback over ``history://``.

Newest-first browse of ``.xlii/repl_history`` (same file as TUI ``/history``).
Select / primary action **prefills** the full line (review-before-run) — never
executes. **Clear typed lines** wipes this file (and face ↑/↓). Not ``/reset``.
Face + TUI dock share this headless pane.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import PREFILL, Action, Outcome, Rendered, RenderedRow, Selection
from xlii.repl_history_util import (
    clear_repl_history,
    format_history_row,
    load_repl_history_strings,
)


def _xli_dir():
    from xlii.active_session import active_session

    project = getattr(active_session(), "project", None)
    return getattr(project, "xli_dir", None) if project is not None else None


class HistoryPane:
    """Headless input-history list; open action seeds the command line."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="history")
        self._lines: list[str] = []
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = Address(scheme="history")  # always root container
        self._reload()
        # Optional select: history://N or bare index
        raw = (select or "").strip()
        if "://" in raw:
            raw = raw.split("://", 1)[-1].strip()
        if raw.isdigit():
            i = int(raw)
            if 0 <= i < len(self._lines):
                self._sel = i

    def _reload(self) -> None:
        xli = _xli_dir()
        self._lines = load_repl_history_strings(xli) if xli else []
        if self._sel >= len(self._lines):
            self._sel = max(0, len(self._lines) - 1)

    def render(self) -> Rendered:
        self._reload()
        if not self._lines:
            return Rendered(
                title="history:// — input lines",
                rows=(RenderedRow(
                    text="empty — submit a line first",
                    address="history://",
                    kind="leaf",
                ),),
                empty=True,
            )
        rows = []
        for i, line in enumerate(self._lines):
            rows.append(RenderedRow(
                text=format_history_row(line),
                address=f"history://{i}",
                kind="leaf",
                selected=(i == self._sel),
            ))
        return Rendered(
            title=f"history:// — {len(self._lines)} line(s)",
            rows=tuple(rows),
            empty=False,
        )

    def selection(self) -> Selection:
        if not self._lines:
            return Selection(node=None)
        i = self._sel
        line = self._lines[i]
        return Selection(node=Node(
            address=f"history://{i}",
            name=format_history_row(line, max_chars=40),
            kind="leaf",
            extra={"type": "history-line", "index": i, "text": line},
        ))

    def actions(self) -> "list[Action]":
        acts: list[Action] = []
        if self._lines:
            line = self._lines[self._sel]
            acts.append(
                Action(
                    "prefill",
                    "Prefill input (review-before-run)",
                    Outcome(PREFILL, f"history://{self._sel}", text=line),
                )
            )
        # Surfaces intercept ``clear`` and call :meth:`clear_typed` (typed
        # lines only — not ``/reset``). The dummy PREFILL is never seeded.
        acts.append(
            Action(
                "clear",
                "Clear typed lines",
                Outcome(PREFILL, "history://", text=""),
            )
        )
        return acts

    def clear_typed(self) -> bool:
        """Wipe ``.xlii/repl_history`` and reload. Face also drops ↑/↓."""
        xli = _xli_dir()
        if xli is not None:
            clear_repl_history(xli)
        self._reload()
        return True

    def handle(self, key: str) -> bool:
        if not self._lines:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._lines) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "enter":
            # Surface executes PREFILL via actions() on action op.
            return False
        if key.startswith("select:"):
            try:
                idx = int(key.split(":", 1)[1])
            except ValueError:
                return False
            if 0 <= idx < len(self._lines):
                self._sel = idx
                return True
        return False

    def select_index(self, index: int) -> bool:
        if 0 <= index < len(self._lines):
            self._sel = index
            return True
        return False
