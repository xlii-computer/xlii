"""``MarketPane`` — read-only offers wall over ``market://``.

Beacons + rep. Never tickets, never payment, never a negotiation surface.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.farm_market import MarketWallRow
from xlii.panes import Rendered, RenderedRow, Selection


class MarketPane:
    def __init__(
        self,
        address: "str | Address | None" = None,
        *,
        rows: tuple[MarketWallRow, ...] = (),
    ) -> None:
        self._address: Address = Address(scheme="market")
        self._rows: tuple[MarketWallRow, ...] = rows
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._sel = 0

    def render(self) -> Rendered:
        if not self._rows:
            return Rendered(
                title="market://",
                empty=True,
                rows=(RenderedRow(
                    text="offers wall — beacons only; agree off-venue, then invite",
                    address="", kind="caption",
                ),),
            )
        rows = [
            RenderedRow(text="─ offers  fingerprint · offers · gig · rate · rep ",
                        address="", kind="caption"),
        ]
        for i, r in enumerate(self._rows):
            fp = r.fingerprint if len(r.fingerprint) <= 16 else r.fingerprint[:12] + "…"
            text = (
                f"{fp:<16} {r.offers or '—':<16} {r.gig or '—':<8} "
                f"{r.rate or '—':<10} "
                f"ok={r.accepted} no={r.disputed} gone={r.abandoned}"
            )
            rows.append(RenderedRow(
                text=text, address=f"market://{r.fingerprint}", kind="leaf",
                selected=(i == self._sel),
            ))
        return Rendered(title="market://", rows=tuple(rows), empty=False)

    def selection(self) -> Selection:
        if not self._rows:
            return Selection(node=None)
        r = self._rows[self._sel]
        return Selection(node=Node(
            address=f"market://{r.fingerprint}", name=r.fingerprint, kind="leaf",
        ))

    def actions(self) -> list:
        return []  # read-only wall

    def handle(self, key: str) -> bool:
        if not self._rows:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._rows) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        return False
