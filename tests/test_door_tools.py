"""Mojo-keeper Phase D — door tools: schemas, switch, new, pane, memory."""

from __future__ import annotations

from types import SimpleNamespace

from tests.helpers import make_agent, make_msg, make_project, make_tool_ctx
from xlii.door_tools import (
    register,
    t_desk_new,
    t_desk_switch,
    t_explain_xlii,
    t_memory_set,
    t_pane_open,
)
from xlii.mode_contract import CHAT_DOOR_TOOLS, chat_tools_policy
from xlii.tools import auto_deny


def _schema_names(agent) -> set[str]:
    captured = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)
        return (make_msg("ok", None), None, False)

    agent._stream_orchestrator_iteration = fake
    agent.run_turn("hello")
    return {s["function"]["name"] for s in captured["schemas"]}


def test_door_schemas_on_conversational_turn_absent_under_controller(tmp_path):
    register()
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.hire = "read"
    names = _schema_names(agent)
    for n in CHAT_DOOR_TOOLS:
        assert n in names, n
    assert "dispatch_subagent" in names
    assert "write_file" not in names

    from xlii.mode_controller import PlanController

    agent.active_mode = PlanController()
    controlled = _schema_names(agent)
    for n in CHAT_DOOR_TOOLS:
        assert n not in controlled, n
    assert "read_file" in controlled


def test_chat_tools_policy_permits_doors():
    policy = chat_tools_policy()
    for n in CHAT_DOOR_TOOLS:
        assert policy.permits(n)
    assert not policy.permits("write_file")
    assert not policy.permits("bash")


def test_desk_switch_unknown_is_tool_text_no_state_change(tmp_path):
    sitting = SimpleNamespace(project=make_project(tmp_path), no_sync=False)
    ctx = make_tool_ctx(tmp_path)
    ctx.sitting = sitting
    before = sitting.project
    result = t_desk_switch(ctx, {"project": "nope"})
    assert "no such project" in result.content or "nope" in result.content
    assert sitting.project is before


def test_desk_new_auto_deny_refuses_and_creates_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.tools._confirm", auto_deny)
    target = tmp_path / "fresh-desk"
    ctx = make_tool_ctx(tmp_path)
    ctx.sitting = SimpleNamespace(project=None, no_sync=False)
    result = t_desk_new(ctx, {"path": str(target), "name": "fresh-desk"})
    assert "refused" in result.content.lower()
    assert result.is_error
    assert not target.exists()


def test_pane_open_git_repl_points_at_slash_git(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    ctx.emit_door = None
    result = t_pane_open(ctx, {"id": "git"})
    assert "/git" in result.content
    assert "no panes here" in result.content


def test_pane_open_git_on_face_emits_door_pane_git(tmp_path):
    events = []
    ctx = make_tool_ctx(tmp_path)
    ctx.emit_door = events.append
    result = t_pane_open(ctx, {"id": "git"})
    assert events == [{"action": "pane:git"}]
    assert "pane:git" in result.content


def test_pane_open_unknown_lists_valid_ids(tmp_path):
    result = t_pane_open(make_tool_ctx(tmp_path), {"id": "not-a-pane"})
    assert result.is_error
    assert "unknown pane id" in result.content
    assert "git" in result.content
    assert "projects" in result.content


def test_memory_set_flips_sitting_knob(tmp_path):
    sitting = SimpleNamespace(no_sync=False, journal_mute=False)
    ctx = make_tool_ctx(tmp_path)
    ctx.sitting = sitting
    result = t_memory_set(ctx, {"sync": False, "journal": False})
    assert sitting.no_sync is True
    assert sitting.journal_mute is True
    assert "sync=off" in result.content
    t_memory_set(ctx, {"sync": True, "journal": True})
    assert sitting.no_sync is False
    assert sitting.journal_mute is False


def test_explain_xlii_returns_selfdoc_or_no_match():
    result = t_explain_xlii(SimpleNamespace(), {"question": "how do I start a project"})
    assert "shipped self-doc" in result.content or "no self-doc" in result.content


def test_door_schemas_absent_on_code_posture_without_controller(tmp_path):
    """K6: code posture (non-conversational) must not advertise door tools."""
    register()
    agent = make_agent(tmp_path)
    agent.session.conversational = False
    agent.active_mode = None
    names = _schema_names(agent)
    for n in CHAT_DOOR_TOOLS:
        assert n not in names, n


def test_door_schemas_present_on_conversational_hire_turn(tmp_path):
    register()
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.hire = "write"
    agent.active_mode = None
    names = _schema_names(agent)
    for n in CHAT_DOOR_TOOLS:
        assert n in names, n
    assert "dispatch_subagent" in names


def test_memory_set_omitted_arg_unchanged(tmp_path):
    sitting = SimpleNamespace(no_sync=False, journal_mute=True, scratch=False)
    ctx = make_tool_ctx(tmp_path)
    ctx.sitting = sitting
    result = t_memory_set(ctx, {"sync": False})
    assert sitting.no_sync is True
    assert sitting.journal_mute is True  # omitted journal → unchanged
    assert "journal=off" in result.content
    result = t_memory_set(ctx, {"journal": True})
    assert sitting.no_sync is True  # omitted sync → unchanged
    assert sitting.journal_mute is False
    assert "sync=off" in result.content


def test_memory_set_scratch_refuses_sync_on(tmp_path):
    sitting = SimpleNamespace(no_sync=True, journal_mute=False, scratch=True)
    ctx = make_tool_ctx(tmp_path)
    ctx.sitting = sitting
    result = t_memory_set(ctx, {"sync": True})
    assert result.is_error
    assert "never-sync" in result.content.lower() or "scratch" in result.content.lower()
    assert sitting.no_sync is True  # unchanged


def test_memory_set_journal_false_stops_observe_turn(tmp_path, monkeypatch):
    from xlii.conversation import complete_turn_effects

    observed = []

    class _Jrnl:
        def observe_turn(self, *a, **k):
            observed.append((a, k))

    sitting = SimpleNamespace(
        no_sync=False,
        journal_mute=False,
        scratch=False,
        journal=_Jrnl(),
        agent=SimpleNamespace(history=[]),
        project=make_project(tmp_path),
        console=SimpleNamespace(),
        profile=None,
        shell_cwd="",
    )
    ctx = make_tool_ctx(tmp_path)
    ctx.sitting = sitting
    t_memory_set(ctx, {"journal": False})
    assert sitting.journal_mute is True

    monkeypatch.setattr("xlii.hooks.run_hooks", lambda *a, **k: None)
    monkeypatch.setattr("xlii.sync.end_of_turn_sync", lambda *a, **k: None)
    monkeypatch.setattr("xlii.turn_receipt.build_and_record_receipt", lambda *a, **k: None)
    monkeypatch.setattr("xlii.episode.update_episode", lambda *a, **k: None)

    complete_turn_effects(sitting, "hi", "yo", set(), SimpleNamespace(tool_calls=0))
    assert observed == []

    t_memory_set(ctx, {"journal": True})
    complete_turn_effects(sitting, "hi", "yo", set(), SimpleNamespace(tool_calls=0))
    assert len(observed) == 1


def test_memory_set_both_args_still_works(tmp_path):
    sitting = SimpleNamespace(no_sync=False, journal_mute=False, scratch=False)
    ctx = make_tool_ctx(tmp_path)
    ctx.sitting = sitting
    result = t_memory_set(ctx, {"sync": False, "journal": False})
    assert sitting.no_sync is True
    assert sitting.journal_mute is True
    assert "sync=off" in result.content and "journal=off" in result.content


def test_desk_switch_face_emits_land_without_direct_switch(tmp_path, monkeypatch):
    events = []
    switched = []

    def boom(*a, **k):
        switched.append(True)
        raise AssertionError("Face path must not call switch_to_code_project directly")

    monkeypatch.setattr("xlii.repl_cmds.switch.switch_to_code_project", boom)
    target = make_project(tmp_path)
    target.name = "lab"
    monkeypatch.setattr(
        "xlii.project_resolver.resolve_registered_project",
        lambda name: SimpleNamespace(ok=True, project=target, entry=None, reason=None),
    )
    sitting = SimpleNamespace(project=make_project(tmp_path), no_sync=False)
    ctx = make_tool_ctx(tmp_path)
    ctx.sitting = sitting
    ctx.emit_door = events.append
    result = t_desk_switch(ctx, {"project": "lab"})
    assert events == [{"action": "land:lab"}]
    assert switched == []
    assert "lab" in result.content


def test_desk_new_face_emits_land(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr("xlii.project_paths.user_home", lambda: tmp_path)
    created = make_project(tmp_path / "fresh")
    created.name = "fresh-desk"

    def fake_init(cfg, path, name=None, local_only=False):
        path.mkdir(parents=True, exist_ok=True)
        return created

    monkeypatch.setattr("xlii.sync.init_project", fake_init)
    switched = []

    def boom(*a, **k):
        switched.append(True)
        raise AssertionError("Face path must land via emit, not direct switch")

    monkeypatch.setattr("xlii.repl_cmds.switch.switch_to_code_project", boom)
    ctx = make_tool_ctx(tmp_path)
    ctx.sitting = SimpleNamespace(project=None, no_sync=False)
    ctx.emit_door = events.append
    ctx.yolo = True
    result = t_desk_new(ctx, {"path": str(tmp_path / "fresh-desk"), "name": "fresh-desk"})
    assert not result.is_error, result.content
    assert events == [{"action": f"land:{created.name}"}]
    assert switched == []


def test_emit_door_land_routes_join_project_and_go_home(tmp_path, monkeypatch):
    """When the turn is idle, land applies immediately on the server."""
    from xlii.serve_face import FaceServer

    state = SimpleNamespace(
        project=make_project(tmp_path),
        scratch=True,
        no_sync=True,
        console=SimpleNamespace(),
        cfg=SimpleNamespace(),
        as_context_dict=lambda: {"state": state},
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    called = []
    server.join_project = lambda name: called.append(("join", name)) or True  # type: ignore
    server.go_home = lambda: called.append(("home", "")) or True  # type: ignore
    sent = []
    server.send = sent.append  # type: ignore

    server._emit_door({"action": "land:lab"})
    assert ("join", "lab") in called
    assert any(e.get("type") == "door" and e.get("action") == "land:lab" for e in sent)

    called.clear()
    server._emit_door({"action": "land:scratch/home"})
    assert ("home", "") in called

    called.clear()
    server._emit_door({"action": "land:home"})
    assert ("home", "") in called


def test_emit_door_land_defers_while_agent_running(tmp_path):
    """Production oneshot: _agent_running is set for the whole turn.

    join_project/go_home refuse via _turn_mutation_busy — land must queue
    at emit and apply when the deferred flush runs after the turn clears.
    """
    from xlii.serve_face import FaceServer

    state = SimpleNamespace(
        project=make_project(tmp_path),
        scratch=True,
        no_sync=True,
        console=SimpleNamespace(),
        cfg=SimpleNamespace(),
        as_context_dict=lambda: {"state": state},
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    called = []
    server.join_project = lambda name: called.append(("join", name)) or True  # type: ignore
    server.go_home = lambda: called.append(("home", "")) or True  # type: ignore
    sent = []
    server.send = sent.append  # type: ignore

    server._agent_running.set()
    server._emit_door({"action": "land:lab"})
    assert called == [], "must not join while agent turn holds the busy gate"
    assert server._pending_door_land == "lab"
    assert any(e.get("type") == "door" and e.get("action") == "land:lab" for e in sent)

    # Mid-turn second land: last wins.
    server._emit_door({"action": "land:other"})
    assert called == []
    assert server._pending_door_land == "other"

    server._agent_running.clear()
    server._flush_deferred_door_land()
    assert ("join", "other") in called
    assert server._pending_door_land is None

    called.clear()
    server._agent_running.set()
    server._emit_door({"action": "land:home"})
    assert called == []
    server._agent_running.clear()
    # _finish_unit path also flushes.
    server._finish_unit(True)
    assert ("home", "") in called


def test_flush_deferred_door_land_skips_when_turn_cancelled(tmp_path):
    """Stop must not apply a desk_switch queued mid-turn."""
    from xlii.serve_face import FaceServer

    state = SimpleNamespace(
        project=make_project(tmp_path),
        scratch=True,
        no_sync=True,
        console=SimpleNamespace(),
        cfg=SimpleNamespace(),
        as_context_dict=lambda: {"state": state},
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    called = []
    server.join_project = lambda name: called.append(("join", name)) or True  # type: ignore
    server._agent_running.set()
    server._emit_door({"action": "land:lab"})
    assert server._pending_door_land == "lab"

    server._turn_cancelled.set()
    server._agent_running.clear()
    server._flush_deferred_door_land()
    assert called == []
    assert server._pending_door_land is None
