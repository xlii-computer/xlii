"""The shared model-picker modal (textual-only) — the ``/config`` panel's
picker. Split out of the one-file ``panels.py`` (V1c decomposition); behavior
unchanged. NOTE: the panels package's one ModalScreen lives here and only here
(fleet rule: no new modals).
"""

from __future__ import annotations

from typing import Optional

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static


class ModelPickerModal(ModalScreen[Optional[str]]):
    """Pick a model for a role — the ``/config`` panel's picker (tui-config-panel v2).

    The candidates ride in from the panel (pricing table ∪ resolved roles),
    each with its price hint so the cost of a switch is visible *before*
    it's made; the current model carries a ``●``. Enter/click chooses (the
    caller persists), Esc cancels → ``None`` and nothing changes."""

    DEFAULT_CSS = """
    ModelPickerModal {
        align: center middle;
    }
    ModelPickerModal > #picker-dialog {
        width: 70%;
        max-width: 80;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    ModelPickerModal #picker-title {
        width: 100%;
        height: 1;
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    ModelPickerModal #picker-list {
        height: auto;
        max-height: 20;
        width: 100%;
        border: none;
    }
    ModelPickerModal #picker-keys {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel", show=False),
    ]

    def __init__(
        self,
        role: str,
        options: "list[tuple[str, str]]",
        *,
        current: str = "",
        title: Optional[str] = None,
    ) -> None:
        super().__init__()
        self._role = role
        self._options = list(options)  # (option id, hint suffix)
        self._current = current
        self._title = title or f"model · {role}"

    def compose(self):  # type: ignore[override]
        from rich.text import Text
        from textual.widgets import OptionList
        from textual.widgets.option_list import Option

        with Vertical(id="picker-dialog"):
            yield Static(Text(self._title), id="picker-title")
            ol = OptionList(id="picker-list")
            for model, hint in self._options:
                mark = "● " if model == self._current else "  "
                ol.add_option(Option(f"{mark}{model}{hint}", id=model))
            yield ol
            yield Static(
                Text.from_markup("[b]enter[/b] choose     [b]esc[/b] cancel"),
                id="picker-keys",
            )

    def on_mount(self) -> None:
        from textual.widgets import OptionList

        ol = self.query_one("#picker-list", OptionList)
        models = [m for m, _ in self._options]
        if self._current in models:
            try:
                ol.highlighted = models.index(self._current)
            except ValueError:
                # Membership was checked, but guard racey option-state edge
                # cases anyway — the picker still opens, just unhighlighted.
                pass
        ol.focus()

    def on_option_list_option_selected(self, event) -> None:
        event.stop()
        self.dismiss(getattr(getattr(event, "option", None), "id", None))

    def action_cancel(self) -> None:
        self.dismiss(None)
