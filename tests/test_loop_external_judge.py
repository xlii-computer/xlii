"""External coding-agent judge (Cursor Composer) adapter — unit tests.

The Cursor CLI is mocked here so these run offline/deterministically. Live
end-to-end coverage against a real `cursor-agent` login is exercised separately.
"""

from __future__ import annotations

import json
import subprocess
import types

from xlii import loop_judge as lj
from xlii.harness import cursor as harness_cursor
from xlii.loop_bundle import VerdictBundle
from xlii.loop_judge import JudgeProfile, run_external_judge, run_harness_judge


def _xli(tmp_path):
    d = tmp_path / ".xlii"
    d.mkdir()
    return d


def _bundle(diff: str = "+x") -> VerdictBundle:
    return VerdictBundle(mode="verify", task={"goal": "g"}, artifact={"diff": diff})


def _proc(stdout: str, *, stderr: str = "", returncode: int = 0):
    return types.SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _json_line(result: str, *, is_error: bool = False, usage: dict | None = None) -> str:
    return json.dumps(
        {
            "type": "result",
            "subtype": "error" if is_error else "success",
            "is_error": is_error,
            "result": result,
            "usage": usage or {"inputTokens": 100, "outputTokens": 20},
        }
    )


# --- resolve_cursor_cli -----------------------------------------------------


def test_resolve_cursor_cli_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "cursor-agent"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv("XLII_CURSOR_AGENT_BIN", str(fake))
    assert lj.resolve_cursor_cli() == str(fake)


def test_resolve_cursor_cli_env_override_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_CURSOR_AGENT_BIN", str(tmp_path / "nope"))
    assert lj.resolve_cursor_cli() is None


# --- _parse_cursor_output ---------------------------------------------------


def test_parse_output_success():
    text, usage, err = lj._parse_cursor_output(
        _json_line("PASS: ok", usage={"inputTokens": 7, "outputTokens": 3}), "", 0
    )
    assert err is None
    assert text == "PASS: ok"
    assert usage == {"inputTokens": 7, "outputTokens": 3}


def test_parse_output_ignores_leading_banner_lines():
    out = "loading...\nworkspace trusted\n" + _json_line("FAIL")
    text, _usage, err = lj._parse_cursor_output(out, "", 0)
    assert err is None
    assert text == "FAIL"


def test_parse_output_is_error():
    text, _usage, err = lj._parse_cursor_output(
        _json_line("rate limited", is_error=True), "", 0
    )
    assert text == ""
    assert err is not None and "rate limited" in err


def test_parse_output_empty_uses_stderr():
    text, _usage, err = lj._parse_cursor_output("", "boom", 1)
    assert text == ""
    assert err == "boom"


def test_parse_output_non_json_falls_back_to_text():
    text, usage, err = lj._parse_cursor_output("PASS: plain text mode", "", 0)
    assert err is None
    assert text == "PASS: plain text mode"
    assert usage == {}


# --- run_external_judge -----------------------------------------------------


def test_run_external_judge_pass(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    monkeypatch.setattr(harness_cursor, "resolve_cursor_cli", lambda: "/fake/cursor-agent")
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        captured["input"] = kw.get("input")
        captured["cwd"] = kw.get("cwd")
        return _proc(_json_line("PASS: looks correct", usage={"inputTokens": 1000, "outputTokens": 200}))

    monkeypatch.setattr(harness_cursor.subprocess, "run", fake_run)

    verdict = run_external_judge(
        JudgeProfile(name="cursor", kind="external", model="composer-2.5"),
        bundle=_bundle("+def foo(): pass"),
        xli_dir=xli,
        project_root=tmp_path,
    )

    assert verdict.passed is True
    assert verdict.model == "composer-2.5"
    assert verdict.tokens_in == 1000
    assert verdict.tokens_out == 200
    assert verdict.cost_usd == round(1000 * 3.0 / 1_000_000 + 200 * 15.0 / 1_000_000, 6)
    # command shape: non-interactive, json, read-only, model pinned
    assert captured["cmd"][0] == "/fake/cursor-agent"
    assert "--print" in captured["cmd"]
    assert "json" in captured["cmd"]
    assert captured["cmd"][captured["cmd"].index("--mode") + 1] == "ask"
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == "composer-2.5"
    assert captured["cwd"] == str(tmp_path)
    # the cold diff is fed to the judge via stdin
    assert "+def foo(): pass" in captured["input"]


def test_run_external_judge_fail_with_findings(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    monkeypatch.setattr(harness_cursor, "resolve_cursor_cli", lambda: "/fake/cursor-agent")
    monkeypatch.setattr(
        harness_cursor.subprocess,
        "run",
        lambda cmd, **kw: _proc(_json_line("FAIL\n1. src/x.py:10 — off-by-one [bug]")),
    )

    verdict = run_external_judge(
        JudgeProfile(name="cursor", kind="external"),
        bundle=_bundle(),
        xli_dir=xli,
    )

    assert verdict.passed is False
    assert verdict.findings
    assert verdict.findings[0].file == "src/x.py"
    # default model is applied when the profile omits one
    assert verdict.model == lj.DEFAULT_CURSOR_MODEL


def test_run_external_judge_default_model_when_unset(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    monkeypatch.setattr(harness_cursor, "resolve_cursor_cli", lambda: "/fake/cursor-agent")
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _proc(_json_line("PASS: ok"))

    monkeypatch.setattr(harness_cursor.subprocess, "run", fake_run)
    run_external_judge(JudgeProfile(name="cursor", kind="external"), bundle=_bundle(), xli_dir=xli)
    assert captured["cmd"][captured["cmd"].index("--model") + 1] == lj.DEFAULT_CURSOR_MODEL


def test_run_external_judge_cli_missing(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    monkeypatch.setattr(harness_cursor, "resolve_cursor_cli", lambda: None)
    verdict = run_external_judge(
        JudgeProfile(name="cursor", kind="external"), bundle=_bundle(), xli_dir=xli
    )
    assert verdict.passed is False
    assert verdict.signature == "unavailable|no-cli"
    assert "not found" in verdict.summary


def test_run_external_judge_timeout(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    monkeypatch.setattr(harness_cursor, "resolve_cursor_cli", lambda: "/fake/cursor-agent")

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(harness_cursor.subprocess, "run", fake_run)
    verdict = run_external_judge(
        JudgeProfile(name="cursor", kind="external"),
        bundle=_bundle(),
        xli_dir=xli,
        timeout_s=5,
    )
    assert verdict.passed is False
    assert verdict.signature == "unavailable|timeout"


def test_run_external_judge_launch_error(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    monkeypatch.setattr(harness_cursor, "resolve_cursor_cli", lambda: "/fake/cursor-agent")

    def fake_run(cmd, **kw):
        raise OSError("exec format error")

    monkeypatch.setattr(harness_cursor.subprocess, "run", fake_run)
    verdict = run_external_judge(
        JudgeProfile(name="cursor", kind="external"), bundle=_bundle(), xli_dir=xli
    )
    assert verdict.passed is False
    assert verdict.signature.startswith("unavailable|OSError")


def test_run_external_judge_cli_reported_error(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    monkeypatch.setattr(harness_cursor, "resolve_cursor_cli", lambda: "/fake/cursor-agent")
    monkeypatch.setattr(
        harness_cursor.subprocess,
        "run",
        lambda cmd, **kw: _proc(_json_line("usage limit reached", is_error=True), returncode=1),
    )
    verdict = run_external_judge(
        JudgeProfile(name="cursor", kind="external"), bundle=_bundle(), xli_dir=xli
    )
    assert verdict.passed is False
    assert "usage limit reached" in verdict.summary


def test_cursor_profile_registered():
    prof = lj.DEFAULT_JUDGES["cursor"]
    assert prof.kind == "harness"
    assert prof.harness == "cursor"
    assert prof.model == lj.DEFAULT_CURSOR_MODEL
    assert prof.tier == "cross_agent"


def test_run_harness_judge_uses_configured_harness(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    monkeypatch.setattr(harness_cursor, "resolve_cursor_cli", lambda: "/fake/cursor-agent")
    monkeypatch.setattr(
        harness_cursor.subprocess,
        "run",
        lambda cmd, **kw: _proc(_json_line("PASS: ok")),
    )
    verdict = run_harness_judge(
        JudgeProfile(name="hc", kind="harness", harness="cursor"),
        bundle=_bundle(),
        xli_dir=xli,
    )
    assert verdict.passed is True
