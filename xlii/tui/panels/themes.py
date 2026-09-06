"""The theme-picker panel (textual-only). Split out of the one-file
``panels.py`` (V1c decomposition); behavior unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.widgets import Static

from xlii.tui.panels.base import _PanelBase

if TYPE_CHECKING:
    from xlii.tui.panels.host import PanelActions


class ThemesPanel(_PanelBase):
    """The available Textual app themes as a clickable list — Options → Theme…. Selecting a row
    (click or Enter) applies that theme live and persists it (``PanelActions.apply_theme`` →
    ``App._apply_theme``); the whole TUI repaints. The active theme carries a ``●`` marker."""

    DEFAULT_CSS = """
    ThemesPanel .panel-header {
        height: 1;
        width: 1fr;
    }
    ThemesPanel .panel-title {
        height: 1;
        width: auto;
        text-style: bold;
        color: $accent;
        padding: 0 1;
    }
    ThemesPanel .panel-spacer {
        width: 1fr;
    }
    ThemesPanel .panel-close {
        height: 1;
        min-width: 3;
        width: auto;
        border: none;
        padding: 0 1;
        margin: 0;
        background: $panel-darken-2;
        color: $text;
    }
    ThemesPanel .panel-close:hover {
        background: $accent;
    }
    ThemesPanel > #panel-themes-list {
        height: 1fr;
        width: 1fr;
        border: none;
        padding: 0 1;
    }
    """

    def __init__(self, state: Any, actions: "PanelActions") -> None:
        super().__init__()
        self._state = state
        self._actions = actions

    def _theme_names(self) -> list[str]:
        from xlii.tui.canvas import normalize_canvas, themes_matching_canvas

        app = getattr(self._actions, "app", None)
        names = getattr(app, "available_themes", None) if app is not None else None
        try:
            all_names = sorted(names) if names else []
        except Exception:
            return []
        cfg = getattr(self._state, "cfg", None)
        if cfg is None:
            alt = getattr(self._actions, "state", None)
            cfg = getattr(alt, "cfg", None) if alt is not None else None
        canvas = normalize_canvas(getattr(cfg, "tui_canvas", "dark") if cfg else "dark")
        return themes_matching_canvas(all_names, canvas)

    def _current(self) -> str:
        app = getattr(self._actions, "app", None)
        return str(getattr(app, "theme", "") or "") if app is not None else ""

    def _label(self, name: str, current: str) -> str:
        return ("● " if name == current else "  ") + name

    def compose(self):  # type: ignore[override]
        from rich.text import Text
        from textual.containers import Horizontal
        from textual.widgets import Button, OptionList
        from textual.widgets.option_list import Option

        names = self._theme_names()
        current = self._current()
        with Horizontal(classes="panel-header"):
            yield Static(Text(f"themes · {len(names)}"), classes="panel-title")
            yield Static("", classes="panel-spacer")
            close = Button("✕", classes="panel-close")
            close.can_focus = False   # the ✕ never steals focus from the list (mc/ranger idiom)
            yield close
        ol = OptionList(id="panel-themes-list")
        for name in names:
            ol.add_option(Option(self._label(name, current), id=name))
        yield ol

    def on_button_pressed(self, event) -> None:
        # ✕ — undock the whole panel, mirroring the dock panes' close button (_SlotView._close_panel).
        event.stop()
        app = getattr(self, "app", None)
        if app is not None and hasattr(app, "hide_panel"):
            try:
                app.hide_panel()
            except Exception:
                # The panel is already closing, or has no host to close it.
                pass

    def on_mount(self) -> None:
        # Start the highlight on the active theme so arrow-keys pick up from there.
        from textual.widgets import OptionList

        names = self._theme_names()
        current = self._current()
        if current in names:
            try:
                self.query_one("#panel-themes-list", OptionList).highlighted = names.index(current)
            except Exception:
                # The list isn't populated yet; the highlight lands on the next rebuild.
                pass

    def on_option_list_option_selected(self, event) -> None:
        # select = apply (click or Enter). The app validates + persists + repaints.
        event.stop()
        name = getattr(getattr(event, "option", None), "id", None)
        if name:
            self._actions.apply_theme(name)
            self._rebuild()

    def _rebuild(self) -> None:
        """Repaint the ``●`` marker after a selection, preserving the cursor row."""
        from textual.widgets import OptionList
        from textual.widgets.option_list import Option

        try:
            ol = self.query_one("#panel-themes-list", OptionList)
        except Exception:
            return
        names = self._theme_names()
        current = self._current()
        keep = ol.highlighted
        ol.clear_options()
        for name in names:
            ol.add_option(Option(self._label(name, current), id=name))
        if keep is not None:
            try:
                ol.highlighted = keep
            except Exception:
                # The remembered row no longer exists -- leave the default highlight.
                pass
