"""Tests for the /claude slash command (ACP front door)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from xlii.repl_cmds.claude import _claude_handler
from xlii.repl_cmds.delegate import _parse, run_delegate_command

FAKE = str(Path(__file__).parent / "acp_fake_server.py")


def _parse_claude(rest: str):
    from xlii.harness.detect import list_harness_names

    if not rest.strip() or rest.strip().split(None, 1)[0].lower() not in list_harness_names():
        rest = f"claude {rest}".strip()
    return _parse(rest)


class Rec:
    def __init__(self):
        self.out: list[str] = []

    def print(self, *a, **k):
        self.out.append(" ".join(str(x) for x in a))

    @property
    def text(self) -> str:
        return "\n".join(self.out)


def _ctx(tmp_path):
    project = types.SimpleNamespace(project_root=tmp_path, xli_dir=tmp_path / ".xlii")
    return {"console": Rec(), "project": project, "state": None}


@pytest.fixture(autouse=True)
def _fake_acp_argv(monkeypatch):
    monkeypatch.setattr(
        "xlii.harness.acp_session.resolve_acp_argv",
        lambda name: [sys.executable, FAKE],
    )


def test_parse_defaults():
    h, mode, model, perm, ctx, task, err = _parse_claude("do the thing")
    assert err is None
    assert (h, mode, model, perm, ctx, task) == (
        "claude",
        "agent",
        None,
        "allow",
        False,
        "do the thing",
    )


def test_parse_ask():
    assert _parse_claude("--ask what is this")[1:6] == (
        "ask",
        None,
        "allow",
        False,
        "what is this",
    )


def test_claude_matches_delegate_spelling(tmp_path):
    def run_line(line, handler):
        root = tmp_path / line.replace("/", "_").replace(" ", "_")[:40]
        root.mkdir()
        project = types.SimpleNamespace(project_root=root, xli_dir=root / ".xlii")
        con = Rec()
        handler(line, {"console": con, "project": project, "state": None})
        return con.text

    assert run_line("/delegate claude --ask explain", run_delegate_command) == run_line(
        "/claude --ask explain", _claude_handler
    )


def test_command_registers_conversational():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    cmd = find_repl_command("/claude", "code")
    assert cmd is not None
    assert cmd.name == "claude"
    assert cmd.conversational is True
