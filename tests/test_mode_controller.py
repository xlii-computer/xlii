"""Track A — ModeController protocol, set_mode(), and mutual exclusion."""

from __future__ import annotations

import inspect
import io
from pathlib import Path

from rich.console import Console

from xlii.agent import Agent
from xlii.commands import dispatch_repl_command
from xlii.debug_mode import DebugController, LAST_PHASE, DebugPhase
from xlii.mode_controller import ModeController, PlanController
from xlii.rail import LAST_STAGE, RailController, RailStage
from xlii.repl import REPLState
from xlii.repl_cmds import register_all
from xlii.tools import (
    dispatch_subagent_schema,
    plan_mode_schemas,
    plan_write_schemas,
    tool_schemas,
)
from tests.test_rail import _bare_agent

register_all()


def test_rail_controller_satisfies_protocol():
    rail = RailController()
    assert isinstance(rail, ModeController)


def test_debug_controller_satisfies_protocol():
    dbg = DebugController()
    assert isinstance(dbg, ModeController)


def test_plan_controller_satisfies_protocol():
    plan = PlanController()
    assert isinstance(plan, ModeController)
    # plan-write-domain P0: the plan palette is the read-only set PLUS the two
    # writers (path-gated to plans/ at the handler layer).
    assert plan.tool_schemas() == plan_write_schemas()
    names = {s["function"]["name"] for s in plan.tool_schemas()}
    assert {"write_file", "edit_file"} <= names
    assert "bash" not in names and "dispatch_subagent" not in names
    assert plan.status_tag() == ("PLAN", "PLAN")
    assert plan.advance() is False
    assert plan.back() is False


def test_agent_active_mode_defaults_none():
    agent = Agent.__new__(Agent)
    assert getattr(agent, "active_mode", "missing") is None


def test_rail_tool_schemas_read_only_matches_agent_ladder():
    rail = RailController()
    assert rail.current_stage in (
        RailStage.REQUIREMENTS_LOCK,
        RailStage.ARCHITECTURE_PLAN,
        RailStage.EDGE_CASES,
        RailStage.PSEUDOCODE,
    )
    assert rail.tool_schemas() == plan_mode_schemas()
    rail.current_stage = RailStage.IMPLEMENTATION
    assert rail.tool_schemas() == tool_schemas() + [dispatch_subagent_schema()]


def test_rail_suppresses_claim_check_on_read_only_stages():
    rail = RailController()
    assert rail.suppresses_claim_check is True
    rail.current_stage = RailStage.IMPLEMENTATION
    assert rail.suppresses_claim_check is False


def test_rail_status_tag():
    rail = RailController()
    assert rail.status_tag() == (
        f"RAIL {rail.current_stage.value}/{LAST_STAGE.value}",
        "RAIL",
    )


def test_debug_tool_schemas_per_phase():
    dbg = DebugController()
    dbg.current_phase = DebugPhase.HYPOTHESIZE
    assert dbg.tool_schemas() == plan_mode_schemas()
    dbg.current_phase = DebugPhase.INSTRUMENT
    from xlii.tools import debug_instrument_schemas

    assert dbg.tool_schemas() == debug_instrument_schemas()
    dbg.current_phase = DebugPhase.REPRODUCE
    from xlii.tools import debug_reproduce_schemas

    assert dbg.tool_schemas() == debug_reproduce_schemas()
    dbg.current_phase = DebugPhase.FIX
    assert dbg.tool_schemas() == tool_schemas() + [dispatch_subagent_schema()]


def test_debug_suppresses_claim_check_on_read_only_phases():
    dbg = DebugController()
    assert dbg.suppresses_claim_check is True
    dbg.current_phase = DebugPhase.INSTRUMENT
    assert dbg.suppresses_claim_check is False


def test_debug_status_tag():
    dbg = DebugController()
    assert dbg.status_tag() == (
        f"DEBUG {dbg.current_phase.value}/{LAST_PHASE.value}",
        "DEBUG",
    )


def test_agent_py_mode_ladders_removed():
    """A4 exit gate: run_turn and _effective_system_prompt route via active_mode."""

    prompt_src = inspect.getsource(Agent._effective_system_prompt)
    turn_src = inspect.getsource(Agent.run_turn)
    for src in (prompt_src, turn_src):
        assert "elif self.debug" not in src
        assert "elif self.plan_mode" not in src
        assert "self.rail is not None" not in src
    assert "active_mode" in turn_src
    assert "active_mode" in prompt_src


def test_effective_system_prompt_uses_active_mode_directive():
    rail = RailController()
    agent = _bare_agent(rail=rail)
    assert rail.get_system_directive() in agent._effective_system_prompt()

    agent.set_mode(DebugController())
    assert "DEBUG MODE — ACTIVE" in agent._effective_system_prompt()
    assert "CODING RAIL" not in agent._effective_system_prompt()

    agent.set_mode(PlanController())
    assert PlanController().get_system_directive() in agent._effective_system_prompt()


def test_run_turn_schemas_from_active_mode(tmp_path):
    from tests.helpers import make_agent, make_msg

    agent = make_agent(tmp_path)
    captured: list[list] = []

    def fake(*, model, schemas, temperature, cache_hdrs, backend=None):
        captured.append(schemas)
        return (make_msg("ok"), None, False)

    agent._stream_orchestrator_iteration = fake

    def expected(mode):
        schemas = mode.tool_schemas(agent.project.xli_dir)
        from xlii.plugin import load_subscriptions
        from xlii.tool_schemas import apply_email_account_gate, apply_xai_docs_gate

        if not load_subscriptions(agent.project.xli_dir):
            schemas = [
                s for s in schemas
                if s["function"]["name"] not in {"plugin_search", "plugin_get"}
            ]
        schemas = apply_email_account_gate(schemas, agent.cfg)
        return apply_xai_docs_gate(schemas, agent.cfg)

    rail = RailController()
    agent.set_mode(rail)
    agent.run_turn("go")
    assert captured[-1] == expected(rail)

    dbg = DebugController()
    dbg.current_phase = DebugPhase.INSTRUMENT
    agent.set_mode(dbg)
    agent.run_turn("go")
    assert captured[-1] == expected(dbg)

    agent.set_mode(PlanController())
    agent.run_turn("go")
    assert captured[-1] == expected(PlanController())
    # plan-write-domain P0: the plan turn advertises the two (path-gated) writers.
    plan_names = {s["function"]["name"] for s in captured[-1]}
    assert {"write_file", "edit_file"} <= plan_names
    assert "bash" not in plan_names and "dispatch_subagent" not in plan_names

    agent.set_mode(None)
    agent.run_turn("go")
    default = tool_schemas() + [dispatch_subagent_schema()]
    from xlii.plugin import load_subscriptions
    from xlii.mode_contract import CHAT_DOOR_TOOLS

    if not load_subscriptions(agent.project.xli_dir):
        default = [
            s for s in default
            if s["function"]["name"] not in {"plugin_search", "plugin_get"}
        ]
    # K6 / Fable blocker 2: door tools stay registered (register_all /
    # conversational turns) but code posture must not advertise them.
    default = [
        s for s in default
        if s["function"]["name"] not in CHAT_DOOR_TOOLS
    ]
    # Media-out: no outbox granted ⇒ send_file is stripped (a dead tool that
    # could only refuse); see tool_schemas.apply_outbox_gate.
    from xlii.tool_schemas import apply_email_account_gate, apply_outbox_gate, apply_xai_docs_gate

    default = apply_email_account_gate(default, agent.cfg)
    default = apply_xai_docs_gate(default, agent.cfg)
    default = apply_outbox_gate(default, None)
    assert captured[-1] == default


def test_set_mode_entering_rail_clears_debug_and_plan():
    agent = _bare_agent()
    agent.plan_mode = True
    agent.set_mode(DebugController())
    assert agent.debug is not None

    agent.set_mode(RailController())
    assert agent.rail is not None
    assert agent.debug is None
    assert agent.plan_mode is False
    assert agent.active_mode is agent.rail


def test_set_mode_entering_debug_clears_rail():
    agent = _bare_agent()
    agent.set_mode(RailController())

    agent.set_mode(DebugController())
    assert agent.debug is not None
    assert agent.rail is None
    assert agent.active_mode is agent.debug


def test_set_mode_entering_debug_clears_plan():
    agent = _bare_agent()
    agent.plan_mode = True

    agent.set_mode(DebugController())
    assert agent.plan_mode is False
    assert agent.debug is not None


def test_set_mode_none_clears_plan_mode():
    agent = _bare_agent()
    agent.plan_mode = True

    agent.set_mode(None)
    assert agent.active_mode is None
    assert agent.plan_mode is False
    assert agent.session.plan_mode is False


def test_plan_mode_setter_activates_plan_controller():
    agent = _bare_agent()
    agent.plan_mode = True
    assert isinstance(agent.active_mode, PlanController)
    assert agent.session.plan_mode is True

    agent.plan_mode = False
    assert agent.active_mode is None


def test_rail_debug_setters_sync_active_mode():
    agent = _bare_agent()
    rail = RailController()
    agent.rail = rail
    assert agent.active_mode is rail

    agent.rail = None
    assert agent.active_mode is None

    dbg = DebugController()
    agent.debug = dbg
    assert agent.active_mode is dbg


def test_plan_command_clears_controller_modes(tmp_path):
    agent = _bare_agent()
    agent.set_mode(RailController())
    state = REPLState(
        console=Console(file=io.StringIO()),
        agent=agent,
        project=type("P", (), {"project_root": tmp_path, "xli_dir": tmp_path / ".xlii"})(),
        cfg=type("C", (), {"orchestrator_temp": lambda: 0.7})(),
        pool=[],
    )
    (tmp_path / ".xlii").mkdir()

    dispatch_repl_command("/plan", state.as_context_dict())
    assert state.plan_mode is True
    assert agent.rail is None
    assert agent.debug is None
    assert isinstance(agent.active_mode, PlanController)


def test_execute_clears_plan_mode_on_agent_and_state(tmp_path):
    """Regression: /execute must not leave plan_mode True on either view."""
    agent = _bare_agent()
    agent.history = [{"role": "assistant", "content": "1. do thing"}]
    xli = tmp_path / ".xlii"
    xli.mkdir()
    state = REPLState(
        console=Console(file=io.StringIO()),
        agent=agent,
        project=type(
            "P",
            (),
            {"project_root": tmp_path, "xli_dir": xli, "local_only": True},
        )(),
        cfg=type("C", (), {"orchestrator_temp": lambda: 0.7})(),
        pool=[],
    )
    dispatch_repl_command("/plan", state.as_context_dict())
    assert state.plan_mode is True

    dispatch_repl_command("/execute", state.as_context_dict())
    assert agent.plan_mode is False
    assert state.plan_mode is False


def test_clear_conflicts_clears_all_modes():
    from xlii.repl_cmds.loop import _clear_conflicts

    agent = _bare_agent()
    agent.set_mode(RailController())
    ctx = {"agent": agent, "console": Console(file=io.StringIO())}
    _clear_conflicts(ctx)
    assert agent.rail is None
    assert agent.active_mode is None

    agent.plan_mode = True
    _clear_conflicts(ctx)
    assert agent.plan_mode is False
    assert agent.active_mode is None


def test_status_display_ladders_removed():
    """A5 exit gate: status/profile render via active_mode.status_tag()."""
    from pathlib import Path

    import xlii.profile as profile_mod
    from xlii.tui import status as status_mod

    status_text = Path(status_mod.__file__).read_text()
    profile_text = Path(profile_mod.__file__).read_text()
    for label, src in (("status.py", status_text), ("profile.py", profile_text)):
        assert "elif state.plan_mode" not in src, label
        assert "agent.rail is not None" not in src, label


def test_mode_handlers_do_not_assign_rail_or_debug_directly():
    """A2 exit gate: controller fields are cleared only via set_mode."""
    from xlii import repl_cmds

    mode_src = (Path(repl_cmds.__file__).parent / "mode.py").read_text()
    loop_src = (Path(repl_cmds.__file__).parent / "loop.py").read_text()
    for label, src in (("mode.py", mode_src), ("loop.py", loop_src)):
        assert "agent.rail = None" not in src, label
        assert "agent.debug = None" not in src, label
