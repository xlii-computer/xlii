"""The input-line scrollback panel — ``/history`` (textual-only). Split out
of the one-file ``panels.py`` (V1c decomposition); behavior unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.widgets import Static

from xlii.tui.panels.base import _PanelBase

if TYPE_CHECKING:
    from xlii.tui.panels.host import PanelActions


class HistoryPanel(_PanelBase):
    """Input-line scrollback — ``/history`` (Track F). Newest-first browse of
    ``.xlii/repl_history``; select prefills the full line (no execute)."""

    DEFAULT_CSS = """
    HistoryPanel .panel-header {
        height: 1;
        width: 1fr;
    }
    HistoryPanel .panel-title {
        height: 1;
        width: auto;
        text-style: bold;
        color: $accent;
        padding: 0 1;
    }
    HistoryPanel .panel-spacer {
        width: 1fr;
    }
    HistoryPanel .panel-close {
        height: 1;
        min-width: 3;
        width: auto;
        border: none;
        padding: 0 1;
        margin: 0;
        background: $panel-darken-2;
        color: $text;
    }
    HistoryPanel .panel-close:hover {
        background: $accent;
    }
    HistoryPanel > #panel-history-list {
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
        self._lines: list[str] = []
        self._reload()

    def _reload(self) -> None:
        from xlii.repl_history_util import load_repl_history_strings

        proj = getattr(self._state, "project", None)
        xli = getattr(proj, "xli_dir", None) if proj is not None else None
        self._lines = load_repl_history_strings(xli) if xli else []

    def _options(self) -> list:
        from textual.widgets.option_list import Option
        from xlii.repl_history_util import format_history_row

        if not self._lines:
            return [Option("(empty — submit a line first)", id="empty", disabled=True)]
        return [
            Option(format_history_row(line), id=f"line:{i}")
            for i, line in enumerate(self._lines)
        ]

    def compose(self):  # type: ignore[override]
        from rich.text import Text
        from textual.containers import Horizontal
        from textual.widgets import Button, OptionList

        with Horizontal(classes="panel-header"):
            yield Static(Text(f"history · {len(self._lines)} line(s)"), classes="panel-title")
            yield Static("", classes="panel-spacer")
            clear = Button("clear", id="panel-history-clear", classes="panel-close")
            clear.can_focus = False
            yield clear
            close = Button("✕", id="panel-history-close", classes="panel-close")
            close.can_focus = False
            yield close
        ol = OptionList(id="panel-history-list")
        ol.add_options(self._options())
        yield ol

    def _clear_typed(self) -> None:
        """Wipe typed lines (``.xlii/repl_history``) — not working talk."""
        from xlii.repl_history_util import clear_repl_history

        proj = getattr(self._state, "project", None)
        xli = getattr(proj, "xli_dir", None) if proj is not None else None
        if xli is not None:
            clear_repl_history(xli)
        self._reload()
        try:
            app = getattr(self, "app", None)
        except Exception:
            app = None
        if app is not None and hasattr(app, "_load_history"):
            try:
                app._load_history()
            except Exception:
                # The typed filter already cleared; the list reloads on the next mount.
                pass
        try:
            ol = self.query_one("#panel-history-list")
            clearer = getattr(ol, "clear_options", None)
            if callable(clearer):
                clearer()
            ol.add_options(self._options())
            title = self.query_one(".panel-title")
            title.update(f"history · {len(self._lines)} line(s)")
        except Exception:
            # The panel may already be unmounted (a clear racing a Dock
            # remount); the list rebuilds when it mounts again.
            pass
        self._actions.notify("typed lines cleared")

    def on_button_pressed(self, event) -> None:
        event.stop()
        btn = getattr(event, "button", None)
        bid = getattr(btn, "id", None) or ""
        if bid == "panel-history-clear":
            self._clear_typed()
            return
        app = getattr(self, "app", None)
        if app is not None and hasattr(app, "hide_panel"):
            try:
                app.hide_panel()
            except Exception as exc:
                print(f"[xlii.tui.history] hide_panel failed: {exc}")

    def on_option_list_option_selected(self, event) -> None:
        event.stop()
        rid = getattr(getattr(event, "option", None), "id", None) or ""
        if not rid.startswith("line:"):
            return
        try:
            idx = int(rid.split(":", 1)[1])
        except ValueError:
            return
        if 0 <= idx < len(self._lines):
            self._actions.prefill_input(self._lines[idx])
            self._actions.notify("prefilled input — review before submit")
