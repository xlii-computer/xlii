"""The bottom status strip — the commander F-key hint bar and the context meter.

Two halves of the TUI's bottom chrome, split out of the ``tui_textual`` monolith
(the-fold Vector D extraction):

* :data:`_FKEY_HINTS` + :func:`_fkey_bar_renderable` + :func:`_fkey_spans` — pure
  (testable without a terminal). The spans map a click's ``x`` back to the F-key
  it hit, exactly like ``menu_bar.title_spans`` / ``status._folder_tab``.
* :class:`_FKeyBar` — the clickable Static row. Mirrors
  :class:`xlii.tui.menu_bar._MenuBar`: it owns all its styling via ``DEFAULT_CSS``
  and reports a click via the ``on_fkey`` callback, so click and key never diverge.
* The context-meter helpers (:func:`_ktok`, :func:`_context_window`) — pure. They
  compute the profile bar's rightmost ``used / cap · N% cached`` segment, which the
  app hands to ``status.profile_bar(state, meter=…)``. Kept here (not in
  ``status.py``) because that file's segment functions belong to a sibling vector.

F-keys are commander **verbs** and do not follow the workbench pack. Pack
doors live in the Panel Workbench menu (and the face slot list). Classic V2b hints
are the bar.

The profile bar CONTENT itself lives in ``xlii/tui/status.py``; this module only
owns the F-key row and the meter arithmetic that feeds the bar.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Sequence

from rich.text import Text
from textual import events
from textual.widgets import Static

# Classic commander row (V2b) — fallback + re-export for tests that pin the shape.
_FKEY_HINTS = [
    ("F1", "help"), ("F2", "Home Hub"), ("F3", "view"), ("F4", "edit"), ("F5", "copy"),
    ("F6", "detach"), ("F7", "add"), ("F8", "rem"), ("F9", "jobs"), ("F10", "tasks"),
]
_FKEY_KEYS = {f"f{i}" for i in range(1, 13)}

_FSEP = " │ "  # gap between F-key chips — a box-drawing rule, styled dim


def _normalize_hints(
    hints: Optional[Sequence[tuple[str, str]]] = None,
) -> list[tuple[str, str]]:
    if not hints:
        return list(_FKEY_HINTS)
    return [(str(k), str(lab)) for k, lab in hints]


def _fkey_bar_renderable(
    hints: Optional[Sequence[tuple[str, str]]] = None,
) -> Text:
    """The bottom function-key hint bar — commander signature / workbench pack."""
    hints = _normalize_hints(hints)
    t = Text(no_wrap=True, overflow="ellipsis")
    for i, (key, label) in enumerate(hints):
        if i:
            t.append(_FSEP, style="dim")
        t.append(key, style="bold")
        t.append(f" {label}", style="dim")
    return t


def _fkey_spans(
    hints: Optional[Sequence[tuple[str, str]]] = None,
) -> "list[tuple[int, int, str]]":
    """``(start_x, end_x, key)`` per F-key chip — inclusive column range of its label.
    ``key`` is the Textual key string ('f1'…'f10')."""
    hints = _normalize_hints(hints)
    spans: list[tuple[int, int, str]] = []
    x = 0
    for i, (key, label) in enumerate(hints):
        if i:
            x += len(_FSEP)
        chip = f"{key} {label}"
        spans.append((x, x + len(chip) - 1, key.lower()))
        x += len(chip)
    return spans


def hints_and_actions_from_rows(
    rows: Sequence[tuple[str, str, str]],
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Split ``fkey_pack_rows`` into bar hints + ``{f1: action}`` map."""
    hints = [(k, lab) for k, lab, _act in rows]
    actions = {k.lower(): act for k, _lab, act in rows if act}
    return hints, actions


class _FKeyBar(Static):
    """Clickable F-key strip. Labels are the V2b commander verbs.

    A click maps x → F-key via :func:`_fkey_spans` and fires ``on_fkey`` (same
    ``_handle_fkey`` path as a focused-input F-key press).
    """

    DEFAULT_CSS = """
    _FKeyBar {
        height: 1;
        background: $panel-darken-1;
        color: $text-muted;
        padding: 0 1;
    }
    """

    def __init__(self, on_fkey: "Callable[[str], None]", **kwargs: "Any") -> None:
        super().__init__(_fkey_bar_renderable(), id="fkey-bar", **kwargs)
        self._on_fkey = on_fkey
        self._hints: list[tuple[str, str]] = list(_FKEY_HINTS)
        self._actions: dict[str, str] = {}
        self._spans = _fkey_spans(self._hints)

    def set_pack(self, rows: Sequence[tuple[str, str, str]]) -> None:
        """Ignored — F-keys are not a workbench pack. Kept so old callers no-op."""
        del rows
        self.reset_commander()

    def reset_commander(self, hints: Optional[Sequence[tuple[str, str]]] = None) -> None:
        """Paint the commander row, optionally overlayed by task binds."""
        self._hints = list(hints) if hints else list(_FKEY_HINTS)
        self._actions = {}
        self._spans = _fkey_spans(self._hints)
        self.update(_fkey_bar_renderable(self._hints))

    def action_for(self, key: str) -> Optional[str]:
        """Pack-driven override. Always None — F-keys are commander verbs."""
        del key
        return None

    def on_click(self, event: "events.Click") -> None:
        # padding: 0 1 shifts content right by 1, so account for it before mapping x → chip.
        x = getattr(event, "x", 0) - 1
        for start, end, key in self._spans:
            if start <= x <= end:
                event.stop()
                self._on_fkey(key)
                return


# Context meter arithmetic lives in xlii.session_meter (face HUD + TUI).
