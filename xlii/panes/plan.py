"""``PlanPane`` — the project's plans over ``plan://``, progress at a glance.

One row per plan file (``.xlii/plans/*.md``): name, age, ``checked/total``
progress, and a pending-amendment count when the implementer has queued
proposals. The working file (``current``) always sorts first. Every action
**PREFILLs a ``/plan …`` command** into the input (review-before-run) — the
pane mutates nothing; the write ops live behind plan_check / plan_amend /
plan mode. Reads through the ambient session (:mod:`xlii.active_session`),
so the list stays live as the plan evolves.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import PREFILL, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection


def _ago(mtime: float) -> str:
    import time

    secs = max(0.0, time.time() - mtime)
    if secs < 90:
        return "just now"
    if secs < 5400:
        return f"{int(round(secs / 60))} min ago"
    if secs < 129600:  # 36h
        return f"{int(round(secs / 3600))} h ago"
    return f"{int(secs // 86400)} d ago"


class PlanPane:
    """A read-only plan list; every action seeds a ``/plan …`` command."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="plan")
        # (name, checked, total, amendments, mtime) per plan, current first.
        self._plans: list[tuple[str, int, int, int, float]] = []
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        self._address = address if isinstance(address, Address) else Address.parse(address)
        self._plans = self._scan()
        self._sel = 0
        from xlii.panes.select import select_target

        target = select_target(select, self._address)
        if target:
            for i, row in enumerate(self._plans):
                if row[0] == target or select == f"plan://{row[0]}":
                    self._sel = i
                    break

    @staticmethod
    def _scan() -> "list[tuple[str, int, int, int, float]]":
        from xlii.addressing.builtins.plan import PlanProvider
        from xlii.plan_ops import pending_amendments, plan_progress

        out: list[tuple[str, int, int, int, float]] = []
        for name, path in PlanProvider()._plan_files():
            try:
                text = path.read_text(errors="replace")
                mtime = path.stat().st_mtime
            except OSError:
                continue
            checked, total = plan_progress(text)
            out.append((name, checked, total, len(pending_amendments(text)), mtime))
        return out

    def render(self) -> Rendered:
        rows = []
        for i, (name, checked, total, amendments, mtime) in enumerate(self._plans):
            bits = [name, f"{checked}/{total}"]
            if amendments:
                bits.append(f"✎{amendments}")
            bits.append(_ago(mtime))
            # Tone precedence: the implementer's queued voice outranks progress;
            # then all-checked green, in-progress yellow, empty dim.
            if amendments:
                tone = "amended"
            elif total and checked == total:
                tone = "added"
            elif total:
                tone = "modified"
            else:
                tone = "empty"
            rows.append(RenderedRow(
                text="  ".join(bits), address=f"plan://{name}", kind="leaf",
                selected=(i == self._sel), accent=bool(amendments), tone=tone,
            ))
        return Rendered(title="plan://", rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        if not self._plans:
            return Selection(node=None)
        name = self._plans[self._sel][0]
        return Selection(node=Node(address=f"plan://{name}", name=name, kind="leaf",
                                   extra={"type": "plan"}))

    def actions(self) -> "list[Action]":
        """show / check / amend / view (+ promote on the working file) — ALL
        PREFILL ``/plan …`` commands (review-before-run); nothing runs or
        writes from the pane."""
        if not self._plans:
            return []
        import shlex

        name = self._plans[self._sel][0]
        addr = f"plan://{name}"
        # The planner's write_file has no name grammar, so a plan name may
        # carry whitespace — quote it so the PREFILLed /plan command (which
        # shlex-tokenizes its args) round-trips as one token.
        q = shlex.quote(name) if any(ch.isspace() for ch in name) else name
        # Ops default to current.md, so only a NAMED plan needs the flag.
        scope = "" if name == "current" else f"--plan {q} "
        acts = [
            Action("show", "Show plan", Outcome(PREFILL, addr, text=f"/plan show {q}")),
            Action("check", "Check item", Outcome(PREFILL, addr, text=f"/plan check {scope}")),
            Action("amend", "Propose amendment", Outcome(PREFILL, addr, text=f"/plan amend {scope}")),
        ]
        if name == "current":
            acts.append(Action("promote", "Promote to named plan",
                               Outcome(PREFILL, addr, text="/plan save ")))
        acts.append(Action("view", "Open items", Outcome(RETARGET_SLOT, addr)))
        return acts

    def handle(self, key: str) -> bool:
        if not self._plans:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._plans) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(self._plans) - 1
            return True
        return False  # enter/back fall through to the surface (actions / close)

    def select_index(self, i: int) -> bool:
        """Select the plan at index ``i`` (a mouse click's target row)."""
        if 0 <= i < len(self._plans):
            self._sel = i
            return True
        return False
