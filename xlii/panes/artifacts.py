"""``ArtifactsPane`` — the gallery of what xlii MADE, over ``artifacts://``.

The LockerPane's disk-backed sibling with the opposite meaning kept crisp
(tui-media-delivery P2): the Tray stages files *to send*; this pane browses
files *xlii generated* (``.xlii/artifacts/``), newest-first.

Opening an image or PDF puts it **on the canvas** (that file becomes the
work). Focus pins one for the next turn; **Save into project…** copies it out
of the store. ``/image edit "…"`` still prefill from here. Locker stays the
durable tray.
"""

from __future__ import annotations

import time
from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import ATTACH, PREFILL, RETARGET_SLOT, SHOW_MEDIA, Action, Outcome, Rendered, RenderedRow, Selection
from xlii.panes.select import select_target


def _age(mtime: float, now: Optional[float] = None) -> str:
    now = time.time() if now is None else now
    secs = max(0, int(now - mtime))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n // 1024}K"
    return f"{n / (1024 * 1024):.1f}M"


class ArtifactsPane:
    """A read-only newest-first gallery list; view → Pane 1, attach → Tray."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="artifacts")
        self._nodes: list[Node] = []
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        from xlii.addressing.builtins.artifacts import ArtifactsProvider

        try:
            self._nodes = ArtifactsProvider().list(Address(scheme="artifacts"))
        except Exception:
            self._nodes = []
        self._sel = 0
        target = select_target(select, self._address) or self._address.key.strip()
        if target:
            for i, n in enumerate(self._nodes):
                if n.name == target:
                    self._sel = i
                    break

    def render(self) -> Rendered:
        rows = []
        for i, n in enumerate(self._nodes):
            extra = n.extra or {}
            bits = [n.name]
            if extra.get("bytes") is not None:
                bits.append(_size(int(extra["bytes"])))
            if extra.get("mtime") is not None:
                bits.append(_age(float(extra["mtime"])))
            path = extra.get("path") or ""
            if path:
                from xlii.artifacts import display_store_path
                from xlii.active_session import active_session

                root = getattr(getattr(active_session(), "project", None), "project_root", None)
                bits.append(display_store_path(path, root))
            rows.append(RenderedRow(
                text=" · ".join(bits), address=n.address, kind="leaf",
                selected=(i == self._sel), accent=(i == 0),   # newest leads
            ))
        return Rendered(title="artifacts://", rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        if not self._nodes:
            return Selection(node=None)
        return Selection(node=self._nodes[self._sel])

    def actions(self) -> "list[Action]":
        if not self._nodes:
            return []
        n = self._nodes[self._sel]
        path = (n.extra or {}).get("path")
        kind = (n.extra or {}).get("type")
        acts: list[Action] = []
        if path and kind in ("image", "pdf"):
            acts.append(Action(
                "open", "On canvas",
                Outcome(RETARGET_SLOT, f"canvas://{n.name}"),
            ))
        elif path:
            dest = f"file://{path}"
            acts.append(Action("view", "View", Outcome(SHOW_MEDIA, dest)))
        acts.append(Action("focus", "Focus", Outcome(ATTACH, n.address)))
        if path and kind == "image":
            acts.append(Action("edit", "Edit…",
                               Outcome(PREFILL, n.address,
                                       text=f'/image edit "…" --ref {n.name}')))
            acts.append(Action("save", "Save into project…",
                               Outcome(PREFILL, n.address,
                                       text=f"/imagine --from {n.name} --save assets/{n.name}")))
        return acts

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
