"""Same-vendor LLM judge integration (L1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.agent import CallStats
from xlii.loop import LoopController, LoopJudgeContext
from xlii.loop_bundle import VerdictBundle, render_bundle_brief
from tests.helpers import make_agent, make_cfg


def test_render_bundle_brief_verify():
    bundle = VerdictBundle(
        mode="verify",
        task={"goal": "add retry", "success_criteria": ["no new deps"]},
        artifact={"files_changed": ["a.py"], "diff": "+code", "diff_truncated": False},
        oracle={"test_command": "pytest -q", "test_exit_code": 0, "test_output_tail": "ok"},
    )
    brief = render_bundle_brief(bundle)
    assert "Original task:" in brief
    assert "add retry" in brief
    assert "no new deps" in brief
    assert "+code" in brief


def test_render_bundle_brief_peer_omits_task():
    bundle = VerdictBundle(
        mode="peer",
        artifact={
            "files_changed": ["b.py"],
            "diff": "+x",
            "commit_range": "abc..HEAD",
            "commit_log": "abc init",
        },
    )
    brief = render_bundle_brief(bundle)
    assert "Original task" not in brief
    assert "abc..HEAD" in brief


def test_loop_tests_plus_verify_fail_routes_findings(tmp_path, monkeypatch):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    ag = make_agent(tmp_path)
    ag.history = [{"role": "system", "content": "s"}]

    fail_text = """FAIL
1. src/foo.py:10 — off by one [correctness]
"""

    monkeypatch.setattr(
        "xlii.loop_judge.spawn_worker_review",
        lambda **kw: (fail_text, CallStats(model="grok-w", iterations=1)),
    )

    ctrl = LoopController.start(
        xli_dir=xli,
        goal="fix foo",
        judges=["tests", "xai-verify"],
        max_cycles=3,
        test_command="true",
    )
    ctx = LoopJudgeContext(agent=ag)
    result = ctrl.advance_after_build(tmp_path, judge_ctx=ctx)
    assert result.status == "continue"
    assert result.next_prompt is not None
    assert "xai-verify" in result.next_prompt or "off by one" in result.next_prompt
    assert "foo.py:10" in result.next_prompt
    assert (xli / "loop-verdict-1-xai-verify.md").exists()


def test_loop_tests_plus_verify_pass_done(tmp_path, monkeypatch):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    ag = make_agent(tmp_path)

    monkeypatch.setattr(
        "xlii.loop_judge.spawn_worker_review",
        lambda **kw: ("PASS: looks correct", CallStats(model="grok-w", iterations=1)),
    )

    ctrl = LoopController.start(
        xli_dir=xli,
        goal="ship",
        judges=["tests", "xai-verify"],
        max_cycles=3,
        test_command="true",
    )
    result = ctrl.advance_after_build(tmp_path, judge_ctx=LoopJudgeContext(agent=ag))
    assert result.status == "done"
    assert ctrl.state.status == "done"


def test_spawn_worker_review_auth_failure_does_not_quarantine(tmp_path, monkeypatch):
    from xlii.loop_judge import spawn_worker_review

    ag = make_agent(tmp_path)
    auth = {"n": 0}
    ag.pool = SimpleNamespace(
        acquire=lambda: SimpleNamespace(label="w"),
        report_success=lambda c: None,
        report_auth_failure=lambda c: auth.__setitem__("n", auth["n"] + 1),
    )

    def boom(**kw):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr("xlii.agent.WorkerAgent.run", lambda self, task, system_prompt_override=None, max_iterations=None: boom())

    with pytest.raises(RuntimeError):
        spawn_worker_review(agent=ag, project=ag.project, brief="x", system_prompt="y")
    assert auth["n"] == 0


def test_spawn_worker_review_honors_model_role(tmp_path, monkeypatch):
    from xlii.loop_judge import spawn_worker_review

    ag = make_agent(tmp_path, cfg=make_cfg(chat="judge-chat-model"))
    seen: dict[str, str] = {}

    class _FakeWorker:
        def __init__(self, **kw):
            seen["model"] = kw.get("model")

        def run(self, task, system_prompt_override=None, max_iterations=None):
            from xlii.agent_stats import CallStats
            return "ok", CallStats(model=seen["model"])

    ag.pool = SimpleNamespace(
        acquire=lambda: SimpleNamespace(label="w"),
        report_success=lambda c: None,
        report_auth_failure=lambda c: None,
    )
    monkeypatch.setattr("xlii.agent.WorkerAgent", _FakeWorker)
    monkeypatch.setattr(
        "xlii.plugin.load_subscriptions",
        lambda xli_dir: [],
    )

    spawn_worker_review(
        agent=ag,
        project=ag.project,
        brief="x",
        system_prompt="y",
        model_role="chat",
    )
    assert seen["model"] == "judge-chat-model"
