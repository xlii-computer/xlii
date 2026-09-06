"""``CanvasPane`` — the pad.

Whatever is in front of you: a note, a drawing, a page. Text, image, or PDF.
Not a gallery. Open a file (or make one) and it sits here.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import (
    PREFILL,
    RETARGET_SLOT,
    Action,
    Outcome,
    Rendered,
    RenderedMedia,
    RenderedRow,
    Selection,
)
from xlii.panes.select import select_target

_MAX_INLINE = 2 * 1024 * 1024
_IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_TEXT = {".txt", ".md", ".rst", ".json", ".csv", ".py", ".toml", ".html", ".css", ".js"}
_MAX_TEXT_CHARS = 80_000


def _size(n: Optional[int]) -> str:
    if n is None:
        return "?"
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n // 1024}K"
    return f"{n / (1024 * 1024):.1f}M"


def _kind_of(path: Path, hinted: str = "") -> str:
    if hinted in ("image", "pdf", "text", "video"):
        return hinted
    suf = path.suffix.lower()
    if suf in _IMAGE:
        return "image"
    if suf == ".pdf":
        return "pdf"
    if suf in _TEXT or not suf:
        return "text"
    return hinted or "file"


def _node_from_path(address: str, name: str, path: str, kind: str = "") -> Optional[Node]:
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    try:
        nbytes = p.stat().st_size
    except OSError:
        nbytes = 0
    return Node(
        address=address, name=name, kind="leaf",
        extra={"type": _kind_of(p, kind), "path": str(p), "bytes": nbytes},
    )


def _node_from_locker(want: str) -> Optional[Node]:
    from xlii.active_session import active_session

    files = getattr(active_session(), "attached_files", None) or []
    for e in files:
        if not isinstance(e, dict):
            continue
        name = str(e.get("name") or "")
        path = str(e.get("path") or "")
        if name == want or Path(path).name == want:
            return _node_from_path(f"canvas://{name or want}", name or want, path,
                                   str(e.get("kind") or ""))
    return None


class CanvasPane:
    """The pad — one thing in front of you."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="canvas")
        self._nodes: list[Node] = []
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        from xlii.addressing.builtins.canvas import CanvasProvider

        try:
            all_nodes = CanvasProvider().list(Address(scheme="canvas"))
        except Exception:
            all_nodes = []
        want = select_target(select, self._address) or self._address.key.strip()
        if not want and select:
            want = Path(str(select)).name
        chosen = None
        if want:
            for n in all_nodes:
                if n.name == want or n.address.endswith(want):
                    chosen = n
                    break
            if chosen is None:
                chosen = _node_from_locker(want)
        elif all_nodes:
            chosen = all_nodes[0]
        self._nodes = [chosen] if chosen is not None else []
        self._sel = 0
        if chosen is not None:
            try:
                self._address = Address.parse(chosen.address)
            except Exception:
                # An unparseable address leaves _address unset and the pane mounts empty.
                pass

    def _current(self) -> Optional[Node]:
        if not self._nodes:
            return None
        return self._nodes[self._sel]

    def _media_for(self, n: Node) -> Optional[RenderedMedia]:
        path = str((n.extra or {}).get("path") or "")
        kind = str((n.extra or {}).get("type") or "")
        try:
            from xlii.artifacts import display_store_path
            from xlii.active_session import active_session

            root = getattr(getattr(active_session(), "project", None), "project_root", None)
            loc = display_store_path(path, root) if path else ""
        except Exception:
            loc = path
        cap = "  ·  ".join(s for s in (n.name, _size((n.extra or {}).get("bytes")), loc) if s)
        if kind == "pdf" or path.lower().endswith(".pdf"):
            from xlii.pdf_text import render_pdf_page

            raw = render_pdf_page(path, 1) if path else None
            b64 = base64.b64encode(raw).decode("ascii") if raw and len(raw) <= _MAX_INLINE else ""
            return RenderedMedia(
                kind="image" if b64 else "pdf",
                address=n.address, path="" if b64 else path,
                caption=cap, b64=b64,
            )
        if kind == "image" or Path(path).suffix.lower() in _IMAGE:
            b64 = ""
            if path:
                try:
                    data = Path(path).read_bytes()
                except OSError:
                    data = b""
                if data and len(data) <= _MAX_INLINE:
                    b64 = base64.b64encode(data).decode("ascii")
            return RenderedMedia(
                kind="image", address=n.address, path=path,
                caption=cap, b64=b64,
            )
        return RenderedMedia(kind=kind or "file", address=n.address,
                             path=path, caption=cap)

    def render(self) -> Rendered:
        cur = self._current()
        if cur is None:
            note = RenderedRow(
                text="the pad — empty. Drop a file, write a note, or open something.",
                address="canvas://", kind="caption", selected=False,
            )
            return Rendered(title="Canvas", rows=(note,), empty=True)
        kind = str((cur.extra or {}).get("type") or "")
        path = str((cur.extra or {}).get("path") or "")
        if kind == "text" or Path(path).suffix.lower() in _TEXT:
            body = ""
            if path:
                try:
                    body = Path(path).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    body = ""
            if "\x00" in body[:4096]:
                body = ""
            if len(body) > _MAX_TEXT_CHARS:
                body = body[:_MAX_TEXT_CHARS] + "\n…"
            rows = [
                RenderedRow(text=cur.name, address=cur.address,
                            kind="caption", selected=False),
            ]
            for line in (body.splitlines() or [""]):
                rows.append(RenderedRow(
                    text=line, address=cur.address, kind="line", selected=False,
                ))
            return Rendered(title=f"Canvas  ·  {cur.name}", rows=tuple(rows),
                            empty=False)
        media = self._media_for(cur)
        rows = [
            RenderedRow(
                text=media.caption if media else cur.name,
                address=cur.address, kind="caption", selected=False,
            )
        ]
        if kind == "pdf" and media and not media.b64:
            rows.append(RenderedRow(
                text="pdf — View pages for the full reader",
                address=cur.address, kind="caption", selected=False,
            ))
        return Rendered(title=f"Canvas  ·  {cur.name}", rows=tuple(rows),
                        empty=False, media=media)

    def selection(self) -> Selection:
        return Selection(node=self._current())

    def actions(self) -> "list[Action]":
        n = self._current()
        if n is None:
            return []
        path = (n.extra or {}).get("path")
        acts: list[Action] = []
        kind = (n.extra or {}).get("type")
        if path and kind == "pdf":
            acts.append(Action("pages", "View pages",
                               Outcome(RETARGET_SLOT, f"file://{path}")))
        if path and kind == "image":
            acts.append(Action("edit", "Edit…",
                               Outcome(PREFILL, n.address,
                                       text=f'/image edit "…" --ref {n.name}')))
        return acts

    def handle(self, key: str) -> bool:
        # One file on the work. Sibling makes are Artifacts, not arrow keys.
        return False

    def select_index(self, i: int) -> bool:
        if i == 0 and self._nodes:
            self._sel = 0
            return True
        return False
