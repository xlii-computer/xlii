"""BookmarksPane — the global bookmark library over mark://.

Lists bookmarks across all personas + the active store with provenance. BOTH verbs (Decision #1):
attach rides the mark's span on the next turn (primary); the load action seeds `/recall <mark>`
(qualified `<persona>:<mark>` only on a name clash) via the PREFILL seam — the sanctioned
review-before-run exception. No network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _no_session(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)


def _active_session(monkeypatch, tmp_path, *, active_marks=(), personas=None):
    """Install an ambient session: an active store (with active_marks) + on-disk personas."""
    import xlii.persona
    from xlii import active_session
    from xlii.persona import Persona
    from xlii.transcript import mark_last_turn, write_turn

    pdir = tmp_path / "personas"
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", pdir)
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", tmp_path / "chat")
    pdir.mkdir(parents=True, exist_ok=True)
    for name, marks in (personas or {}).items():
        (pdir / f"{name}.md").write_text(f"You are {name}.")
        p = Persona(name)
        for mk in marks:
            write_turn(p.turns_dir, "q", "a")
            mark_last_turn(p.turns_dir, mk)

    active_td = tmp_path / "active"
    active_td.mkdir(exist_ok=True)
    for mk in active_marks:
        write_turn(active_td, "q", "a")
        mark_last_turn(active_td, mk)

    state = SimpleNamespace(profile=SimpleNamespace(memory=SimpleNamespace(turns_dir=active_td)),
                            project=SimpleNamespace(name="proj"))
    monkeypatch.setattr(active_session, "_ACTIVE", state)
    return state


def test_bookmarks_pane_lists_with_provenance(tmp_path, monkeypatch):
    _active_session(monkeypatch, tmp_path, active_marks=["note"], personas={"bob": ["auth-insight"]})
    from xlii.panes.bookmarks import BookmarksPane

    texts = [r.text for r in BookmarksPane("mark://").render().rows]
    assert "auth-insight  · from bob" in texts        # cross-persona, provenance shown
    assert any(t.startswith("note  · from ") for t in texts)   # the active store, labelled too


def test_bookmarks_pane_load_seeds_bare_ref_when_unique(tmp_path, monkeypatch):
    _active_session(monkeypatch, tmp_path, personas={"bob": ["auth-insight"]})
    from xlii.panes.bookmarks import BookmarksPane

    act = next(a for a in BookmarksPane("mark://").actions() if a.name == "load")
    from xlii.panes import PREFILL
    assert act.outcome.kind == PREFILL
    assert act.outcome.text == "/recall auth-insight"    # unique → bare, no persona baggage


def test_bookmarks_pane_attach_is_the_primary(tmp_path, monkeypatch):
    """Decision #1: attach (the mark rides the next turn) is the grammar's primary;
    recall-paste stays as the sanctioned prefill; detach completes the toggle."""
    _active_session(monkeypatch, tmp_path, personas={"bob": ["auth-insight"]})
    from xlii.panes import ATTACH, DETACH
    from xlii.panes.bookmarks import BookmarksPane

    acts = BookmarksPane("mark://").actions()
    assert acts[0].name == "attach"
    assert {a.name for a in acts} >= {"attach", "load", "view", "detach"}
    assert acts[0].outcome.kind == ATTACH and acts[0].outcome.address == "mark://auth-insight"
    det = next(a for a in acts if a.name == "detach")
    assert det.outcome.kind == DETACH and det.outcome.address == "mark://auth-insight"


def test_bookmarks_pane_attach_qualifies_on_name_collision(tmp_path, monkeypatch):
    _active_session(monkeypatch, tmp_path, personas={"bob": ["plan"], "sol": ["plan"]})
    from xlii.panes.bookmarks import BookmarksPane

    p = BookmarksPane("mark://")
    addrs = []
    for i in range(len(p.render().rows)):
        p.select_index(i)
        addrs.append(p.actions()[0].outcome.address)
    assert "mark://bob:plan" in addrs and "mark://sol:plan" in addrs


def test_bookmarks_pane_riding_mark_gets_the_dot(tmp_path, monkeypatch):
    st = _active_session(monkeypatch, tmp_path, personas={"bob": ["a", "b"]})
    st.attached_docs = [("mark:a", "# mark: a\n…")]
    from xlii.panes.bookmarks import BookmarksPane

    rows = {r.text.split()[0]: r.accent for r in BookmarksPane("mark://").render().rows}
    assert rows == {"a": True, "b": False}


def test_bookmarks_pane_qualifies_on_name_collision(tmp_path, monkeypatch):
    _active_session(monkeypatch, tmp_path, personas={"bob": ["plan"], "sol": ["plan"]})
    from xlii.panes.bookmarks import BookmarksPane

    p = BookmarksPane("mark://")
    cmds = []
    for i in range(len(p.render().rows)):
        p.select_index(i)
        cmds.append(next(a for a in p.actions() if a.name == "load").outcome.text)
    assert "/recall bob:plan" in cmds and "/recall sol:plan" in cmds   # a clash → qualify with the persona


def test_bookmarks_pane_empty_outside_session():
    from xlii.panes.bookmarks import BookmarksPane

    p = BookmarksPane("mark://")
    r = p.render()
    assert r.empty and not r.rows
    assert p.actions() == [] and p.selection().node is None


def test_bookmarks_pane_nav_and_click(tmp_path, monkeypatch):
    _active_session(monkeypatch, tmp_path, personas={"bob": ["a", "b", "c"]})
    from xlii.panes.bookmarks import BookmarksPane

    p = BookmarksPane("mark://")               # rows sorted by name → a, b, c
    assert p.handle("down") and p.selection().node.name == "b"
    assert p.handle("end") and p.selection().node.name == "c"
    assert p.select_index(0) and p.selection().node.name == "a"
    assert p.handle("enter") is False          # falls through to the surface (actions)


def test_bookmarks_pane_view_action_targets_the_mark(tmp_path, monkeypatch):
    _active_session(monkeypatch, tmp_path, personas={"bob": ["auth-insight"]})
    from xlii.panes import RETARGET_SLOT
    from xlii.panes.bookmarks import BookmarksPane

    act = next(a for a in BookmarksPane("mark://").actions() if a.name == "view")
    assert act.outcome.kind == RETARGET_SLOT
    assert act.outcome.address == "mark://auth-insight"


def test_bookmarks_pane_ghost_persona_is_labelled(tmp_path, monkeypatch):
    """Persona.md deleted, turns leftover — the row says gone."""
    from xlii.persona import Persona

    _active_session(monkeypatch, tmp_path, personas={"ghost": ["old-idea"]})
    Persona("ghost").prompt_path.unlink()
    assert not Persona("ghost").exists()
    # turns still on disk
    assert (Persona("ghost").turns_dir).is_dir()
    from xlii.panes.bookmarks import BookmarksPane

    texts = [r.text for r in BookmarksPane("mark://").render().rows]
    assert any("old-idea" in t and "gone" in t for t in texts)


def test_dock_routes_mark_scheme_to_bookmarks_pane(tmp_path, monkeypatch):
    _active_session(monkeypatch, tmp_path, personas={"bob": ["auth-insight"]})
    from xlii.panes.dock import Dock

    dock = Dock()
    assert type(dock.open_address("mark://")).__name__ == "BookmarksPane"
