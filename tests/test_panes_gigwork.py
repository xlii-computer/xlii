"""GigworkPane + gigwork:// provider — the Panel "Gigwork" doorway.

Providers + jams as one sectioned list; every mutating action seeds a /gigwork ·
/jam command into the command line (review-before-run) via PREFILL. No network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.config import GlobalConfig


def _gig_cfg(monkeypatch, *, providers=("kimi",), allow=(), jams=None, key_set=True):
    cfg = GlobalConfig()
    cfg.gigwork = {
        "providers": {
            p: {"kind": "openai_compat", "base_url": "https://x.test/v1",
                "api_key_env": f"{p.upper()}_PANE_KEY", "model": f"{p}-model"}
            for p in providers
        },
        "defaults": {"allow": list(allow)},
    }
    if jams is not None:
        cfg.gigwork["jams"] = jams
    for p in providers:
        if key_set:
            monkeypatch.setenv(f"{p.upper()}_PANE_KEY", "sk-x")
        else:
            monkeypatch.delenv(f"{p.upper()}_PANE_KEY", raising=False)
    monkeypatch.setattr("xlii.addressing.builtins.gigwork.ambient_gig_cfg", lambda: cfg)
    return cfg


# --- provider ---------------------------------------------------------------

def test_gigwork_provider_lists_providers_and_jams(monkeypatch):
    _gig_cfg(monkeypatch)
    from xlii.addressing import resolve, vfs_list

    assert resolve("gigwork://").ok
    nodes = vfs_list("gigwork://")
    addrs = {n.address for n in nodes}
    assert "gigwork://provider/kimi" in addrs
    assert "gigwork://jam/second-opinion" in addrs        # stock rides along
    assert {n.extra["type"] for n in nodes} == {"gig-provider", "jam"}
    assert all(n.kind == "leaf" for n in nodes)


def test_gigwork_provider_reads_detail_sheets(monkeypatch):
    _gig_cfg(monkeypatch, allow=("kimi",))
    from xlii.addressing import vfs_read

    sheet = vfs_read("gigwork://provider/kimi").decode()
    assert "kimi-model" in sheet and "hireable via dispatch" in sheet
    assert "/gigwork kimi <task>" in sheet

    sheet = vfs_read("gigwork://jam/second-opinion").decode()
    assert "(stock)" in sheet
    assert "/jam add second-opinion xai gig --merge synth_conflicts --cap 2" in sheet


def test_gigwork_provider_unknown_and_bad_config(monkeypatch):
    _gig_cfg(monkeypatch)
    from xlii.addressing import resolve, vfs_list

    assert resolve("gigwork://provider/ghost").ok is False
    assert resolve("gigwork://what/ever").ok is False

    bad = GlobalConfig()
    bad.gigwork = {"providers": {"bad": {"kind": "nope"}}}
    monkeypatch.setattr("xlii.addressing.builtins.gigwork.ambient_gig_cfg", lambda: bad)
    # Graceful-empty on the provider side: stock jams still list, broken
    # provider rows vanish (the commands carry the actionable error).
    assert all(n.extra["type"] == "jam" for n in vfs_list("gigwork://"))


# --- pane -------------------------------------------------------------------

def test_gigwork_pane_sections_and_rows(monkeypatch):
    _gig_cfg(monkeypatch, providers=("kimi", "haiku"), allow=("kimi",))
    from xlii.panes.gigwork import GigworkPane

    p = GigworkPane("gigwork://")
    r = p.render()
    assert "2 providers" in r.title and "3 jams" in r.title
    texts = [row.text for row in r.rows]
    caps = [t for t in texts if t.startswith("─")]
    assert caps[0].startswith("─ Providers (2)") and caps[1].startswith("─ Jams (3)")
    kimi_line = next(t for t in texts if t.startswith("kimi"))
    assert "key set" in kimi_line and "· agent" in kimi_line
    sel = [row for row in r.rows if row.selected]
    assert len(sel) == 1 and sel[0].address == "gigwork://provider/haiku"  # sorted first


def test_gigwork_pane_key_unset_is_toned(monkeypatch):
    _gig_cfg(monkeypatch, key_set=False)
    from xlii.panes.gigwork import GigworkPane

    r = GigworkPane("gigwork://").render()
    kimi = next(row for row in r.rows if row.text.startswith("kimi"))
    assert "$KIMI_PANE_KEY unset" in kimi.text and kimi.tone == "modified"


def test_gigwork_pane_provider_actions_seed_commands(monkeypatch):
    _gig_cfg(monkeypatch)
    from xlii.panes import PREFILL, RETARGET_SLOT
    from xlii.panes.gigwork import GigworkPane

    p = GigworkPane("gigwork://")                    # kimi selected (sole provider)
    acts = {a.name: a for a in p.actions()}
    assert p.actions()[0].name == "hire"             # the Enter default
    assert acts["hire"].outcome.kind == PREFILL
    assert acts["hire"].outcome.text == "/gigwork kimi "
    assert acts["allow"].outcome.text == "/gigwork allow kimi"
    assert "deny" not in acts
    assert acts["remove"].outcome.text == "/gigwork rm kimi"
    assert acts["new"].outcome.text == "/gigwork add "
    assert acts["view"].outcome.kind == RETARGET_SLOT
    assert acts["view"].outcome.address == "gigwork://provider/kimi"

    _gig_cfg(monkeypatch, allow=("kimi",))
    acts = {a.name: a for a in GigworkPane("gigwork://").actions()}
    assert acts["deny"].outcome.text == "/gigwork deny kimi"
    assert "allow" not in acts


def test_gigwork_pane_jam_actions_and_edit_roundtrip(monkeypatch):
    _gig_cfg(monkeypatch, jams={
        "trio": {"members": [{"backend": "xai"},
                             {"backend": "kimi", "kit": "bash", "model": "k2"}],
                 "merge": "concat_digest", "max_parallel": 2}})
    from xlii.panes.gigwork import GigworkPane

    p = GigworkPane("gigwork://jam/trio")         # mount-select by address
    node = p.selection().node
    assert node.name == "trio" and node.extra["type"] == "jam"
    acts = {a.name: a for a in p.actions()}
    assert p.actions()[0].name == "ask"
    assert acts["ask"].outcome.text == "/jam trio "
    assert acts["edit"].outcome.text == (
        "/jam add trio xai kimi:bash@k2 --merge concat_digest --cap 2")
    assert acts["remove"].outcome.text == "/jam rm trio"
    assert acts["new"].outcome.text == "/jam add "

    p2 = GigworkPane("gigwork://jam/scout")       # stock: shadow, never delete
    names = [a.name for a in p2.actions()]
    assert "remove" not in names and "edit" in names


def test_gigwork_pane_empty_and_error_states(monkeypatch):
    _gig_cfg(monkeypatch, providers=())
    from xlii.panes import PREFILL
    from xlii.panes.gigwork import GigworkPane

    p = GigworkPane("gigwork://")                    # no providers; stock jams remain
    assert any("Providers (none)" in row.text for row in p.render().rows)

    bad = GlobalConfig()
    bad.gigwork = {"providers": {"bad": {"kind": "nope"}}}
    monkeypatch.setattr("xlii.addressing.builtins.gigwork.ambient_gig_cfg", lambda: bad)
    p = GigworkPane("gigwork://")
    rows = p.render().rows
    assert len(rows) == 1 and "config error" in rows[0].text
    assert p.actions() == []

    empty = GlobalConfig()
    empty.gigwork = {"providers": {}, "jams": {}}
    monkeypatch.setattr("xlii.addressing.builtins.gigwork.ambient_gig_cfg", lambda: empty)
    p = GigworkPane("gigwork://")
    # Stock jams always exist, so rows are never truly empty — but with no
    # selection target the pane still offers the composer door.
    assert p._rows                                    # stock jams listed
    assert any(a.outcome.kind == PREFILL for a in p.actions())


def test_gigwork_pane_nav_and_click(monkeypatch):
    _gig_cfg(monkeypatch, providers=("a1", "b2"))
    from xlii.panes.gigwork import GigworkPane

    p = GigworkPane("gigwork://")
    assert p.selection().node.name == "a1"
    assert p.handle("down") and p.selection().node.name == "b2"
    assert p.handle("up") and p.selection().node.name == "a1"
    assert p.handle("end") and p.selection().node.kind == "leaf"
    assert p.select_index(0) and p.selection().node.name == "a1"
    assert p.select_index(999) is False
    assert p.handle("enter") is False                # falls through to the surface


# --- dock routing + the doorway ----------------------------------------------

def test_dock_routes_gigwork_scheme(monkeypatch):
    _gig_cfg(monkeypatch)
    from xlii.panes.dock import Dock
    from xlii.panes.view import ViewPane

    dock = Dock()
    assert type(dock.open_address("gigwork://")).__name__ == "GigworkPane"
    # a single provider (a leaf) falls through to the text viewer's detail sheet
    assert isinstance(dock.open_address("gigwork://provider/kimi"), ViewPane)


def test_alt_h_doorway_opens_the_gigwork_pane(tmp_path, monkeypatch):
    """Alt-H (and the Panels-menu Gigwork item) open gigwork:// in Pane 2."""
    pytest.importorskip("textual")
    import asyncio

    from xlii.agent import SessionState
    from xlii.tui.dock_surface import register_dock_view
    from xlii.tui_textual import XliiApp

    _gig_cfg(monkeypatch)
    register_dock_view("vfs")            # the panel view the doorway routes through
    agent = SimpleNamespace(console=None, rail=None, debug=None, plan_mode=False,
                            active_mode=None, howto_mode=False, history=[],
                            model_override=None, session=SessionState())
    st = SimpleNamespace(shell_cwd=tmp_path,
                         project=SimpleNamespace(project_root=tmp_path, name="p", xli_dir=tmp_path),
                         agent=agent, attached_docs=[])
    app = XliiApp(project_name="p", agent=agent, run_turn=lambda q: ("", set(), None), state=st)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("h")
            await pilot.pause()
            await pilot.pause()
            assert app._panel_open
            assert app._current_dock_scheme() == "gigwork"
            assert ("doorway:h", "  Gigwork", True) in app._menu_items("Panel Workbench")

    asyncio.run(scenario())
