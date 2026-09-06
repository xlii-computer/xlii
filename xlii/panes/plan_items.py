"""``PlanItemsPane`` — ONE plan's checkbox items over ``plan://<name>`` (plan-surface T1).

The proposal's point: the plan file IS the todo list — structured checkboxes with
ids, receipts on check, an amendments queue — so the panel renders *items*, not a
raw file. Rows: ☐ open · ☑ receipted · ☑? checked-without-receipt (each showing its
receipt when it has one), then the pending ``[?]`` amendment queue. Actions follow
the house contract — every mutation **PREFILLs a ``/plan …`` command** into the
input (review-before-run): check an open item, upgrade an unreceipted one, contest
via amend; the raw file stays one action away. Works identically whichever brain
wrote the plan (a gig-planned plan is indistinguishable downstream).

Pure projection of ``(address, selection)``: mounted from the plan name, re-read on
every mount; the TUI's plan listener (:func:`xlii.plan_ops.set_plan_listener`)
re-mounts it live as checks/amends/rewrites land.
"""

from __future__ import annotations

import shlex
from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import PREFILL, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection

_STATE_ICON = {" ": "☐", "x": "☑", "x?": "☑?"}
_STATE_TONE = {" ": "", "x": "added", "x?": "modified"}


class PlanItemsPane:
    """Item-level view of one plan; every write action seeds ``/plan …``."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="plan")
        self._name: str = ""
        self._path = None                     # Path | None — the plan file
        self._rows: "list[dict]" = []         # selectable rows, display order
        self._checked = self._total = 0
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._name = self._address.key.strip()
        self._load()
        self._sel = 0
        target = select or self._address.anchor.strip()
        if target:
            for i, r in enumerate(self._rows):
                if target in (r["id"], r["address"]) or select == r["address"]:
                    self._sel = i
                    break

    def _load(self) -> None:
        from xlii.addressing.builtins.plan import PlanProvider
        from xlii.plan_ops import pending_amendments, plan_items

        self._rows, self._path = [], None
        self._checked = self._total = 0
        path = dict(PlanProvider()._plan_files()).get(self._name)
        if path is None:
            return
        self._path = path
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return
        for it in plan_items(text):
            self._total += 1
            if it.state in ("x", "x?"):
                self._checked += 1
            label = f"{_STATE_ICON[it.state]}  {it.text}" if it.text else f"{_STATE_ICON[it.state]}  {{#{it.item_id}}}"
            if it.receipt:
                label += f"  · receipt: {it.receipt}"
            self._rows.append({
                "kind": "item", "id": it.item_id, "state": it.state,
                "address": f"plan://{self._name}#{it.item_id}" if it.item_id else f"plan://{self._name}",
                "text": label, "tone": _STATE_TONE[it.state],
            })
        for am_id, line in pending_amendments(text):
            self._rows.append({
                "kind": "amendment", "id": am_id, "state": "?",
                "address": f"plan://{self._name}#{am_id}",
                "text": line.strip(), "tone": "amended",
            })

    # -- rendering -------------------------------------------------------------

    @property
    def _title(self) -> str:
        n_am = sum(1 for r in self._rows if r["kind"] == "amendment")
        bits = [f"plan://{self._name}", f"{self._checked}/{self._total}"]
        if n_am:
            bits.append(f"✎{n_am}")
        return "  ·  ".join(bits)

    def render(self) -> Rendered:
        if self._path is None:
            return Rendered(title=f"plan://{self._name} (missing)", rows=(), empty=True)
        rows: "list[RenderedRow]" = []
        fi = 0
        items = [r for r in self._rows if r["kind"] == "item"]
        amends = [r for r in self._rows if r["kind"] == "amendment"]
        rows.append(RenderedRow(
            text=f"─ Items ({self._checked}/{self._total}) ", address="", kind="caption"))
        if not items:
            rows.append(RenderedRow(
                text="(no {#id} checkboxes — the planner marks actionable items)",
                address="", kind="caption"))
        for r in items:
            rows.append(RenderedRow(text=r["text"], address=r["address"], kind="leaf",
                                    selected=(fi == self._sel), tone=r["tone"],
                                    accent=(r["state"] == " ")))
            fi += 1
        if amends:
            rows.append(RenderedRow(text=f"─ Amendments ({len(amends)}) ", address="", kind="caption"))
            for r in amends:
                rows.append(RenderedRow(text=r["text"], address=r["address"], kind="leaf",
                                        selected=(fi == self._sel), tone=r["tone"]))
                fi += 1
        return Rendered(title=self._title, rows=tuple(rows), empty=False)

    def selection(self) -> Selection:
        if not self._rows:
            return Selection(node=None)
        r = self._rows[self._sel]
        return Selection(node=Node(
            address=r["address"], name=r["id"] or self._name, kind="leaf",
            extra={"type": "plan-item" if r["kind"] == "item" else "plan-amendment",
                   "state": r["state"]}))

    # -- actions -----------------------------------------------------------------

    def _scope(self) -> str:
        if self._name == "current":
            return ""
        q = shlex.quote(self._name) if any(ch.isspace() for ch in self._name) else self._name
        return f"--plan {q} "

    def actions(self) -> "list[Action]":
        """State-shaped, all PREFILL: open → check; ``[x?]`` → add the receipt;
        any id → contest via amend; amendment rows → resolve in plan mode. The
        raw file is always one action away. Nothing writes from the pane."""
        acts: "list[Action]" = []
        scope = self._scope()
        if self._rows:
            r = self._rows[self._sel]
            addr = r["address"]
            if r["kind"] == "item" and r["id"]:
                if r["state"] == " ":
                    acts.append(Action("check", "Check…", Outcome(
                        PREFILL, addr, text=f"/plan check {r['id']} {scope}")))
                elif r["state"] == "x?":
                    acts.append(Action("receipt", "Add receipt…", Outcome(
                        PREFILL, addr, text=f"/plan check {r['id']} {scope}--receipt ")))
                acts.append(Action("amend", "Contest (amend)…", Outcome(
                    PREFILL, addr, text=f"/plan amend --re {r['id']} {scope}")))
            elif r["kind"] == "amendment":
                acts.append(Action("resolve", "Resolve (plan mode)", Outcome(
                    PREFILL, addr, text="/plan")))
        if self._path is not None:
            acts.append(Action("view", "View raw file", Outcome(
                RETARGET_SLOT, f"file://{self._path}")))
        return acts

    # -- input -------------------------------------------------------------------

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
        return False  # enter/back fall through to the surface (actions / close)

    def select_index(self, i: int) -> bool:
        """Select the row at index ``i`` (a mouse click's target). Returns True if in range."""
        if 0 <= i < len(self._rows):
            self._sel = i
            return True
        return False
