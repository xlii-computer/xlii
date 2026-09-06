"""Vector B (interaction-layer-ii) — /replay re-prints the last output verbatim.

The human-facing half of the capture seam: a token-free, can't-fail re-print of
the cached ``last_output`` that works *after a screen clear* (the cache lives on
SessionState, not the transcript). See proposals/FINDING-harness-output-ephemeral.md.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers import FakeConsole
from tests.test_rail import _bare_agent
from xlii.commands import find_repl_command
from xlii.repl import REPLState
from xlii.repl_cmds import register_all
from xlii.shell_toolkit import capture_output, record_last_shell
from xlii.tui.events import ShellRan

register_all()


@pytest.fixture
def state(tmp_path):
    agent = _bare_agent()
    agent.history = [{"role": "system", "content": "s"}]
    xli = tmp_path / ".xlii"
    xli.mkdir()
    proj = SimpleNamespace(
        project_root=tmp_path, xli_dir=xli, local_only=True, name="proj"
    )
    return REPLState(
        console=FakeConsole(),
        agent=agent,
        project=proj,
        cfg=SimpleNamespace(orchestrator_temp=lambda: 0.7),
        pool=[],
    )


def _replay(state) -> str:
    cmd = find_repl_command("/replay", "code")
    assert cmd is not None
    assert cmd.handler("/replay", {"console": state.console, "state": state}) is True
    return state.console.text


def test_replay_registered_in_both_repls():
    assert find_repl_command("/replay", "code") is not None
    assert find_repl_command("/replay", "chat") is not None
    assert find_repl_command("/last", "code") is not None  # alias


def test_replay_nothing_captured(state):
    assert "nothing to replay" in _replay(state).lower()


def test_replay_reprints_harness_output(state):
    capture_output(state, "Cursor: here is the analysis", source="harness",
                   label="cursor · composer-2.5")
    out = _replay(state)
    assert "Cursor: here is the analysis" in out
    assert "replay" in out.lower() and "cursor" in out.lower()


def test_replay_reprints_shell_output(state):
    record_last_shell(state, ShellRan(
        command="ls", cwd=Path("/tmp"), stdout="file-a\nfile-b\n", stderr="", returncode=0,
    ))
    out = _replay(state)
    assert "file-a" in out and "file-b" in out


def test_replay_survives_clear(state):
    capture_output(state, "persisted across clear", source="harness", label="cursor")
    state.console.lines.clear()  # simulate a screen `clear` — transcript gone, state intact
    assert "persisted across clear" in _replay(state)


def test_replay_is_token_free(state):
    # _bare_agent wires no pool/run_turn, so any model call would raise — a clean
    # re-print proves /replay spends no tokens.
    capture_output(state, "no tokens spent here", source="harness", label="cursor")
    assert "no tokens spent here" in _replay(state)
