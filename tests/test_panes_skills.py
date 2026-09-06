"""SkillsPane — a flat list of skill NAMES over skills://, green-dotting the riding skill."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _fake_skills(monkeypatch, mapping):
    monkeypatch.setattr("xlii.skills.load_skills", lambda *a, **k: mapping)


@pytest.fixture(autouse=True)
def _no_session(monkeypatch):
    # Default: no ambient session → nothing riding. Individual tests install one.
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)


def test_skills_pane_lists_names_only(monkeypatch):
    _fake_skills(monkeypatch, {
        "deploy": SimpleNamespace(name="deploy", short_description="ship it", description="line1\nline2"),
        "test": SimpleNamespace(name="test", short_description="run", description="t1"),
    })
    from xlii.panes.skills import SkillsPane

    p = SkillsPane("skills://")
    rows = p.render().rows
    assert [row.text for row in rows] == ["deploy", "test"]  # just names, sorted, no description
    assert rows[0].selected and not rows[0].accent  # nothing riding → no green dot


def test_skills_pane_green_dot_marks_the_riding_skill(monkeypatch):
    _fake_skills(monkeypatch, {
        "deploy": SimpleNamespace(name="deploy", short_description="", description="x"),
        "test": SimpleNamespace(name="test", short_description="", description="y"),
    })
    from xlii import active_session
    from xlii.panes.skills import SkillsPane
    from xlii.skills import SKILL_ATTACH_PREFIX

    # a session with `test` attached as a rider (rides the /skill doc channel)
    monkeypatch.setattr(active_session, "_ACTIVE",
                        SimpleNamespace(attached_docs=[(SKILL_ATTACH_PREFIX + "test", "body")]))
    p = SkillsPane("skills://")
    by_name = {row.text: row for row in p.render().rows}
    assert by_name["test"].accent is True    # riding → green dot
    assert by_name["deploy"].accent is False  # not riding → no dot


def test_skills_pane_offers_view_attach_detach(monkeypatch):
    _fake_skills(monkeypatch, {"deploy": SimpleNamespace(name="deploy", short_description="", description="x")})
    from xlii.panes import ATTACH, DETACH, RETARGET_SLOT
    from xlii.panes.skills import SkillsPane

    p = SkillsPane("skills://")
    assert p.selection().node.name == "deploy"
    acts = {a.name: a for a in p.actions()}
    assert set(acts) == {"view", "attach", "detach"}
    assert acts["view"].outcome.kind == RETARGET_SLOT and acts["view"].outcome.address == "skills://deploy"
    assert acts["attach"].outcome.kind == ATTACH and acts["attach"].outcome.address == "skills://deploy"
    assert acts["detach"].outcome.kind == DETACH
    assert p.actions()[0].name == "view"  # view is the Enter default (non-mutating)


def test_skills_pane_navigation(monkeypatch):
    _fake_skills(monkeypatch, {
        "a": SimpleNamespace(name="a", short_description="", description=""),
        "b": SimpleNamespace(name="b", short_description="", description=""),
    })
    from xlii.panes.skills import SkillsPane

    p = SkillsPane("skills://")
    assert p.selection().node.name == "a"
    assert p.handle("down") and p.selection().node.name == "b"
    assert p.handle("up") and p.selection().node.name == "a"
    assert p.handle("enter") is False  # no accordion — enter falls through to the surface


def test_skills_pane_shows_origin_and_groups_imports(monkeypatch):
    _fake_skills(monkeypatch, {
        "grounded-analysis": SimpleNamespace(
            name="grounded-analysis", scope="stock",
            short_description="", description="",
        ),
        "create-skill": SimpleNamespace(
            name="create-skill", scope="grok",
            short_description="", description="",
        ),
        "docx": SimpleNamespace(
            name="docx", scope="claude",
            short_description="", description="",
        ),
    })
    from xlii.panes.skills import SkillsPane

    p = SkillsPane("skills://")
    texts = [r.text for r in p.render().rows]
    assert "grounded-analysis  · stock" in texts
    assert "create-skill  · grok" in texts
    assert "docx  · claude" in texts
    assert any("── stock ──" in t for t in texts)
    assert any("import · grok" in t for t in texts)
    assert any("import · claude" in t for t in texts)


def test_skills_pane_restores_selection_from_full_address(monkeypatch):
    """FaceDeck remounts with select=skills://name — must not snap to row 0."""
    _fake_skills(monkeypatch, {
        "alpha": SimpleNamespace(name="alpha", scope="stock",
                                 short_description="", description=""),
        "zeta": SimpleNamespace(name="zeta", scope="stock",
                                short_description="", description=""),
    })
    from xlii.panes.skills import SkillsPane

    p = SkillsPane("skills://")
    assert p.select_index(1) and p.selection().node.name == "zeta"
    p.mount("skills://", select="skills://zeta")
    assert p.selection().node.name == "zeta"


def test_dock_routes_skills_scheme_to_skills_pane(monkeypatch):
    _fake_skills(monkeypatch, {"deploy": SimpleNamespace(name="deploy", short_description="", description="x")})
    from xlii.panes.dock import Dock

    assert type(Dock().open_address("skills://")).__name__ == "SkillsPane"
