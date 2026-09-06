"""Vector P/A — Split-Screen Panels: the XliiApp integration (Textual pilot).

Drives the two-pane structural seam on the real app: the #log-row holds exactly
#log + #panel, ``show_tree``/``show_file_view``/``show_gallery``, tab-click panel
swap, file-click attach-only (panel stays on tree), ``AppPanelHost`` bridge, and
the J6 launch listener install (guarded so it no-ops before Vector J merges).

Driven through Textual's run_test() pilot (no real TTY); skipped without [tui].
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from textual.containers import Vertical  # noqa: E402
from textual.widgets import Static  # noqa: E402

from xlii.tui import panels  # noqa: E402
from xlii.tui_textual import XliiApp  # noqa: E402


def _fake_agent():
    from xlii.agent import SessionState

    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
        howto_mode=False, history=[], model_override=None, session=SessionState(),
    )


def _state(tmp_path):
    st = SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj"),
        agent=_fake_agent(),
    )
    return st


def _app(tmp_path, **state_kw):
    st = _state(tmp_path)
    for k, v in state_kw.items():
        setattr(st, k, v)
    return XliiApp(project_name="proj", agent=st.agent,
                   run_turn=lambda q: ("", set(), None), state=st), st


def _run(coro_fn):
    asyncio.run(coro_fn())


def _panel(app):
    return app.query_one("#panel", Vertical)


def _log_row_children(app):
    row = app.query_one("#log-row")
    return [child.id for child in row.children]


# --------------------------------------------------------------------------- #
#  J1: the #log split — exactly two panes
# --------------------------------------------------------------------------- #

def test_log_row_has_exactly_two_children(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui.transcript import TranscriptLog

            assert app.query_one("#log", TranscriptLog) is not None
            assert app.query_one("#log-row") is not None
            assert _log_row_children(app) == ["log", "panel"]
            assert _panel(app).display is False  # closed by default
    _run(body)


def test_show_panel_docks_single_slot_then_hides(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.show_panel("right", Static("RIGHT", id="probe-r"))
            await pilot.pause()
            assert _panel(app).display is True
            assert app._panel_open is True and app._panel_side == "right"
            assert app.query_one("#probe-r", Static) is not None

            app.show_panel("left", Static("LEFT", id="probe-l"))
            await pilot.pause()
            assert _panel(app).display is True
            assert app._panel_side == "left"
            assert _log_row_children(app) == ["panel", "log"]
            assert app.query_one("#probe-l", Static) is not None

            app.hide_panel()
            await pilot.pause()
            assert _panel(app).display is False
            assert app._panel_open is False and app._panel_view is None
    _run(body)


def test_no_opposite_slot_preview_mount(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hello\n")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.show_tree()
            await pilot.pause()
            assert app._panel_view == "explorer"
            # attach must not mount a preview anywhere else — still one panel child.
            app._panel_on_attach({"name": "note.txt", "path": str(f), "kind": "text"}, str(f))
            await pilot.pause()
            assert app._panel_view == "explorer"
            assert len(_panel(app).children) == 1
            from xlii.tui.panels import ExplorerPanel

            assert _panel(app).query_one(ExplorerPanel) is not None
    _run(body)


# --------------------------------------------------------------------------- #
#  show_tree / show_file_view / show_gallery
# --------------------------------------------------------------------------- #

def test_show_tree_builds_explorer(tmp_path):
    (tmp_path / "f.py").write_text("x = 1\n")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            ok = app.show_tree()
            await pilot.pause()
            assert ok is True
            assert app._panel_view == "explorer"
            assert app.current_panel_target() is None
            from xlii.tui.panels import ExplorerPanel

            assert _panel(app).query_one(ExplorerPanel) is not None
    _run(body)


def test_show_file_view_renders_text_file(tmp_path):
    f = tmp_path / "readme.md"
    f.write_text("# Hi\n")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            ok = app.show_file_view(f)
            await pilot.pause()
            assert ok is True
            assert app.current_panel_target() == f
            assert app._panel_view is None
            assert len(_panel(app).children) == 1
    _run(body)


def test_show_gallery_builds_locker_grid(tmp_path):
    files = [{"name": "a.png", "path": str(tmp_path / "a.png"), "kind": "image", "enabled": True}]
    (tmp_path / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    async def body():
        app, st = _app(tmp_path, attached_files=files)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            ok = app.show_gallery()
            await pilot.pause()
            assert ok is True
            assert app._panel_view == "locker"
            from xlii.tui.panels import LockerPanel

            assert _panel(app).query_one(LockerPanel) is not None
    _run(body)


def test_show_panel_view_unknown_returns_false(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._show_panel_view("right", "no-such-view") is False
            assert app._panel_open is False
    _run(body)


def test_tab_click_swaps_panel_to_file_view(tmp_path):
    f = tmp_path / "api.md"
    f.write_text("# API\n")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.show_tree()
            await pilot.pause()
            app._activate_tab("file", {"path": str(f), "kind": "text"})
            await pilot.pause()
            assert app.current_panel_target() == f
            assert app._panel_view is None
    _run(body)


def test_files_chip_opens_the_dock(tmp_path):
    from xlii.tui.dock_surface import register_dock_view

    register_dock_view("vfs")  # registered at launch in production
    f = tmp_path / "x.txt"
    f.write_text("x\n")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.show_file_view(f)
            await pilot.pause()
            assert app.current_panel_target() == f
            # the `files` chip now opens the one kernel Dock (vfs), not the legacy tree
            app._activate_tab("files", None)
            await pilot.pause()
            assert app._panel_view == "vfs"
    _run(body)


def test_file_click_only_attaches_panel_stays_on_tree(tmp_path):
    f = tmp_path / "pick.py"
    f.write_text("print(1)\n")

    async def body():
        app, st = _app(tmp_path)
        attached = []
        st.attach_file = lambda p: attached.append(p) or {
            "name": "pick.py", "path": str(p), "kind": "text"
        }
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.show_tree()
            await pilot.pause()
            from xlii.tui.panels import ExplorerPanel

            view = _panel(app).query_one(ExplorerPanel)
            ev = SimpleNamespace(path=str(f), stop=lambda: None)
            view.on_directory_tree_file_selected(ev)
            await pilot.pause()
            assert attached == [str(f)]
            assert app._panel_view == "explorer"
            assert app.current_panel_target() is None
    _run(body)


# --------------------------------------------------------------------------- #
#  AppPanelHost — the host bridge /file-tab routes through
# --------------------------------------------------------------------------- #

def test_app_panel_host_show_hide_and_state(tmp_path):
    async def body():
        app, st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            host = panels.AppPanelHost(app)
            assert host.is_open() is False
            assert host.show_panel("left", "explorer", state=st) is True
            await pilot.pause()
            assert host.is_open() is True
            assert host.current_side() == "left"
            assert host.current_view() == "explorer"
            assert host.current_target() is None
            assert host.hide_panel() is True
            await pilot.pause()
            assert host.is_open() is False
    _run(body)


def test_app_panel_host_open_address_routes_to_dock(tmp_path):
    """open_address hands a precise address to the app's canonical dock opener
    (the seam /askjo wiki uses to pop a cited page open to the side)."""
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            seen = []
            app._open_in_dock_view = lambda addr: seen.append(addr)
            host = panels.AppPanelHost(app)
            assert host.open_address("wiki://gitpain#stashing") is True
            assert seen == ["wiki://gitpain#stashing"]
    _run(body)


def test_current_panel_target_module_seam(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("t\n")

    async def body():
        app, st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            prev = panels.set_panel_host(panels.AppPanelHost(app))
            try:
                assert panels.current_panel_target() is None
                assert panels.show_file_view(f) is True
                await pilot.pause()
                assert panels.current_panel_target() == f
                assert panels.show_tree() is True
                await pilot.pause()
                assert panels.current_panel_target() is None
            finally:
                panels.set_panel_host(prev)
    _run(body)


def test_set_panel_host_round_trips_through_module_seam(tmp_path):
    async def body():
        app, st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            prev = panels.set_panel_host(panels.AppPanelHost(app))
            try:
                assert panels.show_panel("right", "explorer", state=st) is True
                await pilot.pause()
                assert _panel(app).display is True
                assert panels.hide_panel() is True
                await pilot.pause()
                assert app._panel_open is False
            finally:
                panels.set_panel_host(prev)
    _run(body)


# --------------------------------------------------------------------------- #
#  J6: launch() installs Vector J's job listener (guarded)
# --------------------------------------------------------------------------- #

def test_launch_installs_and_clears_job_listener_when_jobs_present(tmp_path, monkeypatch):
    recorded = []
    monkeypatch.setattr("xlii.jobs.set_job_listener", lambda cb: recorded.append(cb))
    monkeypatch.setattr(XliiApp, "run", lambda self: None)

    from xlii import tui_textual

    st = _state(tmp_path)
    rc = tui_textual.launch(project_name="proj", agent=st.agent,
                            run_turn=lambda q: ("", set(), None), state=st)
    assert rc == 0
    assert len(recorded) == 2
    assert callable(recorded[0])
    assert recorded[1] is None


def test_launch_is_safe_without_a_jobs_module(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "xlii.jobs", None)
    monkeypatch.setattr(XliiApp, "run", lambda self: None)

    from xlii import tui_textual

    st = _state(tmp_path)
    assert tui_textual.launch(project_name="proj", agent=st.agent,
                              run_turn=lambda q: ("", set(), None), state=st) == 0


# --------------------------------------------------------------------------- #
#  Themes panel (Options → Theme…): open · click-to-apply · ✕ close
# --------------------------------------------------------------------------- #

def test_themes_panel_opens_applies_and_closes(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            # Options → Theme… routes here; the view builds from the registry.
            assert app._show_panel_view("right", "themes") is True
            await pilot.pause()

            from textual.widgets import Button, OptionList

            panel = app.query_one(panels.ThemesPanel)
            assert panel is not None
            ol = app.query_one("#panel-themes-list", OptionList)
            names = panel._theme_names()
            assert ol.option_count == len(names)
            assert names  # canvas-filtered dark themes by default

            # Selecting a row applies that theme through apply_theme → _apply_theme.
            target = next(n for n in names if n != app.theme)
            ol.highlighted = names.index(target)
            await pilot.pause()
            ol.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert app.theme == target

            # The ✕ (borderless header button, like the dock panes) undocks the panel.
            btn = app.query_one(".panel-close", Button)
            assert str(btn.label) == "✕"
            await pilot.click(".panel-close")
            await pilot.pause()
            assert app._panel_open is False
            assert _panel(app).display is False
    _run(body)


def test_set_panel_side_persists_to_config(tmp_path):
    cfg = SimpleNamespace(tui_panel_side="right", saves=0)
    cfg.save = lambda: setattr(cfg, "saves", cfg.saves + 1)

    async def body():
        app, _st = _app(tmp_path, cfg=cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app.set_panel_side("left") == "left"
            assert app._panel_side == "left"
            assert cfg.tui_panel_side == "left"
            assert cfg.saves == 1
            assert _log_row_children(app) == ["panel", "log"]
    _run(body)


def test_mount_restores_saved_panel_side(tmp_path):
    cfg = SimpleNamespace(tui_panel_side="left", save=lambda: None)

    async def body():
        app, _st = _app(tmp_path, cfg=cfg)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._panel_side == "left"
            assert _log_row_children(app) == ["panel", "log"]
    _run(body)
