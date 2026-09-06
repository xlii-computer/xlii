"""``SourcesPane`` — the typed source registry over ``sources://`` (F3).

One row per source card (`.xlii/sources/*.toml`), badged with its type.
The actions: **ask about this source** (a turn with the card inlined — the
same loop as the results viewer) and **view** the raw card in the other
slot. Cards are data (the provider-manifest idiom): authored by the agent
or by hand; the pane never writes.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node, vfs_list, vfs_read
from xlii.panes import ENQUEUE_TURN, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection


class SourcesPane:
    """A read-only list of typed source cards; actions ask about or view one."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="sources")
        self._nodes: list[Node] = []
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._nodes = []
        self._sel = 0
        target = (select or self._address.target).strip()
        self._load(target)

    def _load(self, select: str = "") -> None:
        """(Re)read the card listing, keeping the selection by ADDRESS.

        Cards are authored by hand or by the agent *while* the deck is
        mounted, and re-projection (``render``) is a surface's only refresh —
        so the listing is re-read here rather than cached at mount."""
        keep = select or self._selected_address()
        try:
            self._nodes = vfs_list("sources://")
        except Exception:
            self._nodes = []
        self._sel = 0
        if keep:
            wanted = keep.rsplit("://", 1)[-1].strip("/")
            for i, n in enumerate(self._nodes):
                if n.address == keep or n.name == wanted:
                    self._sel = i
                    break

    def _selected_address(self) -> str:
        if not self._nodes or self._sel >= len(self._nodes):
            return ""
        return self._nodes[self._sel].address

    def render(self) -> Rendered:
        self._load()
        rows = [
            RenderedRow(
                text=f"{n.name}  · {n.extra.get('source_type', 'source')}",
                address=n.address, kind="leaf", selected=(i == self._sel),
            )
            for i, n in enumerate(self._nodes)
        ]
        return Rendered(title="sources://", rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        if not self._nodes:
            return Selection(node=None)
        return Selection(node=self._nodes[self._sel])

    def actions(self) -> "list[Action]":
        if not self._nodes:
            return []
        n = self._nodes[self._sel]
        try:
            card = vfs_read(n.address).decode("utf-8", errors="replace")
        except Exception:
            card = n.address
        prompt = (
            f"Looking at one source card from this project's source registry. "
            f"What is it, and how should it shape the research here?\n\n{card}"
        )
        return [
            Action("ask", "Ask about this source",
                   Outcome(ENQUEUE_TURN, n.address, text=prompt)),
            Action("view", "View card", Outcome(RETARGET_SLOT, n.address)),
        ]

    def handle(self, key: str) -> bool:
        if not self._nodes:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._nodes) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(self._nodes) - 1
            return True
        return False

    def select_index(self, i: int) -> bool:
        if 0 <= i < len(self._nodes):
            self._sel = i
            return True
        return False
