"""``MenuPane`` — the slash-command menu over ``menu://`` (F3).

*Menus do, don't type* (the godzilla-mothra rule) on the face: one row per
registered command — name, usage, one-line description, grouped by category.
The row action **seeds the command into the input, un-executed** (PREFILL —
review-before-run, the same contract as the tasks pane). The pane never runs
a command; it puts the verb in your hand.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node, vfs_list
from xlii.panes import PREFILL, Action, Outcome, Rendered, RenderedRow, Selection

_MAX_DESC = 72


class MenuPane:
    """The command registry as a menu; the action seeds the command line."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="menu")
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
        self._load(select or self._address.target.strip())

    def _load(self, select: str = "") -> None:
        """(Re)read the registry, keeping the selection by ADDRESS. The listing
        is surface-scoped (``menu://`` lists the active REPL's commands), so a
        surface switch must re-read rather than serve a mount-time cache."""
        keep = select or self._selected_address()
        try:
            self._nodes = vfs_list("menu://")
        except Exception:
            self._nodes = []
        self._sel = 0
        target = keep.rsplit("://", 1)[-1].lstrip("/")  # menu://name → name
        if target:
            for i, n in enumerate(self._nodes):
                if n.name == target:
                    self._sel = i
                    break

    def _selected_address(self) -> str:
        if not self._nodes or self._sel >= len(self._nodes):
            return ""
        return self._nodes[self._sel].address

    def render(self) -> Rendered:
        self._load()
        rows = []
        last_cat = None
        for i, n in enumerate(self._nodes):
            cat = n.extra.get("category", "general")
            if cat != last_cat:
                last_cat = cat
                rows.append(RenderedRow(text=f"── {cat} ──", address="key:noop",
                                        kind="container", selected=False))
            desc = (n.extra.get("description") or "")[:_MAX_DESC]
            usage = n.extra.get("usage") or f"/{n.name}"
            rows.append(RenderedRow(
                text=f"{usage:<28} {desc}", address=n.address, kind="leaf",
                selected=(i == self._sel),
            ))
        return Rendered(title="menu:// — commands (click to seed)", rows=tuple(rows),
                        empty=not rows)

    def selection(self) -> Selection:
        if not self._nodes:
            return Selection(node=None)
        return Selection(node=self._nodes[self._sel])

    def actions(self) -> "list[Action]":
        if not self._nodes:
            return []
        n = self._nodes[self._sel]
        usage = n.extra.get("usage") or f"/{n.name}"
        # Seed the command with a trailing space when it takes args.
        seed = f"/{n.name} " if usage.strip() != f"/{n.name}" else f"/{n.name}"
        return [
            Action("seed", "Seed into input (review-before-run)",
                   Outcome(PREFILL, n.address, text=seed)),
        ]

    def handle(self, key: str) -> bool:
        if key == "noop":
            return True  # category chrome — swallow
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
        if key == "enter":
            return False  # the surface consults actions() (seed)
        return False

    # No select_index: category header rows interleave with command rows, so a
    # row INDEX is unreliable. Surfaces select by ADDRESS (menu://<name> — the
    # reconstruct hook), which never lies.
