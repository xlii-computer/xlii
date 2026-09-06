"""Dock panels, tabs, tree/gallery/file views.

Mixin extracted from :mod:`xlii.tui.app` (grades plan Phase 5).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Static

from xlii.tui.input_surface import (
    _ChipRow,
)


class AppPanelMixin:
    """Dock panels, tabs, tree/gallery/file views."""

    def action_focus_tabs(self) -> None:
        """ctrl+b → hand keyboard focus to the chip row so ←/→ cycle attachments."""
        try:
            self.query_one("#input-chips", _ChipRow).focus()
        except Exception:
            # Best-effort UX action: if chips aren't mounted/available yet, don't crash.
            # Surface a lightweight warning when possible instead of silently swallowing.
            try:
                self.notify(
                    "Tabs are not available to focus yet",
                    severity="warning",
                    timeout=2,
                )
            except Exception:
                return

    def _tab_address(self, kind: str, payload: Any) -> Optional[str]:
        """A VFS address for a folder-tab's target, when it has one — used to route a chip click
        into the kernel Dock. A file/locker chip resolves to ``file://<path>`` (a staged image
        then renders in the image pane); kinds without a browseable address (doc/ref/skill/role/
        mode/files) return ``None`` and keep their existing surface."""
        if kind == "file":
            from xlii.tui import panels

            path = panels._payload_path(payload)
            return f"file://{path}" if path is not None else None
        return None

    def _open_in_dock_view(self, address: str) -> None:
        """Open ``address`` in the docked Dock's working pane. If no Dock is docked, mount one
        **already at** ``address`` (Track I1 — no intermediate vfs/home flash), via a one-shot
        on session state consumed by ``dock_view`` / ``dock_root_address``."""
        if address == "history://":
            self._show_panel_view(self._preferred_panel_side(), "history")
            return
        from xlii.tui.dock_surface import open_in_dock

        if not open_in_dock(self, address):
            state = getattr(self, "_state", None)
            if state is not None:
                try:
                    state._dock_open_address = address
                except Exception:
                    # A state object that refuses the attribute just opens the Dock at its root instead.
                    pass
            self._show_panel_view(self._preferred_panel_side(), "vfs")

    def _activate_tab(self, kind: str, payload: Any) -> None:
        """Open a folder tab's surface (click or Enter). A content-type DOORWAY chip opens its
        ``scheme://`` in Pane 2; an addressable file chip morphs the open Dock; other kinds fall to
        the legacy panel / A2 modal surface."""
        from xlii.tui import panels

        # A content-type DOORWAY chip (payload = a scheme) opens scheme:// in Pane 2 (the Dock).
        if kind == "door":
            self._open_in_dock_view(f"{payload}://")
            return

        # The LIVE jobs chip (J's own frame-tab, kind="jobs", payload=the registry) opens the
        # jobs:// browse in Pane 2 — the chip realization of the task→bg-job→done story.
        if kind == "jobs":
            self._open_in_dock_view("jobs://")
            return

        # Done-tab: a completed-unseen job chip (kind="job_done", payload=job id). Mount
        # jobs://<id> and mark it seen so the chip dismisses (Story #1 mechanical half).
        if kind == "job_done" and payload:
            jid = str(payload)
            self._open_in_dock_view(f"jobs://{jid}")
            try:
                reg = getattr(getattr(self, "_state", None), "job_registry", None)
                if reg is not None and hasattr(reg, "mark_seen"):
                    reg.mark_seen(jid)
                if hasattr(self, "_refresh_status"):
                    self._refresh_status()
            except Exception:
                # The Dock view already opened; marking the job seen and repainting the badge are follow-ups.
                pass
            return

        # Converged surface: a chip whose target has a VFS address morphs the open Dock's working
        # pane. Additive — with no Dock docked, open_in_dock returns False and the legacy panel /
        # preview path below runs unchanged (so existing behaviour and tests are preserved).
        addr = self._tab_address(kind, payload)
        if addr is not None:
            from xlii.tui.dock_surface import open_in_dock

            if open_in_dock(self, addr):
                return

        if kind == "file":
            path = panels._payload_path(payload)
            if path is not None:
                self.show_file_view(path)
            return
        if kind == "files":
            # the one file explorer — the kernel Dock surface (not the legacy tree)
            self._show_panel_view(self._preferred_panel_side(), "vfs")
            return
        if self._open_preview_surface(kind, payload):
            return
        try:
            self.notify(
                f"{kind} preview surface isn't available yet",
                severity="warning",
                timeout=3,
            )
        except Exception:
            # Best-effort UI hint only; never block tab activation if notifications fail.
            pass

    def _open_preview_surface(self, kind: str, payload: Any) -> bool:
        """Push A2's preview/edit surface for a tab (seam #2), if A2 is present.

        Codes to A2's published entry — a `surface(kind, payload) -> ModalScreen`
        factory in `xlii.tui.preview` (with `open_surface` accepted as an alias) —
        and never touches A2's files. Returns True once a screen is pushed; False
        when the module/factory is absent or yields nothing, so the caller can
        fall back gracefully."""
        try:
            from xlii.tui import preview as _preview  # type: ignore
        except Exception:
            return False
        factory = getattr(_preview, "surface", None) or getattr(
            _preview, "open_surface", None
        )
        if not callable(factory):
            return False
        try:
            screen = factory(kind, payload)
        except Exception:
            return False
        if screen is None:
            return False
        try:
            self.push_screen(screen)
            return True
        except Exception:
            return False

    # -- split-screen side panel (Vector P · J1 seam) --------------------

    def _query_panel(self) -> Optional[Vertical]:
        try:
            return self.query_one("#panel", Vertical)
        except Exception:
            return None

    def _preferred_panel_side(self) -> str:
        """The saved dock side from config, falling back to the live session default."""
        cfg = getattr(self._state, "cfg", None) if self._state is not None else None
        raw = str(getattr(cfg, "tui_panel_side", None) or self._panel_side or "right").lower()
        return "left" if raw == "left" else "right"

    def set_panel_side(self, side: str, *, persist: bool = True) -> str:
        """Reorder the dock beside the transcript and optionally persist the pref."""
        applied = "left" if str(side).lower() == "left" else "right"
        self._set_panel_side(applied)
        if persist:
            cfg = getattr(self._state, "cfg", None) if self._state is not None else None
            if cfg is not None and hasattr(cfg, "tui_panel_side"):
                try:
                    cfg.tui_panel_side = applied
                    if hasattr(cfg, "save"):
                        cfg.save()
                except Exception as exc:
                    import sys as _sys

                    print(f"failed to persist panel side {applied!r}: {exc}", file=_sys.stderr)
        return applied

    def _set_panel_side(self, side: str) -> None:
        side = "left" if str(side).lower() == "left" else "right"
        self._panel_side = side
        try:
            row = self.query_one("#log-row", Horizontal)
            log = self.query_one("#log")
            panel = self.query_one("#panel")
        except Exception:
            return
        if side == "left":
            row.move_child(panel, before=log)
        else:
            row.move_child(log, before=panel)

    def _mount_panel_content(self, widget: Any) -> None:
        panel = self._query_panel()
        if panel is None:
            return
        for child in list(panel.children):
            child.remove()
        panel.mount(widget)
        panel.display = True
        self._panel_open = True

    def show_panel(self, side: str, widget: Any) -> None:
        """J1 (Vector P): dock ``widget`` beside the transcript on ``side``.
        Main-thread only (mounts widgets); the panel host hops here via
        ``call_from_thread``."""
        self._set_panel_side(side)
        self._mount_panel_content(widget)

    def hide_panel(self) -> None:
        """J1 (Vector P): undock the panel and return to a full-width transcript."""
        panel = self._query_panel()
        if panel is not None:
            for child in list(panel.children):
                child.remove()
            panel.display = False
        self._panel_open = False
        self._panel_view = None
        self._panel_file_target = None

    def current_panel_target(self) -> Optional[Path]:
        return self._panel_file_target

    def show_tree(self, side: Optional[str] = None) -> bool:
        """Point the one panel at the explorer tree."""
        from xlii.tui import panels

        if side is not None:
            self._set_panel_side(side)
        actions = panels.PanelActions(self._state, app=self)
        widget = panels.build_panel_view("explorer", self._state, actions=actions)
        if widget is None:
            try:
                self.notify("no explorer view", severity="warning", timeout=3)
            except Exception:
                # Best-effort UI warning; ignore notify failures to keep flow non-fatal.
                pass
            return False
        self._mount_panel_content(widget)
        self._panel_file_target = None
        self._panel_view = "explorer"
        return True

    def show_gallery(self, side: Optional[str] = None) -> bool:
        """Point the one panel at the image gallery (folded locker view)."""
        from xlii.tui import panels

        if side is not None:
            self._set_panel_side(side)
        actions = panels.PanelActions(self._state, app=self)
        widget = panels.build_panel_view("locker", self._state, actions=actions)
        if widget is None:
            try:
                self.notify("no gallery view", severity="warning", timeout=3)
            except Exception:
                # Best-effort UI warning; ignore notify failures to keep flow non-fatal.
                pass
            return False
        self._mount_panel_content(widget)
        self._panel_file_target = None
        self._panel_view = "locker"
        return True

    def show_file_view(self, target: Any) -> bool:
        """Point the one panel at a file's contents (dispatch by kind)."""
        from xlii.tui import panels

        try:
            path = Path(str(target)).expanduser()
        except Exception:
            return False
        if path.is_dir():
            return self.show_tree()
        kind = self._file_view_kind(path)
        rend = panels.dock_renderable(kind, {"path": str(path), "kind": kind}, state=self._state)
        self._mount_panel_content(VerticalScroll(Static(rend)))
        self._panel_file_target = path
        self._panel_view = None
        return True

    def _file_view_kind(self, path: Path) -> str:
        suffix = path.suffix.lower()
        if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            return "image"
        return "text"

    def _show_panel_view(self, side: str, view_name: str) -> bool:
        """Build view ``view_name`` from the registry and dock it. Returns False
        when the view can't be built."""
        side = "left" if str(side).lower() == "left" else "right"
        if view_name == "locker":
            return self.show_gallery(side)
        if view_name == "explorer":
            return self.show_tree(side)
        from xlii.tui import panels

        actions = panels.PanelActions(self._state, app=self)
        widget = panels.build_panel_view(view_name, self._state, actions=actions)
        if widget is None:
            try:
                self.notify(f"no panel view '{view_name}'", severity="warning", timeout=3)
            except Exception:
                # Best-effort UI warning; ignore notify failures to keep flow non-fatal.
                pass
            return False
        self._set_panel_side(side)
        self._mount_panel_content(widget)
        self._panel_file_target = None
        self._panel_view = view_name
        return True

    def _panel_on_attach(self, entry: Any, path: Any) -> None:
        """A view selected an item and attached it (PanelActions.attach_*).
        Repaint the bar so it surfaces as an A1 tab; the panel stays put."""
        self._refresh_status()
        name = entry.get("name") if isinstance(entry, dict) else None
        try:
            self.notify(f"attached {name or path}", timeout=3)
        except Exception:
            # Best-effort toast; attach already succeeded.
            pass

