"""Tests for the Textual adapter — DockSurface, the surface projection of a Dock.

The pure half (KEY_MAP, slot_renderable) needs no terminal. The live half drives a real Textual
app through run_test()'s pilot (no TTY), proving keys route into the panes and the
open-in-other-pane seam fires end to end. Skipped without [tui]."""

from __future__ import annotations

import asyncio
import io

import pytest

from rich.console import Console

from xlii.tui.dock_surface import KEY_MAP, dock_root_address, file_dock_root_address, slot_renderable
from xlii.panes.explorer import ExplorerPane


def _addr(p) -> str:
    return f"file://{p}"


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "deep.txt").write_text("deep")
    return tmp_path


def _text(renderable) -> str:
    c = Console(width=80, file=io.StringIO(), color_system=None)
    c.print(renderable)
    return c.file.getvalue()


# --- pure: the key table -----------------------------------------------------


def test_key_map_logical_tokens():
    assert KEY_MAP["down"] == "down"
    assert KEY_MAP["enter"] == "enter"
    assert KEY_MAP["left"] == "back"
    assert KEY_MAP["backspace"] == "back"
    # Tab is surface focus, not a pane token. Right/page turn a PDF.
    assert "tab" not in KEY_MAP
    assert KEY_MAP["right"] == "right"
    assert KEY_MAP["pagedown"] == "pagedown"
    assert KEY_MAP["pageup"] == "pageup"
    assert KEY_MAP["ctrl+h"] == "hidden"


# --- pure: the renderable builder -------------------------------------------


def test_slot_renderable_marks_selection(tree):
    pane = ExplorerPane(_addr(tree))  # "sub" focused, containers first
    out = _text(pane.render() and slot_renderable(pane.render()))
    assert _addr(tree) in out  # title
    assert "› sub" in out  # cursor on the selected row
    assert "a.txt" in out and "b.txt" not in out  # (b.txt isn't in this tree)


def test_slot_renderable_none_and_empty(tmp_path):
    assert "empty slot" in _text(slot_renderable(None))
    empty_pane = ExplorerPane(_addr(tmp_path))
    assert "(empty)" in _text(slot_renderable(empty_pane.render()))


def test_dock_root_address_is_home_hub(tree):
    """Track I1: bare Dock chassis is home://, not a silent file tree."""
    from types import SimpleNamespace

    state = SimpleNamespace(shell_cwd=tree, project=None)
    assert dock_root_address(SimpleNamespace()) == "home://"
    assert file_dock_root_address(state) == _addr(tree)


def test_file_dock_follows_files_root_pointer(tree):
    from types import SimpleNamespace

    state = SimpleNamespace(
        shell_cwd=tree,
        project=SimpleNamespace(project_root=tree, files_root="sftp://appbox/srv/apps/x"),
    )
    assert file_dock_root_address(state) == "sftp://appbox/srv/apps/x"


def test_dock_root_address_oneshot_named_scheme():
    """Track I1: cold open consumes _dock_open_address without a home flash."""
    from types import SimpleNamespace

    state = SimpleNamespace(_dock_open_address="skills://")
    assert dock_root_address(state) == "skills://"
    assert not getattr(state, "_dock_open_address", None)


# --- live: drive the widget through a real Textual app -----------------------

pytest.importorskip("textual")

from textual.app import App  # noqa: E402

from xlii.panes.dock import Dock  # noqa: E402
from xlii.panes.view import ViewPane  # noqa: E402
from xlii.tui.dock_surface import DockSurface, dock_view  # noqa: E402


class _DockApp(App):
    def __init__(self, dock: Dock) -> None:
        super().__init__()
        self._dock = dock

    def compose(self):
        yield DockSurface(self._dock)


def _dock_on(tree) -> Dock:
    d = Dock()
    d.open_address(_addr(tree), slot="A", focus=True)
    return d


def _slot_text(app, slot_id: str) -> str:
    return _text(app.query_one(f"#slot-{slot_id}").last_render)


def test_pilot_arrows_move_selection(tree):
    dock = _dock_on(tree)
    app = _DockApp(dock)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("down")  # sub/ -> a.txt
            assert dock.pane("A").selection().node.name == "a.txt"
            # the screen reflects it: cursor moved onto a.txt
            assert "› a.txt" in _slot_text(app, "A")
            await pilot.press("up")
            assert dock.pane("A").selection().node.name == "sub"

    asyncio.run(scenario())


def test_pilot_enter_into_container_navigates(tree):
    dock = _dock_on(tree)
    app = _DockApp(dock)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("enter")  # sub/ is focused -> go in
            assert dock.pane("A").address.target.endswith("/sub")
            assert "deep.txt" in _slot_text(app, "A")
            await pilot.press("backspace")  # back to parent
            assert dock.pane("A").address.target == str(tree)

    asyncio.run(scenario())


def test_pilot_enter_on_leaf_opens_other_pane(tree):
    """The whole adapter seam: navigate to a leaf, Enter falls through handle() to the pane's
    primary action, the Dock opens it in slot B, and the surface repaints B with the content."""
    dock = _dock_on(tree)
    app = _DockApp(dock)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.press("down")  # focus a.txt (leaf)
            await pilot.press("enter")  # not local nav -> primary action -> open in B
            assert isinstance(dock.pane("B"), ViewPane)
            assert dock.focused == "B"
            shown = _slot_text(app, "B")
            assert "hello" in shown and "world" in shown

    asyncio.run(scenario())


def test_pilot_tab_cycles_slot_focus(tree):
    dock = _dock_on(tree)
    app = _DockApp(dock)

    async def scenario():
        async with app.run_test() as pilot:
            assert dock.focused == "A"
            await pilot.press("tab")
            assert dock.focused == "B"
            await pilot.press("tab")
            assert dock.focused == "A"

    asyncio.run(scenario())


def test_dock_view_provider_builds_surface(tree):
    from types import SimpleNamespace

    from xlii.panes.home import HomePane

    state = SimpleNamespace(shell_cwd=tree, project=None)
    surface = dock_view(state)
    assert isinstance(surface, DockSurface)
    # Track I1: rooted at home:// hub (not the silent file tree)
    pane = surface.dock.pane(surface.dock.focused)
    assert isinstance(pane, HomePane)
    assert pane.address.scheme == "home"


# --- pane chrome: ‹back / ✕close + the attach/view/detach action buttons ------


def test_slot_has_back_close_and_action_buttons(tree):
    """Every pane carries a ‹back and ✕close button; the footer holds one button per action the pane
    offers (data-driven), so a directory shows its own open / open-other verbs."""
    dock = _dock_on(tree)
    app = _DockApp(dock)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            slot = app.query_one("#slot-A")
            # a directory (container) offers open/open-other → exactly those buttons, no greyed extras
            assert set(slot._act_buttons) == {"open", "open-other"}
            # count the nav buttons (‹ and ✕) present in the header
            from xlii.tui.dock_surface import _PaneButton

            navs = {b.act for b in slot.query(_PaneButton) if b.act in ("back", "close")}
            assert navs == {"back", "close"}

    asyncio.run(scenario())


def test_skills_buttons_attach_and_detach_via_session_sink(monkeypatch):
    """Clicking attach/detach on a skill routes ATTACH/DETACH through the Dock's session sink."""
    from types import SimpleNamespace

    from textual.widgets import Button

    from xlii import active_session
    from xlii.panes.dock import Dock

    monkeypatch.setattr("xlii.skills.load_skills",
                        lambda *a, **k: {"deploy": SimpleNamespace(name="deploy", short_description="", description="d")})
    monkeypatch.setattr(active_session, "_ACTIVE", None)

    dock = Dock(slots=("A",))
    dock.open_address("skills://", slot="A", focus=True)
    calls: list = []
    dock.set_session_sink(SimpleNamespace(attach=lambda a: calls.append(("attach", a)),
                                          detach=lambda a: calls.append(("detach", a))))
    app = _DockApp(dock)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            slot = app.query_one("#slot-A")
            # skills pane offers all three → the buttons are enabled
            assert not slot._act_buttons["attach"].disabled
            surface = app.query_one(DockSurface)
            surface.on_button_pressed(Button.Pressed(slot._act_buttons["attach"]))
            surface.on_button_pressed(Button.Pressed(slot._act_buttons["detach"]))
            assert calls == [("attach", "skills://deploy"), ("detach", "skills://deploy")]

    asyncio.run(scenario())


def test_pane_hotkeys_and_click_select(monkeypatch):
    """a/v/d act on the focused pane's selection; a body click selects the row under the cursor."""
    from types import SimpleNamespace

    from xlii import active_session
    from xlii.panes.dock import Dock
    from xlii.panes.view import ViewPane

    monkeypatch.setattr("xlii.skills.load_skills", lambda *a, **k: {
        "alpha": SimpleNamespace(name="alpha", short_description="", description="A"),
        "beta": SimpleNamespace(name="beta", short_description="", description="B"),
    })
    monkeypatch.setattr("xlii.skills.render_skill", lambda sk: "body")
    monkeypatch.setattr(active_session, "_ACTIVE", None)

    dock = Dock(slots=("A",))
    dock.open_address("skills://", slot="A", focus=True)
    calls: list = []
    dock.set_session_sink(SimpleNamespace(attach=lambda a: calls.append(("attach", a)),
                                          detach=lambda a: calls.append(("detach", a))))
    app = _DockApp(dock)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            # click row 1 (content y=3 → row index 1 = "beta") — mouse selection parity with arrows
            app.query_one("#slot-A")._body.on_click(SimpleNamespace(y=3))
            await pilot.pause()
            assert dock.pane("A").selection().node.name == "beta"
            # 'a' attaches the selected skill, 'd' detaches — keyboard peers of the footer buttons
            await pilot.press("a")
            assert calls[-1] == ("attach", "skills://beta")
            await pilot.press("d")
            assert calls[-1] == ("detach", "skills://beta")
            # 'v' morphs the pane to the skill's full description
            await pilot.press("v")
            await pilot.pause()
            assert isinstance(dock.pane("A"), ViewPane)

    asyncio.run(scenario())


def test_close_button_hides_surface_standalone(tree):
    """✕ with no app.hide_panel (standalone) hides the surface itself."""
    from textual.widgets import Button

    dock = _dock_on(tree)
    app = _DockApp(dock)

    async def scenario():
        async with app.run_test() as pilot:
            await pilot.pause()
            surface = app.query_one(DockSurface)
            slot = app.query_one("#slot-A")
            close = next(b for b in slot.query(Button) if getattr(b, "act", None) == "close")
            surface.on_button_pressed(Button.Pressed(close))
            assert surface.display is False

    asyncio.run(scenario())
