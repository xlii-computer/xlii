"""``ImageViewPane`` — client #3 of the pane contract: a read-only pane for an image leaf.

Like :class:`~xlii.panes.view.ViewPane` (client #2) it projects a single VFS leaf — but it does
**not** decode the bytes to text. An image isn't text; the text viewer would render mojibake.
This pane *classifies* as ``image`` (the content-class facet) and hands the surface a
:class:`~xlii.panes.RenderedMedia` descriptor: the surface draws the actual picture (terminal
graphics — sixel/kitty/chafa) and degrades to a caption (name · size) where graphics aren't
available. Its selection is the image leaf; Focus pins it for the next turn.

Image renderers operate on *files*, so this is the one pane that resolves its address to a
filesystem path (via :func:`~xlii.addressing.resolve`); schemes with no path degrade to the
caption, never to garbled bytes.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node, resolve, vfs_stat
from xlii.panes import (
    ATTACH,
    Action,
    Outcome,
    Rendered,
    RenderedMedia,
    RenderedRow,
    Selection,
)


def _human_size(n: Optional[int]) -> str:
    if n is None:
        return "?"
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{int(f)} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


class ImageViewPane:
    """A read-only pane that presents an image leaf — as an image, not as decoded text."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="")
        self._node: Optional[Node] = None
        self._path: str = ""  # a filesystem path the surface can render, "" when there is none
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        """Bind to an image leaf. ``select`` is accepted for contract symmetry and ignored."""
        addr = address if isinstance(address, Address) else Address.parse(address)
        node = vfs_stat(addr)
        if node.kind != "leaf":
            raise IsADirectoryError(f"{addr}: not a leaf — an image pane reads a file, not a container")
        self._address = addr
        self._node = node
        r = resolve(addr)
        self._path = str(r.path) if (r.path is not None and r.path.is_file()) else ""

    def _caption(self) -> str:
        name = self._address.target.rsplit("/", 1)[-1] or self._address.target
        size = self._node.size if self._node is not None else None
        return f"🖼  {name}  ·  {_human_size(size)}"

    def render(self) -> Rendered:
        cap = self._caption()
        media = RenderedMedia(kind="image", address=str(self._address), path=self._path, caption=cap)
        # The caption also rides as a text row, so a surface that ignores `media` still reads
        # "this is an image" rather than a blank slot.
        rows = (RenderedRow(text=cap, address=str(self._address), kind="caption", selected=False),)
        return Rendered(title=str(self._address), rows=rows, empty=False, media=media)

    def selection(self) -> Selection:
        """The image leaf itself — the turn context an AI grab receives."""
        return Selection(node=self._node)

    def actions(self) -> "list[Action]":
        if self._node is None:
            return []
        return [
            Action("focus", "Focus", Outcome(ATTACH, self._node.address)),
        ]

    def handle(self, key: str) -> bool:
        """No local navigation — scrolling/zoom is a surface concern; every key falls through."""
        return False
