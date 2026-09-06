"""Delegate command and harness delegation tests."""

from __future__ import annotations

import json

from xlii.harness.delegate import run_delegate
from xlii.harness.mcp_context import ensure_grok_mcp_config
from xlii.repl_cmds.delegate import _parse


def test_delegate_parse():
    h, mode, model, perm, ctx, task, err = _parse("claude --ask explain the bug")
    assert err is None
    assert h == "claude" and mode == "ask" and task == "explain the bug"
    h, mode, model, perm, ctx, task, err = _parse("codex --plan refactor auth")
    assert h == "codex" and mode == "plan"
    _, _, _, _, _, _, err = _parse("nope --ask x")
    assert err is not None


def test_run_delegate_acp_claude(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    import xlii.harness.claude as claude_mod

    fake = str(Path(__file__).parent / "acp_fake_server.py")
    monkeypatch.setattr(claude_mod, "resolve_claude_cli", lambda: sys.executable)
    monkeypatch.setattr(
        "xlii.harness.acp_session.resolve_acp_argv",
        lambda name: [sys.executable, fake],
    )
    result = run_delegate(
        "claude",
        "what failed?",
        mode="ask",
        project_root=tmp_path,
    )
    assert result.error is None
    assert "mode=ask" in result.text
    assert "plan=False" in result.text
    assert "picked=rej-1" in result.text
    assert result.tier == "cross_org"


def test_ensure_grok_mcp_config_adds(tmp_path):
    action = ensure_grok_mcp_config(tmp_path)
    assert action == "added"
    data = json.loads((tmp_path / ".grok" / "mcp.json").read_text())
    assert "xlii-deep-contexts" in data["mcpServers"]
