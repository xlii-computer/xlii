"""Vector F — harness answers wear the native framed Markdown grammar."""

from __future__ import annotations

import io
import sys
import types
from pathlib import Path

import pytest
from rich.console import Console

from xlii.repl_cmds.cursor import _cursor_handler
from xlii.repl_cmds.delegate import (
    HarnessStreamRelay,
    _print_delegate_result,
    emit_harness_answer,
    run_delegate_command,
)
from xlii.harness.session import PersistentHarnessSession, run_session_turn
from xlii.tui.shell import styled_enabled

FAKE = str(Path(__file__).parent / "acp_fake_server.py")


def _styled_console() -> Console:
    return Console(file=io.StringIO(), width=80, force_terminal=True)


@pytest.fixture(autouse=True)
def _fake_acp_argv(monkeypatch):
    monkeypatch.setattr(
        "xlii.harness.session.resolve_acp_argv",
        lambda name: [sys.executable, FAKE],
    )
    monkeypatch.setattr(
        "xlii.harness.acp_session.resolve_acp_argv",
        lambda name: [sys.executable, FAKE],
    )


def test_emit_harness_answer_framed_like_native(monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    con = _styled_console()
    emit_harness_answer(con, "Fixed **it**.", "cursor · composer-2.5")
    out = con.file.getvalue()
    assert "cursor" in out
    assert "composer-2.5" in out
    assert "Fixed" in out
    assert "╭" in out


def test_markup_injection_renders_literally(monkeypatch):
    """Harness prose with Rich-markup lookalikes must not crash or restyle."""
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    con = _styled_console()
    poison = "see [/path/to/file] for details"
    emit_harness_answer(con, poison, "claude code")
    out = con.file.getvalue()
    assert "[/path/to/file]" in out
    assert "see" in out


def test_relay_interleaves_tool_lines_and_frames(monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    con = _styled_console()
    relay = HarnessStreamRelay(con, harness="cursor", model="composer-2.5")
    relay.on_event("agent_message_chunk", {"content": {"type": "text", "text": "before "}})
    relay.on_event("tool_call", {"title": "Edit File", "kind": "edit"})
    relay.on_event("agent_message_chunk", {"content": {"type": "text", "text": "after"}})
    relay.finish()
    out = con.file.getvalue()
    assert "before" in out
    assert "Edit File" in out
    assert "after" in out
    assert out.index("before") < out.index("Edit File") < out.index("after")


def test_on_event_path_framed_cursor_one_shot(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    project = types.SimpleNamespace(project_root=tmp_path, xli_dir=tmp_path / ".xlii")
    con = _styled_console()
    ctx = {"console": con, "project": project, "state": None}
    _cursor_handler("/cursor --ask what failed", ctx)
    out = con.file.getvalue()
    assert "cursor" in out
    assert "mode=ask" in out
    assert "╭" in out


def test_headless_print_delegate_result_framed(monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    con = _styled_console()
    result = types.SimpleNamespace(
        harness="codex",
        text="headless **answer**",
        error=None,
        notes=[],
        files_touched=[],
        stop_reason="end_turn",
    )
    _print_delegate_result(con, result, project_root=Path("/tmp"), harness="codex")
    out = con.file.getvalue()
    assert "codex" in out
    assert "headless" in out
    assert "╭" in out


def test_session_turn_stream_framed(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    con = _styled_console()
    session = PersistentHarnessSession("cursor", "build", project_root=tmp_path)
    state = types.SimpleNamespace(console=con)
    run_session_turn(state, session, "hello", stream=True)
    out = con.file.getvalue()
    assert "cursor" in out
    assert "picked=" in out
    assert "╭" in out


def test_session_turn_bg_block_framed(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    con = _styled_console()
    session = PersistentHarnessSession("claude", "bg", project_root=tmp_path)
    state = types.SimpleNamespace(console=con)
    run_session_turn(state, session, "bg task", stream=False)
    out = con.file.getvalue()
    assert "claude code" in out
    assert "mode=agent" in out
    assert "╭" in out


def test_plain_mode_skips_frame(monkeypatch):
    monkeypatch.delenv("XLII_SHELL_STYLE", raising=False)
    con = Console(file=io.StringIO(), width=80, force_terminal=False)
    assert not styled_enabled()
    emit_harness_answer(con, "plain text", "cursor")
    out = con.file.getvalue()
    assert "plain text" in out
    assert "╭" not in out


def test_claude_door_byte_identical_to_delegate(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    project = types.SimpleNamespace(project_root=tmp_path, xli_dir=tmp_path / ".xlii")

    def run(line, handler):
        con = _styled_console()
        handler(line, {"console": con, "project": project, "state": None})
        return con.file.getvalue()

    from xlii.repl_cmds.claude import _claude_handler

    d = run("/delegate claude --ask q", lambda l, c: run_delegate_command(l, c))
    c = run("/claude --ask q", _claude_handler)
    assert d == c
