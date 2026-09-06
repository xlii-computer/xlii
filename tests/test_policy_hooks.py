"""Policy (control) hooks — cursor-workflows.md B1.

on-turn-stop hooks may, ONLY when control is enabled, return {"followup": ...}
to drive capped follow-up turns. Covers the project.json gate, run_control_hooks
parsing/observer behavior, and the _drive_policy_hooks cap.
"""

from __future__ import annotations

import json
import stat
from types import SimpleNamespace

from xlii.hooks import (
    POLICY_HOOK_DEFAULT_MAX,
    control_enabled_for_project,
    run_control_hooks,
)
from xlii.repl import _control_hooks_enabled, _drive_policy_hooks
from tests.helpers import FakeConsole


def _hook(xli, body: str) -> None:
    d = xli / "hooks" / "on-turn-stop"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "h.sh"
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


# --------------------------------------------------------------------------- #
#  Enablement gate
# --------------------------------------------------------------------------- #

def test_project_gate_defaults_off_and_reads_flag(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    assert control_enabled_for_project(xli) is False           # no project.json
    (xli / "project.json").write_text(json.dumps({"name": "p"}))
    assert control_enabled_for_project(xli) is False           # no hooks.control
    (xli / "project.json").write_text(json.dumps({"hooks": {"control": True}}))
    assert control_enabled_for_project(xli) is True


def test_session_override_beats_project_default(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    (xli / "project.json").write_text(json.dumps({"hooks": {"control": True}}))
    from xlii.agent import SessionState
    state = SimpleNamespace(
        agent=SimpleNamespace(session=SessionState()),
        project=SimpleNamespace(xli_dir=xli),
    )
    assert _control_hooks_enabled(state) is True               # defers to project
    state.agent.session.hook_control = False
    assert _control_hooks_enabled(state) is False              # session override wins
    state.agent.session.hook_control = None
    assert _control_hooks_enabled(state) is True               # back to project default


# --------------------------------------------------------------------------- #
#  run_control_hooks parsing + observer fallback
# --------------------------------------------------------------------------- #

def test_followup_returned_only_when_enabled(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    _hook(xli, "echo '{\"followup\": \"run tests again\"}'")
    got = run_control_hooks(xli, {"user_input": "x"}, control_enabled=True)
    assert got and got["followup"] == "run tests again"
    assert run_control_hooks(xli, {"user_input": "x"}, control_enabled=False) is None


def test_non_followup_output_is_observer_only(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    _hook(xli, "echo 'just observing, no json'")
    console = FakeConsole()
    assert run_control_hooks(xli, {}, console=console, control_enabled=True) is None
    assert any("just observing" in line for line in console.lines)


# --------------------------------------------------------------------------- #
#  _drive_policy_hooks cap
# --------------------------------------------------------------------------- #

def _state(tmp_path, *, hook_control):
    from xlii.agent import SessionState
    xli = tmp_path / ".xlii"
    (xli / "hooks" / "on-turn-stop").mkdir(parents=True, exist_ok=True)
    session = SessionState()
    session.hook_control = hook_control
    return SimpleNamespace(
        agent=SimpleNamespace(session=session, history=[]),
        project=SimpleNamespace(xli_dir=xli, project_root=tmp_path),
        console=FakeConsole(),
        loop=None,
    )


def _runner():
    calls: list[str] = []

    def run_turn(text, **kw):
        calls.append(text)
        return ("ok", set(), SimpleNamespace(tool_calls=0, total_cost=0.0))

    return calls, run_turn


def test_driver_runs_followups_up_to_default_cap(tmp_path):
    st = _state(tmp_path, hook_control=True)
    _hook(st.project.xli_dir, "echo '{\"followup\": \"keep going\"}'")
    calls, run_turn = _runner()
    _drive_policy_hooks(st, run_turn, lambda *a: None, "orig", set(), None)
    assert len(calls) == POLICY_HOOK_DEFAULT_MAX  # hard cap stops the unbounded hook
    assert calls == ["keep going"] * POLICY_HOOK_DEFAULT_MAX


def test_driver_respects_hook_max_loops(tmp_path):
    st = _state(tmp_path, hook_control=True)
    _hook(st.project.xli_dir, "echo '{\"followup\": \"once\", \"max_loops\": 1}'")
    calls, run_turn = _runner()
    _drive_policy_hooks(st, run_turn, lambda *a: None, "orig", set(), None)
    assert calls == ["once"]


def test_driver_noop_when_control_disabled(tmp_path):
    st = _state(tmp_path, hook_control=False)
    _hook(st.project.xli_dir, "echo '{\"followup\": \"should be ignored\"}'")
    calls, run_turn = _runner()
    _drive_policy_hooks(st, run_turn, lambda *a: None, "orig", set(), None)
    assert calls == []  # observer only — followup ignored


def test_driver_stops_when_hook_returns_no_followup(tmp_path):
    st = _state(tmp_path, hook_control=True)
    _hook(st.project.xli_dir, "echo 'nothing to do here'")
    calls, run_turn = _runner()
    _drive_policy_hooks(st, run_turn, lambda *a: None, "orig", set(), None)
    assert calls == []
