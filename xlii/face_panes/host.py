from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .deck import FaceDeck
from .views import _SCHEME_TO_PANE, _VIEW_TO_PANE

class FacePanelHost:
    """PanelHost seam for the face — makes ``/panel`` and open_doorway work.

    Textual installs :class:`~xlii.tui.panels.app_host.AppPanelHost`; without a
    host, ``/panel home`` only printed "need --tui". The face mounts the same
    kernel Dock panes and focuses them over the wire.
    """

    def __init__(self, server: Any) -> None:
        self._server = server

    def _deck(self) -> FaceDeck:
        return self._server.deck

    def show_panel(self, side: str, view: str, *, state: Any) -> bool:
        del side, state  # face has one dock; side is a TUI preference
        v = (view or "vfs").strip().lower()
        pid = _VIEW_TO_PANE.get(v) or _SCHEME_TO_PANE.get(v) or v
        return self._deck().open_pane(pid)

    def hide_panel(self) -> bool:
        return self._deck().close_pane()

    def is_open(self) -> bool:
        return bool(getattr(self._deck(), "_open_pane_id", None))

    def current_side(self) -> str:
        cfg = getattr(getattr(self._server, "state", None), "cfg", None)
        raw = str(getattr(cfg, "tui_panel_side", "") or "right").lower()
        return "left" if raw == "left" else "right"

    def set_panel_side(self, side: str, *, persist: bool = True) -> str:
        applied = "left" if str(side).lower() == "left" else "right"
        if persist:
            cfg = getattr(getattr(self._server, "state", None), "cfg", None)
            if cfg is not None:
                cfg.tui_panel_side = applied
                save = getattr(cfg, "save", None)
                if callable(save):
                    try:
                        save()
                    except Exception:
                        # Persisting the layout is cosmetic; the new side still
                        # applies for this session.
                        pass
            try:
                self._server.send(self._server.chrome_state())
            except Exception:
                # A dead client picks up the new panel side on reconnect.
                pass
        return applied

    def current_view(self) -> Optional[str]:
        return getattr(self._deck(), "_open_pane_id", None)

    def current_target(self) -> Optional[Path]:
        return None

    def show_file(self, path: Any) -> bool:
        del path
        return False

    def show_tree(self, *, side: Optional[str] = None) -> bool:
        del side
        return self._deck().open_pane("explorer")

    def show_gallery(self, *, side: Optional[str] = None) -> bool:
        del side
        return self._deck().open_pane("locker")

    def open_doorway(self, scheme: str) -> bool:
        return self._deck().open_scheme(scheme)

    def open_address(self, address: str) -> bool:
        from xlii.addressing import Address

        try:
            addr = Address.parse(address)
        except Exception:
            return False
        return self.open_doorway(addr.scheme)
