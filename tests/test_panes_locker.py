"""LockerPane — the attached-file (image) list over locker://; select renders in Pane 1, not here."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def locker(monkeypatch):
    from xlii import active_session

    files = [
        {"name": "cat.png", "path": "/tmp/cat.png", "kind": "image", "enabled": True},
        {"name": "dog.jpg", "path": "/tmp/dog.jpg", "kind": "image", "enabled": False},
    ]
    monkeypatch.setattr(active_session, "_ACTIVE", SimpleNamespace(attached_files=files))
    return files


def test_locker_pane_lists_names_and_enabled_dot(locker):
    from xlii.panes.locker import LockerPane

    p = LockerPane("locker://")
    rows = p.render().rows
    assert [r.text for r in rows] == ["cat.png", "dog.jpg"]   # just names
    assert [r.accent for r in rows] == [True, False]           # green dot = enabled (shared)


def test_locker_pane_open_on_pad_and_attach_toggle(locker):
    from xlii.panes import ATTACH, DETACH, RETARGET_SLOT
    from xlii.panes.locker import LockerPane

    p = LockerPane("locker://")
    acts = {a.name: a for a in p.actions()}
    assert set(acts) == {"open", "detach"}
    assert acts["open"].outcome.kind == RETARGET_SLOT
    assert acts["open"].outcome.address == "canvas://cat.png"
    assert acts["detach"].outcome.kind == DETACH
    assert p.actions()[0].name == "open"
    assert p.handle("down")
    acts2 = {a.name: a for a in p.actions()}
    assert "attach" in acts2 and acts2["attach"].outcome.kind == ATTACH


def test_locker_pane_navigation_and_click_select(locker):
    from xlii.panes.locker import LockerPane

    p = LockerPane("locker://")
    assert p.selection().node.name == "cat.png"
    assert p.handle("down") and p.selection().node.name == "dog.jpg"
    assert p.select_index(0) and p.selection().node.name == "cat.png"


def test_locker_pane_restores_selection_from_full_address(locker):
    from xlii.panes.locker import LockerPane

    p = LockerPane("locker://")
    p.mount("locker://", select="locker://dog.jpg")
    assert p.selection().node.name == "dog.jpg"


def test_dock_routes_locker_scheme_to_locker_pane(locker):
    from xlii.panes.dock import Dock

    assert type(Dock().open_address("locker://")).__name__ == "LockerPane"
