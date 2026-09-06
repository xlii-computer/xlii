"""The locker image-gallery panel + its clickable item widget (textual-only).
Split out of the one-file ``panels.py`` (V1c decomposition); behavior unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.containers import VerticalScroll
from textual.widgets import Static

from xlii.tui.panels.base import _PanelBase

if TYPE_CHECKING:
    from xlii.tui.panels.host import PanelActions


class _LockerItem(Static):
    """One locker image — name + an inline chafa-symbols thumbnail, clickable.
    A click docks a read-only preview (the glance) and a second affordance —
    the input-cap A1 tab / A2's surface — stays the explicit deeper action."""

    DEFAULT_CSS = """
    _LockerItem {
        height: auto;
        padding: 0 1;
        margin-bottom: 1;
        border: round $panel-darken-1;
    }
    _LockerItem:hover {
        border: round $accent;
    }
    """

    def __init__(self, entry: dict, actions: "PanelActions") -> None:
        super().__init__()
        self._entry = dict(entry)
        self._actions = actions

    def on_mount(self) -> None:
        from rich.console import Group
        from rich.text import Text

        name = self._entry.get("name") or self._entry.get("path") or "image"
        on = self._entry.get("enabled", True)
        head = Text(f"{'●' if on else '○'} {name}", style="bold" if on else "dim")
        thumb = None
        path = self._entry.get("path")
        if path:
            try:
                from xlii import terminal_image

                thumb = terminal_image.image_renderable(path, max_width=40)
            except Exception:
                thumb = None
        self.update(Group(head, thumb) if thumb is not None else head)

    def on_click(self, event) -> None:
        event.stop()
        path = self._entry.get("path")
        if path:
            self._actions.view_file(path)
            self._actions.notify(f"view · {self._entry.get('name', 'image')}")


class LockerPanel(_PanelBase):
    """A glanceable grid of the locker's *image* attachments
    (``attached_files`` filtered to ``kind=="image"``), inline thumbnails via
    the shipped chafa-symbols sink — the always-visible tab that replaces the
    ``/locker`` popup for images. Clicking one docks a bigger preview."""

    DEFAULT_CSS = """
    LockerPanel > #panel-locker-body {
        height: 1fr;
        width: 1fr;
    }
    """

    def __init__(self, state: Any, actions: "PanelActions") -> None:
        super().__init__()
        self._state = state
        self._actions = actions

    def _images(self) -> list[dict]:
        files = getattr(self._state, "attached_files", None) or []
        return [e for e in files if isinstance(e, dict) and e.get("kind") == "image"]

    def compose(self):  # type: ignore[override]
        from rich.text import Text

        images = self._images()
        yield Static(Text(f"locker · {len(images)} image{'' if len(images) == 1 else 's'}"), classes="panel-title")
        with VerticalScroll(id="panel-locker-body"):
            if not images:
                yield Static(Text("no images attached — /locker add <path>", style="dim italic"))
            else:
                for entry in images:
                    yield _LockerItem(entry, self._actions)
