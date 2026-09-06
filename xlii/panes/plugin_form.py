"""``PluginFormPane`` — closed HTML form for one plugin action."""

from __future__ import annotations

from typing import Any, Optional

from xlii.addressing import Address, Node
from xlii.panes import RETARGET_SLOT, Action, Outcome, Rendered, Selection


class PluginFormPane:
    """Slot body is the form (client paints ``rendered.form.html``)."""

    def __init__(self, address: "str | Address | None" = None) -> None:
        self._address: Address = Address(scheme="pluginform")
        self._plugin_id: str = ""
        self._action_id: str = ""
        self._seed: dict[str, Any] = {}
        if address is not None:
            self.mount(address)

    @property
    def address(self) -> Address:
        return self._address

    def mount(self, address: "str | Address", *, select: Optional[str] = None) -> None:
        del select
        self._address = address if isinstance(address, Address) else Address.parse(str(address))
        parts = [p for p in self._address.target.strip().split("/") if p]
        self._plugin_id = parts[0] if parts else ""
        self._action_id = parts[1] if len(parts) > 1 else ""

    def set_seed(self, seed: Optional[dict[str, Any]]) -> None:
        self._seed = dict(seed or {})

    def _spec(self) -> dict[str, Any]:
        from xlii.plugin import Plugin
        from xlii.plugin_form import form_spec

        if not self._plugin_id or not self._action_id:
            return {
                "plugin": "",
                "action": "",
                "title": "plugin form",
                "lead": "No action.",
                "fields": [],
                "html": "",
            }
        p = Plugin(id=self._plugin_id)
        m = p.manifest() if p.exists() else None
        action = m.get_action(self._action_id) if m is not None else None
        if action is None:
            return {
                "plugin": self._plugin_id,
                "action": self._action_id,
                "title": f"{self._plugin_id}.{self._action_id}",
                "lead": "No such action.",
                "fields": [],
                "html": "",
            }
        return form_spec(self._plugin_id, action, seed=self._seed, name=p.name())

    def render(self) -> Rendered:
        spec = self._spec()
        title = spec.get("title") or "plugin form"
        return Rendered(
            title=title,
            rows=(),
            empty=False,
            form=spec,
        )

    def selection(self) -> Selection:
        addr = str(self._address)
        return Selection(node=Node(
            address=addr,
            name=self._action_id or "form",
            kind="container",
            extra={"type": "pluginform"},
        ))

    def actions(self) -> list[Action]:
        back = "plugins://"
        if self._plugin_id:
            back = f"plugins://{self._plugin_id}"
        return [Action("back", "◂ back", Outcome(RETARGET_SLOT, address=back))]

    def handle(self, key: str) -> bool:
        return False

    def select_index(self, i: int) -> bool:
        del i
        return False
