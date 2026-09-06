"""Cross-vendor judges, budget cap, and collusion (L2)."""

from __future__ import annotations

import pytest

from xlii.loop import LoopController
from xlii.secondary_ai import SecondaryResponse
from tests.helpers import make_agent


ANTHROPIC_CFG = {
    "anthropic": {
        "kind": "cross_vendor",
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "api_key_env": "ANTHROPIC_API_KEY",
        "tier": "cross_org",
    }
}


def test_cross_vendor_runs_after_tests_pass(tmp_path, monkeypatch):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    calls: list[str] = []

    def fake_query_verdict(brief, *, profile, mode="verify", pricing=None):
        calls.append(brief[:40])
        return SecondaryResponse(
            text="PASS: independent check ok",
            model="claude-sonnet-4-6",
            provider="anthropic",
            cost_usd=0.02,
        )

    monkeypatch.setattr("xlii.secondary_ai.query_verdict", fake_query_verdict)

    ctrl = LoopController.start(
        xli_dir=xli,
        goal="ship",
        judges=["tests", "anthropic"],
        max_cycles=3,
        test_command="true",
        config_judges=ANTHROPIC_CFG,
    )
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "done"
    assert len(calls) == 1
    assert ctrl.state.cost["judges_usd"] == pytest.approx(0.02)


def test_cross_vendor_skipped_when_tests_fail(tmp_path, monkeypatch):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    called = {"n": 0}

    def fake_query_verdict(*a, **kw):
        called["n"] += 1
        return SecondaryResponse(text="PASS: ok", model="m", provider="anthropic")

    monkeypatch.setattr("xlii.secondary_ai.query_verdict", fake_query_verdict)

    ctrl = LoopController.start(
        xli_dir=xli,
        goal="fix",
        judges=["tests", "anthropic"],
        max_cycles=3,
        test_command="false",
        config_judges=ANTHROPIC_CFG,
    )
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "continue"
    assert called["n"] == 0


def test_judge_budget_cap(tmp_path, monkeypatch):
    xli = tmp_path / ".xlii"
    xli.mkdir()

    monkeypatch.setattr(
        "xlii.secondary_ai.query_verdict",
        lambda *a, **kw: SecondaryResponse(
            text="PASS: ok", model="m", provider="anthropic", cost_usd=0.05
        ),
    )

    ctrl = LoopController.start(
        xli_dir=xli,
        goal="ship",
        judges=["tests", "anthropic"],
        max_cycles=3,
        test_command="true",
        budget_usd=0.01,
        config_judges=ANTHROPIC_CFG,
    )
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "capped"
    assert ctrl.state.status == "capped"


def test_collusion_alarm_on_weakened_tests(tmp_path, monkeypatch):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    test_file = tmp_path / "tests" / "test_weak.py"
    test_file.parent.mkdir()
    test_file.write_text(
        "def test_a():\n    assert 1\n\ndef test_b():\n    assert 2\n"
    )

    monkeypatch.setattr(
        "xlii.secondary_ai.query_verdict",
        lambda *a, **kw: SecondaryResponse(
            text="PASS: ok", model="m", provider="anthropic", cost_usd=0.01
        ),
    )

    ctrl = LoopController.start(
        xli_dir=xli,
        goal="ship",
        judges=["tests", "anthropic"],
        max_cycles=3,
        test_command="true",
        config_judges=ANTHROPIC_CFG,
    )
    r1 = ctrl.advance_after_build(tmp_path)
    assert r1.status == "done"

    ctrl.state.status = "active"
    ctrl.state.phase = "build"
    ctrl.state.cycle = 2
    ctrl.save()

    test_file.write_text("def test_a():\n    assert 1\n")
    r2 = ctrl.advance_after_build(tmp_path)
    assert r2.status == "failed"
    assert (xli / "loop-collusion-alarm.md").exists()


def test_sync_test_lock_on_session(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    ag = make_agent(tmp_path)
    ctrl = LoopController.start(
        xli_dir=xli,
        goal="g",
        judges=["anthropic"],
        max_cycles=1,
        test_command="true",
        config_judges=ANTHROPIC_CFG,
    )
    ctrl.sync_test_lock(ag.session)
    assert ag.session.loop_lock_tests is True
    ctrl.cancel()
    ag.session.loop_lock_tests = True
    ctrl2 = LoopController.load(xli, ANTHROPIC_CFG)
    assert ctrl2 is None
    ctrl.sync_test_lock(ag.session)
    assert ag.session.loop_lock_tests is False


def test_cross_vendor_fail_routes_findings(tmp_path, monkeypatch):
    xli = tmp_path / ".xlii"
    xli.mkdir()

    fail_text = "FAIL\n1. src/x.py:1 — bug [correctness]\n"
    monkeypatch.setattr(
        "xlii.secondary_ai.query_verdict",
        lambda *a, **kw: SecondaryResponse(
            text=fail_text, model="m", provider="anthropic", cost_usd=0.01
        ),
    )

    ctrl = LoopController.start(
        xli_dir=xli,
        goal="fix",
        judges=["tests", "anthropic"],
        max_cycles=3,
        test_command="true",
        config_judges=ANTHROPIC_CFG,
    )
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "continue"
    assert "anthropic" in (result.next_prompt or "") or "bug" in (result.next_prompt or "")
