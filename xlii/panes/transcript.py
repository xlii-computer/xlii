"""``TranscriptPane`` — the dialogue surface as a projection of a ``conv://`` conversation.

The design doc: *"The transcript pane is just where the dialogue piles up."* In the kernel model
that makes it a pure projection like any other pane — of the **conversation address space**
(``conv://``, the turns VFS already shipped). It mounts ``conv://.``, lists the turn leaves, and
renders one selectable summary row per turn. There is no second conversation model: a turn leaf
*is* a ``conv://`` address, so the existing :class:`~xlii.panes.view.ViewPane` opens the full
turn for free (the transcript's "view" action just retargets the other slot to that address).

Where a turn comes *from* is the kernel's job, not the pane's. The transcript offers a "re-ask"
action whose :data:`~xlii.panes.ENQUEUE_TURN` outcome the Dock routes to its
:class:`~xlii.panes.TurnSink`; the sink runs the turn and appends to the conversation, which this
pane re-projects on :meth:`refresh`. That keeps the state-ownership rule intact — the transcript
owns only ``(address, selection)``; the turns live in the VFS, the turn-owner is the kernel.

Phase 6: ``mount``/``refresh`` use an mtime-keyed parse cache (immutable timestamped leaves);
``__rescan__`` / new turn files invalidate via mtime or address set change. Live in-flight rows
come from the ``conv://`` ``__inflight__.md`` Node when present, else a synthetic fallback from
``live_conv``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from xlii.addressing import Address, Node, supports_vfs, vfs_list, vfs_read
from xlii.conversation import INFLIGHT_LEAF
from xlii.panes import (
    ENQUEUE_TURN,
    RETARGET_SLOT,
    Action,
    Outcome,
    Rendered,
    RenderedRow,
    Selection,
)
from xlii.transcript import Turn, parse_turn_markdown

# The live Conversation appears only in annotations — the pane duck-types
# has_in_flight()/in_flight at runtime, so no runtime import is needed.
if TYPE_CHECKING:  # pragma: no cover
    from xlii.conversation import Conversation


def _summary(turn: Optional[Turn], node: Node) -> str:
    """One-line digest of a turn for the transcript list: timestamp + the start of the user
    message. Falls back to the node name when the file doesn't parse as a turn."""
    if turn is None:
        return node.name
    first = turn.user.splitlines()[0] if turn.user else ""
    if len(first) > 72:
        first = first[:71] + "…"
    stamp = turn.timestamp or node.name
    if node.extra.get("live"):
        preview = (turn.assistant or "(thinking...)")[:60]
        if len(turn.assistant or "") > 60:
            preview += "…"
        return f"▶ {first or stamp} → {preview}"
    return f"{stamp}  ▸ {first}" if first else stamp


class TranscriptPane:
    """A conversation pane: ``(conv address, selected turn) → rendered turn summaries``."""

    def __init__(self, address: "str | Address | None" = None, *, live_conv: "Conversation | None" = None) -> None:
        self._address: Address = Address(scheme="")
        self._nodes: tuple[Node, ...] = ()
        self._turns: dict[str, Optional[Turn]] = {}  # node address → parsed turn
        # Immutable leaf cache: address → (mtime_ns | None, Turn | None).
        # Live / missing-mtime entries are never reused across refreshes.
        self._parse_cache: dict[str, tuple[Optional[int], Optional[Turn]]] = {}
        self._selected: str = ""  # selected turn's address, "" when the conversation is empty
        self._live_conv: "Conversation | None" = live_conv
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        """Bind to a ``conv://`` conversation and project its turns. ``select`` (a turn address)
        restores a prior selection — the reconstruct hook; the default is the newest turn."""
        addr = address if isinstance(address, Address) else Address.parse(address)
        if not supports_vfs(addr.scheme):
            raise NotImplementedError(f"{addr.scheme or 'no'}:// is not browseable")
        self._address = addr
        # turn files are leaves sorted by their timestamped name → chronological order
        self._nodes = tuple(n for n in vfs_list(addr) if n.kind == "leaf")
        self._turns = {n.address: self._parse_leaf(n) for n in self._nodes}
        # Drop cache entries for addresses that left the conversation.
        alive = set(self._turns)
        self._parse_cache = {k: v for k, v in self._parse_cache.items() if k in alive}
        self._selected = select if (select and self._index_of(select) is not None) else self._newest()

    def refresh(self) -> None:
        """Re-list the conversation (picks up a turn the sink just appended), keeping the
        selection if it survives, else snapping to the newest turn.
        Also picks up in-flight from live_conv / ConvProvider when present."""
        prior = self._selected
        self.mount(self._address, select=prior)

    def render(self) -> Rendered:
        rows = [
            RenderedRow(
                text=_summary(self._turns.get(n.address), n),
                address=n.address,
                kind="turn",
                selected=(n.address == self._selected),
                accent=bool(n.extra.get("live")),
            )
            for n in self._nodes
        ]
        # Fallback when the pane has live_conv but ConvProvider didn't list a
        # real Node (unmounted tests / no ambient session).
        has_live_node = any(n.extra.get("live") for n in self._nodes)
        if (
            not has_live_node
            and self._live_conv is not None
            and self._live_conv.has_in_flight()
        ):
            infl = self._live_conv.in_flight
            if infl:
                preview = (infl.assistant_so_far or "(thinking...)")[:60]
                if len(infl.assistant_so_far or "") > 60:
                    preview += "…"
                live_text = f"▶ {infl.user[:40]} → {preview}"
                live_addr = f"{self._address}#inflight"
                rows.append(
                    RenderedRow(
                        text=live_text,
                        address=live_addr,
                        kind="turn",
                        selected=(self._selected == live_addr),
                        accent=True,
                    )
                )
        return Rendered(title=str(self._address), rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        return Selection(node=self._focused())

    def actions(self) -> "list[Action]":
        node = self._focused()
        if node is None:
            return []
        if node.extra.get("live") or node.name == INFLIGHT_LEAF:
            # In-flight: view only (no re-ask of a partial turn).
            return [Action("view", "View turn", Outcome(RETARGET_SLOT, node.address))]
        turn = self._turns.get(node.address)
        acts = [Action("view", "View turn", Outcome(RETARGET_SLOT, node.address))]
        if turn is not None and turn.user:
            # re-ask the same question — the Dock routes ENQUEUE_TURN to the turn sink
            acts.append(Action("re-ask", "Re-ask", Outcome(ENQUEUE_TURN, node.address, text=turn.user)))
        return acts

    def handle(self, key: str) -> bool:
        if key in ("down", "up", "home", "end"):
            return self._move(key)
        # Enter on a turn isn't local nav — the surface falls through to the primary action (view)
        return False

    # --- internals -----------------------------------------------------------

    def _parse_leaf(self, node: Node) -> Optional[Turn]:
        """Parse a turn leaf, reusing the mtime-keyed cache for committed files."""
        if node.extra.get("live") or node.name == INFLIGHT_LEAF:
            # Always rebuild from live Conversation when available.
            if self._live_conv is not None and self._live_conv.has_in_flight():
                infl = self._live_conv.in_flight
                assert infl is not None
                return Turn(
                    timestamp=infl.started_at or "in-flight",
                    user=infl.user,
                    assistant=infl.assistant_so_far,
                )
            try:
                return parse_turn_markdown(
                    vfs_read(node.address).decode("utf-8", "replace"),
                    fallback_ts=node.name,
                )
            except Exception:
                return None

        mtime = node.extra.get("mtime_ns")
        cached = self._parse_cache.get(node.address)
        if cached is not None and mtime is not None and cached[0] == mtime:
            return cached[1]
        try:
            turn = parse_turn_markdown(
                vfs_read(node.address).decode("utf-8", "replace"),
                fallback_ts=node.name,
            )
        except Exception:
            turn = None
        if mtime is not None:
            self._parse_cache[node.address] = (mtime, turn)
        return turn

    def _focused(self) -> Optional[Node]:
        i = self._index_of(self._selected)
        return self._nodes[i] if i is not None else None

    def _index_of(self, node_address: str) -> Optional[int]:
        for i, n in enumerate(self._nodes):
            if n.address == node_address:
                return i
        return None

    def _newest(self) -> str:
        return self._nodes[-1].address if self._nodes else ""

    def _move(self, key: str) -> bool:
        if not self._nodes:
            return False
        i = self._index_of(self._selected)
        i = (len(self._nodes) - 1) if i is None else i
        if key == "down":
            i = min(i + 1, len(self._nodes) - 1)
        elif key == "up":
            i = max(i - 1, 0)
        elif key == "home":
            i = 0
        elif key == "end":
            i = len(self._nodes) - 1
        self._selected = self._nodes[i].address
        return True
