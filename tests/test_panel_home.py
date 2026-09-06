"""Track I — home:// panel hub + Dock chassis."""

from __future__ import annotations

from xlii.addressing import vfs_list, vfs_stat
from xlii.addressing.builtins import register_builtins
from xlii.home_catalog import HOME_CATALOG
from xlii.panes.dock import Dock
from xlii.panes.home import HomePane, resolve_home_target
from xlii.panes import NAVIGATE


def setup_module():
    register_builtins()


def test_home_vfs_lists_catalog():
    root = vfs_stat("home://")
    assert root.kind == "container"
    names = [n.name for n in vfs_list("home://")]
    assert names == [e.label for e in HOME_CATALOG]
    assert "Files" in names and "Skills" in names and "Jobs" in names
    assert "Typed history" in names and "Home Stream" in names
    assert "Attachments" in names


def test_home_catalog_is_the_curated_f2_launcher():
    """Every panel + stream, sectioned; xlii:// stays the full kernel index."""
    labels = [e.label for e in HOME_CATALOG]
    assert labels[0] == "Home Stream" and "Files" in labels and "Git" in labels
    assert labels[-1] == "Plugins"
    assert "Attachments" in labels and "Canvas" in labels
    assert "Artifacts" in labels
    assert "Locker" not in labels
    assert all(e.hint and e.section and e.pane for e in HOME_CATALOG)
    assert len({e.section for e in HOME_CATALOG}) >= 4
    rows = HomePane("home://").render().rows
    assert rows[0].address == "key:cycle-open" and "open ·" in rows[0].text
    desk = next(r for r in rows if r.kind == "caption" and "desk" in r.text)
    assert desk is not None
    stream = next(r for r in rows if r.address == "home://stream")
    assert stream.text.startswith("Home Stream  · ")
    files = next(r for r in rows if r.address == "home://files")
    assert files.text.startswith("Files  · ")
    att = next(r for r in rows if r.address == "home://images")
    assert att.text.startswith("Attachments  · ")


def test_home_pane_navigate_action():
    pane = HomePane("home://")
    pane.select_index(next(i for i, e in enumerate(HOME_CATALOG) if e.slug == "files"))
    acts = pane.actions()
    assert acts and acts[0].outcome.kind == NAVIGATE
    assert acts[0].outcome.address.startswith("file://")


def test_home_hub_grays_the_other_slot():
    pane = HomePane("home://")
    pane.set_block("stream")
    rows = pane.render().rows
    stream = next(r for r in rows if r.address == "home://stream")
    files = next(r for r in rows if r.address == "home://files")
    assert stream.tone == "empty"
    assert files.tone == ""
    pane.select_index(0)  # Home Stream — blocked
    assert pane.actions() == []


def test_home_hub_cycle_open_mode(tmp_path, monkeypatch):
    from xlii.panes.home import HUB_OPEN_OTHER, hub_open_mode

    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path))
    # Re-import global config path? GlobalConfig uses module-level GLOBAL_CONFIG_DIR
    # captured at import. Cycle via a session cfg instead.
    from types import SimpleNamespace

    cfg = SimpleNamespace(hub_open="this", saved=[])
    cfg.save = lambda: cfg.saved.append(cfg.hub_open)
    monkeypatch.setattr("xlii.active_session.active_session", lambda: SimpleNamespace(cfg=cfg))
    pane = HomePane("home://")
    assert "this panel" in pane.render().rows[0].text
    assert pane.handle("cycle-open") is True
    assert hub_open_mode() == HUB_OPEN_OTHER
    pane.select_index(next(i for i, e in enumerate(HOME_CATALOG) if e.slug == "files"))
    assert pane.actions()[0].outcome.kind != NAVIGATE


def test_home_pane_back_is_noop():
    pane = HomePane("home://")
    assert pane.handle("back") is True
    assert pane.address.scheme == "home"


def test_dock_opens_home_pane():
    dock = Dock()
    pane = dock.open_address("home://", slot="A", focus=True)
    assert isinstance(pane, HomePane)
    leaves = [r for r in pane.render().rows if r.kind == "leaf"]
    assert len(leaves) == len(HOME_CATALOG)


def test_resolve_home_target_passthrough():
    assert resolve_home_target("skills://") == "skills://"
    assert resolve_home_target("history://") == "history://"


def _select_home_row(dock, slot, slug):
    dock.open_address("home://", slot=slot, focus=True)
    hp = dock.slots[slot]
    for i, e in enumerate(HOME_CATALOG):
        if e.slug == slug:
            hp.select_index(i)
    return hp


def test_home_navigate_opens_target_pane_type():
    """Selecting a home:// row must land on the target scheme's pane TYPE — not
    re-mount the HomePane in place (which re-drew the catalog → dead nav for
    everything but the special-cased history)."""
    dock = Dock()
    for slug, scheme in (("git", "git"), ("tasks", "tasks"), ("wiki", "wiki")):
        hp = _select_home_row(dock, "A", slug)
        pane = dock.dispatch(hp.actions()[0].outcome, from_slot="A")
        assert not isinstance(pane, HomePane), f"{slug} stayed on the home hub"
        assert pane.address.scheme == scheme
        assert dock.slots["A"] is pane


def test_home_navigate_same_scheme_remounts_in_place(tmp_path):
    """The explorer folder↔file morph must still re-mount in place (same pane
    instance) — the fix only changes cross-scheme navigation."""
    from xlii.panes import NAVIGATE, Outcome

    (tmp_path / "sub").mkdir()
    dock = Dock()
    pane = dock.open_address(f"file://{tmp_path}", slot="A", focus=True)
    same = dock.dispatch(Outcome(NAVIGATE, f"file://{tmp_path}/sub"), from_slot="A")
    assert same is pane  # same instance — re-mounted, not replaced
    assert same.address.scheme == "file"
