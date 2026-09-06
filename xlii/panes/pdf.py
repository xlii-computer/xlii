"""``PdfViewPane`` — client #4 of the pane contract: a read-only PDF leaf.

Like :class:`~xlii.panes.image.ImageViewPane` it projects a single VFS leaf and
does **not** decode the bytes as text (a PDF is a binary container). The pane
extracts page text (:func:`~xlii.pdf_text.extract_pdf_pages`) and shows one page
at a time. Local nav is page-turn; the surface scrolls the page. Selection is
the file — Summarize folds extracted text into the turn.

No extractor (the ``[files]`` extra) or a scanned page degrades to a note, never
to garbled bytes.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from xlii.addressing import Address, Node, resolve, vfs_stat
from xlii.panes import (
    ENQUEUE_TURN,
    Action,
    Outcome,
    Rendered,
    RenderedMedia,
    RenderedRow,
    Selection,
)
from xlii.pdf_text import DEFAULT_MAX_PAGES, extract_pdf_pages, pdf_page_count, render_pdf_page

_MAX_PAGE_LINES = 400
_MAX_PAGE_PNG = 2 * 1024 * 1024


def _human_size(n: Optional[int]) -> str:
    if n is None:
        return "?"
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{int(f)} {unit}" if unit == "B" else f"{f:.1f} {unit}"
        f /= 1024
    return f"{f:.1f} TB"


def _page_from_select(select: Optional[str]) -> Optional[int]:
    if not select:
        return None
    raw = select.strip()
    if raw.lower().startswith("page:"):
        raw = raw.split(":", 1)[1]
    try:
        n = int(raw)
    except ValueError:
        return None
    return n if n >= 1 else None


class PdfViewPane:
    """A read-only pane that presents a PDF leaf as extracted pages."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="")
        self._node: Optional[Node] = None
        self._path: str = ""
        self._pages: tuple[str, ...] = ()
        self._page: int = 1
        self._missing: bool = False  # no extractor installed
        self._stamp: Optional[tuple] = None
        self._raster: dict[int, Optional[bytes]] = {}
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    @property
    def page(self) -> int:
        return self._page

    @property
    def page_count(self) -> int:
        return max(1, len(self._pages)) if self._pages else 1

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        """Bind to a PDF leaf. ``select`` of ``page:N`` (or a bare ``N``) restores the page."""
        addr = address if isinstance(address, Address) else Address.parse(address)
        node = vfs_stat(addr)
        if node.kind != "leaf":
            raise IsADirectoryError(f"{addr}: not a leaf — a PDF pane reads a file, not a container")
        r = resolve(addr)
        path = str(r.path) if (r.path is not None and r.path.is_file()) else ""
        same_file = bool(path) and path == self._path
        self._address = addr
        self._node = node
        self._path = path
        self._load(path, same_file=same_file)
        picked = _page_from_select(select)
        if picked is not None:
            self._page = picked
        elif not same_file:
            self._page = 1
        self._page = max(1, min(self._page, self.page_count))

    def _load(self, path: str, *, same_file: bool) -> None:
        if not path:
            self._pages = ()
            self._missing = False
            self._stamp = None
            return
        try:
            st = Path(path).stat()
            stamp = (path, int(st.st_mtime), int(st.st_size))
        except OSError:
            self._pages = ()
            self._missing = False
            self._stamp = None
            return
        if same_file and stamp == self._stamp:
            return
        pages = extract_pdf_pages(path, max_pages=DEFAULT_MAX_PAGES)
        self._missing = pages is None
        if pages:
            self._pages = tuple(pages)
        else:
            n = pdf_page_count(path) or 0
            self._pages = tuple("" for _ in range(min(n, DEFAULT_MAX_PAGES)))
        self._stamp = stamp
        self._raster = {}

    def _turn(self, delta: int) -> bool:
        nxt = self._page + delta
        if nxt < 1 or nxt > self.page_count:
            return False
        self._page = nxt
        return True

    def _caption(self) -> str:
        name = self._address.target.rsplit("/", 1)[-1] or self._address.target
        size = self._node.size if self._node is not None else None
        n = len(self._pages)
        if self._missing:
            return f"📄  {name}  ·  {_human_size(size)}"
        if n:
            return f"📄  {name}  ·  {n} page{'s' if n != 1 else ''}  ·  {_human_size(size)}"
        return f"📄  {name}  ·  {_human_size(size)}"

    def _page_text(self) -> str:
        if not self._pages:
            return ""
        i = max(0, min(self._page - 1, len(self._pages) - 1))
        return self._pages[i] or ""

    def _page_png(self) -> bytes:
        """Raster of the current page (scans and native pages).

        A text-only dump is the fallback when nothing on the box can
        render — native PDFs often extract one word per line.
        """
        if not self._path:
            return b""
        if self._page in self._raster:
            return self._raster[self._page] or b""
        raw = render_pdf_page(self._path, self._page)
        if raw and len(raw) > _MAX_PAGE_PNG:
            raw = None
        self._raster[self._page] = raw
        return raw or b""

    def render(self) -> Rendered:
        cap = self._caption()
        png = self._page_png()
        b64 = base64.b64encode(png).decode("ascii") if png else ""
        media = RenderedMedia(
            kind="image" if png else "pdf",
            address=str(self._address),
            path="" if png else self._path,  # never hand a .pdf to an image renderer
            caption=cap,
            b64=b64,
        )
        rows: list[RenderedRow] = [
            RenderedRow(text=cap, address=str(self._address), kind="caption", selected=False),
        ]
        n = self.page_count
        if len(self._pages) > 1:
            rows.append(RenderedRow(
                text="◂ previous page", address="key:left", kind="caption", selected=False,
            ))
        rows.append(RenderedRow(
            text=f"page {self._page} / {n}",
            address=str(self._address), kind="caption", selected=False,
        ))
        if len(self._pages) > 1:
            rows.append(RenderedRow(
                text="next page ▸", address="key:right", kind="caption", selected=False,
            ))
        if self._missing and not png:
            rows.append(RenderedRow(
                text="needs the [files] extra (pypdf) to read PDF text",
                address=str(self._address), kind="caption", selected=False,
            ))
        elif not self._path:
            rows.append(RenderedRow(
                text="no filesystem path — cannot read this PDF",
                address=str(self._address), kind="caption", selected=False,
            ))
        elif not self._pages and not png:
            rows.append(RenderedRow(
                text="no extractable text — scanned or encrypted",
                address=str(self._address), kind="caption", selected=False,
            ))
        else:
            body = self._page_text()
            if png:
                rows.append(RenderedRow(
                    text="page image",
                    address=str(self._address), kind="caption", selected=False,
                ))
            elif not body.strip():
                rows.append(RenderedRow(
                    text="(this page has no extractable text)",
                    address=str(self._address), kind="caption", selected=False,
                ))
            else:
                lines = body.splitlines() or [body]
                if len(lines) > _MAX_PAGE_LINES:
                    lines = lines[:_MAX_PAGE_LINES] + ["… [truncated]"]
                rows.extend(
                    RenderedRow(text=line, address=str(self._address), kind="line", selected=False)
                    for line in lines
                )
        title = f"{self._address}  ·  {self._page}/{n}"
        return Rendered(title=title, rows=tuple(rows), empty=False, media=media)

    def selection(self) -> Selection:
        return Selection(node=self._node)

    def actions(self) -> "list[Action]":
        if self._node is None:
            return []
        return [
            Action(
                "summarize",
                "Summarize",
                Outcome(ENQUEUE_TURN, self._node.address, text="Summarize this PDF."),
            )
        ]

    def handle(self, key: str) -> bool:
        if key in ("right", "pagedown"):
            return self._turn(+1)
        if key in ("left", "pageup"):
            return self._turn(-1)
        if key == "back":
            return self._turn(-1)
        if key == "home":
            if self._page == 1:
                return False
            self._page = 1
            return True
        if key == "end":
            last = self.page_count
            if self._page == last:
                return False
            self._page = last
            return True
        return False

    def select_index(self, i: int) -> bool:
        """Clicks on a line stay on this page — do not remount."""
        n = len(self.render().rows)
        return 0 <= i < n
