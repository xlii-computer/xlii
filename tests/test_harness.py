"""Harness adapter unit tests (offline, subprocess mocked)."""

from __future__ import annotations

import json
import subprocess
import types

import pytest

from xlii.harness import claude as claude_mod
from xlii.harness import cursor as cursor_mod
from xlii.harness.base import parse_json_stdout
from xlii.harness.brief import HarnessBrief
from xlii.harness.detect import detect_all, harness_meta, list_harness_names


def _json_line(result: str, *, is_error: bool = False, usage: dict | None = None) -> str:
    return json.dumps(
        {
            "type": "result",
            "is_error": is_error,
            "result": result,
            "usage": usage or {"inputTokens": 10, "outputTokens": 5},
        }
    )


def test_harness_brief_render():
    b = HarnessBrief(
        question="Is this safe?",
        messages=[{"role": "user", "content": "prior context"}],
        system="You are a reviewer.",
    )
    text = b.render_prompt()
    assert "You are a reviewer." in text
    assert "[user]" in text
    assert "prior context" in text
    assert "Is this safe?" in text


def test_parse_json_stdout_success():
    text, usage, err = parse_json_stdout(_json_line("hello"), "", 0)
    assert err is None
    assert text == "hello"
    assert usage["inputTokens"] == 10


def test_list_harness_names():
    names = list_harness_names()
    assert "cursor" in names
    assert "claude" in names


def test_harness_meta_cursor():
    meta = harness_meta("cursor")
    assert meta["tier"] == "cross_agent"


def test_harness_meta_unknown():
    with pytest.raises(ValueError, match="unknown harness"):
        harness_meta("nope")


def test_cursor_run_ask_success(tmp_path, monkeypatch):
    monkeypatch.setattr(cursor_mod, "resolve_cursor_cli", lambda: "/fake/cursor-agent")
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")
        captured["cwd"] = kw.get("cwd")
        return types.SimpleNamespace(stdout=_json_line("second opinion"), stderr="", returncode=0)

    monkeypatch.setattr(cursor_mod.subprocess, "run", fake_run)
    result = cursor_mod.run_ask(
        HarnessBrief(question="q?", project_root=tmp_path),
        model="composer-2.5",
    )
    assert result.error is None
    assert result.text == "second opinion"
    assert result.tier == "cross_agent"
    assert captured["cmd"][captured["cmd"].index("--mode") + 1] == "ask"
    assert captured["cwd"] == str(tmp_path)


def test_cursor_run_ask_missing_cli():
    # resolve_cursor_cli returns None when env override path missing — patch directly
    import xlii.harness.cursor as cmod

    orig = cmod.resolve_cursor_cli
    try:
        cmod.resolve_cursor_cli = lambda: None
        result = cmod.run_ask(HarnessBrief(question="q"))
    finally:
        cmod.resolve_cursor_cli = orig
    assert result.error is not None
    assert "not found" in result.error.lower()


def test_claude_run_ask_success(monkeypatch):
    monkeypatch.setattr(claude_mod, "resolve_claude_cli", lambda: "/fake/claude")
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return types.SimpleNamespace(stdout=_json_line("claude says hi"), stderr="", returncode=0)

    monkeypatch.setattr(claude_mod.subprocess, "run", fake_run)
    result = claude_mod.run_ask(HarnessBrief(question="q?"), model="claude-sonnet-4-6")
    assert result.error is None
    assert result.text == "claude says hi"
    assert "--bare" in captured["cmd"]


def test_claude_run_ask_timeout(monkeypatch):
    monkeypatch.setattr(claude_mod, "resolve_claude_cli", lambda: "/fake/claude")

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(claude_mod.subprocess, "run", fake_run)
    result = claude_mod.run_ask(HarnessBrief(question="q"), timeout_s=3)
    assert result.error is not None
    assert "timed out" in result.error


def test_detect_all_reports_known_harnesses():
    rows = detect_all()
    names = {r[0] for r in rows}
    assert names == {"cursor", "claude", "codex", "grok"}
