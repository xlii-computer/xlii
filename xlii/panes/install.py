"""``InstallPane`` — Xlii → Install node form."""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import PREFILL, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection


class InstallPane:
    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="install")
        self._rows: list[tuple[str, str]] = []
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

    def _reload(self) -> None:
        from xlii.install_form import form_spec

        spec = form_spec()
        self._spec = spec
        rows: list[tuple[str, str]] = [("(stamp a node)", "xlii node setup ")]
        for n in spec.get("nodes") or []:
            rows.append((f"node · {n}", "xlii fabric nodes"))
        self._rows = rows
        self._sel = min(self._sel, max(0, len(rows) - 1))

    def render(self) -> Rendered:
        self._reload()
        painted = [
            RenderedRow(text=label, address=f"install://{i}", kind="leaf",
                        selected=(i == self._sel))
            for i, (label, _seed) in enumerate(self._rows)
        ]
        return Rendered(
            title="install:// — stamp a node",
            rows=tuple(painted),
            empty=False,
            form=getattr(self, "_spec", None),
        )

    def selection(self) -> Selection:
        return Selection(node=Node(
            address="install://", name="install", kind="container",
            extra={"type": "install"},
        ))

    def actions(self) -> list[Action]:
        seed = self._rows[self._sel][1] if self._rows else "xlii node setup "
        return [
            Action("seed", "Seed", Outcome(PREFILL, "install://", text=seed)),
            Action("jids", "JIDs", Outcome(RETARGET_SLOT, address="jidmake://")),
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
