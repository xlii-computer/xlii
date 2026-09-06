"""``FarmPane`` — the house classifieds board over ``farm://``.

``jobs://`` is the session's background-job chip (F3). This pane is the
farm: ads on the front, node beacons on the flip. Pools are rooms.

Throne-only controls (post / cancel / bench / evict) are ``PREFILL``
seeds — review-before-run. Non-owner panes have no control buttons.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.farm_view import FarmView, ambient_farm_view
from xlii.panes import PREFILL, NAVIGATE, Action, Outcome, Rendered, RenderedRow, Selection


def _pool_label(pool: str) -> str:
    if not pool or pool == "file":
        return "file"
    if "@" in pool:
        return pool.split("@", 1)[0]
    return pool


class FarmPane:
    """Ads (front) / nodes (flip). Control buttons only when ``view.is_owner``."""

    def __init__(
        self,
        address: "str | Address | None" = None,
        *,
        view: Optional[FarmView] = None,
    ) -> None:
        self._address: Address = Address(scheme="farm")
        self._view: Optional[FarmView] = view
        self._sel: int = 0
        self._rows: list[dict] = []
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def _face(self) -> str:
        key = self._address.key.strip()
        return "nodes" if key == "nodes" else "ads"

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._load()
        self._sel = 0
        if select:
            for i, r in enumerate(self._rows):
                if r.get("address") == select or r.get("id") == select:
                    self._sel = i
                    break

    def _load(self) -> None:
        view = self._view if self._view is not None else ambient_farm_view()
        self._cached = view
        self._rows = []
        if self._face() == "nodes":
            for n in view.nodes:
                self._rows.append({
                    "kind": "node",
                    "id": n.node,
                    "pool": n.pool,
                    "address": f"farm://nodes/{n.node}",
                    "text": (
                        f"{n.node:<14} {_pool_label(n.pool):<10} "
                        f"{n.offers or '—':<12} {n.gig or '—':<8} "
                        f"{n.allowance or '—':<12} {n.busy}"
                    ),
                })
        else:
            for a in view.ads:
                task = a.task
                if len(task) > 40:
                    task = task[:37] + "…"
                self._rows.append({
                    "kind": "ad",
                    "id": a.ticket_id,
                    "pool": a.pool,
                    "address": f"farm://{a.ticket_id}",
                    "text": (
                        f"{a.ticket_id[:8]}  {_pool_label(a.pool):<10} "
                        f"{a.state:<9} {a.job:<8} {a.poster:<10} "
                        f"{a.budget:<10} {a.claimant or '—':<10} {a.age}  {task}"
                    ),
                })

    def render(self) -> Rendered:
        self._load()
        view = self._cached
        face = self._face()
        title = "farm://nodes" if face == "nodes" else "farm://"
        rows: list[RenderedRow] = []
        if not view.pools and not self._rows:
            return Rendered(
                title=title,
                empty=True,
                rows=(RenderedRow(
                    text="no rooms — a ticket in a pool you are not in is absent",
                    address="", kind="caption",
                ),),
            )
        shown: set[str] = set()
        fi = 0
        grouped = self._rows
        by_pool: dict[str, list[dict]] = {}
        for r in grouped:
            by_pool.setdefault(r["pool"], []).append(r)
        order = [p for p in view.pools if p in by_pool] + [
            p for p in by_pool if p not in view.pools
        ]
        section = "nodes" if face == "nodes" else "ads"
        for pool in order:
            items = by_pool[pool]
            rows.append(RenderedRow(
                text=f"─ {_pool_label(pool)} · {section} ",
                address="", kind="caption",
            ))
            shown.add(pool)
            for r in items:
                rows.append(RenderedRow(
                    text=r["text"], address=r["address"], kind="leaf",
                    selected=(fi == self._sel),
                ))
                fi += 1
        for pool in view.pools:
            if pool not in shown:
                rows.append(RenderedRow(
                    text=f"─ {_pool_label(pool)} · {section} (none) ",
                    address="", kind="caption",
                ))
        return Rendered(title=title, rows=tuple(rows), empty=False)

    def selection(self) -> Selection:
        if not self._rows:
            return Selection(node=None)
        r = self._rows[self._sel]
        return Selection(node=Node(
            address=r["address"], name=r["id"], kind="leaf",
            extra={"type": r["kind"], "pool": r["pool"]},
        ))

    def actions(self) -> "list[Action]":
        view = getattr(self, "_cached", None) or (
            self._view if self._view is not None else ambient_farm_view()
        )
        face = self._face()
        acts: list[Action] = []
        if face == "nodes":
            acts.append(Action("flip", "Ads", Outcome(NAVIGATE, "farm://")))
        else:
            acts.append(Action("flip", "Nodes", Outcome(NAVIGATE, "farm://nodes")))
        if not view.is_owner:
            return acts
        if face == "ads":
            acts.append(Action(
                "post", "Post to pool",
                Outcome(PREFILL, "farm://", text="xlii job post --job explore --task "),
            ))
            if self._rows:
                r = self._rows[self._sel]
                acts.append(Action(
                    "cancel", "Cancel ticket",
                    Outcome(PREFILL, r["address"], text=f"xlii job cancel {r['id']}"),
                ))
        else:
            if self._rows:
                r = self._rows[self._sel]
                acts.append(Action(
                    "bench", "Bench node",
                    Outcome(PREFILL, r["address"], text=f"xlii job bench {r['id']}"),
                ))
                acts.append(Action(
                    "evict", "Evict node (enforced)",
                    Outcome(PREFILL, r["address"], text=f"xlii job evict {r['id']}"),
                ))
        return acts

    def handle(self, key: str) -> bool:
        if not self._rows:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._rows) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(self._rows) - 1
            return True
        return False
