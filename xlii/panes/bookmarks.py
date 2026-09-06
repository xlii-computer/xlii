"""``BookmarksPane`` — the global bookmark library over ``mark://``.

Lists every bookmark across all personas + the active store, each row showing its **source persona**
(provenance). Bookmarks carry BOTH verbs (campaign Decision #1 — they are different jobs):

* **attach** (the 3-action-grammar primary) — the mark's recall span rides the next turn as a
  ``mark:`` doc, like a skill (wired through :mod:`xlii.attach`; the green ● dot follows);
* **recall-paste** — ``/recall <mark>`` seeded into the command line: the SANCTIONED prefill
  exception (Fleet rule 4), review-before-run content the user inspects before running.

Bookmarks are one global namespace: the persona is *shown* but never typed, folded into a
``<persona>:<mark>`` qualifier (attach address or ``/recall``) only when a name lives in more
than one place. Reads the library through the ambient session (:mod:`xlii.active_session`).
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import ATTACH, DETACH, PREFILL, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection


class BookmarksPane:
    """A provenance-tagged list of bookmarks: attach rides the turn; load seeds ``/recall``."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="mark")
        self._marks: list = []   # (name, persona, addressable, ts)
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        from xlii.active_session import active_session
        from xlii.repl_cmds.chat import global_bookmarks

        self._address = address if isinstance(address, Address) else Address.parse(address)
        state = active_session()
        marks = global_bookmarks(state) if state is not None else []
        self._marks = sorted(marks, key=lambda m: (m[0].lower(), m[1].lower()))
        self._sel = 0
        from xlii.panes.select import select_target

        target = select_target(select, self._address)
        if target:
            for i, (name, _persona, _a, _ts) in enumerate(self._marks):
                if name == target or select == f"mark://{name}":
                    self._sel = i
                    break

    def render(self) -> Rendered:
        riding = self._riding_names()
        rows = [
            RenderedRow(
                text=f"{name}  · from {persona}" + (
                    "  · gone" if self._mark_is_gone(name, persona, addressable) else ""
                ),
                address=self._attach_address(name, persona, addressable),
                kind="leaf",
                selected=(i == self._sel),
                accent=(name in riding),
            )
            for i, (name, persona, addressable, _ts) in enumerate(self._marks)
        ]
        return Rendered(title="mark://", rows=tuple(rows), empty=not rows)

    def selection(self) -> Selection:
        if not self._marks:
            return Selection(node=None)
        name, persona, _a, _ts = self._marks[self._sel]
        return Selection(node=Node(address=f"mark://{name}", name=name, kind="leaf",
                                   extra={"type": "mark", "persona": persona}))

    def actions(self) -> "list[Action]":
        """attach / load / detach over the selected bookmark — the 3-action grammar.

        ``attach`` (first = the Enter default) rides the mark's recall span on the next turn as a
        ``mark:`` doc; ``load`` is the adjudicated recall-paste — seed ``/recall <mark>`` into the
        command line for review-before-run (bare name when unique; ``<persona>:<mark>`` only on a
        clash); ``detach`` unrides it (the green dot follows)."""
        if not self._marks:
            return []
        name, persona, addressable, _ts = self._marks[self._sel]
        addr = self._attach_address(name, persona, addressable)
        return [
            Action("attach", "Attach to next turn", Outcome(ATTACH, addr)),
            Action("load", "Load into command line",
                   Outcome(PREFILL, f"mark://{name}", text=self._recall_command(name, persona, addressable))),
            Action("view", "View",
                   Outcome(RETARGET_SLOT, address=addr)),
            Action("detach", "Detach", Outcome(DETACH, addr)),
        ]

    @staticmethod
    def _mark_is_gone(name: str, persona: str, addressable: bool) -> bool:
        """True when the persona island is missing or the turn span is gone."""
        from xlii.persona import Persona
        from xlii.transcript import get_marked_span, list_marks

        from xlii.persona import CHAT_STATE_DIR

        if not Persona(persona).exists():
            orphan = CHAT_STATE_DIR / persona / "turns"
            if addressable or orphan.is_dir():
                return True
        from xlii.active_session import active_session
        from xlii.repl_cmds.chat import _all_mark_stores

        state = active_session()
        if state is None:
            return False
        for label, td, _addr in _all_mark_stores(state):
            if label != persona:
                continue
            if name not in {n for n, _ in list_marks(td)}:
                return True
            return get_marked_span(td, name) is None
        return True

    def _attach_address(self, name: str, persona: str, addressable: bool) -> str:
        """The address ATTACH/DETACH act on: bare when the name is unique in the library,
        persona-qualified on a clash (the one flat-namespace rule, same as ``/recall``)."""
        clashes = sum(1 for m in self._marks if m[0] == name)
        if addressable and clashes > 1:
            return f"mark://{persona}:{name}"
        return f"mark://{name}"

    def _recall_command(self, name: str, persona: str, addressable: bool) -> str:
        clashes = sum(1 for m in self._marks if m[0] == name)
        if addressable and clashes > 1:
            return f"/recall {persona}:{name}"
        return f"/recall {name}"

    @staticmethod
    def _riding_names() -> "set[str]":
        """Bare names of bookmarks currently riding the session (the ``mark:`` /doc channel)."""
        from xlii.active_session import active_session
        from xlii.attach import MARK_ATTACH_PREFIX

        docs = getattr(active_session(), "attached_docs", None) or []
        return {n[len(MARK_ATTACH_PREFIX):] for n, _ in docs
                if isinstance(n, str) and n.startswith(MARK_ATTACH_PREFIX)}

    def handle(self, key: str) -> bool:
        if not self._marks:
            return False
        if key == "down":
            self._sel = min(self._sel + 1, len(self._marks) - 1)
            return True
        if key == "up":
            self._sel = max(self._sel - 1, 0)
            return True
        if key == "home":
            self._sel = 0
            return True
        if key == "end":
            self._sel = len(self._marks) - 1
            return True
        return False  # enter/back fall through to the surface (actions / close)

    def select_index(self, i: int) -> bool:
        """Select the bookmark at index ``i`` (a mouse click's target row). Returns True if in range."""
        if 0 <= i < len(self._marks):
            self._sel = i
            return True
        return False
