"""The pure-python half of the panel seam: the name → view registry,
``PanelActions`` (what a view can do back to the session), the ``PanelHost``
contract, and the module-level host façade. Rich-free and textual-free by
design — this imports cleanly **without** the optional ``[tui]`` extra
(``repl_cmds/file_tab`` imports the package at session start). The concrete
Textual widgets live in their own per-panel modules and are pulled in by the
package façade only when textual is present.

Design rule (the parallel-build merge contract): the signatures here are
published day 1 and additive — registering a view or opening the dock is a
published-function call, not a shared edit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

# A provider turns the session into a dockable widget. It is called as
# ``provider(state, actions=actions)``; a provider that doesn't need the
# view-actions object may declare just ``provider(state)`` (we adapt the call).
PanelProvider = Callable[..., Any]


# --------------------------------------------------------------------------- #
#  The name → view registry (mirrors status.register_frame_tab / register_preview)
# --------------------------------------------------------------------------- #

_PANEL_VIEWS: dict[str, PanelProvider] = {}
_builtins_loaded = False


def register_panel_view(name: str, provider: PanelProvider) -> PanelProvider:
    """Register a dockable view *provider* under ``name`` (e.g. ``explorer``).

    Last registration wins so a vector (or a project) can override a built-in,
    and a re-import is a harmless no-op rather than a crash — registration is
    additive and collision-free, the point of the seam. Returns the provider so
    it can be used as a decorator."""
    if not isinstance(name, str) or not name:
        raise ValueError("panel view name must be a non-empty string")
    _PANEL_VIEWS[name] = provider
    return provider


def unregister_panel_view(name: str) -> None:
    """Drop a previously-registered view (no-op if absent) — plugin teardown and
    test isolation, mirroring ``status.unregister_frame_tab``."""
    _PANEL_VIEWS.pop(name, None)


def get_panel_view(name: str) -> Optional[PanelProvider]:
    _ensure_builtins()
    return _PANEL_VIEWS.get(name)


def panel_views() -> list[str]:
    """The registered view names, sorted (built-ins first by being registered up
    front). Used by ``/file-tab`` to validate a requested view and list them."""
    _ensure_builtins()
    return sorted(_PANEL_VIEWS)


def _ensure_builtins() -> None:
    """Lazily register the built-in views (client #1). Imported here, not at
    module top, so the widget classes (textual) are only touched when something
    actually asks for a view — the registry/host stay import-light without [tui]."""
    global _builtins_loaded
    if _builtins_loaded:
        return
    _builtins_loaded = True  # set first: a provider import error must not loop
    try:
        # Function-local on purpose: the factories live in the package façade
        # (textual-guarded); importing them here at module top would cycle.
        from xlii.tui.panels import (
            config_view,
            explorer_view,
            history_view,
            locker_view,
            themes_view,
        )

        register_panel_view("explorer", explorer_view)
        register_panel_view("locker", locker_view)
        register_panel_view("themes", themes_view)
        register_panel_view("config", config_view)
        register_panel_view("history", history_view)
    except Exception:
        # The registry still works for any explicitly-registered view; the
        # built-ins need [tui] and are best-effort.
        pass


def build_panel_view(name: str, state: Any, *, actions: Optional["PanelActions"] = None) -> Optional[Any]:
    """Build the widget for view ``name`` over ``state``, or ``None``.

    Adapts the provider call so a provider may accept ``(state, actions=...)`` or
    just ``(state)``; a missing view or a raising provider returns ``None`` so the
    caller (``/file-tab``) degrades to a note instead of taking down the app."""
    provider = get_panel_view(name)
    if provider is None:
        return None
    if actions is None:
        actions = PanelActions(state)
    try:
        return provider(state, actions=actions)
    except TypeError:
        try:
            return provider(state)
        except Exception:
            return None
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  PanelActions — what a view can do back to the session/app (the select verb)
# --------------------------------------------------------------------------- #

class PanelActions:
    """The bridge a panel view uses to act on a selection — *attach* (the primary
    verb), *view_file* (show a file in the one panel), *open surface* (A2's
    explicit deeper action), and *notify*.

    Kept tiny and tolerant: ``state`` is always present (so attach works headless
    via ``state.attach_file``); ``app`` is the running ``XliiApp`` when under the
    TUI (so file-view/surface/notify light up) and ``None`` in tests/headless,
    where those degrade to no-ops. A view never imports the app — it calls these."""

    def __init__(self, state: Any, *, app: Any = None) -> None:
        self.state = state
        self.app = app

    # -- the primary verb: select = attach (rides the next turn, shows as A1 tab)
    def attach_file(self, path: Any) -> Optional[dict]:
        """Stage a local file via the locker (``state.attach_file``) so it rides
        the next turn and surfaces as an A1 frame tab. Returns the entry dict."""
        entry: Optional[dict] = None
        attach = getattr(self.state, "attach_file", None)
        if callable(attach):
            try:
                entry = attach(path)
            except Exception:
                entry = None
        self._after_attach(entry, path)
        return entry

    def attach_doc(self, name: str, content: str) -> None:
        attach = getattr(self.state, "attach_doc", None)
        if callable(attach):
            try:
                attach(name, content)
            except Exception:
                # PanelActions is documented as tolerant: a failing attach degrades to a no-op.
                pass
        self._after_attach({"name": name, "kind": "doc"}, name)

    def _after_attach(self, entry: Optional[dict], path: Any) -> None:
        if self.app is not None:
            hook = getattr(self.app, "_panel_on_attach", None)
            if callable(hook):
                try:
                    hook(entry, path)
                except Exception:
                    # The attach already happened; the app's post-attach hook is decoration.
                    pass

    # -- show a file in the one panel (gallery click → file-view)
    def view_file(self, path: Any) -> None:
        if self.app is not None:
            hook = getattr(self.app, "show_file_view", None)
            if callable(hook):
                try:
                    hook(path)
                except Exception:
                    # Degrades to a no-op when the app can't show the file (see the class docstring).
                    pass

    # -- the explicit deeper action: A2's preview/edit surface (seam #2)
    def open_surface(self, kind: str, payload: Any) -> None:
        if self.app is not None:
            hook = getattr(self.app, "_open_preview_surface", None)
            if callable(hook):
                try:
                    hook(kind, payload)
                except Exception:
                    # Same contract: no surface opens, rather than raising into the panel view.
                    pass

    def notify(self, message: str, *, severity: str = "information") -> None:
        if self.app is not None and hasattr(self.app, "notify"):
            try:
                self.app.notify(message, severity=severity, timeout=3)
            except Exception:
                # A dropped toast is not worth failing the caller's verb.
                pass

    def prefill_input(self, text: str) -> None:
        """Seed the command line (review-before-run) — the history panel's verb."""
        if self.app is not None:
            hook = getattr(self.app, "_prefill_input", None)
            if callable(hook):
                try:
                    hook(text)
                except Exception as exc:
                    # Prefill is best-effort; a broken hook should not break the panel.
                    print(f"warning: prefill_input hook failed: {exc}")

    # -- an app/display setting: apply a Textual theme (the Themes panel's verb)
    def apply_theme(self, name: str) -> None:
        """Apply a Textual app theme by name — the Themes view's select verb. No-op headless
        (``app is None``, e.g. tests); the app validates + persists (``App._apply_theme``)."""
        if self.app is not None:
            hook = getattr(self.app, "_apply_theme", None)
            if callable(hook):
                try:
                    hook(name)
                except Exception:
                    # The theme stays as it was and the panel keeps working.
                    pass


# --------------------------------------------------------------------------- #
#  The host seam — who actually owns the dock (mirrors preview.set_surface_host)
# --------------------------------------------------------------------------- #

class PanelHost:
    """Anything that can dock/undock the side panel for us. The concrete host is
    ``AppPanelHost`` (wraps the running Textual app); a test may install its own.
    Kept tiny on purpose."""

    def show_panel(self, side: str, view: str, *, state: Any) -> bool:
        raise NotImplementedError

    def hide_panel(self) -> bool:
        raise NotImplementedError

    def is_open(self) -> bool:
        return False

    def current_side(self) -> str:
        return "right"

    def set_panel_side(self, side: str, *, persist: bool = True) -> str:
        """Persistable dock-side pref — no-op on the abstract host."""
        return "left" if str(side).lower() == "left" else "right"

    def current_view(self) -> Optional[str]:
        return None

    def current_target(self) -> Optional[Path]:
        return None

    def show_file(self, path: Any) -> bool:
        return False

    def show_tree(self, *, side: Optional[str] = None) -> bool:
        return False

    def show_gallery(self, *, side: Optional[str] = None) -> bool:
        return False

    def open_doorway(self, scheme: str) -> bool:
        return False

    def open_address(self, address: str) -> bool:
        """Open a specific ``scheme://target#anchor`` in the working pane (vs
        :meth:`open_doorway`, which opens a scheme root). ``False`` on the
        abstract host — a non-TUI caller falls back to printing the address."""
        return False


_HOST: Optional[PanelHost] = None


def set_panel_host(host: Optional[PanelHost]) -> Optional[PanelHost]:
    """Install (or clear, with ``None``) the panel host; return the previous one
    so the caller can restore it on teardown (``launch()`` does this in a
    finally)."""
    global _HOST
    prev = _HOST
    _HOST = host
    return prev


def current_panel_host() -> Optional[PanelHost]:
    return _HOST


def show_panel(side: str, view: str, *, state: Any) -> bool:
    """Dock ``view`` on ``side`` through the installed host. Returns ``True`` when
    a host took it (we're under the TUI), ``False`` when there is none (the caller
    prints a "panels need --tui" note)."""
    host = _HOST
    if host is None:
        return False
    return bool(host.show_panel(side, view, state=state))


def hide_panel() -> bool:
    """Undock the panel through the installed host. ``False`` when there is no
    host (nothing to hide)."""
    host = _HOST
    if host is None:
        return False
    return bool(host.hide_panel())


def open_doorway(scheme: str) -> bool:
    """Open ``scheme://`` in Pane 2 through the installed host — the same content-type doorway a chip
    click / ``Alt-<letter>`` hotkey opens, mounting the Dock if none is docked. ``/panel <target>``
    routes here. ``False`` when there is no host (off the TUI); the caller prints the "--tui" nudge."""
    host = _HOST
    if host is None:
        return False
    return bool(host.open_doorway(scheme))


def current_panel_target() -> Optional[Path]:
    """The file the panel is currently file-viewing, or None. Vector B consumes this."""
    host = _HOST
    if host is None:
        return None
    try:
        return host.current_target()
    except Exception:
        return None


def show_file_view(path: Any) -> bool:
    """Open the panel to a file's contents (file-view face)."""
    host = _HOST
    if host is None:
        return False
    return bool(host.show_file(path))


def show_tree(*, side: Optional[str] = None) -> bool:
    """Open the panel to the explorer tree."""
    host = _HOST
    if host is None:
        return False
    return bool(host.show_tree(side=side))


def show_gallery(*, side: Optional[str] = None) -> bool:
    """Open the panel to the image gallery (folded locker view)."""
    host = _HOST
    if host is None:
        return False
    return bool(host.show_gallery(side=side))


def route_to_dock(address: str) -> bool:
    """Open ``address`` in the live kernel Dock surface's working slot, if one is docked.

    The seam ``/image`` uses to render an image into the open pane instead of the transcript;
    a clicked chip (step 4) will reuse it. Returns ``False`` when there's no host, app, or
    mounted Dock surface — the caller falls back to an inline/transcript preview."""
    host = _HOST
    app = getattr(host, "_app", None) if host is not None else None
    if app is None:
        return False
    from xlii.tui.dock_surface import open_in_dock

    return open_in_dock(app, address)
