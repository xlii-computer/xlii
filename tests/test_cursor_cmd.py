"""Tests for the /cursor slash command (ACP driver surface)."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from xlii.repl_cmds.cursor import _cursor_handler
from xlii.repl_cmds.delegate import _parse

FAKE = str(Path(__file__).parent / "acp_fake_server.py")


def _parse_cursor(rest: str):
    """Parse like /cursor (implicit harness=cursor)."""
    from xlii.harness.detect import list_harness_names

    if not rest.strip() or rest.strip().split(None, 1)[0].lower() not in list_harness_names():
        rest = f"cursor {rest}".strip()
    return _parse(rest)


class Rec:
    def __init__(self):
        self.out: list[str] = []

    def print(self, *a, **k):
        self.out.append(" ".join(str(x) for x in a))

    @property
    def text(self) -> str:
        return "\n".join(self.out)


def test_parse_defaults():
    h, mode, model, perm, ctx, task, err = _parse_cursor("do the thing")
    assert err is None
    assert (h, mode, model, perm, ctx, task) == (
        "cursor",
        "agent",
        None,
        "allow",
        False,
        "do the thing",
    )


def test_parse_flags():
    assert _parse_cursor("--plan refactor foo")[1:6] == (
        "plan",
        None,
        "allow",
        False,
        "refactor foo",
    )
    assert _parse_cursor("--ask what is this")[1:6] == (
        "ask",
        None,
        "allow",
        False,
        "what is this",
    )
    assert _parse_cursor("--model grok-build-0.1 build it")[1:6] == (
        "agent",
        "grok-build-0.1",
        "allow",
        False,
        "build it",
    )
    assert _parse_cursor("--reject --ask hi")[1:6] == ("ask", None, "reject", False, "hi")
    assert _parse_cursor("--mode plan --model composer-2.5 go")[1:6] == (
        "plan",
        "composer-2.5",
        "allow",
        False,
        "go",
    )
    assert _parse_cursor("--context wire it")[1:6] == ("agent", None, "allow", True, "wire it")
    assert _parse_cursor("--ask --context what")[1:6] == ("ask", None, "allow", True, "what")


def test_parse_empty():
    _, _, _, _, _, task, _ = _parse_cursor("")
    assert task == ""


def _ctx(tmp_path):
    project = types.SimpleNamespace(project_root=tmp_path, xli_dir=tmp_path / ".xlii")
    return {"console": Rec(), "project": project, "state": None}


@pytest.fixture(autouse=True)
def _fake_acp_argv(monkeypatch):
    monkeypatch.setattr(
        "xlii.harness.acp_session.resolve_acp_argv",
        lambda name: [sys.executable, FAKE],
    )


def test_handler_runs_and_reports_files(tmp_path):
    ctx = _ctx(tmp_path)
    handled = _cursor_handler("/cursor build a thing", ctx)
    assert handled is True
    out = ctx["console"].text
    assert "[cursor ·" in out
    assert "may edit files" in out
    assert "out.txt" in out
    assert "mode=" in out and "agent" in out


def test_handler_no_context_by_default(tmp_path):
    ctx = _ctx(tmp_path)
    _cursor_handler("/cursor build a thing", ctx)
    assert not (tmp_path / ".cursor" / "mcp.json").exists()


def test_handler_context_registers_and_enables(tmp_path, monkeypatch):
    enabled = {}
    monkeypatch.setattr(
        "xlii.harness.mcp_context.enable_cursor_mcp",
        lambda **kw: enabled.setdefault("called", kw) or True,
    )
    ctx = _ctx(tmp_path)
    _cursor_handler("/cursor --context wire it", ctx)
    cfg = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert "xlii-deep-contexts" in cfg["mcpServers"]
    assert enabled["called"]["cwd"] == tmp_path
    assert "+xlii context" in ctx["console"].text


def test_handler_read_only_label(tmp_path):
    ctx = _ctx(tmp_path)
    _cursor_handler("/cursor --ask what does this do", ctx)
    out = ctx["console"].text
    assert "ask" in out
    assert "read-only" in out
    assert "mode=" in out and "ask" in out.split("mode=")[-1]


def test_handler_blocks_agent_mode_in_read_only_palette(tmp_path, monkeypatch):
    from tests.helpers import make_agent
    from xlii.mode_controller import PlanController

    calls = []
    monkeypatch.setattr(
        "xlii.repl_cmds.delegate.run_delegate",
        lambda *a, **kw: calls.append((a, kw)),
    )
    project = types.SimpleNamespace(project_root=tmp_path, xli_dir=tmp_path / ".xlii")
    agent = make_agent(tmp_path)
    agent.set_mode(PlanController())
    ctx = {
        "console": Rec(),
        "project": project,
        "state": types.SimpleNamespace(project=project, agent=agent),
        "agent": agent,
    }

    assert _cursor_handler("/cursor build a thing", ctx) is True

    assert calls == []
    assert "agent mode is not available in read-only mode" in ctx["console"].text


def test_handler_blocks_context_setup_in_read_only_palette(tmp_path, monkeypatch):
    from tests.helpers import make_agent
    from xlii.mode_controller import PlanController

    calls = []
    monkeypatch.setattr(
        "xlii.repl_cmds.delegate.run_delegate",
        lambda *a, **kw: calls.append((a, kw)),
    )
    project = types.SimpleNamespace(project_root=tmp_path, xli_dir=tmp_path / ".xlii")
    agent = make_agent(tmp_path)
    agent.set_mode(PlanController())
    ctx = {
        "console": Rec(),
        "project": project,
        "state": types.SimpleNamespace(project=project, agent=agent),
        "agent": agent,
    }

    assert _cursor_handler("/cursor --ask --context inspect", ctx) is True

    assert calls == []
    assert not (tmp_path / ".cursor" / "mcp.json").exists()
    assert "--context writes harness MCP config" in ctx["console"].text


def test_handler_usage_when_no_task(tmp_path):
    ctx = _ctx(tmp_path)
    _cursor_handler("/cursor", ctx)
    assert "usage:" in ctx["console"].text


def test_handler_unavailable_is_caught(tmp_path, monkeypatch):
    import xlii.acp_client as acp_client

    def boom(_name):
        raise acp_client.AcpError("cursor-agent CLI not found")

    monkeypatch.setattr("xlii.harness.acp_session.resolve_acp_argv", boom)
    ctx = _ctx(tmp_path)
    handled = _cursor_handler("/cursor do it", ctx)
    assert handled is True
    assert "unavailable" in ctx["console"].text


def test_command_registers():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()  # idempotent
    cmd = find_repl_command("/cursor", "code")
    assert cmd is not None
    assert cmd.name == "cursor"
