"""Loop integration for CI judge."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from xlii.loop import LoopController
from xlii.loop_verdict import Verdict


def _start(tmp_path, *, judges=None, push_mode="each", commit_mode="each"):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    return LoopController.start(
        xli_dir=xli,
        goal="ship it",
        judges=judges or ["tests", "github-ci"],
        max_cycles=3,
        test_command="true",
        push_mode=push_mode,
        commit_mode=commit_mode,
        project_root=tmp_path,
    )


def test_loop_start_rejects_push_without_commit(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    with pytest.raises(ValueError, match="push_mode requires commit_mode"):
        LoopController.start(
            xli_dir=xli,
            goal="ship",
            judges=["tests", "github-ci"],
            max_cycles=3,
            test_command="true",
            push_mode="each",
            commit_mode="never",
            project_root=tmp_path,
        )


def test_ci_judge_runs_after_tests(tmp_path, monkeypatch):
    ctrl = _start(tmp_path)
    ci_calls: list[bool] = []

    def fake_ci(profile, *, cwd, console=None, do_push=False, run_gh=None):
        ci_calls.append(do_push)
        return Verdict(
            passed=True,
            summary="ci ok",
            signature="ci:pass|abc",
            judge=profile.name,
        )

    monkeypatch.setattr("xlii.loop.run_ci_judge", fake_ci)
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "done"
    assert ci_calls == [True]


def test_ci_fail_routes_back(tmp_path, monkeypatch):
    ctrl = _start(tmp_path)

    def fake_ci(profile, *, cwd, console=None, do_push=False, run_gh=None):
        return Verdict(
            passed=False,
            summary="unit failed",
            signature="ci:fail|deadbeef",
            raw="log tail",
            judge=profile.name,
            exit_code=1,
        )

    monkeypatch.setattr("xlii.loop.run_ci_judge", fake_ci)
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "continue"
    assert result.next_prompt is not None
    assert "remote CI" in result.next_prompt
    assert ctrl.state.cycle == 2


def test_ci_unavailable_fails_loop(tmp_path, monkeypatch):
    ctrl = _start(tmp_path)

    def fake_ci(profile, *, cwd, console=None, do_push=False, run_gh=None):
        return Verdict(
            passed=False,
            summary="ci judge unavailable: no gh",
            signature="unavailable|no gh",
            judge=profile.name,
        )

    monkeypatch.setattr("xlii.loop.run_ci_judge", fake_ci)
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "failed"


def test_judge_panel_skips_ci(tmp_path, monkeypatch):
    ctrl = _start(tmp_path)
    monkeypatch.setattr(
        "xlii.loop.run_ci_judge",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ci should not run")),
    )
    verdict = ctrl.judge_panel(tmp_path)
    assert verdict is None


def test_push_mode_final_pushes_once(tmp_path, monkeypatch):
    ctrl = _start(tmp_path, push_mode="final", commit_mode="final")
    pushes: list[bool] = []

    def fake_ci(profile, *, cwd, console=None, do_push=False, run_gh=None):
        pushes.append(do_push)
        return Verdict(passed=True, summary="ok", signature="ci:pass|1", judge=profile.name)

    monkeypatch.setattr("xlii.loop.run_ci_judge", fake_ci)
    ctrl.advance_after_build(tmp_path)
    assert pushes == [True]
    pushes.clear()
    ctrl.state.status = "active"
    ctrl.state.phase = "build"
    ctrl.advance_after_build(tmp_path)
    assert pushes == [False]


def test_final_commit_before_ci_push(tmp_path, monkeypatch):
    import xlii.ci_judge as cj

    _init_git_repo(tmp_path, branch="feature")
    (tmp_path / "fix.txt").write_text("fix\n")

    xli = tmp_path / ".xlii"
    xli.mkdir()
    ctrl = LoopController.start(
        xli_dir=xli,
        goal="ship",
        judges=["tests", "github-ci"],
        max_cycles=3,
        test_command="true",
        push_mode="final",
        commit_mode="final",
        project_root=tmp_path,
    )

    def fake_ci(profile, *, cwd, console=None, do_push=False, run_gh=None):
        assert do_push is True
        assert not cj.is_dirty(cwd), "final commit should run before CI push"
        return Verdict(passed=True, summary="ok", signature="ci:pass|x", judge=profile.name)

    monkeypatch.setattr("xlii.loop.run_ci_judge", fake_ci)
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "done"


def test_final_commit_before_ci_push_surfaces_status_line(tmp_path, monkeypatch):
    """The commit made before a final-mode CI push is reported in the done banner.

    Without surfacing it, ``_maybe_commit_on_done`` sees a clean tree and the
    ``committed (final)`` receipt is silently lost.
    """
    _init_git_repo(tmp_path, branch="feature")
    (tmp_path / "fix.txt").write_text("fix\n")

    xli = tmp_path / ".xlii"
    xli.mkdir()
    ctrl = LoopController.start(
        xli_dir=xli,
        goal="ship",
        judges=["tests", "github-ci"],
        max_cycles=3,
        test_command="true",
        push_mode="final",
        commit_mode="final",
        project_root=tmp_path,
    )

    def fake_ci(profile, *, cwd, console=None, do_push=False, run_gh=None):
        return Verdict(passed=True, summary="ok", signature="ci:pass|y", judge=profile.name)

    monkeypatch.setattr("xlii.loop.run_ci_judge", fake_ci)
    result = ctrl.advance_after_build(tmp_path)
    assert result.status == "done"
    assert "committed (final)" in result.message


def _init_git_repo(path: Path, *, branch: str = "main") -> None:
    subprocess.run(["git", "init", "-b", branch], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "README").write_text("hi\n")
    subprocess.run(["git", "add", "README"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True)
