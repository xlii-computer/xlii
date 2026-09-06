"""``ViewPane`` — client #2 of the pane contract, a read-only leaf viewer over ``vfs_read``.

The explorer (client #1) browses containers; the view pane reads a *leaf*. Building a second
pane type is what proves the contract actually generalizes — and it gives the explorer's
``RETARGET_SLOT`` outcome ("open in other pane") something to open into.

A view pane is a pure projection of its address: ``render`` decodes ``vfs_read(address)`` into a
renderable tree of lines. It owns **no** scroll/viewport state — viewport height is a *surface*
concern (the doc's "encode no terminal-cell assumptions"), so a surface scrolls the rendered
tree; the pane just projects the whole content. Its selection is the leaf itself — the turn
context an AI grab would receive. Local nav is therefore empty (``handle`` returns ``False``);
the surface owns scrolling, the kernel owns the actions.

The headless core stays dependency-light (bytes → lines). The TUI adapter is where the existing
``xlii/tui/preview*`` renderers get re-pointed at an ``Address``.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node, vfs_read, vfs_stat
from xlii.panes import (
    ENQUEUE_TURN,
    Action,
    Outcome,
    Rendered,
    RenderedRow,
    Selection,
)


class ViewPane:
    """A read-only pane that renders the bytes of a single VFS leaf."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="")
        self._node: Optional[Node] = None
        self._lines: tuple[str, ...] = ()
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        """Bind to a leaf address and project its bytes. ``select`` is accepted for contract
        symmetry (a view pane has no within-leaf selection yet) and ignored."""
        addr = address if isinstance(address, Address) else Address.parse(address)
        node = vfs_stat(addr)
        if node.kind != "leaf":
            raise IsADirectoryError(f"{addr}: not a leaf — a view pane reads files, not containers")
        self._address = addr
        self._node = node
        text = vfs_read(addr).decode("utf-8", errors="replace")
        # splitlines() drops a single trailing newline (the usual editor convention) and never
        # yields a phantom empty last line; an empty file → no lines → empty projection.
        self._lines = tuple(text.splitlines())

    def render(self) -> Rendered:
        rows = tuple(
            RenderedRow(text=line, address=str(self._address), kind="line", selected=False)
            for line in self._lines
        )
        return Rendered(title=str(self._address), rows=rows, empty=not rows)

    def selection(self) -> Selection:
        """The leaf itself — the file is the turn context an AI grab receives."""
        return Selection(node=self._node)

    def actions(self) -> "list[Action]":
        if self._node is None:
            return []
        # An AI context-grab over the viewed file. Offered as data; the Dock routes ENQUEUE_TURN
        # to the turn sink — the bridge into the turn/automation stack.
        return [
            Action("summarize", "Summarize", Outcome(ENQUEUE_TURN, self._node.address, text="Summarize this file."))
        ]

    def handle(self, key: str) -> bool:
        """No local navigation: scrolling the rendered tree is the surface's job (it owns the
        viewport height), so every key falls through."""
        return False
