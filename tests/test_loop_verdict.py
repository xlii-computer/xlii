"""Verdict parsing tests."""

from __future__ import annotations

from xlii.loop_verdict import parse_verdict, shell_verdict


def test_shell_verdict_pass():
    v = shell_verdict(judge="tests", exit_code=0, output="ok\n")
    assert v.passed
    assert "exit:0" in v.signature


def test_parse_pass():
    v = parse_verdict("PASS: all good", judge="xai-verify")
    assert v.passed
    assert v.summary == "all good"


def test_parse_fail_findings():
    text = """FAIL
1. src/foo.py:10 — off by one [correctness]
2. src/bar.py:3 — missing branch [correctness]
"""
    v = parse_verdict(text, judge="anthropic")
    assert not v.passed
    assert len(v.findings) == 2
    assert v.findings[0].file == "src/foo.py"
    assert v.findings[0].line == 10


def test_starved_worker_is_inconclusive_not_fail():
    # A starved reviewer said nothing about the code. Before this contract,
    # the sentinel prose hashed into a constant fail| signature and the
    # no-progress rail read repeated starvation as "same code failure 3x"
    # (the 2026-07-10 loop postmortem).
    from xlii.worker_agent import STARVED_SENTINEL

    v = parse_verdict(STARVED_SENTINEL, judge="xai-verify")
    assert v.passed is False
    assert v.signature == "inconclusive|worker-budget"
    assert "could not finish" in v.summary
    assert "inconclusive" in v.summary
    # parse_verdict keys on the exact string worker_agent returns — pin the pair
    # (the literal is duplicated in loop_verdict so the leaf module imports nothing).
    assert STARVED_SENTINEL == "(worker hit max_worker_iterations without finishing)"


def test_starved_sentinel_exact_match_only():
    # Only the bare worker sentinel is inconclusive — a real verifier report that
    # quotes the literal must still parse as PASS/FAIL.
    from xlii.worker_agent import STARVED_SENTINEL

    v = parse_verdict(f"partial notes...\n{STARVED_SENTINEL}", judge="xai-verify")
    assert v.signature != "inconclusive|worker-budget"
