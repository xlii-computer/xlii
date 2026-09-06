"""L3 polish: plan integration, commits, READ_REQUEST, hooks, external stub."""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

from xlii.hooks import HOOK_EVENTS
from xlii.loop import PLAN_LAST_FILE, LoopController
from xlii.loop_bundle import VerdictBundle, fetch_read_excerpts
from xlii.loop_judge import JudgeProfile, run_llm_judge
from xlii.loop_verdict import ReadRequest, parse_read_requests
from tests.helpers import make_agent


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README").write_text("init\n")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)


def test_parse_read_requests():
    text = """READ_REQUEST:
1. src/a.py:10-20
2. tests/t.py:1-5
"""
    reqs = parse_read_requests(text)
    assert len(reqs) == 2
    assert reqs[0] == ReadRequest(path="src/a.py", start_line=10, end_line=20)


def test_fetch_read_excerpts(tmp_path):
    fp = tmp_path / "src" / "a.py"
    fp.parent.mkdir(parents=True)
    fp.write_text("line1\nline2\nline3\n")
    out = fetch_read_excerpts(
        tmp_path,
        [ReadRequest(path="src/a.py", start_line=2, end_line=2)],
        max_files=3,
    )
    assert "line2" in out
    assert "src/a.py" in out


def test_load_plan_goal(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    (xli / PLAN_LAST_FILE).write_text("Ship the retry feature\n")
    assert "retry" in LoopController.load_plan_goal(xli)


def test_commit_final_on_done(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    _git_init(tmp_path)
    (tmp_path / "feat.py").write_text("x = 1\n")

    ctrl = LoopController.start(
        xli_dir=xli,
        goal="add feat",
        judges=["tests"],
        max_cycles=3,
        test_command="true",
        commit_mode="final",
    )
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "done"
    log = subprocess.run(
        ["git", "log", "--oneline", "-1"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "loop:" in log.stdout


def test_on_loop_cycle_hook_event_registered():
    assert "on-loop-cycle" in HOOK_EVENTS


def test_on_loop_cycle_hook_fires(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    hook_dir = xli / "hooks" / "on-loop-cycle"
    hook_dir.mkdir(parents=True)
    hook = hook_dir / "10-echo.sh"
    hook.write_text('#!/bin/sh\nread p; echo "cycle:$p"\n')
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)

    class Con:
        lines: list[str] = []

        def print(self, msg):
            self.lines.append(str(msg))

    con = Con()
    ctrl = LoopController.start(
        xli_dir=xli,
        goal="noop",
        judges=["tests"],
        max_cycles=1,
        test_command="true",
    )
    ctrl.emit_cycle_hook(tmp_path, outcome="done", console=con)
    assert any("cycle:" in ln for ln in con.lines)


def test_read_followup_requeries(tmp_path, monkeypatch):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    src = tmp_path / "src" / "x.py"
    src.parent.mkdir(parents=True)
    src.write_text("secret_value = 42\n")

    calls: list[str] = []

    def fake_spawn(**kw):
        calls.append(kw["brief"])
        stub = type(
            "C",
            (),
            {"model": "m", "cost_usd": 0.0, "prompt_tokens": 0, "completion_tokens": 0},
        )()
        if len(calls) == 1:
            return ("READ_REQUEST:\n1. src/x.py:1-1\n", stub)
        return ("PASS: ok after read", stub)

    monkeypatch.setattr("xlii.loop_judge.spawn_worker_review", lambda **kw: fake_spawn(**kw))

    ag = make_agent(tmp_path)
    bundle = VerdictBundle(mode="verify", task={"goal": "g"}, artifact={"diff": "+x"})
    verdict = run_llm_judge(
        JudgeProfile(name="v", kind="same_vendor"),
        bundle=bundle,
        xli_dir=xli,
        agent=ag,
        project=ag.project,
        project_root=tmp_path,
        read_budget=3,
    )
    assert verdict.passed
    assert len(calls) == 2
    assert "secret_value" in calls[1]


def test_external_judge_unavailable_without_cli(tmp_path, monkeypatch):
    # External judge degrades to a failing "unavailable" verdict (never raises)
    # when the Cursor CLI is absent, so the loop can record it and move on.
    xli = tmp_path / ".xlii"
    xli.mkdir()
    monkeypatch.setattr("xlii.harness.cursor.resolve_cursor_cli", lambda: None)
    bundle = VerdictBundle(mode="verify", task={"goal": "g"})
    verdict = run_llm_judge(
        JudgeProfile(name="composer", kind="external"),
        bundle=bundle,
        xli_dir=xli,
    )
    assert verdict.passed is False
    assert verdict.signature == "unavailable|no-cli"


def test_loop_start_allows_external_profile(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    ctrl = LoopController.start(
        xli_dir=xli,
        goal="x",
        judges=["composer"],
        max_cycles=1,
        test_command="true",
        config_judges={"composer": {"kind": "external"}},
    )
    assert "composer" in ctrl.state.judges
