"""Debug mode (cursor-workflows.md B0) — staged bug-hunt mode.

Covers the DebugController phase machine + per-phase tool gating, the system
directive injection/precedence, the Instrument-phase marker enforcement at the
tool layer, the Verify cleanup gate (find_debug_markers + /debug exit blocking),
and the /debug command flow + mutual exclusion with plan/rail.
"""

from __future__ import annotations

from types import SimpleNamespace

from rich.console import Console

from xlii import tools
from xlii.debug_mode import (
    DEBUG_MARKER,
    DebugController,
    DebugPhase,
    find_debug_markers,
)
from tests.helpers import make_tool_ctx
from tests.test_rail import _bare_agent


# --------------------------------------------------------------------------- #
#  Controller: phase machine + tool modes
# --------------------------------------------------------------------------- #

def test_phase_machine_and_tool_modes():
    d = DebugController()
    assert d.current_phase is DebugPhase.HYPOTHESIZE
    modes = [d.tool_mode]
    while d.advance():
        modes.append(d.tool_mode)
    # Hypothesize, Instrument, Reproduce, Analyze, Fix, Verify
    assert modes == ["read_only", "instrument", "reproduce", "read_only", "write", "read_only"]
    assert d.is_final_phase and not d.advance()
    assert d.back() and d.current_phase is DebugPhase.FIX


def test_only_instrument_phase_flags_instrument():
    d = DebugController()
    flags = [d.is_instrument_phase]
    while d.advance():
        flags.append(d.is_instrument_phase)
    assert flags == [False, True, False, False, False, False]


def test_directive_names_phase_and_marker_and_gate():
    d = DebugController()
    assert "PHASE 0" in d.get_system_directive()
    d.advance()  # Instrument
    assert DEBUG_MARKER in d.get_system_directive()
    d.current_phase = DebugPhase.VERIFY
    verify = d.get_system_directive()
    assert DEBUG_MARKER in verify and "blocked" in verify.lower()


# --------------------------------------------------------------------------- #
#  System prompt injection + precedence (rail > debug > plan)
# --------------------------------------------------------------------------- #

def test_debug_directive_injected_and_rail_outranks_it():
    from xlii.rail import RailController

    a = _bare_agent(base="BASE")
    a.debug = DebugController()
    sp = a._effective_system_prompt()
    assert "BASE" in sp and "DEBUG MODE — ACTIVE" in sp and "PHASE 0" in sp

    a.rail = RailController()  # rail wins the elif chain
    sp2 = a._effective_system_prompt()
    assert "CODING RAIL" in sp2 and "DEBUG MODE" not in sp2


def test_debug_off_returns_base_prompt_verbatim():
    a = _bare_agent(base="BASE ONLY")
    assert a._effective_system_prompt() == "BASE ONLY"


# --------------------------------------------------------------------------- #
#  Tool-layer marker enforcement (Instrument phase)
# --------------------------------------------------------------------------- #

def test_instrument_rejects_unmarked_added_line(tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    ctx = make_tool_ctx(tmp_path)
    ctx.debug_instrument = True
    r = tools.t_edit_file(ctx, {
        "path": "m.py",
        "old_string": "    return 1",
        "new_string": "    print('x')\n    return 1",
    })
    assert r.is_error and DEBUG_MARKER in r.content
    assert (tmp_path / "m.py").read_text() == "def f():\n    return 1\n"  # untouched


def test_instrument_allows_marked_added_line(tmp_path):
    (tmp_path / "m.py").write_text("def f():\n    return 1\n")
    ctx = make_tool_ctx(tmp_path)
    ctx.debug_instrument = True
    r = tools.t_edit_file(ctx, {
        "path": "m.py",
        "old_string": "    return 1",
        "new_string": "    print('x')  # xlii-debug\n    return 1",
    })
    assert not r.is_error
    assert "xlii-debug" in (tmp_path / "m.py").read_text()


def test_instrument_refuses_write_file(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    ctx.debug_instrument = True
    r = tools.t_write_file(ctx, {"path": "new.py", "content": "x = 1\n"})
    assert r.is_error and not (tmp_path / "new.py").exists()


def test_edit_unrestricted_when_not_instrumenting(tmp_path):
    (tmp_path / "m.py").write_text("a = 1\n")
    ctx = make_tool_ctx(tmp_path)  # debug_instrument defaults False
    r = tools.t_edit_file(ctx, {"path": "m.py", "old_string": "a = 1", "new_string": "a = 2"})
    assert not r.is_error and (tmp_path / "m.py").read_text() == "a = 2\n"


# --------------------------------------------------------------------------- #
#  Cleanup gate: find_debug_markers
# --------------------------------------------------------------------------- #

def test_find_markers_reports_source_and_skips_ignored(tmp_path):
    (tmp_path / "src.py").write_text("x = 1  # xlii-debug\ny = 2\n")
    (tmp_path / ".xlii").mkdir()  # ignored by the sync spec — must not trip the gate
    (tmp_path / ".xlii" / "scratch.txt").write_text("noise xlii-debug\n")
    assert find_debug_markers(tmp_path) == [("src.py", 1, "x = 1  # xlii-debug")]


def test_find_markers_clean_tree_is_empty(tmp_path):
    (tmp_path / "src.py").write_text("x = 1\n")
    assert find_debug_markers(tmp_path) == []


# --------------------------------------------------------------------------- #
#  /debug command flow + mutual exclusion
# --------------------------------------------------------------------------- #

def _state(tmp_path):
    from xlii.repl import REPLState
    from xlii.repl_cmds import register_all

    register_all()  # idempotent
    agent = _bare_agent()
    agent.history = []
    xli = tmp_path / ".xlii"
    xli.mkdir(exist_ok=True)
    project = SimpleNamespace(
        project_root=tmp_path, xli_dir=xli, extra_ignores=None,
        local_only=True, name="p",
    )
    return REPLState(console=Console(), agent=agent, project=project,
                     cfg=SimpleNamespace(), pool=[])


def _run(state, line):
    from xlii.commands import _INDEX
    return _INDEX["debug"][0].handler(line, state.as_context_dict())


def test_debug_start_writes_artifact_and_sets_controller(tmp_path):
    state = _state(tmp_path)
    _run(state, "/debug")
    assert state.agent.debug is not None
    assert state.agent.debug.current_phase is DebugPhase.HYPOTHESIZE
    assert (tmp_path / ".xlii" / "debug" / "session.json").is_file()


def test_debug_exit_blocked_until_markers_cleared(tmp_path):
    state = _state(tmp_path)
    _run(state, "/debug")
    (tmp_path / "leak.py").write_text("print()  # xlii-debug\n")
    _run(state, "/debug exit")
    assert state.agent.debug is not None  # gate blocked the exit
    (tmp_path / "leak.py").write_text("print()\n")  # clean it
    _run(state, "/debug exit")
    assert state.agent.debug is None      # now allowed


def test_debug_next_advances_and_rewrites_turn(tmp_path):
    state = _state(tmp_path)
    state.agent.history = [{"role": "user", "content": "the bug"}]
    _run(state, "/debug")
    ctx = state.as_context_dict()
    from xlii.commands import _INDEX
    ret = _INDEX["debug"][0].handler("/debug next", ctx)
    assert ret is False and ctx.get("_debug_rewritten")
    assert state.agent.debug.current_phase is DebugPhase.INSTRUMENT


def test_plan_clears_debug(tmp_path):
    from xlii.commands import _INDEX

    state = _state(tmp_path)
    _run(state, "/debug")
    assert state.agent.debug is not None
    _INDEX["plan"][0].handler("/plan", state.as_context_dict())
    assert state.agent.debug is None and state.agent.plan_mode is True


def test_debug_consult_uses_harness(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    import xlii.harness.claude as claude_mod

    state = _state(tmp_path)
    (state.project.xli_dir / "debug").mkdir(parents=True, exist_ok=True)
    (state.project.xli_dir / "debug" / "session.json").write_text('{"phase":"ANALYZE"}')
    captured = {}
    monkeypatch.setattr(claude_mod, "resolve_claude_cli", lambda: "/fake/claude")
    monkeypatch.setattr(
        claude_mod.subprocess,
        "run",
        lambda cmd, **kw: (
            captured.setdefault("input", kw.get("input")),
            SimpleNamespace(
                stdout=json.dumps({"result": "hypothesis confirmed"}),
                stderr="",
                returncode=0,
            ),
        )[1],
    )
    _run(state, "/debug")
    _run(state, "/debug consult what does the session say?")
    assert captured.get("input") and "HYPOTHESIZE" in captured["input"]
    assert "what does the session say?" in captured["input"]


def test_debug_config_shows_map(tmp_path, capsys):
    state = _state(tmp_path)
    _run(state, "/debug config")
    out = capsys.readouterr().out
    assert "analyze" in out
