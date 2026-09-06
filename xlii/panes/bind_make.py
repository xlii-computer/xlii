"""``BindMakePane`` — picker for task → menu / F-key binds.

Face paints ``rendered.form.html`` (closed srcdoc). TUI lists current binds
and seeds ``/bind``.
"""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import PREFILL, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection


class BindMakePane:
    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="bindmake")
        self._rows: list[tuple[str, str]] = []  # (label, seed)
        self._sel: int = 0
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        del select
        self._address = address if isinstance(address, Address) else Address.parse(str(address))
        self._reload()

    def _xli(self):
        from xlii.active_session import active_xli_dir

        return active_xli_dir()

    def _reload(self) -> None:
        from xlii.bind_form import form_spec

        spec = form_spec(self._xli())
        self._spec = spec
        rows: list[tuple[str, str]] = [("(new bind)", "/bind ")]
        for b in spec.get("binds") or []:
            bits = [b.get("task") or "?"]
            if b.get("menu"):
                bits.append(b["menu"])
            if b.get("fkey"):
                bits.append(b["fkey"])
            rows.append((" · ".join(bits), f"/bind rm {b.get('task') or ''}"))
        self._rows = rows
        self._sel = min(self._sel, max(0, len(rows) - 1))

    def render(self) -> Rendered:
        self._reload()
        painted = []
        for i, (label, _seed) in enumerate(self._rows):
            painted.append(RenderedRow(
                text=label,
                address=f"bindmake://{i}",
                kind="leaf",
                selected=(i == self._sel),
            ))
        return Rendered(
            title="bindmake:// — pin a task",
            rows=tuple(painted),
            empty=False,
            form=getattr(self, "_spec", None),
        )

    def selection(self) -> Selection:
        return Selection(node=Node(
            address="bindmake://",
            name="binds",
            kind="container",
            extra={"type": "bindmake"},
        ))

    def actions(self) -> list[Action]:
        if not self._rows:
            return []
        seed = self._rows[self._sel][1]
        return [
            Action("seed", "Seed /bind", Outcome(PREFILL, "bindmake://", text=seed)),
            Action("back", "Back", Outcome(RETARGET_SLOT, address="tasks://")),
        ]

    def handle(self, key: str) -> bool:
        if key == "down" and self._rows:
            self._sel = min(self._sel + 1, len(self._rows) - 1)
            return True
        if key == "up" and self._rows:
            self._sel = max(self._sel - 1, 0)
            return True
        return False

    def select_index(self, i: int) -> bool:
        if 0 <= i < len(self._rows):
            self._sel = i
            return True
        return False
