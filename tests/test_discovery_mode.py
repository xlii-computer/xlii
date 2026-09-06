"""Discovery mode — read-only "just talk about the code" gate.

Lighter than plan mode: same read-only tool palette, but no plan deliverable and
no /execute step. Covers the controller surface, the agent's mode slot (mutual
exclusion), the /discovery command toggle (+/research alias), the talk-primary
routing flip, and the --discovery launch flag.
"""

from __future__ import annotations

from types import SimpleNamespace

from xlii.mode_controller import DiscoveryController
from xlii.repl_cmds.mode import h_discovery
from xlii.rail import RailController
from tests.helpers import FakeConsole, make_agent


# --------------------------------------------------------------------------- #
#  Controller surface
# --------------------------------------------------------------------------- #

def test_controller_is_read_only_like_plan_but_distinct_tag():
    dc = DiscoveryController()
    # Same read-only palette as plan mode — research must read code, never write.
    from xlii.tool_schemas import plan_mode_schemas
    assert dc.tool_schemas() == plan_mode_schemas()
    # A discussion turn can legitimately call zero tools (answer from context).
    assert dc.suppresses_claim_check is True
    # Distinct status tag (so it doesn't read as plan mode anywhere).
    assert dc.status_tag() == ("DISC", "DISC")
    # No stage machine — it's a flat toggle.
    assert dc.advance() is False
    assert dc.back() is False


def test_directive_forbids_changes_and_a_plan_deliverable():
    text = DiscoveryController().get_system_directive().lower()
    assert "read-only" in text
    assert "no write_file" in text or "cannot modify" in text
    # The whole point vs plan mode: no numbered plan, no execute.
    assert "plan" in text  # mentions it only to say "don't produce one"


# --------------------------------------------------------------------------- #
#  Agent mode slot — mutual exclusion (one ModeController at a time)
# --------------------------------------------------------------------------- #

def test_discovery_mode_property_toggles_the_slot(tmp_path):
    agent = make_agent(tmp_path)
    assert agent.discovery_mode is False
    agent.discovery_mode = True
    assert agent.discovery_mode is True
    assert isinstance(agent.active_mode, DiscoveryController)
    agent.discovery_mode = False
    assert agent.discovery_mode is False
    assert agent.active_mode is None


def test_entering_discovery_clears_a_sibling_mode(tmp_path):
    agent = make_agent(tmp_path)
    agent.rail = RailController()
    agent.discovery_mode = True
    assert agent.discovery_mode is True
    assert agent.rail is None  # rail displaced — only one slot


def test_entering_plan_clears_discovery(tmp_path):
    agent = make_agent(tmp_path)
    agent.discovery_mode = True
    agent.plan_mode = True
    assert agent.plan_mode is True
    assert agent.discovery_mode is False


# --------------------------------------------------------------------------- #
#  /discovery command toggle
# --------------------------------------------------------------------------- #

def _ctx(tmp_path, **agent_kw):
    con = FakeConsole()
    agent = make_agent(tmp_path, console=con, **agent_kw)
    return agent, con, {"agent": agent, "console": con}


def test_bare_discovery_enters_then_toggles_off(tmp_path):
    agent, con, ctx = _ctx(tmp_path)
    assert h_discovery("/discovery", ctx) is True
    assert agent.discovery_mode is True
    assert "discovery mode ON" in con.text
    # Same keystroke toggles back off.
    h_discovery("/discovery", ctx)
    assert agent.discovery_mode is False
    assert "discovery mode OFF" in con.text


def test_discovery_off_when_inactive_is_a_noop(tmp_path):
    agent, con, ctx = _ctx(tmp_path)
    h_discovery("/discovery off", ctx)
    assert agent.discovery_mode is False
    assert "not in discovery mode" in con.text


def test_discovery_status_reports_state(tmp_path):
    agent, con, ctx = _ctx(tmp_path)
    agent.discovery_mode = True
    h_discovery("/discovery status", ctx)
    assert "ON" in con.text


def test_discovery_command_displaces_plan_mode(tmp_path):
    agent, con, ctx = _ctx(tmp_path)
    agent.plan_mode = True
    h_discovery("/discovery", ctx)
    assert agent.discovery_mode is True
    assert agent.plan_mode is False
    assert "plan mode OFF" in con.text


# --------------------------------------------------------------------------- #
#  Registration: /research is an alias in the code REPL
# --------------------------------------------------------------------------- #

def test_research_is_a_registered_alias():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all
    register_all()
    assert find_repl_command("/discovery", "code") is not None
    assert find_repl_command("/research", "code") is not None
    # Code-scoped, like /rail and /debug — not present in chat.
    assert find_repl_command("/discovery", "chat") is None


# --------------------------------------------------------------------------- #
#  Routing — discovery is talk-primary (bare input → the agent)
# --------------------------------------------------------------------------- #

def test_discovery_flips_routing_to_conversational():
    from xlii.repl import _is_shell_primary
    st = SimpleNamespace(persona=None, plan_mode=False, howto_mode=False,
                         discovery_mode=False)
    assert _is_shell_primary(st) is True
    st.discovery_mode = True
    assert _is_shell_primary(st) is False


def test_mode_switch_commands_still_dispatch_inside_discovery():
    from xlii.repl import _slash_deferred_in_talk_primary
    st = SimpleNamespace(persona=None, plan_mode=False, howto_mode=False,
                         discovery_mode=True)
    # Switching out into /plan or /rail is an action — must dispatch, not defer.
    assert _slash_deferred_in_talk_primary(st, "/plan") is False
    assert _slash_deferred_in_talk_primary(st, "/rail") is False
    # Leaving discovery dispatches too.
    assert _slash_deferred_in_talk_primary(st, "/discovery off") is False
    # But asking *about* an unrelated action command still defers to the agent.
    assert _slash_deferred_in_talk_primary(st, "/loop") is True


# --------------------------------------------------------------------------- #
#  Launch flag
# --------------------------------------------------------------------------- #

def test_code_accepts_discovery_launch_flag():
    from xlii.cli import build_parser
    parser = build_parser()
    assert parser.parse_args(["code", "--discovery"]).discovery is True
    assert parser.parse_args(["code", "--disc"]).discovery is True
    assert parser.parse_args(["code"]).discovery is False
