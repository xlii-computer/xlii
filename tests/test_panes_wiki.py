"""WikiPane — trust-marked page list over wiki://, green-dotting the riding pages."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii import wiki as W


@pytest.fixture(autouse=True)
def _no_session(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)


def _session_at(monkeypatch, xli_dir, attached_docs=()):
    from xlii import active_session

    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(project=SimpleNamespace(xli_dir=xli_dir),
                        attached_docs=list(attached_docs)),
    )


def test_wiki_pane_rows_wear_the_trust_marker(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    W.write_page(tmp_path, "draft", "unchecked claims")
    W.write_page(tmp_path, "facts", "checked")
    W.mark_verified(tmp_path, "facts")
    from xlii.panes.wiki import WikiPane

    rows = WikiPane("wiki://").render().rows
    assert [r.text for r in rows] == ["? draft", "✓ facts"]   # verify-before-trust, visible
    assert rows[0].selected and not rows[0].accent


def test_wiki_pane_results_mode_lists_ranked_hits(tmp_path, monkeypatch):
    """wiki://?q=… renders just the matching pages (the browser's results page),
    each a selectable row that opens the page — no forced jump to the first."""
    _session_at(monkeypatch, tmp_path)
    W.write_page(tmp_path, "gitpain", "# gitpain\n\nStash requires a message via -m.")
    W.write_page(tmp_path, "tasks", "# tasks\n\nSplit and join arms.")
    W.write_page(tmp_path, "colors", "# colors\n\nTheme palette.")
    from xlii.panes.wiki import WikiPane

    rendered = WikiPane("wiki://?q=stash message").render()
    names = [r.text.split(" ", 1)[1] for r in rendered.rows]
    assert names == ["gitpain"]                     # only the match, not the whole corpus
    assert rendered.rows[0].address == "wiki://gitpain"   # row opens the page
    assert '"stash message"' in rendered.title      # results-page title shows the query

    # empty query → the full list (unchanged browse behavior)
    assert len(WikiPane("wiki://").render().rows) == 3


def test_wiki_pane_green_dot_marks_the_riding_page(tmp_path, monkeypatch):
    W.write_page(tmp_path, "arch", "body")
    W.write_page(tmp_path, "other", "body")
    _session_at(monkeypatch, tmp_path, attached_docs=[("wiki:arch", "body")])
    from xlii.panes.wiki import WikiPane

    by = {r.text.split(" ", 1)[1]: r for r in WikiPane("wiki://").render().rows}
    assert by["arch"].accent is True
    assert by["other"].accent is False


def test_wiki_pane_restores_selection_from_full_address(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    W.write_page(tmp_path, "alpha", "a")
    W.write_page(tmp_path, "zeta", "z")
    from xlii.panes.wiki import WikiPane

    p = WikiPane("wiki://")
    assert p.select_index(1) and p.selection().node.name == "zeta"
    p.mount("wiki://", select="wiki://zeta")
    assert p.selection().node.name == "zeta"


def test_wiki_pane_offers_view_attach_detach(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    W.write_page(tmp_path, "arch", "body")
    from xlii.panes import ATTACH, DETACH, RETARGET_SLOT
    from xlii.panes.wiki import WikiPane

    p = WikiPane("wiki://")
    assert p.selection().node.name == "arch"
    assert p.selection().node.extra["type"] == "wiki"
    acts = {a.name: a for a in p.actions()}
    assert set(acts) == {"view", "attach", "detach"}
    assert acts["view"].outcome.kind == RETARGET_SLOT and acts["view"].outcome.address == "wiki://arch"
    assert acts["attach"].outcome.kind == ATTACH
    assert acts["detach"].outcome.kind == DETACH
    assert p.actions()[0].name == "view"   # view is the Enter default (non-mutating)


def test_wiki_pane_navigation_and_click(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    for n in ("a", "b", "c"):
        W.write_page(tmp_path, n, n)
    from xlii.panes.wiki import WikiPane

    p = WikiPane("wiki://")
    assert p.handle("down") and p.selection().node.name == "b"
    assert p.handle("end") and p.selection().node.name == "c"
    assert p.handle("home") and p.selection().node.name == "a"
    assert p.select_index(1) and p.selection().node.name == "b"
    assert p.select_index(9) is False
    assert p.handle("enter") is False      # falls through to the surface (actions)


def test_wiki_pane_mount_selects_the_addressed_page(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    for n in ("a", "b"):
        W.write_page(tmp_path, n, n)
    from xlii.panes.wiki import WikiPane

    assert WikiPane("wiki://b").selection().node.name == "b"


def test_wiki_pane_empty_outside_a_project(monkeypatch):
    from xlii.panes.wiki import WikiPane

    p = WikiPane("wiki://")
    r = p.render()
    assert r.empty and not r.rows
    assert p.actions() == [] and p.selection().node is None


def test_dock_routes_wiki_scheme_to_wiki_pane(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    W.write_page(tmp_path, "arch", "body")
    from xlii.panes.dock import Dock
    from xlii.panes.view import ViewPane

    dock = Dock()
    assert type(dock.open_address("wiki://")).__name__ == "WikiPane"
    # a single page (a leaf) falls through to the text viewer
    assert isinstance(dock.open_address("wiki://arch"), ViewPane)


def test_alt_w_doorway_opens_the_wiki_pane(tmp_path, monkeypatch):
    """The commander wiring: Alt-W (and the Right-menu Wiki item) open wiki:// in Pane 2."""
    pytest.importorskip("textual")
    import asyncio

    from xlii.agent import SessionState
    from xlii.tui.dock_surface import register_dock_view
    from xlii.tui_textual import XliiApp

    register_dock_view("vfs")            # the panel view the doorway routes through
    W.write_page(tmp_path, "arch", "# One kernel")
    agent = SimpleNamespace(console=None, rail=None, debug=None, plan_mode=False,
                            active_mode=None, howto_mode=False, history=[],
                            model_override=None, session=SessionState())
    st = SimpleNamespace(shell_cwd=tmp_path,
                         project=SimpleNamespace(project_root=tmp_path, name="p", xli_dir=tmp_path),
                         agent=agent, attached_docs=[])
    _session_at(monkeypatch, tmp_path)   # the ambient session the provider/pane read
    app = XliiApp(project_name="p", agent=agent, run_turn=lambda q: ("", set(), None), state=st)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("w")
            await pilot.pause()
            await pilot.pause()
            assert app._panel_open
            assert app._current_dock_scheme() == "wiki"
            assert ("doorway:w", "  Wiki", True) in app._menu_items("Panel Workbench")
            app.action_doorway("w")      # toggle closed
            await pilot.pause()
            assert not app._panel_open

    asyncio.run(scenario())
