"""Regression: every state-mutating slash command must be visible on
REPLState immediately after dispatch (single-ownership guarantee)."""

import io
from types import SimpleNamespace

import pytest
from rich.console import Console

from xlii.commands import dispatch_repl_command
from xlii.repl import REPLState
from xlii.repl_cmds import register_all
from tests.test_rail import _bare_agent

register_all()  # built-in slash commands are registered explicitly, not on import


@pytest.fixture
def state(tmp_path, monkeypatch):
    agent = _bare_agent()
    agent.history = [{"role": "system", "content": "s"}]
    xli = tmp_path / ".xlii"
    xli.mkdir()
    st = REPLState(
        console=Console(file=io.StringIO()),
        agent=agent,
        project=SimpleNamespace(project_root=tmp_path, xli_dir=xli, local_only=True),
        cfg=SimpleNamespace(orchestrator_temp=lambda: 0.7),
        pool=[],
    )
    return st


def test_plan_and_cancel(state):
    dispatch_repl_command("/plan", state.as_context_dict())
    assert state.plan_mode is True
    dispatch_repl_command("/cancel", state.as_context_dict())
    assert state.plan_mode is False


def test_yolo_and_safe(state):
    dispatch_repl_command("/yolo", state.as_context_dict())
    assert state.yolo is True
    dispatch_repl_command("/safe", state.as_context_dict())
    assert state.yolo is False


def test_temp_override(state):
    dispatch_repl_command("/temp 1.3", state.as_context_dict())
    assert state.next_turn_temp_override == 1.3


def test_reset_clears_plan_mode_on_state(state):
    dispatch_repl_command("/plan", state.as_context_dict())
    dispatch_repl_command("/reset", state.as_context_dict())
    assert state.plan_mode is False
    assert state.agent.history == state.agent.history[:1]


def test_reset_drops_parked_tape_so_reload_stays_empty(state):
    """Stream is project history. RAM-only reset used to come back on reload."""
    turns = state.project.xli_dir / "turns"
    turns.mkdir()
    (turns / "20260101T000000-1.md").write_text("# user\nhi\n# assistant\nhey\n")
    state._history_stash = {"code:proj": [{"role": "user", "content": "hi"}]}
    state.project.name = "proj"
    seen = []
    state.on_stream_reset = lambda: seen.append("sync")
    dispatch_repl_command("/reset", state.as_context_dict())
    assert list(turns.glob("*.md")) == []
    assert state._history_stash == {}
    assert seen == ["sync"]


def test_reset_says_forgot_this_chat_not_history(tmp_path):
    import io

    from xlii.commands import find_repl_command

    cmd = find_repl_command("/reset", "code")
    assert cmd is not None
    desc = (cmd.description or "").lower()
    assert "forget this chat" in desc
    assert "history" not in desc

    agent = _bare_agent()
    agent.history = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "hi"},
    ]
    buf = io.StringIO()
    st = REPLState(
        console=Console(file=buf, force_terminal=False, color_system=None),
        agent=agent,
        project=SimpleNamespace(project_root=tmp_path, xli_dir=tmp_path / ".xlii",
                                local_only=True),
        cfg=SimpleNamespace(orchestrator_temp=lambda: 0.7),
        pool=[],
    )
    xli = tmp_path / ".xlii"
    xli.mkdir()
    from prompt_toolkit.history import FileHistory
    hist = FileHistory(str(xli / "repl_history"))
    hist.append_string("typed line stays")

    dispatch_repl_command("/reset", st.as_context_dict())
    out = buf.getvalue().lower()
    assert "forgot this chat" in out
    assert "history cleared" not in out
    # typed lines are a different pile — History pane clears them
    assert (xli / "repl_history").exists()
    from xlii.repl_history_util import load_repl_history_strings
    assert "typed line stays" in load_repl_history_strings(xli)


def test_attachments_shared_with_agent(state):
    state.attach_doc("notes", "BODY")
    assert state.agent.attached_docs == [("notes", "BODY")]
    state.attach_ref("spark", "")   # bookmark shape — the one live ref kind (§5)
    assert state.agent.attached_refs == [("spark", "")]
    # and the reverse direction: agent-side mutation is visible on state
    state.agent.attached_docs.append(("x", "y"))
    assert ("x", "y") in state.attached_docs


def test_iterations_session_override(state):
    state.cfg.max_tool_iterations = 20
    dispatch_repl_command("/iterations 50", state.as_context_dict())
    assert state.cfg.max_tool_iterations == 50
    # out-of-range and garbage leave the value untouched
    dispatch_repl_command("/iterations 0", state.as_context_dict())
    dispatch_repl_command("/iterations lots", state.as_context_dict())
    assert state.cfg.max_tool_iterations == 50
    # bare form just reports
    dispatch_repl_command("/iterations", state.as_context_dict())
    assert state.cfg.max_tool_iterations == 50


def test_iterations_session_override_chat_cap(state):
    state.cfg.max_tool_iterations = 20
    state.cfg.max_chat_tool_iterations = 8
    state.agent.session.conversational = True
    dispatch_repl_command("/iterations 12", state.as_context_dict())
    assert state.cfg.max_chat_tool_iterations == 12
    assert state.cfg.max_tool_iterations == 20
    dispatch_repl_command("/iterations 0", state.as_context_dict())
    assert state.cfg.max_chat_tool_iterations == 12
