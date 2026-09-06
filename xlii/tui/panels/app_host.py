"""AppPanelHost — bridge a running ``XliiApp`` into the host seam. Split out
of the one-file ``panels.py`` (V1c decomposition); behavior unchanged. Pure
python (duck-typed app), but only imported by the façade when textual is
present, exactly as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from xlii.tui.panels.host import PanelHost


class AppPanelHost(PanelHost):
    """Bridge a running ``XliiApp`` into the host seam.

    Docking/undocking mounts widgets, which must happen on the app's own
    thread; ``/file-tab`` runs on a worker thread, so we hop via
    ``call_from_thread`` (the same bridge ``AppSurfaceHost`` uses). The app
    owns the J1 split + ``show_panel(side, widget)``/``hide_panel()``; this
    host builds the view widget from the registry and hands it over."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def _call(self, fn: Callable, *args: Any) -> Any:
        try:
            return self._app.call_from_thread(fn, *args)
        except RuntimeError:
            # already on the app thread (or no running loop) — call directly.
            return fn(*args)

    def show_panel(self, side: str, view: str, *, state: Any) -> bool:
        side = "left" if str(side).lower() == "left" else "right"
        if view == "locker":
            return bool(self._call(self._app.show_gallery, side))
        if view in (None, "", "explorer", "tree"):
            return bool(self._call(self._app.show_tree, side))
        # any other registered view (e.g. the kernel "vfs" Dock surface) docks through the
        # app's generic builder rather than being forced back to the tree.
        return bool(self._call(self._app._show_panel_view, side, view))

    def hide_panel(self) -> bool:
        # XliiApp.hide_panel returns None (it's idempotent); the undock always
        # succeeds once a host is installed, so report True.
        self._call(self._app.hide_panel)
        return True

    def is_open(self) -> bool:
        return bool(getattr(self._app, "_panel_open", False))

    def current_side(self) -> str:
        return getattr(self._app, "_panel_side", "right") or "right"

    def set_panel_side(self, side: str, *, persist: bool = True) -> str:
        setter = getattr(self._app, "set_panel_side", None)
        if not callable(setter):
            return "left" if str(side).lower() == "left" else "right"
        return self._call(lambda: setter(side, persist=persist))

    def current_view(self) -> Optional[str]:
        return getattr(self._app, "_panel_view", None)

    def current_target(self) -> Optional[Path]:
        fn = getattr(self._app, "current_panel_target", None)
        if not callable(fn):
            return None
        try:
            return fn()
        except Exception:
            return None

    def show_file(self, path: Any) -> bool:
        return bool(self._call(self._app.show_file_view, path))

    def show_tree(self, *, side: Optional[str] = None) -> bool:
        if side is not None:
            return bool(self._call(self._app.show_tree, side))
        return bool(self._call(self._app.show_tree))

    def show_gallery(self, *, side: Optional[str] = None) -> bool:
        if side is not None:
            return bool(self._call(self._app.show_gallery, side))
        return bool(self._call(self._app.show_gallery))

    def open_doorway(self, scheme: str) -> bool:
        # The app's canonical doorway opener — mounts the vfs Dock if none is docked, then morphs
        # it to scheme:// (the same call a chip click / Alt-<letter> hotkey routes through).
        self._call(self._app._open_in_dock_view, f"{scheme}://")
        return True

    def open_address(self, address: str) -> bool:
        # Same opener, but for a precise address (e.g. wiki://page#section) rather than a
        # scheme root — the seam /howto uses to pop a cited wiki page open to the side.
        self._call(self._app._open_in_dock_view, address)
        return True
