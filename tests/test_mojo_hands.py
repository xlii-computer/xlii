"""Mojo-keeper Phase A — hire gates dispatch; lab write needs a folder desk."""

from __future__ import annotations

import time
from types import SimpleNamespace

from tests.helpers import FakeConsole, make_agent, make_msg, make_project
from xlii.agent_stats import CallStats
from xlii.occupancy_store import mutate


def _schema_names(agent) -> set[str]:
    captured = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)
        return (make_msg("ok", None), None, False)

    agent._stream_orchestrator_iteration = fake
    agent.run_turn("hello")
    return {s["function"]["name"] for s in captured["schemas"]}


def _home_desk(tmp_path):
    home = make_project(tmp_path / "home")
    home.name = "scratch/home"
    return home


def _fake_worker(built):
    class FakeWorker:
        def __init__(self, **kw):
            built.update(kw)
            self.project = kw["project"]

        def run(self, task, context=None):
            return ("ok", CallStats())

    return FakeWorker


def test_conversational_hire_gates_dispatch_not_occupancy(tmp_path, monkeypatch):
    """hire=read advertises dispatch_subagent; hire=none does not; occupancy is irrelevant."""
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    agent = make_agent(tmp_path)
    agent.session.conversational = True

    agent.session.hire = "read"
    closed_read = _schema_names(agent)
    agent.session.hire = "none"
    closed_none = _schema_names(agent)
    assert "dispatch_subagent" in closed_read
    assert "dispatch_subagent" not in closed_none
    assert "write_file" not in closed_read

    now = time.time()
    mutate(lambda o: o.open_remote_lab(now=now), now=now)
    agent.session.hire = "read"
    open_read = _schema_names(agent)
    agent.session.hire = "none"
    open_none = _schema_names(agent)
    assert "dispatch_subagent" in open_read
    assert "dispatch_subagent" not in open_none


def test_lab_without_lab_project_refuses(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.session.hire = "write"
    monkeypatch.setattr(
        "xlii.agent_dispatch.WorkerAgent",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("WorkerAgent must not be built")),
    )
    text, stats = agent._run_worker({"task": "scaffold it", "role": "lab"})
    assert "no desk to write to" in text
    assert "switch into a folder first" in text
    assert stats.total_tokens == 0


def test_lab_on_home_desk_refuses(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.lab_project = _home_desk(tmp_path)
    agent.session.hire = "write"
    monkeypatch.setattr(
        "xlii.agent_dispatch.WorkerAgent",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("WorkerAgent must not be built")),
    )
    text, stats = agent._run_worker({"task": "scaffold it", "role": "lab"})
    assert "no desk to write to" in text
    assert stats.total_tokens == 0


def test_lab_on_folder_desk_uses_lab_project_not_persona(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    persona = agent.project
    desk = make_project(tmp_path / "desk")
    agent.lab_project = desk
    agent.session.hire = "write"
    agent.pool.acquire = lambda: SimpleNamespace(label="primary")
    built = {}
    monkeypatch.setattr("xlii.agent_dispatch.WorkerAgent", _fake_worker(built))
    text, _stats = agent._run_worker({"task": "scaffold it", "role": "lab"})
    assert built["project"] is desk
    assert built["project"] is not persona
    assert built["worker_writes"] is True
    assert built["role"] == "lab"
    assert "ok" in text


def test_bash_on_home_uses_persona_project(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    persona = agent.project
    agent.lab_project = _home_desk(tmp_path)
    agent.session.hire = "read"
    agent.pool.acquire = lambda: SimpleNamespace(label="primary")
    built = {}
    monkeypatch.setattr("xlii.agent_dispatch.WorkerAgent", _fake_worker(built))
    agent._run_worker({"task": "find the Cursor install", "role": "bash"})
    assert built["project"] is persona
    assert built["worker_writes"] is False
    assert built["role"] == "bash"


def test_lab_schema_drops_remote_control_and_names_home_refusal():
    from xlii.tool_schemas import dispatch_subagent_schema

    desc = dispatch_subagent_schema()["function"]["description"]
    assert "(remote-control)" not in desc
    assert "refused on Home" in desc
    assert "switch into a folder first" in desc


def test_h_mojo_sets_lab_project_and_hire(tmp_path, monkeypatch):
    from xlii.repl_cmds import mojo

    captured = {}

    def fake_oneshot(persona, prompt, **kw):
        agent = SimpleNamespace()
        on_agent = kw.get("on_agent")
        if on_agent is not None:
            on_agent(agent)
        captured["agent"] = agent
        captured["kw"] = kw
        return "ok"

    monkeypatch.setattr(
        "xlii.cmds.sessions.resolve._lookup_persona",
        lambda pid: SimpleNamespace(name=pid),
    )
    monkeypatch.setattr("xlii.cmds.sessions.ask.run_persona_oneshot", fake_oneshot)

    folder = make_project(tmp_path / "folder")
    ctx = {
        "console": FakeConsole(),
        "state": SimpleNamespace(
            project=folder, profile=None, persona=None, journal=None,
            pool=SimpleNamespace(), cfg=SimpleNamespace(), yolo=False,
        ),
    }
    assert mojo.h_mojo("/mojo find cursor", ctx) is True
    assert captured["agent"].lab_project is folder
    assert captured["kw"]["hire"] == "write"

    captured.clear()
    home = _home_desk(tmp_path)
    ctx["state"].project = home
    ctx["console"] = FakeConsole()
    assert mojo.h_mojo("/mojo find cursor", ctx) is True
    assert captured["agent"].lab_project is home
    assert captured["kw"]["hire"] == "read"


def test_input_mixin_sets_lab_project_and_hire(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_FG_TURNS", "1")
    from xlii.serve_face import FaceServer

    captured = {}

    def fake_oneshot(persona, prompt, **kw):
        agent = SimpleNamespace()
        on_agent = kw.get("on_agent")
        if on_agent is not None:
            on_agent(agent)
        captured["agent"] = agent
        captured["kw"] = kw
        return "ok"

    monkeypatch.setattr("xlii.cmds.sessions.ask.run_persona_oneshot", fake_oneshot)
    monkeypatch.setattr("xlii.persona.talk_persona_id", lambda **kw: "mojo")
    monkeypatch.setattr(
        "xlii.cmds.sessions.resolve._lookup_persona",
        lambda pid: SimpleNamespace(name="mojo"),
    )
    monkeypatch.setattr("xlii.repl_cmds.mojo.build_mojo_ambient", lambda state, q, **k: "")

    folder = make_project(tmp_path / "folder")
    from xlii.agent import SessionState

    state = SimpleNamespace(
        project=folder,
        pool=SimpleNamespace(),
        cfg=SimpleNamespace(),
        console=FakeConsole(),
        persona=None,
        journal=None,
        agent=SimpleNamespace(session=SessionState()),
        yolo=False,
        howto_mode=False,
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    server._run_chat_input("find my Cursor install")
    assert captured["agent"].lab_project is folder
    assert captured["kw"]["hire"] == "write"

    captured.clear()
    home = _home_desk(tmp_path)
    state.project = home
    server._run_chat_input("find my Cursor install")
    assert captured["agent"].lab_project is home
    assert captured["kw"]["hire"] == "read"
