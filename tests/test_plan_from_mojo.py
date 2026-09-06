""" /plan --from-mojo — opt-in roll of recent talk into the planner."""

from __future__ import annotations

from types import SimpleNamespace

from tests.helpers import FakeConsole, make_agent
from xlii.transcript import write_turn


def _ctx(agent, state=None):
    return {
        "console": FakeConsole(),
        "agent": agent,
        "state": state,
        "project": agent.project,
    }


def test_plan_enter_flags_parse():
    from xlii.repl_cmds.mode import _plan_enter_flags

    assert _plan_enter_flags([]) == (False, None, [])
    assert _plan_enter_flags(["--from-mojo"]) == (True, None, [])
    assert _plan_enter_flags(["--with", "kimi", "--from-mojo"]) == (True, "kimi", [])
    assert _plan_enter_flags(["--from-mojo", "--with", "kimi"]) == (True, "kimi", [])
    assert _plan_enter_flags(["--with"]) == (False, "", [])
    assert _plan_enter_flags(["--add-context", "save"]) == (True, None, ["save"])


def test_bare_plan_does_not_roll_talk(tmp_path, monkeypatch):
    from xlii.repl_cmds.mode import _FROM_TALK_MARK, h_plan

    turns = tmp_path / "mojo-turns"
    write_turn(turns, "widget in src/w.py", "yeah a Widget class")
    persona = SimpleNamespace(name="mojo", turns_dir=turns)
    monkeypatch.setattr("xlii.cmds.sessions.resolve._lookup_persona", lambda pid: persona)
    monkeypatch.setattr("xlii.persona.talk_persona_id", lambda **k: "mojo")

    agent = make_agent(tmp_path)
    ctx = _ctx(agent, SimpleNamespace(project=agent.project, cfg=agent.cfg))
    assert h_plan("/plan", ctx) is True
    assert agent.plan_mode
    blob = " ".join(str(m.get("content") or "") for m in agent.history if isinstance(m, dict))
    assert _FROM_TALK_MARK not in blob
    assert "fo shizzle" not in blob
    assert "from talk:" not in ctx["console"].text


def test_from_mojo_rolls_recent_talk(tmp_path, monkeypatch):
    from xlii.repl_cmds.mode import _FROM_TALK_MARK, h_plan

    turns = tmp_path / "mojo-turns"
    write_turn(turns, "we could put a widget in src/w.py", "yeah, a small Widget class")
    write_turn(turns, "should we plan that?", "fo shizzle my nizzle")
    persona = SimpleNamespace(name="mojo", turns_dir=turns)
    monkeypatch.setattr("xlii.cmds.sessions.resolve._lookup_persona", lambda pid: persona)
    monkeypatch.setattr("xlii.persona.talk_persona_id", lambda **k: "mojo")

    agent = make_agent(tmp_path)
    ctx = _ctx(agent, SimpleNamespace(project=agent.project, cfg=agent.cfg))
    assert h_plan("/plan --from-mojo", ctx) is True
    assert agent.plan_mode
    blob = "\n".join(str(m.get("content") or "") for m in agent.history if isinstance(m, dict))
    assert _FROM_TALK_MARK in blob
    assert "fo shizzle my nizzle" in blob
    assert "widget in src/w.py" in blob
    assert "from talk: 2 recent mojo" in ctx["console"].text
    # Session-only — not a lab turn file.
    lab_turns = tmp_path / ".xlii" / "turns"
    if lab_turns.exists():
        assert not any(lab_turns.glob("*.md"))


def test_from_mojo_aliases_and_with_order(tmp_path, monkeypatch):
    from xlii.repl_cmds.mode import _FROM_TALK_MARK, h_plan

    turns = tmp_path / "mojo-turns"
    write_turn(turns, "idea", "ok")
    persona = SimpleNamespace(name="mojo", turns_dir=turns)
    monkeypatch.setattr("xlii.cmds.sessions.resolve._lookup_persona", lambda pid: persona)
    monkeypatch.setattr("xlii.persona.talk_persona_id", lambda **k: "mojo")

    agent = make_agent(tmp_path)
    ctx = _ctx(agent, SimpleNamespace(project=agent.project, cfg=agent.cfg))
    assert h_plan("/plan --add-context", ctx) is True
    blob = "\n".join(str(m.get("content") or "") for m in agent.history if isinstance(m, dict))
    assert _FROM_TALK_MARK in blob


def test_from_mojo_empty_talk_still_enters_plan(tmp_path, monkeypatch):
    from xlii.repl_cmds.mode import h_plan

    persona = SimpleNamespace(name="mojo", turns_dir=tmp_path / "empty-turns")
    monkeypatch.setattr("xlii.cmds.sessions.resolve._lookup_persona", lambda pid: persona)
    monkeypatch.setattr("xlii.persona.talk_persona_id", lambda **k: "mojo")

    agent = make_agent(tmp_path)
    ctx = _ctx(agent, SimpleNamespace(project=agent.project, cfg=agent.cfg))
    assert h_plan("/plan --from-talk", ctx) is True
    assert agent.plan_mode
    assert "no recent talk to roll" in ctx["console"].text


def test_from_mojo_is_idempotent(tmp_path, monkeypatch):
    from xlii.repl_cmds.mode import _FROM_TALK_MARK, h_plan

    turns = tmp_path / "mojo-turns"
    write_turn(turns, "a", "b")
    persona = SimpleNamespace(name="mojo", turns_dir=turns)
    monkeypatch.setattr("xlii.cmds.sessions.resolve._lookup_persona", lambda pid: persona)
    monkeypatch.setattr("xlii.persona.talk_persona_id", lambda **k: "mojo")

    agent = make_agent(tmp_path)
    ctx = _ctx(agent, SimpleNamespace(project=agent.project, cfg=agent.cfg))
    h_plan("/plan --from-mojo", ctx)
    n = sum(1 for m in agent.history if isinstance(m, dict)
            and _FROM_TALK_MARK in str(m.get("content") or ""))
    h_plan("/plan --from-mojo", ctx)
    n2 = sum(1 for m in agent.history if isinstance(m, dict)
             and _FROM_TALK_MARK in str(m.get("content") or ""))
    assert n == n2 == 1
    assert "already carrying" in ctx["console"].text
