"""LoopController state machine and persistence (L0)."""

from __future__ import annotations

import json

import pytest

from xlii.loop import LoopController


@pytest.fixture
def xli_dir(tmp_path):
    d = tmp_path / ".xlii"
    d.mkdir()
    return d


def test_start_persists_active_file(xli_dir):
    ctrl = LoopController.start(
        xli_dir=xli_dir,
        goal="fix tests",
        judges=["tests"],
        max_cycles=3,
        test_command="true",
    )
    assert (xli_dir / "loop-active.json").exists()
    data = json.loads((xli_dir / "loop-active.json").read_text())
    assert data["goal"] == "fix tests"
    assert data["cycle"] == 1
    assert ctrl.is_active


def test_load_round_trip(xli_dir):
    LoopController.start(
        xli_dir=xli_dir,
        goal="ship it",
        judges=["tests"],
        max_cycles=5,
        test_command="pytest -q",
    )
    loaded = LoopController.load(xli_dir)
    assert loaded is not None
    assert loaded.state.goal == "ship it"
    assert loaded.state.max_cycles == 5


def test_corrupt_active_file_returns_none(xli_dir):
    (xli_dir / "loop-active.json").write_text("{not json")
    assert LoopController.load(xli_dir) is None


def test_advance_pass_marks_done(xli_dir, tmp_path):
    ctrl = LoopController.start(
        xli_dir=xli_dir,
        goal="noop",
        judges=["tests"],
        max_cycles=3,
        test_command="true",
    )
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "done"
    assert ctrl.state.status == "done"
    assert (xli_dir / "loop-bundle-1.json").exists()


def test_advance_fail_routes_fix(xli_dir, tmp_path):
    ctrl = LoopController.start(
        xli_dir=xli_dir,
        goal="fix red tests",
        judges=["tests"],
        max_cycles=3,
        test_command="false",
    )
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "continue"
    assert result.next_prompt is not None
    assert "fix red tests" in result.next_prompt
    assert ctrl.state.cycle == 2


def test_repeat_signature_fails(xli_dir, tmp_path):
    ctrl = LoopController.start(
        xli_dir=xli_dir,
        goal="stuck",
        judges=["tests"],
        max_cycles=5,
        test_command="false",
    )
    r1 = ctrl.advance_after_build(tmp_path)
    assert r1.status == "continue"
    # Simulate another build turn without changing tests outcome
    r2 = ctrl.advance_after_build(tmp_path)
    assert r2.status == "failed"
    assert ctrl.state.status == "failed"


def test_max_cycles_caps(xli_dir, tmp_path):
    counter = xli_dir / "fail_counter"
    counter.write_text("0")
    py = tmp_path / "fail_incr.py"
    py.write_text(
        f"""import pathlib
p = pathlib.Path({str(counter)!r})
n = int(p.read_text() or '0')
p.write_text(str(n + 1))
print(f'fail-{{n}}', flush=True)
raise SystemExit(1)
"""
    )
    ctrl = LoopController.start(
        xli_dir=xli_dir,
        goal="never green",
        judges=["tests"],
        max_cycles=2,
        test_command=f"python3 {py}",
    )
    ctrl.advance_after_build(tmp_path)  # cycle 1 fail → cycle 2
    result = ctrl.advance_after_build(tmp_path)  # cycle 2 fail → capped
    assert result.status == "capped"
    assert ctrl.state.status == "capped"


def test_cross_vendor_judge_allowed(xli_dir):
    ctrl = LoopController.start(
        xli_dir=xli_dir,
        goal="x",
        judges=["anthropic"],
        max_cycles=1,
        test_command="true",
        config_judges={
            "anthropic": {
                "kind": "cross_vendor",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
                "api_key_env": "ANTHROPIC_API_KEY",
                "tier": "cross_org",
            }
        },
    )
    assert ctrl.state.lock_tests is True
    assert "anthropic" in ctrl.state.judges


def test_xai_verify_allowed(xli_dir):
    ctrl = LoopController.start(
        xli_dir=xli_dir,
        goal="ship",
        judges=["tests", "xai-verify"],
        max_cycles=3,
        test_command="true",
    )
    assert "xai-verify" in ctrl.state.judges


def test_pause_and_resume(xli_dir):
    ctrl = LoopController.start(
        xli_dir=xli_dir,
        goal="g",
        judges=["tests"],
        max_cycles=1,
        test_command="true",
    )
    ctrl.pause()
    assert ctrl.state.status == "paused"
    ctrl.resume()
    assert ctrl.state.status == "active"


def test_inconclusive_signature_halts_with_starvation_message(xli_dir):
    # Repeated verifier starvation still ends the loop (it IS non-progress),
    # but the operator must hear "the judge couldn't run", not "your code
    # keeps failing" — opposite diagnoses (2026-07-10 postmortem).
    from xlii.loop_verdict import Verdict

    ctrl = LoopController.start(
        xli_dir=xli_dir,
        goal="g",
        judges=["tests", "xai-verify"],
        max_cycles=9,
        test_command="true",
    )
    v = Verdict(
        passed=False,
        summary="verifier could not finish — inconclusive",
        signature="inconclusive|worker-budget",
        judge="xai-verify",
    )
    results = [ctrl._route_back(v, failure_kind="llm") for _ in range(3)]
    assert results[0].status == "paused"
    assert results[1].status == "paused"
    assert results[-1].status == "failed"
    assert "verifier starvation" in results[-1].message
    assert "NOT a code failure" in results[-1].message
    assert "not progressing" not in results[-1].message


def test_inconclusive_does_not_route_builder_fix(xli_dir):
    from xlii.loop_verdict import Verdict

    ctrl = LoopController.start(
        xli_dir=xli_dir,
        goal="g",
        judges=["xai-verify"],
        max_cycles=9,
        test_command="true",
    )
    v = Verdict(
        passed=False,
        summary="verifier could not finish — inconclusive",
        signature="inconclusive|worker-budget",
        judge="xai-verify",
    )
    before_cycle = ctrl.state.cycle
    result = ctrl._route_back(v, failure_kind="llm")
    assert result.status == "paused"
    assert result.next_prompt is None
    assert ctrl.state.cycle == before_cycle
    assert ctrl.state.phase != "build"


def test_status_lines_label_verdicts_with_cycle(xli_dir):
    # A stale prior-cycle ✗ must not read as the current state (2026-07-10:
    # an unlabeled "tests ✗" from cycle 1 looked live during cycle 2).
    ctrl = LoopController.start(
        xli_dir=xli_dir,
        goal="g",
        judges=["tests"],
        max_cycles=3,
        test_command="true",
    )
    ctrl.state.last_verdicts = [
        {"judge": "tests", "passed": False, "cycle": 1, "signature": "x", "summary": "tests failed"},
        {"judge": "tests", "passed": True, "cycle": 2, "signature": "y", "summary": "tests passed"},
    ]
    text = "\n".join(ctrl.status_lines())
    assert "c1 tests ✗" in text
    assert "c2 tests ✓" in text
