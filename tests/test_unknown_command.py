"""An unknown slash command is rejected (with a suggestion), never sent to the
agent — a typo must not spend a turn or let the model start editing files."""

from __future__ import annotations

from tests.helpers import FakeConsole
from xlii.commands import dispatch_repl_command
from xlii.repl_cmds import register_all

register_all()


def _dispatch(line: str):
    con = FakeConsole()
    handled = dispatch_repl_command(line, {"console": con, "command_scope": "code"})
    return handled, con.text


def test_typo_rejected_with_suggestion():
    handled, out = _dispatch("/tue")
    assert handled is True  # handled → REPL stops here, no agent fall-through
    assert "unknown command" in out
    assert "/tui" in out  # did-you-mean


def test_typo_without_close_match_still_rejected():
    handled, out = _dispatch("/zzzzznope")
    assert handled is True
    assert "unknown command" in out


def test_pathlike_slash_still_falls_through():
    # A slash followed by a path (not a command-like word) is not a command typo,
    # so it falls through (handled=False) — preserves the rare prompt-with-slash.
    handled, _ = _dispatch("/home/user/notes.md summarize this")
    assert handled is False


def test_bare_slash_does_not_crash():
    # A lone "/" used to IndexError in find_repl_command and kill the REPL.
    handled, out = _dispatch("/")
    assert handled is True
    assert "type a command after" in out


def test_whitespace_only_slash_does_not_crash():
    handled, out = _dispatch("/   ")
    assert handled is True
    assert "type a command after" in out


def test_find_repl_command_bare_slash_is_none_not_crash():
    from xlii.commands import find_repl_command

    assert find_repl_command("/", repl="code") is None
    assert find_repl_command("/   ", repl="code") is None


def test_bare_slash_through_repl_does_not_crash(tmp_path):
    """End-to-end: a lone '/' through process_repl_input must not IndexError —
    regression for both find_repl_command and the repl.py token-split-on-save."""
    from types import SimpleNamespace

    from tests.test_rail import _bare_agent
    from xlii.repl import REPLState, process_repl_input

    agent = _bare_agent()
    agent.history = [{"role": "system", "content": "s"}]
    xli = tmp_path / ".xlii"
    xli.mkdir()
    proj = SimpleNamespace(project_root=tmp_path, xli_dir=xli, local_only=True, name="proj")
    st = REPLState(
        console=FakeConsole(), agent=agent, project=proj,
        cfg=SimpleNamespace(orchestrator_temp=lambda: 0.7), pool=[],
    )
    st.shell_cwd = tmp_path.resolve()

    assert process_repl_input(st, "/") == (None, True)  # handled, no crash, no turn
    assert process_repl_input(st, "/   ") == (None, True)
