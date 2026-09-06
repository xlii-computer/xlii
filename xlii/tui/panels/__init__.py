"""The split-screen side panel — view registry · panel host · dock preview (Vector P).

This is Vector P's published seam, the *available* corner of the file triangle
(A1 tabs = *what's attached* · this panel = *what's available* · A2's surface =
*act on one*). It mirrors the two shapes the foundation already ships:

- ``register_panel_view(name, provider)`` / ``panel_views()`` — a name → provider
  registry, exactly like ``status.register_frame_tab`` (seam #1) and
  ``preview.register_preview`` (seam #2). A provider turns the live session into a
  dockable Textual widget; the built-ins (``explorer`` · ``locker``) are client
  #1, and a third party registers its own view **from its own file**, never by
  editing this one.
- ``set_panel_host(host)`` / ``show_panel(side, view, *, state)`` / ``hide_panel()``
  — the host seam, mirroring ``preview.set_surface_host`` / ``terminal_image.
  set_renderable_sink``. ``launch()`` installs an ``AppPanelHost`` over the running
  Textual app; ``/file-tab`` then opens/closes the dock through these module
  functions without importing the app. Off the TUI (inline REPL, headless, tests)
  there is no host and the caller degrades to a note.

The leaf is intentionally import-light: the registry, host seam, and the
``dock_renderable`` preview builder are rich-only and import cleanly **without**
the optional ``[tui]`` extra (``repl_cmds/file_tab`` imports this at session
start). The concrete Textual view widgets + ``AppPanelHost`` are defined only when
textual is present, the same guard ``tui/preview`` uses for ``PreviewSurface``.

Design rule (the parallel-build merge contract): the signatures here are published
day 1 and additive — registering a view or opening the dock is a published-function
call, not a shared edit.
"""

from __future__ import annotations

from typing import Any

# The pure-python half of the seam (registry · PanelActions · host contract ·
# module façade · dock preview) lives in sibling modules; the façade re-exports
# the exact public surface so existing `from xlii.tui import panels` and
# `from xlii.tui.panels import X` call sites keep working unchanged.
from xlii.tui.panels.dock_preview import _payload_path, dock_renderable
from xlii.tui.panels.host import (
    PanelActions,
    PanelHost,
    PanelProvider,
    build_panel_view,
    current_panel_host,
    current_panel_target,
    get_panel_view,
    hide_panel,
    open_doorway,
    panel_views,
    register_panel_view,
    route_to_dock,
    set_panel_host,
    show_file_view,
    show_gallery,
    show_panel,
    show_tree,
    unregister_panel_view,
)

__all__ = [
    # host seam (pure-python head)
    "PanelProvider",
    "register_panel_view",
    "unregister_panel_view",
    "get_panel_view",
    "panel_views",
    "build_panel_view",
    "PanelActions",
    "PanelHost",
    "set_panel_host",
    "current_panel_host",
    "show_panel",
    "hide_panel",
    "open_doorway",
    "current_panel_target",
    "show_file_view",
    "show_tree",
    "show_gallery",
    "route_to_dock",
    # dock preview
    "dock_renderable",
    "_payload_path",
    # concrete views + host (defined below when textual is present)
    "ExplorerPanel",
    "LockerPanel",
    "_LockerItem",
    "ThemesPanel",
    "ModelPickerModal",
    "HistoryPanel",
    "ConfigPanel",
    "AppPanelHost",
    "explorer_view",
    "locker_view",
    "themes_view",
    "config_view",
    "history_view",
]

# --------------------------------------------------------------------------- #
#  The concrete views + host (textual-only — guarded like tui/preview)
# --------------------------------------------------------------------------- #
#
# Imported lazily-at-class-definition: textual is the optional [tui] dep, so the
# registry/host/dock builder above import cleanly without it. We guard the import
# and only define the widgets / AppPanelHost when textual is present.

try:
    # Probe only — the per-panel modules import their own widgets; the façade
    # just needs to know whether the optional [tui] extra is importable.
    from textual.widgets import Static  # noqa: F401

    _TEXTUAL = True
except Exception:  # pragma: no cover - exercised only without [tui]
    _TEXTUAL = False


if _TEXTUAL:

    from xlii.tui.panels.config import ConfigPanel
    from xlii.tui.panels.explorer import ExplorerPanel
    from xlii.tui.panels.history import HistoryPanel
    from xlii.tui.panels.locker import LockerPanel, _LockerItem
    from xlii.tui.panels.models import ModelPickerModal
    from xlii.tui.panels.themes import ThemesPanel

    def explorer_view(state: Any, *, actions: "PanelActions") -> Any:
        return ExplorerPanel(state, actions)

    def locker_view(state: Any, *, actions: "PanelActions") -> Any:
        return LockerPanel(state, actions)

    def themes_view(state: Any, *, actions: "PanelActions") -> Any:
        return ThemesPanel(state, actions)

    def config_view(state: Any, *, actions: "PanelActions") -> Any:
        return ConfigPanel(state, actions)

    def history_view(state: Any, *, actions: "PanelActions") -> Any:
        return HistoryPanel(state, actions)

    from xlii.tui.panels.app_host import AppPanelHost

else:  # pragma: no cover - no [tui]: views/host are unavailable, registry isn't

    def explorer_view(state: Any, *, actions: "PanelActions") -> Any:  # type: ignore[misc]
        return None

    def locker_view(state: Any, *, actions: "PanelActions") -> Any:  # type: ignore[misc]
        return None

    def themes_view(state: Any, *, actions: "PanelActions") -> Any:  # type: ignore[misc]
        return None

    def config_view(state: Any, *, actions: "PanelActions") -> Any:  # type: ignore[misc]
        return None

    def history_view(state: Any, *, actions: "PanelActions") -> Any:  # type: ignore[misc]
        return None
