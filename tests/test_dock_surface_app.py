"""Integration: the kernel Dock surface docked inside the real XliiApp.

Proves the live wiring added for `/file-tab vfs` — register_dock_view → the panel-view registry
→ AppPanelHost delegation → app._show_panel_view mounts a DockSurface in #panel, focused and
key-drivable. Driven through Textual's run_test() pilot (no TTY); skipped without [tui]."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from xlii.tui import panels  # noqa: E402
from xlii.tui.dock_surface import DockSurface, register_dock_view, register_transcript_view  # noqa: E402
from xlii.tui_textual import XliiApp  # noqa: E402


def _fake_agent():
    from xlii.agent import SessionState

    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
        howto_mode=False, history=[], model_override=None, session=SessionState(),
    )


def _app(tmp_path):
    st = SimpleNamespace(shell_cwd=tmp_path, project=SimpleNamespace(project_root=tmp_path, name="proj"),
                         agent=_fake_agent())
    app = XliiApp(project_name="proj", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    return app, st


def _tree(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld")
    (tmp_path / "sub").mkdir()
    return tmp_path


# --- registration is visible to /file-tab's view validation ------------------


def test_register_dock_view_lists_in_panel_views():
    register_dock_view("vfs")
    assert "vfs" in panels.panel_views()


# --- the host delegates a non-tree view to the app's generic builder ----------


def test_app_panel_host_delegates_unknown_view_to_show_panel_view():
    calls = []
    fake_app = SimpleNamespace(
        call_from_thread=lambda fn, *a: fn(*a),
        show_gallery=lambda side: calls.append(("gallery", side)) or True,
        show_tree=lambda side: calls.append(("tree", side)) or True,
        _show_panel_view=lambda side, view: calls.append(("view", side, view)) or True,
    )
    host = panels.AppPanelHost(fake_app)
    assert host.show_panel("right", "vfs", state=None) is True
    assert calls == [("view", "right", "vfs")]
    # tree/gallery still take their dedicated paths
    calls.clear()
    host.show_panel("left", "locker", state=None)
    host.show_panel("right", "explorer", state=None)
    assert calls == [("gallery", "left"), ("tree", "right")]


# --- the real app: dock the vfs surface and drive it -------------------------


def test_show_panel_view_mounts_and_drives_dock_surface(tmp_path):
    _tree(tmp_path)
    register_dock_view("vfs")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            # Track I: bare vfs mounts home://; Files doorway / oneshot opens the tree.
            from xlii.tui.dock_surface import file_dock_root_address
            from xlii.panes.home import HomePane

            ok = app._show_panel_view("right", "vfs")
            await pilot.pause()
            assert ok is True
            assert app._panel_view == "vfs"
            surface = app.query_one("#panel").query_one(DockSurface)
            dock = surface.dock
            assert dock.slot_ids == ("A",)
            assert isinstance(dock.pane(dock.focused), HomePane)

            app._open_in_dock_view(file_dock_root_address(app._state))
            await pilot.pause()
            assert dock.pane(dock.focused).address.target == str(tmp_path)

            # keys reach the focused surface: move onto a.txt, open it — the SAME slot morphs
            # to the file (no second slot opens).
            await pilot.press("down")
            assert dock.pane("A").selection().node.name == "a.txt"
            await pilot.press("enter")
            await pilot.pause()
            from xlii.panes.view import ViewPane

            assert isinstance(dock.pane("A"), ViewPane)  # morphed in place, not a new slot
            assert dock.pane("A").address.target == str(tmp_path / "a.txt")

            # Backspace steps the working pane back to the tree (file -> explorer at the parent)
            await pilot.press("backspace")
            await pilot.pause()
            from xlii.panes.explorer import ExplorerPane

            assert isinstance(dock.pane("A"), ExplorerPane)
            assert dock.pane("A").address.target == str(tmp_path)

            # Escape hands the keyboard back to the REPL input
            await pilot.press("escape")
            await pilot.pause()
            assert app.focused is app.query_one("#input")

    asyncio.run(body())


def test_show_panel_view_docks_the_transcript(tmp_path, monkeypatch):
    """The conversation docks live as a TranscriptPane via /file-tab transcript."""
    from xlii.panes.transcript import TranscriptPane
    from xlii.transcript import write_turn

    write_turn(tmp_path / ".xlii" / "turns", "what is 2+2", "4")
    write_turn(tmp_path / ".xlii" / "turns", "and 3+3", "6")
    monkeypatch.chdir(tmp_path)  # conv://. resolves to the cwd project
    register_transcript_view("transcript")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            ok = app._show_panel_view("right", "transcript")
            await pilot.pause()
            assert ok is True
            assert app._panel_view == "transcript"
            surface = app.query_one("#panel").query_one(DockSurface)
            pane = surface.dock.pane(surface.dock.focused)
            assert isinstance(pane, TranscriptPane)
            shown = surface._views[surface.dock.focused].last_render
            from rich.console import Console
            import io

            c = Console(width=100, file=io.StringIO(), color_system=None)
            c.print(shown)
            text = c.file.getvalue()
            assert "what is 2+2" in text and "and 3+3" in text

    asyncio.run(body())


def test_file_tab_off_closes_the_vfs_panel(tmp_path):
    _tree(tmp_path)
    register_dock_view("vfs")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._show_panel_view("right", "vfs")
            await pilot.pause()
            assert app._panel_open is True
            app.hide_panel()
            await pilot.pause()
            assert app._panel_open is False
            assert app.query_one("#panel").display is False

    asyncio.run(body())


# --- Alt-<letter> doorway hotkeys: toggle a content type in Pane 2 -----------


def test_alt_doorway_hotkey_opens_and_toggles(tmp_path):
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert not app._panel_open
            app.action_doorway("s")           # Alt-S → open the skills doorway
            await pilot.pause()
            await pilot.pause()
            assert app._panel_open
            assert app._current_dock_scheme() == "skills"
            app.action_doorway("s")           # Alt-S again → toggle it closed
            await pilot.pause()
            assert not app._panel_open

    asyncio.run(scenario())


def test_alt_doorway_hotkey_switches_scheme(tmp_path):
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("d")           # docs
            await pilot.pause()
            await pilot.pause()
            assert app._current_dock_scheme() == "docs"
            app.action_doorway("m")           # switch to marks (not a toggle-close — different scheme)
            await pilot.pause()
            await pilot.pause()
            assert app._panel_open
            assert app._current_dock_scheme() == "mark"

    asyncio.run(scenario())


# --- F5: copy the pane's selection into the command line ---------------------


def test_f5_copies_pane_selection_into_input(tmp_path):
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("f")            # open the files explorer in Pane 2
            await pilot.pause()
            await pilot.pause()
            from xlii.tui.dock_surface import DockSurface

            dock = app.query_one(DockSurface).dock
            sel_addr = dock.pane(dock.focused).selection().node.address
            assert sel_addr                      # something is selected

            app.action_copy_selection()          # F5
            await pilot.pause()
            from xlii.tui_textual import _PromptInput

            assert sel_addr in app.query_one("#input", _PromptInput).text

    asyncio.run(scenario())


def test_f5_appends_space_separated(tmp_path):
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui_textual import _PromptInput

            inp = app.query_one("#input", _PromptInput)
            inp.text = "explain"
            app.action_doorway("f")
            await pilot.pause()
            await pilot.pause()
            app.action_copy_selection()
            await pilot.pause()
            assert inp.text.startswith("explain ")   # appended, space-separated, not clobbered

    asyncio.run(scenario())


def test_f5_noop_without_a_dock(tmp_path):
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui_textual import _PromptInput

            inp = app.query_one("#input", _PromptInput)
            inp.text = "hi"
            app.action_copy_selection()          # no panel docked → nothing to copy
            await pilot.pause()
            assert inp.text == "hi"

    asyncio.run(scenario())


# --- the top menu bar (Left/File/Command/Options/Right) ----------------------


def test_menu_title_spans_are_ordered_and_disjoint():
    from xlii.tui.menu_bar import MENU_TITLES, title_spans

    spans = title_spans()
    assert [t for *_, t in spans] == list(MENU_TITLES)
    for (s0, e0, _), (s1, _e1, _t) in zip(spans, spans[1:]):
        assert s0 <= e0 < s1                      # increasing, non-overlapping


def test_options_menu_is_all_cycle_toggles(tmp_path):
    """One grammar: the two former multi-row/popup settings are single rows whose
    id carries the NEXT value (cycle-on-click)."""
    app, st = _app(_tree(tmp_path))               # no cfg → default 'alt', tier auto
    items = app._menu_items("Options")
    assert [i[0] for i in items] == [
        "opt:hotkey:ctrl+alt",                    # current alt → next in the ring
        "opt:config", "opt:fkeys",
        "opt:tier:fast",                          # current auto → next tier
        "opt:theme", "opt:bindmake", "opt:screenshot",
    ]
    labels = {i[0]: i[1] for i in items}
    assert labels["opt:hotkey:ctrl+alt"] == "  Doorway key: alt-<letter>"
    assert labels["opt:tier:fast"] == "  Chat tier: auto"    # no '…' — no popup
    assert labels["opt:fkeys"] == "  F-keys: visible"


def test_options_cycle_rows_wrap_around(tmp_path):
    app, st = _app(_tree(tmp_path))
    # Doorway key at the ring's end wraps to alt (the row reads cfg).
    st.cfg = SimpleNamespace(tui_hotkey_modifier="ctrl+shift+alt")
    ids = [i[0] for i in app._menu_items("Options")]
    assert "opt:hotkey:alt" in ids
    # Tier 'off' wraps to auto.
    st.agent.session.chat_tier = None
    rows = {i[0]: i[1] for i in app._menu_items("Options")}
    assert rows.get("opt:tier:auto") == "  Chat tier: off"


def test_menu_hotkey_option_flips_the_doorway_keys(tmp_path):
    app, st = _app(_tree(tmp_path))
    assert "alt+s" in app._doorway_keys
    app._run_menu_action("opt:hotkey:ctrl+alt")   # pick a different modifier from the Options menu
    assert "ctrl+alt+s" in app._doorway_keys and "alt+s" not in app._doorway_keys


def test_menu_bar_click_opens_dropdown_then_runs_doorway(tmp_path):
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui.menu_bar import MenuDropdown, _MenuBar, title_spans

            bar = app.query_one("#menu-bar", _MenuBar)
            start = next(s for s, _e, t in title_spans() if t == "Panel Workbench")
            bar.on_click(SimpleNamespace(x=start + 1, stop=lambda: None))   # +1 for the bar's padding
            await pilot.pause()
            assert isinstance(app.screen, MenuDropdown)                     # the dropdown opened
            app.screen.dismiss("doorway:s")                                # choose Skills
            await pilot.pause()
            await pilot.pause()
            assert app._panel_open and app._current_dock_scheme() == "skills"

    asyncio.run(scenario())


async def _real_click_bar_title(pilot, bar, title):
    """Drive a REAL mouse click on ``title`` in the menu bar row (screen coords), exercising the
    full routing — the click must reach MenuDropdown.on_click through the open modal."""
    from xlii.tui.menu_bar import title_spans

    cr = bar.content_region
    start = next(s for s, _e, t in title_spans() if t == title)
    await pilot.click(offset=(cr.x + start + 1, cr.y))   # +1 to land on the title text
    await pilot.pause()
    await pilot.pause()


def test_open_dropdown_switches_when_another_title_is_clicked(tmp_path):
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui.menu_bar import MenuDropdown, _MenuBar, title_spans

            bar = app.query_one("#menu-bar", _MenuBar)
            opt_start = next(s for s, _e, t in title_spans() if t == "Options")
            bar.on_click(SimpleNamespace(x=opt_start + 1, stop=lambda: None))
            await pilot.pause()
            assert isinstance(app.screen, MenuDropdown) and app.screen._title == "Options"

            await _real_click_bar_title(pilot, bar, "Panel Workbench")  # click a *different* title while open
            assert isinstance(app.screen, MenuDropdown) and app.screen._title == "Panel Workbench"  # switched

    asyncio.run(scenario())


def test_clicking_the_open_title_again_closes_the_dropdown(tmp_path):
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui.menu_bar import MenuDropdown, _MenuBar, title_spans

            bar = app.query_one("#menu-bar", _MenuBar)
            opt_start = next(s for s, _e, t in title_spans() if t == "Options")
            bar.on_click(SimpleNamespace(x=opt_start + 1, stop=lambda: None))
            await pilot.pause()
            assert isinstance(app.screen, MenuDropdown)

            await _real_click_bar_title(pilot, bar, "Options")   # re-click SAME open title → toggle off
            assert not isinstance(app.screen, MenuDropdown)      # dropdown closed

    asyncio.run(scenario())


# --- F1 help · F2 cmds · F3 view · F4 edit ------------------------------------


def test_f1_help_seeds_howto(tmp_path):
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui_textual import _PromptInput

            app.action_help()
            await pilot.pause()
            assert app.query_one("#input", _PromptInput).text == "/howto "

    asyncio.run(scenario())


def test_f3_view_morphs_pane_to_the_selection(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    docmod.create_doc("readme")
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("d")                       # docs:// list, "readme" selected (a leaf)
            await pilot.pause()
            await pilot.pause()
            app.action_view_selection()                   # F3 → view it
            await pilot.pause()
            assert app._current_dock_scheme() == "docs"
            # the working slot morphed from the list (docs://) to the doc leaf (docs://readme)
            assert app._current_dock_address() == "docs://readme"

    asyncio.run(scenario())


def test_f4_attach_sends_the_selection_to_next_turn_context(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    docmod.create_doc("notes")
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))
    attached = []
    import xlii.attach as attach_mod

    monkeypatch.setattr(attach_mod, "attach_address", lambda state, addr: attached.append(addr))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("d")
            await pilot.pause()
            await pilot.pause()
            app.action_attach_selection()               # F4
            await pilot.pause()
            assert attached == ["docs://notes"]         # the pane's attach verb, dispatched

    asyncio.run(scenario())


def test_edit_arg_for_maps_schemes(tmp_path):
    app, st = _app(_tree(tmp_path))
    assert app._edit_arg_for("docs://conventions") == "--doc conventions"
    assert app._edit_arg_for("persona://ada") == "--id ada"
    assert app._edit_arg_for("file:///tmp/x.py") == "--file /tmp/x.py"
    assert app._edit_arg_for("jobs://t1") == ""           # not an editable kind


def test_f2_home_panel_opens_and_toggles(tmp_path):
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_home_panel()                       # F2 — open home://
            await pilot.pause()
            await pilot.pause()
            assert app._panel_open and app._current_dock_scheme() == "home"
            app.action_home_panel()                       # F2 again — toggle shut
            await pilot.pause()
            assert not app._panel_open

    asyncio.run(scenario())


def test_f9_jobs_and_f10_builder_bindings(tmp_path):
    # F9 = jobs, F10 = tasks (Decision #2 — F10 is NOT quit).
    from xlii.tui_textual import XliiApp

    actions = {b.key: b.action for b in XliiApp.BINDINGS}
    assert actions.get("f9") == "jobs_panel"
    assert actions.get("f10") == "task_builder"
    assert actions.get("f2") == "home_panel"
    assert actions.get("f4") == "edit_selection"
    assert actions.get("f5") == "copy_export"
    assert actions.get("f6") == "detach_all"


def test_f5_copy_export_writes_the_shell_dialect_to_the_input(tmp_path):
    """F5 on a file panel selection exports through to_shell_arg — a path inside
    the shell cwd renders RELATIVE (the destination's dialect), not file://."""
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui_textual import _PromptInput

            app.action_doorway("f")                       # files explorer at shell_cwd=tmp_path
            await pilot.pause()
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            app.action_copy_export()                      # F5
            await pilot.pause()
            assert inp.text == "sub"                      # relative path, not file:///tmp/…

    asyncio.run(scenario())


def test_f5_copy_export_failure_copies_the_native_address(tmp_path, monkeypatch):
    """When the export seam can't render an item (ok=False), F5 still copies —
    the item's NATIVE address, to the clipboard (and says why)."""
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    docmod.create_doc("notes")
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))
    copied = []
    import xlii.addressing as addressing

    monkeypatch.setattr(
        addressing, "to_shell_arg",
        lambda addr, **kw: addressing.ShellArg(
            ok=False, address=addressing.Address.parse(str(addr)), reason="boom",
        ),
    )

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("d")
            await pilot.pause()
            await pilot.pause()
            monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
            app.action_copy_export()
            await pilot.pause()
            assert copied == ["docs://notes"]             # native address fallback

    asyncio.run(scenario())


def test_panels_menu_copy_address_item(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    docmod.create_doc("readme")
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))
    copied = []

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("d")
            await pilot.pause()
            await pilot.pause()
            monkeypatch.setattr(app, "copy_to_clipboard", copied.append)
            app._run_menu_action("panel:copyaddr")
            await pilot.pause()
            assert copied == ["docs://readme"]

    asyncio.run(scenario())


def test_f6_detach_all_clears_every_attachment_channel(tmp_path, monkeypatch):
    app, st = _app(_tree(tmp_path))
    st.attached_docs = [("notes", "body"), ("point:idea", "span")]
    st.attached_refs = [("ada", "coll-1")]
    detached = []
    import xlii.repl_cmds.attach as attach_cmd

    monkeypatch.setattr(attach_cmd, "_detach_named",
                        lambda target, name, console, typ: detached.append(name) or True)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_detach_all()                       # F6
            await pilot.pause()
            assert sorted(detached) == ["ada", "notes", "point:idea"]

    asyncio.run(scenario())


# --- Command menu: jobs / tasks / plugins ------------------------------------


def test_commands_menu_items():
    from types import SimpleNamespace

    from xlii.tui_textual import XliiApp

    st = SimpleNamespace(shell_cwd=None, project=None, agent=SimpleNamespace(session=None), cfg=SimpleNamespace())
    app = XliiApp(project_name="p", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    ids = [i[0] for i in app._menu_items("Commands")]
    assert ids[:3] == ["attach:selection", "attach:detach", "attach:show"]
    assert ids[4:] == ["cmd:xlii", "cmd:cat:System", "cmd:cat:Network", "cmd:cat:Packages",
                       "cmd:cat:Searches", "cmd:history"]


def test_tools_menu_items():
    from types import SimpleNamespace

    from xlii.tui_textual import XliiApp

    st = SimpleNamespace(shell_cwd=None, project=None, agent=SimpleNamespace(session=None), cfg=SimpleNamespace())
    app = XliiApp(project_name="p", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    ids = [i[0] for i in app._menu_items("Tools")]
    builtin = ["tools:jobs", "tools:runtask", "tools:skills", "tools:newterm", "tools:gigwork",
               "tools:plugin", "tools:shelltools", "tools:taskbuilder", "tools:remote"]
    assert ids[:len(builtin)] == builtin
    assert all(i.startswith("bind:") for i in ids[len(builtin):])


def test_tools_jobs_opens_jobs_pane(tmp_path):
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._run_menu_action("tools:jobs")
            await pilot.pause()
            await pilot.pause()
            assert app._panel_open and app._current_dock_scheme() == "jobs"

    asyncio.run(scenario())


def test_run_task_opens_the_tasks_pane_and_plugin_opens_the_panel(tmp_path):
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui_textual import _PromptInput

            inp = app.query_one("#input", _PromptInput)
            app._run_menu_action("tools:runtask")
            await pilot.pause()
            await pilot.pause()
            # the panel select: pick a saved task → its load action seeds the command
            assert app._panel_open and app._current_dock_scheme() == "tasks"
            assert inp.text == ""                          # the menu itself typed nothing
            # Plugin… opens the catalog panel — subscription is the parallel act,
            # not /get invocation (the-fold B ask #1, post-D).
            called = []
            app._show_panel_view = lambda side, view: called.append((side, view)) or True
            app._run_menu_action("tools:plugin")
            await pilot.pause()
            assert called and called[0][1] == "plugins"
            assert inp.text == ""                          # input untouched by the panel act

    asyncio.run(scenario())


# --- Xlii / Project / Commands menus -------------------------------------------


def test_xlii_menu_has_terminal_clear_modes_and_exit():
    from types import SimpleNamespace

    from xlii.tui_textual import XliiApp

    st = SimpleNamespace(shell_cwd=None, project=None, agent=SimpleNamespace(session=None), cfg=SimpleNamespace())
    app = XliiApp(project_name="p", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    items = {i[0]: i[2] for i in app._menu_items("Xlii")}
    assert "xlii:newterm" not in items
    assert items["xlii:clear"] is True
    assert items["xlii:homehub"] is True
    assert items["xlii:exit"] is True            # Decision #2: Exit lives here, not F10
    # the mode-switch rows are wired now (the V3a/V3b lane): each runs its in-session
    # switch command. Scratch is a code-surface overlay, on here (default surface).
    assert items["xlii:mode:chat"] is True
    assert items["xlii:mode:code"] is True
    assert items["xlii:mode:scratch"] is True


def test_project_menu_rows():
    from types import SimpleNamespace

    from xlii.tui_textual import XliiApp

    st = SimpleNamespace(shell_cwd=None, project=None, agent=SimpleNamespace(session=None), cfg=SimpleNamespace())
    app = XliiApp(project_name="p", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    items = app._menu_items("Project")
    ids = [i[0] for i in items]
    # Artifacts only: the locker (per-turn ride-along) is evicted to Attach→Tray.
    builtin = ["proj:newfile", "proj:newfolder", "proj:create", "proj:file",
               "proj:wiki", "proj:plans", "proj:sync"]
    assert ids[:len(builtin)] == builtin
    assert all(i.startswith("bind:") for i in ids[len(builtin):])
    # The ' Panel' suffix noise is gone from the destination rows.
    assert not any(label.rstrip().endswith(" panel") for _id, label, _e in items)
    enabled = {i[0]: i[2] for i in items}
    assert enabled["proj:create"] is True        # in-session create via the code-entry gate (V3a init)


def test_project_menu_includes_user_bind(tmp_path, monkeypatch):
    from xlii import binds as B

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setenv("XLII_CONFIG_DIR", str(cfg))
    B.save_user_binds([B.Bind(task="echo-hello", menu="project", label="Hello")])
    from types import SimpleNamespace

    from xlii.tui_textual import XliiApp

    st = SimpleNamespace(shell_cwd=None, project=None, agent=SimpleNamespace(session=None),
                         cfg=SimpleNamespace())
    app = XliiApp(project_name="p", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    ids = [i[0] for i in app._menu_items("Project")]
    assert "bind:echo-hello" in ids


def test_attach_menu_items():
    """Attach verbs live under Commands. Skills/Rules/Ref/Locker stay panes."""
    from types import SimpleNamespace

    from xlii.tui.menu_bar import MENU_TITLES
    from xlii.tui_textual import XliiApp

    assert "Attach" not in MENU_TITLES
    st = SimpleNamespace(shell_cwd=None, project=None, agent=SimpleNamespace(session=None), cfg=SimpleNamespace())
    app = XliiApp(project_name="p", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    assert app._menu_items("Attach") == []
    ids = [i[0] for i in app._menu_items("Commands")]
    assert ids[:3] == ["attach:selection", "attach:detach", "attach:show"]


def test_attach_menu_dispatch(tmp_path):
    app, st = _app(_tree(tmp_path))
    calls, submitted = [], []
    app.action_attach_selection = lambda: calls.append("f4")
    app.action_detach_all = lambda: calls.append("f6")
    app._submit_prompt = lambda text: submitted.append(text)
    app._run_menu_action("attach:selection")
    app._run_menu_action("attach:detach")
    app._run_menu_action("attach:show")
    assert calls == ["f4", "f6"]
    assert submitted == ["/attachments"]


def test_panels_menu_is_the_flat_index():
    """Panel Workbench = the complete alphabetical index (incl. the once-missing Remotes
    row) + the two pane ops; labels are the honest names."""
    from types import SimpleNamespace

    from xlii.tui_textual import XliiApp

    st = SimpleNamespace(shell_cwd=None, project=None, agent=SimpleNamespace(session=None), cfg=SimpleNamespace())
    app = XliiApp(project_name="p", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    items = app._menu_items("Panel Workbench")
    ids = [i[0] for i in items]
    assert "doorway:r" in ids                     # Remotes joins the index
    labels = [i[1].strip() for i in items]
    assert {"Locker", "Rules", "Ref", "Remotes"} <= set(labels)
    assert not {"Images", "Docs", "Bookmarks"} & set(labels)
    assert labels[:-2] == sorted(labels[:-2])     # alphabetical index
    assert ids[-2:] == ["panel:copyaddr", "panel:close"]


def test_help_menu_howto_and_about(tmp_path):
    from types import SimpleNamespace

    from xlii.tui_textual import XliiApp

    st = SimpleNamespace(shell_cwd=None, project=None, agent=SimpleNamespace(session=None), cfg=SimpleNamespace())
    app = XliiApp(project_name="p", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    items = app._menu_items("Help")
    ids = [i[0] for i in items]
    assert ids[0] == "howto:"
    assert any(i.startswith("howto:") and i != "howto:" for i in ids)
    assert ids[-1] == "help:about"
    labels = [i[1].strip() for i in items if i[0].startswith("howto:") and i[0] != "howto:"]
    assert labels and all(len(lab) <= 22 and " — " not in lab for lab in labels)
    submitted = []
    app._submit_prompt = lambda text: submitted.append(text)
    app._run_menu_action("howto:install")
    app._run_menu_action("howto:")
    assert submitted == ["/howto install", "/howto"]


def test_menu_bar_titles_are_all_drawn():
    from xlii.tui.menu_bar import MENU_TITLES, menu_bar_renderable

    plain = menu_bar_renderable().plain
    for title in MENU_TITLES:
        assert title in plain
    assert "Help" in MENU_TITLES and "Panel Workbench" in MENU_TITLES
    assert "Attach" not in MENU_TITLES


def test_commands_menu_categories_and_no_file_ops():
    from types import SimpleNamespace

    from xlii.tui_textual import XliiApp

    st = SimpleNamespace(shell_cwd=None, project=None, agent=SimpleNamespace(session=None), cfg=SimpleNamespace())
    app = XliiApp(project_name="p", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    ids = [i[0] for i in app._menu_items("Commands")]
    assert ids[0] == "attach:selection"
    cmd_i = ids.index("cmd:xlii")
    cats = [f"cmd:cat:{name}" for name in app._CONSOLE_CATEGORIES]
    assert ids[cmd_i + 1:-1] == cats             # System…/Network…/Packages…/Searches…
    assert ids[-1] == "cmd:history"
    # No file ops (MC territory) and no git (there's a git panel) in the shortcut set.
    all_cmds = [c for cmds in app._CONSOLE_CATEGORIES.values() for c in cmds]
    assert not any(c.split()[0] in ("cp", "mv", "rm", "mkdir", "git") for c in all_cmds)


def test_options_tier_cycle_submits_through_the_real_command():
    from types import SimpleNamespace

    from xlii.tui_textual import XliiApp

    st = SimpleNamespace(shell_cwd=None, project=None, agent=_fake_agent(), cfg=SimpleNamespace())
    app = XliiApp(project_name="p", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)

    # The Options row carries the live pick (fresh sessions default to auto) and
    # its id carries the NEXT tier in the ring. No picker popup exists anymore.
    rows = {i[0]: i[1] for i in app._menu_items("Options")}
    assert rows["opt:tier:fast"] == "  Chat tier: auto"
    assert not hasattr(app, "_open_console_tier")     # the picker is deleted

    # Clicking submits /tier <next> through the real command — the menu is a
    # client of the command, not a second write-path to session state.
    submitted = []
    app._submit_prompt = lambda text: submitted.append(text)
    app._run_menu_action("opt:tier:fast")
    assert submitted == ["/tier fast"]


def test_project_newfile_prompts_a_name_and_creates(tmp_path):
    """Project → New project file… claims THE input line for the name (the
    minibuffer rule — no popup), then creates + opens the file."""
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui.input_surface import _PromptInput

            inp = app.query_one("#input", _PromptInput)
            app._run_menu_action("proj:newfile")
            await pilot.pause()
            assert inp.claim_active                       # the line morphs — no modal
            inp.text = "notes.txt"
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "notes.txt").exists()
            assert not inp.claim_active                   # released back to the REPL

    asyncio.run(scenario())


def test_project_newfolder_runs_system_task(tmp_path):
    """Project → New project folder… claims a name, then submits the stock task."""
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui.input_surface import _PromptInput

            inp = app.query_one("#input", _PromptInput)
            submitted = []
            app._submit_prompt = submitted.append
            app._run_menu_action("proj:newfolder")
            await pilot.pause()
            assert inp.claim_active
            inp.text = "plugin-test"
            await pilot.press("enter")
            await pilot.pause()
            assert submitted == ["/tasks run new-folder plugin-test --yes"]
            assert not (tmp_path / "plugin-test").exists()  # task not executed here

    asyncio.run(scenario())


def test_category_pick_without_placeholder_runs_immediately(tmp_path):
    """Menus DO: a placeholder-free shell pick (htop) runs through the real submit
    path — it never sits in the input waiting to be typed."""
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui_textual import _PromptInput

            inp = app.query_one("#input", _PromptInput)
            submitted = []
            app._submit_prompt = submitted.append
            app._run_menu_action("cmd:cat:System")      # opens the System sub-dropdown
            await pilot.pause()
            await pilot.pause()
            app.screen.dismiss("csh:0")                   # pick the first cmd (htop)
            await pilot.pause()
            assert submitted == ["!htop"]                 # DO, not type
            assert inp.text == "" and not inp.claim_active

    asyncio.run(scenario())


def test_category_pick_with_placeholder_claims_the_input_line(tmp_path):
    """A pick with a <placeholder> needs input: THE line transforms (claimed,
    seeded) — Enter runs the edited command, Esc cancels."""
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui_textual import _PromptInput

            inp = app.query_one("#input", _PromptInput)
            submitted = []
            app._submit_prompt = submitted.append
            app._open_console_category("Network")
            await pilot.pause()
            app.screen.dismiss("csh:2")                   # ping -c 4 <host>
            await pilot.pause()
            assert inp.claim_active
            assert inp.text == "!ping -c 4 <host>"        # seeded for editing
            inp.text = "!ping -c 4 example.com"
            await pilot.press("enter")
            await pilot.pause()
            assert submitted == ["!ping -c 4 example.com"]
            assert not inp.claim_active                   # released back to the REPL

    asyncio.run(scenario())


# --- F7 New… — create a doc / skill / persona / folder -----------------------


def test_create_skill_writes_and_validates(tmp_path):
    import pytest

    from xlii.skills import create_skill, load_skills

    md = create_skill("deploy", project_root=tmp_path, description="ship it")
    assert md.exists() and md.name == "SKILL.md"
    assert "deploy" in load_skills(tmp_path)
    with pytest.raises(FileExistsError):
        create_skill("deploy", project_root=tmp_path)
    with pytest.raises(ValueError):
        create_skill("bad/name", project_root=tmp_path)


def test_f7_opens_new_kind_picker_then_claims_the_line(tmp_path):
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui.input_surface import _PromptInput
            from xlii.tui.menu_bar import MenuDropdown

            app.action_new()                          # F7
            await pilot.pause()
            assert isinstance(app.screen, MenuDropdown)
            app.screen.dismiss("new:doc")             # choose Doc → the line morphs (no popup)
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            assert inp.claim_active
            assert not isinstance(app.screen, MenuDropdown)

    asyncio.run(scenario())


def test_f7_new_doc_creates_and_opens_in_pane(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path / "docs")
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._create_artifact("doc", "myproj")
            await pilot.pause()
            await pilot.pause()
            assert (tmp_path / "docs" / "myproj.md").exists()
            assert app._panel_open and app._current_dock_scheme() == "docs"

    asyncio.run(scenario())


def test_f7_new_folder_lands_in_the_file_panel_dir(tmp_path):
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("f")                   # files explorer rooted at the cwd (tmp_path)
            await pilot.pause()
            await pilot.pause()
            app._create_artifact("folder", "newdir")
            await pilot.pause()
            assert (tmp_path / "newdir").is_dir()

    asyncio.run(scenario())


def test_f7_duplicate_is_a_toast_not_a_crash(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    docmod.create_doc("dup")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._create_artifact("doc", "dup")        # already exists
            await pilot.pause()
            assert app.is_running                     # survived — surfaced as a toast

    asyncio.run(scenario())


# --- F8 Remove… — delete the selected doc / skill / persona (danger-gated) ---


def test_delete_skill_removes_and_reports(tmp_path):
    from xlii.skills import create_skill, delete_skill, load_skills

    create_skill("temp", project_root=tmp_path)
    assert "temp" in load_skills(tmp_path)
    assert delete_skill("temp", project_root=tmp_path) is True
    assert "temp" not in load_skills(tmp_path)
    assert delete_skill("ghost", project_root=tmp_path) is False   # missing → not-found, not a raise


def test_f8_removes_selected_doc_after_typed_phrase(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    docmod.create_doc("scratch")
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("d")                       # open docs:// (Alt-D)
            await pilot.pause()
            await pilot.pause()
            assert app._current_selection_address() == "docs://scratch"
            app.action_remove()                           # F8 → the line morphs (typed-phrase)
            await pilot.pause()
            from xlii.tui.input_surface import _PromptInput

            inp = app.query_one("#input", _PromptInput)
            assert inp.claim_active
            inp.text = "scratch"                          # type the name to confirm
            await pilot.press("enter")
            await pilot.pause()
            await pilot.pause()
            assert not (tmp_path / "docs" / "scratch.md").exists()

    asyncio.run(scenario())


def test_f8_wrong_phrase_keeps_the_file(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    docmod.create_doc("keep")
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("d")
            await pilot.pause()
            await pilot.pause()
            app.action_remove()
            await pilot.pause()
            from xlii.tui.input_surface import _PromptInput

            inp = app.query_one("#input", _PromptInput)
            inp.text = "something-else"                   # not the name → aborts
            await pilot.press("enter")
            await pilot.pause()
            assert (tmp_path / "docs" / "keep.md").exists()   # untouched
            assert not inp.claim_active                       # line released

    asyncio.run(scenario())


def test_f8_escape_keeps_the_file(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path / "docs")
    (tmp_path / "docs").mkdir()
    docmod.create_doc("keep2")
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("d")
            await pilot.pause()
            await pilot.pause()
            app.action_remove()
            await pilot.pause()
            await pilot.press("escape")                   # Esc releases without an answer
            await pilot.pause()
            assert (tmp_path / "docs" / "keep2.md").exists()  # untouched

    asyncio.run(scenario())


def test_f8_noop_on_non_removable_selection(tmp_path):
    register_dock_view("vfs")
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("f")                       # files explorer — file:// isn't removable
            await pilot.pause()
            await pilot.pause()
            app.action_remove()
            await pilot.pause()
            from xlii.tui.input_surface import _PromptInput

            inp = app.query_one("#input", _PromptInput)
            assert not inp.claim_active                   # no ask → nothing to delete

    asyncio.run(scenario())


# --- /image -> pane: open_in_dock routes an image into the working slot -------

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
)


def test_open_in_dock_routes_image_into_working_slot(tmp_path):
    (tmp_path / "art.png").write_bytes(_PNG)
    (tmp_path / "a.txt").write_text("x")
    register_dock_view("vfs")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._show_panel_view("right", "vfs")
            await pilot.pause()

            from xlii.panes.image import ImageViewPane
            from xlii.tui.dock_surface import open_in_dock

            assert open_in_dock(app, f"file://{tmp_path}/art.png") is True
            await pilot.pause()
            dock = app.query_one("#panel").query_one(DockSurface).dock
            working = dock.slot_ids[-1]  # slot B; A keeps the root explorer
            assert isinstance(dock.pane(working), ImageViewPane)
            assert dock.pane(working).address.target == str(tmp_path / "art.png")

    asyncio.run(body())


def test_open_in_dock_false_without_a_surface(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            from xlii.tui.dock_surface import open_in_dock

            assert open_in_dock(app, f"file://{tmp_path}") is False  # nothing docked

    asyncio.run(body())


def test_route_to_dock_false_without_host():
    panels.set_panel_host(None)
    assert panels.route_to_dock("file:///x.png") is False


def test_route_to_dock_delegates_to_open_in_dock():
    # a host whose app has no Dock surface -> delegates to open_in_dock, which returns False
    def _raise(*a, **k):
        raise RuntimeError("no such widget")

    fake_app = SimpleNamespace(query_one=_raise)
    panels.set_panel_host(panels.AppPanelHost(fake_app))
    try:
        assert panels.route_to_dock("file:///x.png") is False
    finally:
        panels.set_panel_host(None)


# --- step 4: a chip click morphs the open Dock (additive — legacy path when none) -----


def test_chip_click_morphs_open_dock(tmp_path):
    (tmp_path / "shot.png").write_bytes(_PNG)
    register_dock_view("vfs")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._show_panel_view("right", "vfs")  # the kernel Dock is now docked
            await pilot.pause()

            # a locker-file chip click routes into the Dock's working slot (an image → ImageView)
            app._activate_tab("file", {"path": str(tmp_path / "shot.png"), "kind": "image"})
            await pilot.pause()

            from xlii.panes.image import ImageViewPane

            dock = app.query_one("#panel").query_one(DockSurface).dock
            assert isinstance(dock.pane(dock.slot_ids[-1]), ImageViewPane)

    asyncio.run(body())


def test_tab_address_only_files_are_addressable(tmp_path):
    app, _st = _app(tmp_path)
    assert app._tab_address("file", {"path": "/x/a.png", "kind": "image"}) == "file:///x/a.png"
    assert app._tab_address("doc", [("conventions", "...")]) is None  # docs keep the modal path
    assert app._tab_address("files", None) is None  # the explorer tab is not a single address


# --- Enter on a file view must not crash (ENQUEUE_TURN with no turn-sink) -----


def test_enter_on_file_view_runs_a_turn(tmp_path):
    (tmp_path / "a.txt").write_text("hello world")
    (tmp_path / "sub").mkdir()
    register_dock_view("vfs")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui.dock_surface import file_dock_root_address

            app._open_in_dock_view(file_dock_root_address(app._state))
            await pilot.pause()
            submitted = []
            app._submit_prompt = lambda raw: submitted.append(raw)  # spy on the one turn pipeline

            await pilot.press("down")   # onto a.txt (sub/ is row 0)
            await pilot.press("enter")  # RETARGET (layout) -> morph to the ViewPane
            await pilot.pause()
            from xlii.panes.view import ViewPane

            dock = app.query_one("#panel").query_one(DockSurface).dock
            assert isinstance(dock.pane("A"), ViewPane)

            await pilot.press("enter")  # summarize -> turn-sink -> _submit_prompt
            await pilot.pause()
            assert submitted, "the view's summarize action should feed the turn pipeline"
            assert "Summarize this file." in submitted[0]
            assert "hello world" in submitted[0]  # the file content inlined as context
            assert app.is_running  # and it never crashes

    asyncio.run(body())


def test_with_context_inlines_text_and_references_an_image(tmp_path):
    from xlii.tui.dock_surface import _with_context

    (tmp_path / "n.txt").write_text("note body")
    out = _with_context("Summarize this file.", f"file://{tmp_path}/n.txt")
    assert "Summarize this file." in out and "note body" in out  # text inlined

    (tmp_path / "p.png").write_bytes(_PNG)
    out2 = _with_context("Describe this image.", f"file://{tmp_path}/p.png")
    assert "Describe this image." in out2 and "the file in question" in out2  # referenced, not decoded


# --- content-type doorway: a chip opens scheme:// in Pane 2 -------------------


def test_docs_doorway_opens_docs_scheme_in_the_dock(tmp_path, monkeypatch):
    import xlii.doc as docmod

    monkeypatch.setattr(docmod, "DOCS_DIR", tmp_path)
    (tmp_path / "conventions.md").write_text("# Conventions")
    register_dock_view("vfs")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._activate_tab("door", "docs")   # click the [docs] doorway
            await pilot.pause()
            await pilot.pause()  # let call_after_refresh dock the vfs surface, then morph it

            from xlii.panes.explorer import ExplorerPane

            dock = app.query_one("#panel").query_one(DockSurface).dock
            pane = dock.pane(dock.focused)
            assert isinstance(pane, ExplorerPane)
            assert pane.address.scheme == "docs"          # morphed to docs://
            assert "conventions" in [row.text for row in pane.render().rows]

    asyncio.run(body())


# --- commander chrome: F-key bar + status at the bottom ----------------------


def test_chrome_has_fkey_bar_and_status_at_bottom(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from textual.widgets import Static

            fk = app.query_one("#fkey-bar", Static)
            plain = getattr(fk.render(), "plain", str(fk.render()))
            # Commander verbs — F1 help · F2 Home Hub · … not the workbench pack.
            assert "F1 help" in plain
            assert "F2 Home Hub" in plain
            assert app.query_one("#status", Static) is not None  # status still present (now bottom)
            # status now flows AFTER the input + f-key bar (the very bottom)
            ids = [w.id for w in app.query("Static")]
            assert ids.index("status") > ids.index("fkey-bar")

    asyncio.run(body())


# --- F6 roles: the content-F-key opens persona:// in Pane 2 ------------------


def test_f6_opens_personas_in_the_dock(tmp_path, monkeypatch):
    import xlii.persona as pmod

    monkeypatch.setattr(pmod, "PERSONAS_DIR", tmp_path)
    (tmp_path / "ada.md").write_text("You are Ada.")
    register_dock_view("vfs")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_fkey_open("persona")   # the F6 binding's action
            await pilot.pause()
            await pilot.pause()

            from xlii.panes.explorer import ExplorerPane

            dock = app.query_one("#panel").query_one(DockSurface).dock
            pane = dock.pane(dock.focused)
            assert isinstance(pane, ExplorerPane)
            assert pane.address.scheme == "persona"
            assert "ada" in [row.text for row in pane.render().rows]

    asyncio.run(body())


def test_fkey_keypress_delegates_from_the_input(tmp_path):
    # F-keys don't reach app BINDINGS while the input holds focus, so _PromptInput delegates them.
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            called = []
            app.action_help = lambda: called.append(True)
            await pilot.press("f1")
            await pilot.pause()
            assert called == [True]  # f1 -> help, even with the input focused

    asyncio.run(body())


def test_fkey_spans_are_ordered_and_disjoint():
    from xlii.tui_textual import _FKEY_HINTS, _fkey_spans

    spans = _fkey_spans()
    assert [k for *_, k in spans] == [k.lower() for k, _ in _FKEY_HINTS]   # f1…f10, in order
    for (s0, e0, _), (s1, _e1, _k) in zip(spans, spans[1:]):
        assert s0 <= e0 < s1                      # increasing, non-overlapping


def test_fkey_bar_click_fires_the_binding(tmp_path):
    # Clicking a chip routes the same action its F-key press would (via _handle_fkey).
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui.status_strip import _FKeyBar

            called = []
            app.action_help = lambda: called.append(True)
            bar = app.query_one("#fkey-bar", _FKeyBar)
            # F1 help is always first in the pack
            start = next(s for s, _e, k in bar._spans if k == "f1")
            bar.on_click(SimpleNamespace(x=start + 1, stop=lambda: None))   # +1 for the bar's padding
            await pilot.pause()
            assert called == [True]   # clicking "F1 help" == pressing F1

    asyncio.run(body())


def test_fkey_bar_stays_commander_after_workbench_switch(tmp_path):
    """Workbench pack must not remap F2–F10 (muscle memory)."""
    from xlii.workbench import BUILTIN_WORKBENCHES

    async def body():
        app, st = _app(tmp_path)
        st.workbench = BUILTIN_WORKBENCHES["code"]
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._refresh_fkey_bar()
            await pilot.pause()
            from xlii.tui.status_strip import _FKeyBar

            bar = app.query_one("#fkey-bar", _FKeyBar)
            plain = getattr(bar.render(), "plain", str(bar.render()))
            assert "F1 help" in plain
            assert "F2 Home Hub" in plain
            assert "F4 edit" in plain
            assert "F9 jobs" in plain
            assert "F10 tasks" in plain
            assert "plan" not in plain
            assert bar.action_for("f2") is None

    asyncio.run(body())


# --- sub-dropdowns share the one dismiss protocol (the "switch" tuple crash) ----------


def test_console_category_subdropdown_switches_to_bar_menu(tmp_path):
    """Regression: with a Console category sub-dropdown (System…) open, clicking another bar
    title dismisses it with the ("switch", title, x) tuple — the sub-dropdown's old callback
    called .startswith on that tuple (AttributeError) and crashed the whole TUI. It must open
    the clicked menu instead, exactly like a top-level dropdown."""
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui.menu_bar import MenuDropdown, _MenuBar

            bar = app.query_one("#menu-bar", _MenuBar)
            app._open_console_category("System")
            await pilot.pause()
            assert isinstance(app.screen, MenuDropdown) and app.screen._title == "System"

            await _real_click_bar_title(pilot, bar, "Commands")  # bar click while the SUB-dropdown is open
            assert isinstance(app.screen, MenuDropdown) and app.screen._title == "Commands"

    asyncio.run(scenario())


def test_console_category_pick_runs_the_shell_command(tmp_path):
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            submitted = []
            app._submit_prompt = submitted.append
            app._open_console_category("System")
            await pilot.pause()
            app.screen.dismiss("csh:0")                 # pick the first shortcut (htop)
            await pilot.pause()
            await pilot.pause()
            assert submitted == ["!htop"]               # menus DO — it runs, never types

    asyncio.run(scenario())


def test_new_picker_switches_to_bar_menu(tmp_path):
    """The F7 New… kind picker rides the same protocol — a bar click while it's open switches
    menus rather than feeding the switch tuple to its item callback."""
    app, st = _app(_tree(tmp_path))

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            from xlii.tui.menu_bar import MenuDropdown, _MenuBar

            bar = app.query_one("#menu-bar", _MenuBar)
            app.action_new()
            await pilot.pause()
            assert isinstance(app.screen, MenuDropdown) and app.screen._title == "New"

            await _real_click_bar_title(pilot, bar, "Project")
            assert isinstance(app.screen, MenuDropdown) and app.screen._title == "Project"

    asyncio.run(scenario())


def test_app_input_sink_binds_pane_prefill_bridges():
    """Constructing the sink wires the pane-side bridges (TasksPane compose
    chain, Gitpanel stash asks) to this sink's claim/prefill."""
    from xlii.panes.git import GitpainPrefillBridge
    from xlii.tui.dock_surface import AppInputSink
    from xlii.tui.task_builder import ClaimPrefillBridge

    saved = (ClaimPrefillBridge._claim, ClaimPrefillBridge._prefill, GitpainPrefillBridge._prefill)
    seen: list = []
    fake = SimpleNamespace(
        _prefill_input=lambda text: seen.append(text),
        query_one=lambda sel: SimpleNamespace(claim_active=False),
    )
    try:
        sink = AppInputSink(fake)
        assert ClaimPrefillBridge._claim == sink.claim
        assert ClaimPrefillBridge._prefill == sink.prefill
        GitpainPrefillBridge.prefill("/gitpain stash -m fix")
        assert seen == ["/gitpain stash -m fix"]
    finally:
        ClaimPrefillBridge._claim, ClaimPrefillBridge._prefill = saved[0], saved[1]
        GitpainPrefillBridge._prefill = saved[2]
