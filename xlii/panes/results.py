"""``ResultsRootPane`` / ``ResultsTablePane`` — the provider results viewer (F2).

Two panes over ``results://``: the root lists providers with stored runs
(name · record count · age); a provider opens as a **table** — columns from
the record keys, one row per record, sorted by any column. Sorting is LOCAL
nav (``handle("sort:<field>")`` — the pane contract's own channel; the face
sends it via the generic ``key:`` row convention, no wire additions). The
one record action **enqueues a turn with the record inlined** — the
sort → store → compare → *ask* loop, reviewable in the transcript like any
turn. Pure projections of the stored ``latest.json`` (state-ownership:
``(address, selection)`` + sort field held as nav state only).
"""

from __future__ import annotations

import json
import numbers
from datetime import datetime
from typing import Optional

from xlii.addressing import Address, Node, vfs_list, vfs_read
from xlii.panes import ENQUEUE_TURN, NAVIGATE, Action, Outcome, Rendered, RenderedRow, Selection
from xlii.panes.artifacts import _age

_MAX_ROWS = 100
_MAX_COLS = 6
_CELL = 28  # column width cap (chars)


def _fmt_cell(v, width: int) -> str:
    s = "" if v is None else (json.dumps(v) if isinstance(v, (list, dict)) else str(v))
    return s[: width - 1] + "…" if len(s) > width else s.ljust(width)


def _age_from_fetched(fetched_at: str) -> str:
    try:
        ts = datetime.fromisoformat(fetched_at.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return "?"
    return _age(ts)


def _cell_blank(rec, field: str) -> bool:
    v = rec.get(field) if isinstance(rec, dict) else rec
    return v is None or v == ""


class ResultsRootPane:
    """One row per provider with a stored run; Enter/Open navigates to its table."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="results")
        self._names: list[str] = []
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._names = []
        self._sel = 0
        self._load(select or self._address.target.strip())

    def _load(self, select: str = "") -> None:
        """(Re)list the stored runs, keeping the selection by NAME.

        A ``/providers run`` stores its records while the deck is mounted, and
        re-projection (``render``) is a surface's only refresh — so the listing
        is re-read here rather than cached at mount."""
        keep = select or (self._names[self._sel] if self._sel < len(self._names) else "")
        keep = keep.rsplit("://", 1)[-1].strip("/")
        try:
            self._names = [n.name for n in vfs_list("results://")]
        except Exception:
            self._names = []
        self._sel = 0
        if keep:
            for i, name in enumerate(self._names):
                if name == keep:
                    self._sel = i
                    break

    def _meta(self, name: str) -> str:
        try:
            data = json.loads(vfs_read(f"results://{name}"))
            return f"{data.get('count', '?')} records · {_age_from_fetched(data.get('fetched_at', ''))}"
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return ""

    def render(self) -> Rendered:
        self._load()
        rows = [
            RenderedRow(text=f"{name}  · {self._meta(name)}",
                        address=f"results://{name}", kind="container",
                        selected=(i == self._sel))
            for i, name in enumerate(self._names)
        ]
        return Rendered(title="results://", rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        if not self._names:
            return Selection(node=None)
        name = self._names[self._sel]
        return Selection(node=Node(address=f"results://{name}", name=name,
                                   kind="container", extra={"type": "results"}))

    def actions(self) -> "list[Action]":
        if not self._names:
            return []
        name = self._names[self._sel]
        return [Action("open", "Open table", Outcome(NAVIGATE, f"results://{name}"))]

    def handle(self, key: str) -> bool:
        if not self._names:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._names) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "enter":
            return False  # the surface consults actions() (open)
        if key == "back":
            return False
        return False

    def select_index(self, i: int) -> bool:
        if 0 <= i < len(self._names):
            self._sel = i
            return True
        return False


class ResultsTablePane:
    """A provider's latest run as a sortable table.

    Nav state (rebuildable, per the state-ownership rule): the selected row
    index, the sort field, and the sort direction. ``handle("sort:<field>")``
    toggles direction on repeat; ``handle("back")`` returns to the root."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="results")
        self._provider = ""
        self._rows: list[tuple[int, object]] = []
        self._columns: list[str] = []
        self._sel: int = 0
        self._sort: str = ""
        self._desc: bool = False
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._provider = self._address.target.strip().split("/")[0]
        self._rows = []
        self._columns = []
        self._sel = 0
        try:
            data = json.loads(vfs_read(f"results://{self._provider}"))
            self._rows = list(enumerate(data.get("records", [])))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # Unreadable or missing run — empty table is the graceful fallback.
            self._rows = []
        keys: list[str] = []
        for _, rec in self._rows[:_MAX_ROWS]:
            if isinstance(rec, dict):
                for k in rec:
                    if k not in keys:
                        keys.append(k)
        self._columns = keys[:_MAX_COLS]
        if select is not None and select.startswith("sort:"):
            self._apply_sort(select[5:])
        if self._sort and self._rows:
            # Re-mounts re-read unsorted records; re-apply the held ordering
            # (state-ownership: sort field + direction are the nav state).
            field, want_desc = self._sort, self._desc
            self._sort, self._desc = "", False
            self._apply_sort(field)  # lands asc
            if want_desc:
                self._apply_sort(field)  # toggles to desc
        if select is not None:
            # The reconstruct hook's record form: results://<name>/<i>.
            tail = select.rsplit("/", 1)[-1]
            try:
                want = int(tail)
                for i, (idx, _) in enumerate(self._rows):
                    if idx == want:
                        self._sel = i
                        break
            except ValueError:
                # Non-numeric selection tail — keep the default row.
                pass

    def _apply_sort(self, field: str) -> None:
        if field == self._sort:
            self._desc = not self._desc
        else:
            self._sort, self._desc = field, False

        selected = self._rows[self._sel] if self._rows and 0 <= self._sel < len(self._rows) else None

        def key(rec):
            v = rec.get(field) if isinstance(rec, dict) else rec
            if isinstance(v, bool):
                return (2, int(v))
            if isinstance(v, numbers.Number):
                return (0, v)
            if isinstance(v, str):
                return (1, v.casefold())
            return (3, json.dumps(v, sort_keys=True, ensure_ascii=False))

        valued = [row for row in self._rows if not _cell_blank(row[1], field)]
        blanks = [row for row in self._rows if _cell_blank(row[1], field)]
        valued.sort(key=lambda row: key(row[1]), reverse=self._desc)
        self._rows = valued + blanks  # blanks always last, either direction
        if selected is not None:
            try:
                self._sel = self._rows.index(selected)
            except ValueError:
                self._sel = 0
        else:
            self._sel = 0

    def _cycle_sort(self) -> None:
        """Header click: walk asc → desc → next column (the row model has no
        per-cell clicks, so the header is one cycle button)."""
        if not self._columns:
            return
        if not self._sort:
            self._apply_sort(self._columns[0])
        elif not self._desc:
            self._apply_sort(self._sort)  # same field toggles to desc
        else:
            i = (self._columns.index(self._sort) + 1) % len(self._columns)
            self._apply_sort(self._columns[i])  # new field lands asc

    def render(self) -> Rendered:
        rows: list[RenderedRow] = []
        if self._columns:
            arrow = {c: (" ↓" if self._desc else " ↑") if c == self._sort else "" for c in self._columns}
            header = "  ".join(_fmt_cell(c + arrow[c], _CELL) for c in self._columns)
            rows.append(RenderedRow(text="⇅ " + header, address="key:sort:next",
                                    kind="container", selected=False))
        for i, (idx, rec) in enumerate(self._rows[:_MAX_ROWS]):
            if isinstance(rec, dict):
                text = "  ".join(_fmt_cell(rec.get(c), _CELL) for c in self._columns)
            else:
                text = _fmt_cell(rec, _CELL * 2)
            rows.append(RenderedRow(text=text, address=f"results://{self._provider}/{idx}",
                                    kind="leaf", selected=(i == self._sel)))
        truncated = len(self._rows) > _MAX_ROWS
        note = f"results://{self._provider} — {len(self._rows)} records" + (
            f" (first {_MAX_ROWS})" if truncated else "") + " · ⇅ header sorts"
        return Rendered(title=note, rows=tuple(rows), empty=not self._rows)

    def selection(self) -> Selection:
        if not self._rows or self._sel >= len(self._rows):
            return Selection(node=None)
        idx, _ = self._rows[self._sel]
        return Selection(node=Node(
            address=f"results://{self._provider}/{idx}",
            name=f"record {idx}", kind="leaf", extra={"type": "record"}))

    def actions(self) -> "list[Action]":
        if not self._rows:
            return []
        idx, _ = self._rows[self._sel]
        prompt = (
            f"Looking at one record from the '{self._provider}' provider results. "
            "Tell me what it is and whether it's worth pursuing."
        )
        return [
            Action("ask", "Ask about this record",
                   Outcome(ENQUEUE_TURN, f"results://{self._provider}/{idx}",
                           text=prompt)),
        ]

    def handle(self, key: str) -> bool:
        if key == "back":
            return False  # the surface steps back to the root pane
        if key.startswith("sort:"):
            if key[5:] == "next":
                self._cycle_sort()
                return True
            field = key[5:]
            if not self._columns:
                return True  # nothing to sort — swallow
            target = field if field in self._columns else self._columns[0]
            self._apply_sort(target)
            return True
        if not self._rows:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, min(len(self._rows), _MAX_ROWS) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = min(len(self._rows), _MAX_ROWS) - 1
            return True
        return False

    # No select_index: the rendered rows lead with the sort header, so a row
    # INDEX is off by one from a record index. Surfaces select by ADDRESS
    # (results://<name>/<i> — the reconstruct hook), which never lies.
