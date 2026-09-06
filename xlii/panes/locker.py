"""``LockerPane`` — Attachments. On or off.

Engine address stays ``locker://``. The user sees a list of files: accent =
attached (goes with the next message). Open puts it on the pad (canvas).
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import ATTACH, DETACH, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection
from xlii.panes.select import select_target


class LockerPane:
    """A read-only list of attached-file names; select → render in the REPL, detach → remove."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="locker")
        self._files: list = []           # list[dict] — the attached_files entries
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        from xlii.active_session import active_session

        self._address = address if isinstance(address, Address) else Address.parse(address)
        files = getattr(active_session(), "attached_files", None) or []
        self._files = [e for e in files if isinstance(e, dict) and e.get("name")]
        self._sel = 0
        target = select_target(select, self._address) or self._address.key.strip()
        if target:
            for i, e in enumerate(self._files):
                if e.get("name") == target:
                    self._sel = i
                    break

    def render(self) -> Rendered:
        rows = [
            RenderedRow(text=e["name"], address=f"locker://{e['name']}", kind="leaf",
                        selected=(i == self._sel), accent=bool(e.get("enabled", True)))
            for i, e in enumerate(self._files)
        ]
        return Rendered(title="Attachments", rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        if not self._files:
            return Selection(node=None)
        e = self._files[self._sel]
        return Selection(node=Node(address=f"locker://{e['name']}", name=e["name"], kind="leaf",
                                   extra={"type": e.get("kind", "image")}))

    def actions(self) -> "list[Action]":
        if not self._files:
            return []
        e = self._files[self._sel]
        name = e["name"]
        acts: list[Action] = [
            Action("open", "Open", Outcome(RETARGET_SLOT, f"canvas://{name}")),
        ]
        if e.get("enabled", True):
            acts.append(Action("detach", "Detach",
                               Outcome(DETACH, f"locker://{name}")))
        else:
            acts.append(Action("attach", "Attach",
                               Outcome(ATTACH, f"locker://{name}")))
        return acts

    def handle(self, key: str) -> bool:
        if not self._files:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._files) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(self._files) - 1
            return True
        return False

    def select_index(self, i: int) -> bool:
        if 0 <= i < len(self._files):
            self._sel = i
            return True
        return False
