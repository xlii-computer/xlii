"""``RemoteMakePane`` — Tools → Remotes form."""

from __future__ import annotations

from typing import Optional

from xlii.addressing import Address, Node
from xlii.panes import PREFILL, RETARGET_SLOT, Action, Outcome, Rendered, RenderedRow, Selection


class RemoteMakePane:
    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="remotemake")
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
        from xlii.remote_form import form_spec

        spec = form_spec()
        self._spec = spec
        rows: list[tuple[str, str]] = [("(add remote)", "/remote add ")]
        for r in spec.get("remotes") or []:
            name = r.get("id") or ""
            rows.append((f"{name} · {r.get('protocol') or ''}", f"/remote ls {name}"))
        self._rows = rows
        self._sel = min(self._sel, max(0, len(rows) - 1))

    def render(self) -> Rendered:
        self._reload()
        painted = [
            RenderedRow(text=label, address=f"remotemake://{i}", kind="leaf",
                        selected=(i == self._sel))
            for i, (label, _seed) in enumerate(self._rows)
        ]
        return Rendered(
            title="remotemake:// — add a wire",
            rows=tuple(painted),
            empty=False,
            form=getattr(self, "_spec", None),
        )

    def selection(self) -> Selection:
        return Selection(node=Node(
            address="remotemake://", name="remotes", kind="container",
            extra={"type": "remotemake"},
        ))

    def actions(self) -> list[Action]:
        seed = self._rows[self._sel][1] if self._rows else "/remote add "
        return [
            Action("seed", "Seed", Outcome(PREFILL, "remotemake://", text=seed)),
            Action("board", "Open remotes", Outcome(RETARGET_SLOT, address="remote://")),
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
