"""The commander top menu bar — ``Xlii · Project · Tools · Attach · Commands · Options · Panel Workbench · Help`` + its dropdowns.

Two pieces, split so the pure part is testable without a terminal:

* :data:`MENU_TITLES` + :func:`menu_bar_renderable` + :func:`title_spans` — pure. The spans map a
  click's ``x`` back to the title it hit (mirroring ``status._folder_tab`` / ``_InputCap``).
* :class:`_MenuBar` (the clickable Static row) + :class:`MenuDropdown` (a modal list of a menu's
  items) — Textual-guarded. The app owns the *contents* (``_menu_items``) and the *actions*
  (``_run_menu_action``); this module only draws the frame and reports the selection, exactly like
  the chip strip reports a tab click. Keeping the model in the app is what lets Options flip live TUI
  prefs (the hotkey modifier) through the seams already built.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from rich.text import Text

MENU_TITLES = (
    "Xlii", "Project", "Tools", "Commands", "Options",
    "Panel Workbench", "Help",
)

# Commander hotkey cue — X · P · T · C · O · N · H. Attach folded into
# Commands (those rows are slash verbs). Panel Workbench still takes n.
_ACCEL = {
    "Xlii": 0, "Project": 0, "Tools": 0, "Commands": 0,
    "Options": 0, "Panel Workbench": 2, "Help": 0,
}

_SEP = "   "  # gap between titles


def menu_bar_renderable(titles: "tuple[str, ...]" = MENU_TITLES) -> Text:
    """The menu bar row — each title's accelerator letter underlined
    (X · P · T · A · C · O · N · H). Underlining is a style, not a glyph, so
    the row stays char-for-char with :func:`title_spans`."""
    t = Text(no_wrap=True, overflow="ellipsis")
    for i, title in enumerate(titles):
        if i:
            t.append(_SEP)
        accel = _ACCEL.get(title)
        if accel is None or not (0 <= accel < len(title)):
            t.append(f" {title} ", style="bold")
            continue
        t.append(f" {title[:accel]}", style="bold")
        t.append(title[accel], style="bold underline")
        t.append(f"{title[accel + 1:]} ", style="bold")
    return t


def accelerator_letters(titles: "tuple[str, ...]" = MENU_TITLES) -> "dict[str, str]":
    """Each title → its lowercased accelerator letter (the underlined mnemonic). Keyboard
    menu-open maps ``<modifier>+<letter>`` to opening that menu — the traditional commander cue
    the underline advertises. Titles with no accelerator (none, today) are omitted."""
    out: dict[str, str] = {}
    for title in titles:
        i = _ACCEL.get(title)
        if i is not None and 0 <= i < len(title):
            out[title] = title[i].lower()
    return out


def title_spans(titles: "tuple[str, ...]" = MENU_TITLES) -> "list[tuple[int, int, str]]":
    """``(start_x, end_x, title)`` per title — the inclusive column range of its chip, so a click's
    x maps back to the menu it hit. Mirrors the render above char-for-char."""
    spans: list[tuple[int, int, str]] = []
    x = 0
    for i, title in enumerate(titles):
        if i:
            x += len(_SEP)
        inner = f" {title} "
        spans.append((x, x + len(inner) - 1, title))
        x += len(inner)
    return spans


try:
    from textual import events
    from textual.binding import Binding
    from textual.containers import Vertical
    from textual.screen import ModalScreen
    from textual.widgets import OptionList, Static
    from textual.widgets.option_list import Option

    _TEXTUAL = True
except Exception:  # pragma: no cover - only without [tui]
    _TEXTUAL = False


if _TEXTUAL:

    class _MenuBar(Static):
        """The top row of menu titles. A click opens that menu's dropdown via ``on_open(title, x)``."""

        DEFAULT_CSS = """
        _MenuBar {
            height: 1;
            background: $panel-darken-1;
            color: $text;
            padding: 0 1;
        }
        """

        def __init__(self, on_open: Callable[[str, int], None], **kwargs: Any) -> None:
            super().__init__(menu_bar_renderable(), id="menu-bar", **kwargs)
            self._on_open = on_open
            self._spans = title_spans()

        def on_click(self, event: "events.Click") -> None:
            # padding: 0 1 shifts the content right by 1; account for it so x maps to the span.
            x = getattr(event, "x", 0) - 1
            for start, end, title in self._spans:
                if start <= x <= end:
                    event.stop()
                    self._on_open(title, start)
                    return

    class MenuDropdown(ModalScreen):
        """A dropdown under a menu title: a list of ``(item_id, label, enabled)``; selecting one
        dismisses with its ``item_id`` (the app runs the action), Esc dismisses with ``None``."""

        DEFAULT_CSS = """
        MenuDropdown {
            align: left top;
            background: $background 0%;
        }
        MenuDropdown > #menu-drop {
            /* Transparent, border-less wrapper that hugs the list — the OptionList's own frame
               IS the dropdown box, so there's no larger $panel band around it. This container
               only exists to carry the drop-position margin set in on_mount.
               height: auto is load-bearing: without it the Vertical defaults to 1fr and balloons
               to max-height (a tall blank slab over the transcript), instead of hugging the list. */
            width: auto;
            height: auto;
            max-height: 20;
            background: $background 0%;
        }
        MenuDropdown #menu-list {
            height: auto;
            max-height: 18;
            width: auto;
            max-width: 36;
        }
        """

        BINDINGS = [Binding("escape", "dismiss", "Close", show=False)]

        def __init__(self, title: str, items: "list[tuple[str, str, bool]]", x: int = 0) -> None:
            super().__init__()
            self._title = title
            self._items = items
            self._x = max(0, x)

        def compose(self):  # type: ignore[override]
            with Vertical(id="menu-drop"):
                yield OptionList(id="menu-list")

        def on_mount(self) -> None:
            ol = self.query_one("#menu-list", OptionList)
            for item_id, label, enabled in self._items:
                ol.add_option(Option(label, id=item_id, disabled=not enabled))
            # Sit the dropdown under the clicked title (row 1 = just below the bar).
            self.query_one("#menu-drop").styles.margin = (1, 0, 0, self._x)
            ol.focus()

        def on_unmount(self) -> None:
            # This modal's opaque OptionList "cuts" a hole in the transcript beneath while it's open;
            # Textual skips re-rendering that region and doesn't always repaint it on close, leaving a
            # stale dropdown rectangle behind (very visible once a full-screen .nfo splash fills the
            # transcript). Force the revealed base screen to repaint so no ghost box is left.
            try:
                self.app.screen_stack[0].refresh()
            except Exception:
                # No base screen left to repaint -- the dropdown went away with it.
                pass

        def action_dismiss(self) -> None:
            self.dismiss(None)

        def on_option_list_option_selected(self, event: "OptionList.OptionSelected") -> None:
            self.dismiss(event.option.id)

        def on_click(self, event: "events.Click") -> None:
            # The modal captures every click, so the menu bar underneath can't be reached. This
            # handler fires for clicks on the modal *backdrop* (the OptionList owns clicks on its
            # own options). If such a click lands on a title in the bar row, switch to that menu —
            # classic menu-bar behaviour; a click on the *same* title or empty space just closes.
            ol = self.query_one("#menu-list", OptionList)
            sx = getattr(event, "screen_x", getattr(event, "x", 0))
            sy = getattr(event, "screen_y", getattr(event, "y", 0))
            if ol.region.contains(sx, sy):
                return                                      # inside the list → leave it to OptionList
            event.stop()
            title = self._title_at(sx, sy)
            if title is not None and title != self._title:
                start = next((s for s, _e, t in title_spans() if t == title), 0)
                self.dismiss(("switch", title, start))      # app._open_menu opens the clicked menu
            else:
                self.dismiss(None)                          # same title or click-away → close

        def _title_at(self, sx: int, sy: int) -> "Optional[str]":
            """The menu-bar title under screen point ``(sx, sy)``, or ``None`` if the point isn't on
            the bar row. Mirrors :meth:`_MenuBar.on_click` but in absolute screen coordinates."""
            try:
                bar = self.app.screen_stack[0].query_one("#menu-bar", _MenuBar)
            except Exception:
                return None
            cr = bar.content_region                         # already inside the bar's padding
            if sy != cr.y:
                return None
            x = sx - cr.x
            for start, end, title in title_spans():
                if start <= x <= end:
                    return title
            return None
