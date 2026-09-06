"""``HomePane`` — Dock chassis listing panel surfaces (``home://``).

Track I: the side panel bottoms out here instead of a silent file explorer.
The hub lists every panel (plus stream), sectioned. The view already occupying
the other slot is grayed — no mirrors. Open goes in **this** panel by default
(so ``back`` returns here) or the **other** panel when that knob is on.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.home_catalog import HOME_CATALOG
from xlii.panes import NAVIGATE, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection

_CAPTION = "caption"
HUB_OPEN_THIS = "this"
HUB_OPEN_OTHER = "other"


def normalize_hub_open(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if s in ("other", "other-panel", "other_slot", "b"):
        return HUB_OPEN_OTHER
    return HUB_OPEN_THIS


def hub_open_mode(state: object | None = None) -> str:
    """``this`` | ``other`` — where a hub row opens."""
    cfg = getattr(state, "cfg", None) if state is not None else None
    if cfg is None:
        try:
            from xlii.active_session import active_session

            st = active_session()
            cfg = getattr(st, "cfg", None) if st is not None else None
        except Exception:
            cfg = None
    if cfg is None:
        try:
            from xlii.config import GlobalConfig

            cfg = GlobalConfig.load()
        except Exception:
            return HUB_OPEN_THIS
    return normalize_hub_open(getattr(cfg, "hub_open", None))


def cycle_hub_open(state: object | None = None) -> str:
    """Flip this/other and persist. Returns the new mode."""
    cfg = getattr(state, "cfg", None) if state is not None else None
    if cfg is None:
        try:
            from xlii.active_session import active_session

            st = active_session()
            cfg = getattr(st, "cfg", None) if st is not None else None
        except Exception:
            cfg = None
    if cfg is None:
        from xlii.config import GlobalConfig

        cfg = GlobalConfig.load()
    nxt = HUB_OPEN_OTHER if hub_open_mode(state) == HUB_OPEN_THIS else HUB_OPEN_THIS
    try:
        cfg.hub_open = nxt
        save = getattr(cfg, "save", None)
        if callable(save):
            save()
    except Exception:
        # An unwritable config leaves the previous hub-open mode in place.
        pass
    return nxt


def resolve_home_target(target: str, state: object | None = None) -> str:
    """Map a catalog target to a concrete address (``file://.`` → live file root)."""
    if target == "stream":
        return "stream"
    if target == "history://":
        return "history://"
    if target == "file://.":
        from xlii.tui.dock_surface import file_dock_root_address

        return file_dock_root_address(state)
    return target


class HomePane:
    """Sectioned list of every panel; primary action opens the chosen scheme."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="home")
        self._sel: int = 0
        self._block: str = ""
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def set_block(self, pane_id: str) -> None:
        """Pane id occupying the other slot — that hub row is unavailable."""
        self._block = (pane_id or "").strip()

    def _blocked(self, entry) -> bool:
        return bool(self._block) and entry.pane == self._block

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._sel = 0
        from xlii.panes.select import select_target

        target = select_target(select, self._address)
        if target:
            for i, entry in enumerate(HOME_CATALOG):
                if entry.slug == target or select == f"home://{entry.slug}":
                    self._sel = i
                    break

    def render(self) -> Rendered:
        rows: list[RenderedRow] = []
        mode = hub_open_mode()
        where = "this panel" if mode == HUB_OPEN_THIS else "other panel"
        rows.append(RenderedRow(
            text=f"open · {where}",
            address="key:cycle-open",
            kind=_CAPTION,
        ))
        section = ""
        for i, entry in enumerate(HOME_CATALOG):
            if entry.section and entry.section != section:
                section = entry.section
                rows.append(RenderedRow(
                    text=f"── {section} ──",
                    address="",
                    kind=_CAPTION,
                ))
            blocked = self._blocked(entry)
            hint = "already showing" if blocked else entry.hint
            rows.append(RenderedRow(
                text=f"{entry.label}  · {hint}" if hint else entry.label,
                address=f"home://{entry.slug}",
                kind="leaf",
                selected=(i == self._sel),
                tone="empty" if blocked else "",
            ))
        return Rendered(title="home://", rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        if not HOME_CATALOG:
            return Selection(node=None)
        entry = HOME_CATALOG[self._sel]
        return Selection(
            node=Node(
                address=f"home://{entry.slug}",
                name=entry.label,
                kind="leaf",
                extra={"type": "home", "target": entry.target,
                       "pane": entry.pane, "blocked": self._blocked(entry)},
            )
        )

    def actions(self) -> list[Action]:
        if not HOME_CATALOG:
            return []
        entry = HOME_CATALOG[self._sel]
        if self._blocked(entry):
            return []
        dest = resolve_home_target(entry.target)
        kind = RETARGET_SLOT if hub_open_mode() == HUB_OPEN_OTHER else NAVIGATE
        label = f"Open {entry.label}"
        if kind == RETARGET_SLOT:
            label = f"Open {entry.label} in other panel"
        return [
            Action("open", label, Outcome(kind, dest)),
        ]

    def handle(self, key: str) -> bool:
        if key == "back":
            return True  # I2: home ‹ is a no-op (✕ undocks)
        if key in ("cycle-open", "open-mode"):
            cycle_hub_open()
            return True
        if not HOME_CATALOG:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(HOME_CATALOG) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(HOME_CATALOG) - 1
            return True
        return False

    def select_index(self, i: int) -> bool:
        if 0 <= i < len(HOME_CATALOG):
            self._sel = i
            return True
        return False
