"""Shared chrome for a docked view (textual-only). Split out of the one-file
``panels.py`` (V1c decomposition); behavior unchanged.
"""

from __future__ import annotations

from textual.containers import Vertical


class _PanelBase(Vertical):
    """Shared chrome for a docked view: a bold title row + a 1fr body. The
    panel container in the app sizes us; we fill it."""

    DEFAULT_CSS = """
    _PanelBase {
        width: 1fr;
        height: 1fr;
    }
    _PanelBase > .panel-title {
        height: 1;
        text-style: bold;
        color: $accent;
        padding: 0 1;
    }
    """
