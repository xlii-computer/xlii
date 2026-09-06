"""``JobsPane`` — the session's background jobs over ``jobs://`` (F3).

The job board as a deck tab: one row per job (glyph · id · status · label).
**View** a job marks its strip pill seen. **Clear done** drops finished jobs
and their pills (``/jobs clear``). Cancel still seeds ``/jobs cancel <id>``.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node, vfs_list
from xlii.panes import PREFILL, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection


def _job_id(address: str) -> str:
    """The job's id out of its address (``jobs://j1`` → ``j1``) — a node's
    *name* is a display label (glyph · id · status), never an identifier."""
    return Address.parse(address).key.strip()


def _reg():
    from xlii.active_session import active_session
    from xlii.jobs import _peek_registry

    return _peek_registry(active_session())


class JobsPane:
    """A live list of background jobs; view marks seen, clear drops finished."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="jobs")
        self._nodes: list[Node] = []
        self._sel: int = 0
        self._on_clear: bool = False
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._nodes = []
        self._sel = 0
        self._on_clear = False
        keyed = self._address.key.strip()
        pick = select or (f"jobs://{keyed}" if keyed and not keyed.startswith("#") else "")
        self._load(pick)
        if keyed and not keyed.startswith("#"):
            reg = _reg()
            if reg is not None:
                reg.mark_seen(keyed)

    def _load(self, select: str = "") -> None:
        """(Re)read the live registry, keeping the selection by ADDRESS."""
        keep = select or self._selected_address()
        try:
            nodes = vfs_list("jobs://")
        except Exception:
            nodes = []
        self._nodes = nodes
        self._sel = 0
        if keep:
            wanted = _job_id(keep)
            for i, n in enumerate(nodes):
                if n.address == keep or _job_id(n.address) == wanted:
                    self._sel = i
                    break

    def _selected_address(self) -> str:
        if not self._nodes or self._sel >= len(self._nodes):
            return ""
        return self._nodes[self._sel].address

    def render(self) -> Rendered:
        self._load()
        rows = [
            RenderedRow(
                text="clear done · dismiss pills",
                address="jobs://#clear",
                kind="leaf",
                selected=self._on_clear,
                tone="knob",
            ),
        ]
        for i, n in enumerate(self._nodes):
            rows.append(RenderedRow(
                text=n.name, address=n.address, kind="leaf",
                selected=(not self._on_clear and i == self._sel),
            ))
        return Rendered(title="jobs://", rows=tuple(rows), empty=False)

    def selection(self) -> Selection:
        if self._on_clear:
            return Selection(node=Node(
                address="jobs://#clear", name="clear done", kind="leaf",
                extra={"verb": "clear"},
            ))
        if not self._nodes:
            return Selection(node=None)
        return Selection(node=self._nodes[self._sel])

    def actions(self) -> "list[Action]":
        acts = [
            Action("clear", "Clear done", Outcome(PREFILL, "jobs://", text="/jobs clear")),
        ]
        if not self._nodes or self._on_clear:
            return acts
        n = self._nodes[self._sel]
        jid = _job_id(n.address)
        if not jid:
            return acts
        acts.extend([
            Action("view", "View detail", Outcome(RETARGET_SLOT, n.address)),
            Action("cancel", "Cancel job (seed)",
                   Outcome(PREFILL, n.address, text=f"/jobs cancel {jid}")),
        ])
        return acts

    def apply_selection(self) -> bool:
        """Clear-done knob applies immediately. A job row marks its pill seen."""
        reg = _reg()
        if self._on_clear:
            if reg is None:
                return False
            reg.clear_finished()
            self._load()
            self._on_clear = False
            return True
        jid = _job_id(self._selected_address()) if self._nodes else ""
        if jid and reg is not None:
            return bool(reg.mark_seen(jid))
        return False

    def handle(self, key: str) -> bool:
        if not self._nodes:
            return False
        if key == "down":
            self._on_clear = False
            self._sel = min(self._sel + 1, len(self._nodes) - 1)
            return True
        if key == "up":
            self._on_clear = False
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._on_clear = False
            self._sel = 0
            return True
        if key == "end":
            self._on_clear = False
            self._sel = len(self._nodes) - 1
            return True
        return False

    def select_index(self, i: int) -> bool:
        # Rendered leaves: 0 = clear-done, 1… = jobs.
        if i == 0:
            self._on_clear = True
            return True
        job_i = i - 1
        if 0 <= job_i < len(self._nodes):
            self._on_clear = False
            self._sel = job_i
            self._ack_selected()
            return True
        return False

    def _ack_selected(self) -> None:
        if not self._nodes:
            return
        jid = _job_id(self._nodes[self._sel].address)
        if not jid:
            return
        reg = _reg()
        if reg is not None:
            reg.mark_seen(jid)
