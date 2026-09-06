"""AcpClient unit tests — driven against a deterministic fake ACP server.

Offline/no-network: the client talks to `tests/acp_fake_server.py` instead of a
real `cursor-agent acp`. Live coverage is exercised separately.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from xlii.acp_client import (
    XLII_MCP_SERVER_NAME,
    AcpClient,
    AcpError,
    _pick_option,
    ensure_cursor_mcp_config,
    resolve_cursor_cli,
    run_turn,
    xlii_mcp_server_spec,
)

FAKE = str(Path(__file__).parent / "acp_fake_server.py")


def _argv():
    return [sys.executable, FAKE]


def test_initialize_and_session(tmp_path):
    c = AcpClient(cwd=tmp_path, argv=_argv()).start()
    try:
        assert c.agent_capabilities.get("loadSession") is True
        assert any(a["id"] == "cursor_login" for a in c.auth_methods)
        sid = c.new_session()
        assert sid == "sess-1"
        assert c.current_model_id == "composer-2.5[fast=true]"
        assert any(m["id"] == "ask" for m in c.available_modes)
    finally:
        c.close()


def test_prompt_streams_and_permission_allow(tmp_path):
    events = []
    c = AcpClient(cwd=tmp_path, argv=_argv(), on_event=lambda k, u: events.append(k))
    with c:
        c.new_session()
        res = c.prompt("do it")
    assert res.stop_reason == "end_turn"
    assert "picked=allow-1" in res.text  # default policy = allow-once
    assert "q=opt-a" in res.text
    assert "plan=True" in res.text
    assert res.title == "Fake Turn"
    assert "t1" in res.tool_calls
    assert res.tool_calls["t1"].status == "completed"
    assert res.files_touched
    assert (tmp_path / "out.txt").read_text() == "written via fs"
    assert "agent_message_chunk" in events
    assert "tool_call" in events


def test_permission_reject_policy(tmp_path):
    c = AcpClient(cwd=tmp_path, argv=_argv(), permission="reject")
    with c:
        c.new_session()
        res = c.prompt("do it")
    assert "picked=rej-1" in res.text


def test_custom_permission_callback(tmp_path):
    c = AcpClient(cwd=tmp_path, argv=_argv(), on_permission=lambda p: "rej-1")
    with c:
        c.new_session()
        res = c.prompt("x")
    assert "picked=rej-1" in res.text


def test_set_mode_and_model(tmp_path):
    c = AcpClient(cwd=tmp_path, argv=_argv(), mode="ask", model="grok-build-0.1")
    with c:
        c.new_session()
        res = c.prompt("x")
    assert "mode=ask" in res.text
    # friendly name resolves to the concrete bracketed modelId via base-id match
    assert "model=grok-build-0.1[context=200k]" in res.text


def test_unknown_model_records_note(tmp_path):
    c = AcpClient(cwd=tmp_path, argv=_argv(), model="does-not-exist")
    with c:
        c.new_session()
        res = c.prompt("x")
    assert "model=None" in res.text  # never set on the server
    assert any("not in availableModels" in n for n in c.notes)


def test_run_turn_helper(tmp_path):
    res = run_turn("x", cwd=tmp_path, argv=_argv())
    assert res.stop_reason == "end_turn"
    assert "picked=allow-1" in res.text


def test_resolve_cursor_cli_env(tmp_path, monkeypatch):
    f = tmp_path / "cursor-agent"
    f.write_text("#!/bin/sh\n")
    monkeypatch.setenv("XLII_CURSOR_AGENT_BIN", str(f))
    assert resolve_cursor_cli() == str(f)
    monkeypatch.setenv("XLII_CURSOR_AGENT_BIN", str(tmp_path / "missing"))
    assert resolve_cursor_cli() is None


def test_pick_option_prefers_kind():
    opts = [{"optionId": "a", "kind": "allow_once"}, {"optionId": "r", "kind": "reject_once"}]
    assert _pick_option(opts, ("allow_once", "allow")) == "a"
    assert _pick_option(opts, ("reject_once", "reject")) == "r"
    assert _pick_option([], ("allow",)) is None


def test_missing_cli_raises(monkeypatch):
    monkeypatch.setattr("xlii.acp_client.resolve_cursor_cli", lambda: None)
    with pytest.raises(AcpError, match="not found"):
        AcpClient(cwd="/tmp")


def test_xlii_mcp_server_spec():
    s = xlii_mcp_server_spec()
    assert s == {"command": "xlii", "args": ["mcp", "deep-contexts"]}
    assert xlii_mcp_server_spec(command="python", args=["-m", "x"])["command"] == "python"


def test_ensure_cursor_mcp_config_adds(tmp_path):
    action = ensure_cursor_mcp_config(tmp_path)
    assert action == "added"
    import json

    data = json.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    assert data["mcpServers"][XLII_MCP_SERVER_NAME] == {"command": "xlii", "args": ["mcp", "deep-contexts"]}
    # idempotent
    assert ensure_cursor_mcp_config(tmp_path) == "present"


def test_ensure_cursor_mcp_config_preserves_existing(tmp_path):
    import json

    cdir = tmp_path / ".cursor"
    cdir.mkdir()
    (cdir / "mcp.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
    action = ensure_cursor_mcp_config(tmp_path)
    assert action == "added"
    data = json.loads((cdir / "mcp.json").read_text())
    assert "other" in data["mcpServers"]  # not clobbered
    assert XLII_MCP_SERVER_NAME in data["mcpServers"]


def test_fs_write_blocked_in_read_only_modes(tmp_path):
    for mode in ("ask", "plan"):
        c = AcpClient(cwd=tmp_path, argv=_argv(), mode=mode)
        with pytest.raises(AcpError, match="read-only"):
            c._handle_fs_write({"path": "out.txt", "content": "x"})


def test_create_plan_approval_by_mode(tmp_path):
    # plan/agent modes auto-approve a plan (planning is the point);
    # pure read-only ask mode rejects; --reject always cancels.
    for mode in ("plan", "agent"):
        c = AcpClient(cwd=tmp_path, argv=_argv(), mode=mode)
        assert c._handle_create_plan({}) == {"approved": True}
    c_ask = AcpClient(cwd=tmp_path, argv=_argv(), mode="ask")
    assert c_ask._handle_create_plan({}) == {"approved": False}
    c_rej = AcpClient(cwd=tmp_path, argv=_argv(), mode="agent", permission="reject")
    assert c_rej._handle_create_plan({}) == {"approved": False}


def test_safe_path_blocks_write_escape(tmp_path):
    c = AcpClient(cwd=tmp_path, argv=_argv())
    with pytest.raises(AcpError, match="outside workspace"):
        c._safe_path("../escape.txt", for_write=True)
    # reads outside the workspace are allowed (agent already has read intent)
    p = c._safe_path("/etc/hostname", for_write=False)
    assert str(p) == "/etc/hostname"
