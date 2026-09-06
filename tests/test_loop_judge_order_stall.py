"""Loop judge ordering + stall-guard grace.

Two fixes from the field failure where a same-vendor judge (grok grading grok)
FAILed twice with findings-less prose, short-circuited the cross-vendor judge,
and tripped the no-progress guard:

  1. _llm_profiles() runs cross-vendor judges before same-vendor ones, so a weak
     self-judge can't veto the loop before the authoritative judge is heard.
  2. _route_back() gives a findings-less LLM FAIL one extra cycle of grace before
     declaring "not progressing" (its signature is a low-signal prose hash).
"""

from __future__ import annotations

import pytest

from xlii.loop import LoopController
from xlii.loop_judge import JudgeProfile
from xlii.loop_verdict import Finding, Verdict


@pytest.fixture
def xli_dir(tmp_path):
    d = tmp_path / ".xlii"
    d.mkdir()
    return d


def _ctrl(xli_dir, max_cycles=10):
    return LoopController.start(
        xli_dir=xli_dir,
        goal="build it",
        judges=["tests"],
        max_cycles=max_cycles,
        test_command="true",
    )


# --- Fix 1: cross-vendor judges run first ---------------------------------- #

def test_llm_profiles_put_cross_vendor_before_same_vendor(xli_dir):
    ctrl = _ctrl(xli_dir)
    # User order: same-vendor first (the order that caused the field failure).
    ctrl.profiles = [
        JudgeProfile(name="tests", kind="shell"),
        JudgeProfile(name="xai-verify", kind="same_vendor"),
        JudgeProfile(name="anthropic", kind="cross_vendor"),
    ]
    order = [p.name for p in ctrl._llm_profiles()]
    assert order == ["anthropic", "xai-verify"]   # cross-vendor heard first


def test_llm_profiles_is_stable_within_a_tier(xli_dir):
    ctrl = _ctrl(xli_dir)
    ctrl.profiles = [
        JudgeProfile(name="x1", kind="same_vendor"),
        JudgeProfile(name="c1", kind="cross_vendor"),
        JudgeProfile(name="c2", kind="cross_vendor"),
        JudgeProfile(name="x2", kind="same_vendor"),
    ]
    order = [p.name for p in ctrl._llm_profiles()]
    assert order == ["c1", "c2", "x1", "x2"]      # tier order kept within group


# --- Fix 2: findings-less LLM FAIL gets one extra cycle -------------------- #

def _llm_fail(sig="fail|deadbeefdeadbeef", findings=None):
    return Verdict(
        passed=False, summary="FAIL", signature=sig, raw="some prose",
        judge="xai-verify", findings=findings or [],
    )


def test_findingsless_llm_fail_gets_one_extra_cycle(xli_dir):
    ctrl = _ctrl(xli_dir)
    v = _llm_fail()
    # 1st FAIL → route back; 2nd identical → still route back (grace);
    # 3rd identical → "not progressing".
    assert ctrl._route_back(v, failure_kind="llm").status == "continue"
    assert ctrl._route_back(v, failure_kind="llm").status == "continue"
    third = ctrl._route_back(v, failure_kind="llm")
    assert third.status == "failed"
    assert "not progressing" in third.message


def test_llm_fail_with_findings_stops_on_first_repeat(xli_dir):
    ctrl = _ctrl(xli_dir)
    v = _llm_fail(sig="app.kt:3:bug", findings=[Finding(file="app.kt", line=3, tag="bug", text="x")])
    # Structured findings are high-signal: a repeat means genuinely stuck.
    assert ctrl._route_back(v, failure_kind="llm").status == "continue"
    assert ctrl._route_back(v, failure_kind="llm").status == "failed"


def test_test_failure_stops_on_first_repeat(xli_dir):
    ctrl = _ctrl(xli_dir)
    v = Verdict(passed=False, summary="tests failed", signature="exit:1|abcd", judge="tests")
    # Shell-oracle failures are concrete; no grace.
    assert ctrl._route_back(v, failure_kind="tests").status == "continue"
    assert ctrl._route_back(v, failure_kind="tests").status == "failed"
