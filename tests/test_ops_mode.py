"""Ops mode — OS diagnostics / workflow on the host (terminal-native Phase 8).

Read-only file tools plus bash for platform-correct probes. Covers the controller
surface, the agent's mode slot (mutual exclusion), the /ops command toggle, the
talk-primary routing flip, and the --ops launch flag.
"""

from __future__ import annotations

from types import SimpleNamespace

from xlii.mode_controller import OpsController
from xlii.repl_cmds.mode import h_ops
from xlii.rail import RailController
from xlii.tool_schemas import ops_mode_schemas, plan_mode_schemas
from tests.helpers import FakeConsole, make_agent


# --------------------------------------------------------------------------- #
#  Controller surface
# --------------------------------------------------------------------------- #

def test_controller_exposes_read_only_plus_bash():
    oc = OpsController()
    schemas = ops_mode_schemas()
    assert oc.tool_schemas() == schemas
    names = {s["function"]["name"] for s in schemas}
    assert "bash" in names
    assert "write_file" not in names
    assert "edit_file" not in names
    assert oc.tool_schemas() == plan_mode_schemas() + [
        s for s in schemas if s["function"]["name"] == "bash"
    ]
    assert oc.suppresses_claim_check is True
    assert oc.status_tag() == ("OPS", "OPS")
    assert oc.advance() is False
    assert oc.back() is False


def test_directive_grounds_in_system_and_read_only_first():
    text = OpsController().get_system_directive().lower()
    assert "[system]" in text or "system" in text
    assert "read-only" in text
    assert "bash" in text
    assert "no write_file" in text or "not a code-editing" in text
    assert "destructive" in text


# --------------------------------------------------------------------------- #
#  Agent mode slot — mutual exclusion
# --------------------------------------------------------------------------- #

def test_ops_mode_property_toggles_the_slot(tmp_path):
    agent = make_agent(tmp_path)
    assert agent.ops_mode is False
    agent.ops_mode = True
    assert agent.ops_mode is True
    assert isinstance(agent.active_mode, OpsController)
    agent.ops_mode = False
    assert agent.ops_mode is False
    assert agent.active_mode is None


def test_entering_ops_clears_a_sibling_mode(tmp_path):
    agent = make_agent(tmp_path)
    agent.rail = RailController()
    agent.ops_mode = True
    assert agent.ops_mode is True
    assert agent.rail is None


def test_entering_plan_clears_ops(tmp_path):
    agent = make_agent(tmp_path)
    agent.ops_mode = True
    agent.plan_mode = True
    assert agent.plan_mode is True
    assert agent.ops_mode is False


def test_entering_ops_clears_discovery(tmp_path):
    agent = make_agent(tmp_path)
    agent.discovery_mode = True
    agent.ops_mode = True
    assert agent.ops_mode is True
    assert agent.discovery_mode is False


# --------------------------------------------------------------------------- #
#  /ops command toggle
# --------------------------------------------------------------------------- #

def _ctx(tmp_path, **agent_kw):
    con = FakeConsole()
    agent = make_agent(tmp_path, console=con, **agent_kw)
    return agent, con, {"agent": agent, "console": con}


def test_bare_ops_enters_then_toggles_off(tmp_path):
    agent, con, ctx = _ctx(tmp_path)
    assert h_ops("/ops", ctx) is True
    assert agent.ops_mode is True
    assert "ops mode ON" in con.text
    h_ops("/ops", ctx)
    assert agent.ops_mode is False
    assert "ops mode OFF" in con.text


def test_ops_off_when_inactive_is_a_noop(tmp_path):
    agent, con, ctx = _ctx(tmp_path)
    h_ops("/ops off", ctx)
    assert agent.ops_mode is False
    assert "not in ops mode" in con.text


def test_ops_status_reports_state(tmp_path):
    agent, con, ctx = _ctx(tmp_path)
    agent.ops_mode = True
    h_ops("/ops status", ctx)
    assert "ON" in con.text


def test_ops_command_displaces_plan_mode(tmp_path):
    agent, con, ctx = _ctx(tmp_path)
    agent.plan_mode = True
    h_ops("/ops", ctx)
    assert agent.ops_mode is True
    assert agent.plan_mode is False
    assert "plan mode OFF" in con.text


# --------------------------------------------------------------------------- #
#  Registration
# --------------------------------------------------------------------------- #

def test_ops_is_registered_in_code_repl():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all
    register_all()
    assert find_repl_command("/ops", "code") is not None
    assert find_repl_command("/ops", "chat") is None


# --------------------------------------------------------------------------- #
#  Routing — ops is talk-primary (bare input → the agent)
# --------------------------------------------------------------------------- #

def test_ops_flips_routing_to_conversational():
    from xlii.repl import _is_shell_primary
    st = SimpleNamespace(persona=None, plan_mode=False, howto_mode=False,
                         discovery_mode=False, ops_mode=False)
    assert _is_shell_primary(st) is True
    st.ops_mode = True
    assert _is_shell_primary(st) is False


def test_mode_switch_commands_still_dispatch_inside_ops():
    from xlii.repl import _slash_deferred_in_talk_primary
    st = SimpleNamespace(persona=None, plan_mode=False, howto_mode=False,
                         discovery_mode=False, ops_mode=True)
    assert _slash_deferred_in_talk_primary(st, "/plan") is False
    assert _slash_deferred_in_talk_primary(st, "/discovery") is False
    assert _slash_deferred_in_talk_primary(st, "/ops off") is False
    assert _slash_deferred_in_talk_primary(st, "/loop") is True


# --------------------------------------------------------------------------- #
#  Launch flag + preamble wiring
# --------------------------------------------------------------------------- #

def test_code_accepts_ops_launch_flag():
    from xlii.cli import build_parser
    parser = build_parser()
    assert parser.parse_args(["code", "--ops"]).ops is True
    assert parser.parse_args(["code"]).ops is False


def test_ops_preamble_loaded():
    from xlii.turn_prompt import OPS_MODE_PREAMBLE
    assert "[OPS MODE ACTIVE]" in OPS_MODE_PREAMBLE


def test_effective_system_prompt_includes_ops_directive(tmp_path):
    agent = make_agent(tmp_path)
    agent.ops_mode = True
    prompt = agent._effective_system_prompt()
    assert "[OPS MODE ACTIVE]" in prompt
