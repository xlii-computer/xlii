"""Vector P — Split-Screen Panels: the seam-level + view tests.

Covers the seams Vector P publishes for Interaction Layer III:
  • the view registry — ``register_panel_view`` / ``panel_views`` / ``build_panel_view``
    (mirrors ``status.register_frame_tab`` / ``preview.register_preview``);
  • the panel host seam — ``set_panel_host`` / ``show_panel`` / ``hide_panel``
    (mirrors ``preview.set_surface_host``);
  • ``PanelActions`` — the view→app bridge (the select=attach verb);
  • ``dock_renderable`` — the inline preview builder (preview_for for A2 kinds,
    a light local preview for a plain file).

The registry / host / dock builder are pure (no Textual). The built-in
explorer/locker widgets need the [tui] extra and run under the Textual pilot.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from xlii.tui import panels


# --------------------------------------------------------------------------- #
#  view registry (pure)
# --------------------------------------------------------------------------- #

def test_builtin_views_registered():
    views = panels.panel_views()
    assert "explorer" in views
    assert "locker" in views


def test_register_and_unregister_panel_view():
    def provider(state, *, actions=None):
        return SimpleNamespace(name="custom-widget")

    panels.register_panel_view("xtest", provider)
    try:
        assert "xtest" in panels.panel_views()
        assert panels.get_panel_view("xtest") is provider
        built = panels.build_panel_view("xtest", SimpleNamespace())
        assert getattr(built, "name", None) == "custom-widget"
    finally:
        panels.unregister_panel_view("xtest")
    assert "xtest" not in panels.panel_views()


def test_register_rejects_empty_name():
    with pytest.raises(ValueError):
        panels.register_panel_view("", lambda s: None)


def test_build_unknown_view_returns_none():
    assert panels.build_panel_view("nope-not-a-view", SimpleNamespace()) is None


def test_build_adapts_provider_without_actions_kwarg():
    # a provider that only accepts (state) must still build (TypeError fallback).
    def provider(state):
        return SimpleNamespace(got=state)

    panels.register_panel_view("xnoact", provider)
    try:
        out = panels.build_panel_view("xnoact", "ST")
        assert getattr(out, "got", None) == "ST"
    finally:
        panels.unregister_panel_view("xnoact")


def test_build_swallows_a_raising_provider():
    def provider(state, *, actions=None):
        raise RuntimeError("boom")

    panels.register_panel_view("xraise", provider)
    try:
        assert panels.build_panel_view("xraise", SimpleNamespace()) is None
    finally:
        panels.unregister_panel_view("xraise")


# --------------------------------------------------------------------------- #
#  panel host seam (pure — a fake host)
# --------------------------------------------------------------------------- #

class _FakeHost(panels.PanelHost):
    def __init__(self):
        self.calls = []
        self._open = False
        self._side = "right"
        self._view = None

    def show_panel(self, side, view, *, state):
        self.calls.append(("show", side, view))
        self._open = True
        self._side = side
        self._view = view
        return True

    def hide_panel(self):
        self.calls.append(("hide",))
        self._open = False
        self._view = None
        return True

    def is_open(self):
        return self._open

    def current_side(self):
        return self._side

    def current_view(self):
        return self._view


def test_base_host_open_address_returns_false():
    """The abstract host has no dock — open_address/open_doorway report False so a
    non-TUI caller (e.g. /askjo wiki inline) falls back to printing the address."""
    host = panels.PanelHost()
    assert host.open_address("wiki://gitpain#stashing") is False
    assert host.open_doorway("wiki") is False


def test_show_hide_route_through_installed_host():
    host = _FakeHost()
    prev = panels.set_panel_host(host)
    try:
        assert panels.current_panel_host() is host
        assert panels.show_panel("left", "explorer", state=SimpleNamespace()) is True
        assert host.calls[-1] == ("show", "left", "explorer")
        assert panels.hide_panel() is True
        assert host.calls[-1] == ("hide",)
    finally:
        panels.set_panel_host(prev)


def test_show_hide_without_host_return_false():
    prev = panels.set_panel_host(None)
    try:
        assert panels.show_panel("right", "explorer", state=SimpleNamespace()) is False
        assert panels.hide_panel() is False
    finally:
        panels.set_panel_host(prev)


# --------------------------------------------------------------------------- #
#  PanelActions — the select=attach bridge
# --------------------------------------------------------------------------- #

def test_actions_attach_file_calls_state_and_app_hook():
    attached = {}

    def _attach(p):
        attached["path"] = p
        return {"name": "x.png", "path": p, "kind": "image"}

    state = SimpleNamespace(attach_file=_attach)
    seen = {}
    app = SimpleNamespace(_panel_on_attach=lambda entry, path: seen.update(entry=entry, path=path))
    actions = panels.PanelActions(state, app=app)
    entry = actions.attach_file("/tmp/x.png")
    assert attached["path"] == "/tmp/x.png"
    assert entry["kind"] == "image"
    assert seen["entry"]["name"] == "x.png"
    assert seen["path"] == "/tmp/x.png"


def test_actions_attach_file_headless_no_app():
    state = SimpleNamespace(attach_file=lambda p: {"name": "n", "path": p, "kind": "text"})
    actions = panels.PanelActions(state)  # app=None
    entry = actions.attach_file("/tmp/n.txt")  # must not raise
    assert entry["path"] == "/tmp/n.txt"


def test_actions_open_surface_and_view_file_route_to_app():
    calls = []
    app = SimpleNamespace(
        _open_preview_surface=lambda k, p: calls.append(("surface", k, p)),
        show_file_view=lambda p: calls.append(("view", p)),
    )
    actions = panels.PanelActions(SimpleNamespace(), app=app)
    actions.open_surface("image", {"path": "/a.png"})
    actions.view_file("/a.png")
    assert ("surface", "image", {"path": "/a.png"}) in calls
    assert ("view", "/a.png") in calls


# --------------------------------------------------------------------------- #
#  dock_renderable — image via preview_for, plain file via local preview
# --------------------------------------------------------------------------- #

def test_dock_renderable_image_uses_preview_for():
    # missing image still returns a renderable (image_preview's path-note group).
    r = panels.dock_renderable("image", {"name": "m.png", "path": "/no/such.png", "kind": "image"})
    assert r is not None


def test_dock_renderable_text_file_shows_head(tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("line one\nline two\nline three\n")
    r = panels.dock_renderable("text", {"name": "notes.txt", "path": str(f), "kind": "text"})
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    Console(file=buf, width=80, no_color=True).print(r)
    out = buf.getvalue()
    assert "notes.txt" in out
    assert "line two" in out


def test_dock_renderable_markdown_file_renders_markdown(tmp_path):
    f = tmp_path / "readme.md"
    f.write_text("# Title\n\n- a\n- b\n")
    r = panels.dock_renderable("text", {"path": str(f), "kind": "text"})
    from rich.console import Group

    assert isinstance(r, Group)  # header + Markdown body


def test_dock_renderable_missing_file_returns_note():
    from rich.text import Text

    r = panels.dock_renderable("other", {"path": "/no/such/file.bin", "kind": "other"})
    assert isinstance(r, Text)


# --------------------------------------------------------------------------- #
#  built-in views (Textual pilot)
# --------------------------------------------------------------------------- #

pytest.importorskip("textual")

from textual.app import App, ComposeResult  # noqa: E402
from textual.widgets import Static  # noqa: E402


def _run(coro_fn):
    asyncio.run(coro_fn())


class _Host(App):
    def compose(self) -> ComposeResult:
        yield Static("host")


def test_explorer_view_builds_and_attaches_on_select(tmp_path):
    (tmp_path / "hello.py").write_text("print('hi')\n")
    state = SimpleNamespace(shell_cwd=str(tmp_path), project=None)
    attached = {}

    def _attach(p):
        attached["path"] = p
        return {"name": "hello.py", "path": p, "kind": "text"}

    state.attach_file = _attach

    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            view = panels.build_panel_view("explorer", state, actions=panels.PanelActions(state))
            await app.mount(view)
            await pilot.pause()
            from textual.widgets import DirectoryTree

            assert view.query_one(DirectoryTree) is not None
            # select = attach: call the handler directly with a fake file event
            ev = SimpleNamespace(path=str(tmp_path / "hello.py"), stop=lambda: None)
            view.on_directory_tree_file_selected(ev)
            assert attached["path"].endswith("hello.py")
    _run(body)


def test_locker_view_lists_image_entries():
    files = [
        {"name": "a.png", "path": "/x/a.png", "kind": "image", "enabled": True},
        {"name": "doc.txt", "path": "/x/doc.txt", "kind": "text", "enabled": True},
        {"name": "b.jpg", "path": "/x/b.jpg", "kind": "image", "enabled": False},
    ]
    state = SimpleNamespace(attached_files=files)

    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            view = panels.build_panel_view("locker", state, actions=panels.PanelActions(state))
            await app.mount(view)
            await pilot.pause()
            from xlii.tui.panels import _LockerItem

            shown = view.query(_LockerItem)
            assert len(shown) == 2  # only the two image entries (txt excluded)
    _run(body)


def test_locker_view_empty_shows_note():
    state = SimpleNamespace(attached_files=[])

    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            view = panels.build_panel_view("locker", state, actions=panels.PanelActions(state))
            await app.mount(view)
            await pilot.pause()
            from xlii.tui.panels import _LockerItem

            assert len(view.query(_LockerItem)) == 0
    _run(body)
