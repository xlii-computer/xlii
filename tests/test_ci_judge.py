"""CI judge adapter tests."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from xlii import ci_judge as cj
from xlii.loop import resolve_commit_mode, resolve_push_mode
from xlii.loop_bundle import git_cmd
from xlii.loop_judge import JudgeProfile


def _profile(**kwargs) -> JudgeProfile:
    base = dict(name="github-ci", kind="ci")
    base.update(kwargs)
    return JudgeProfile(**base)


def _init_git_repo(path: Path, *, branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(["git", "init", "-b", branch], cwd=path, capture_output=True)
    if proc.returncode != 0:
        subprocess.run(["git", "init"], cwd=path, check=True)
        subprocess.run(["git", "checkout", "-b", branch], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / "README").write_text("hi\n")
    subprocess.run(["git", "add", "README"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True)


def test_classify_checks_pass():
    status, failing = cj.classify_checks([
        {"name": "test", "state": "SUCCESS"},
        {"name": "lint", "state": "SKIPPED"},
    ])
    assert status == "pass"
    assert failing == []


def test_classify_checks_pending():
    status, _failing = cj.classify_checks([{"name": "test", "state": "IN_PROGRESS"}])
    assert status == "pending"


def test_classify_checks_fail():
    status, failing = cj.classify_checks([{"name": "test", "state": "FAILURE"}])
    assert status == "fail"
    assert len(failing) == 1


def test_classify_checks_empty_passes():
    status, failing = cj.classify_checks([])
    assert status == "pass"
    assert failing == []


def test_classify_checks_mixed_pending_and_fail_is_pending():
    """A pending check alongside a failure stays pending, regardless of order.

    Guards the pending-wins ordering: a refactor that returned ``fail`` the
    moment it saw a failure (before scanning for pending) would route a still-
    running CI back to the builder as a hard failure.
    """
    for checks in (
        [{"name": "a", "state": "FAILURE"}, {"name": "b", "state": "IN_PROGRESS"}],
        [{"name": "b", "state": "IN_PROGRESS"}, {"name": "a", "state": "FAILURE"}],
    ):
        status, failing = cj.classify_checks(checks)
        assert status == "pending"
        assert failing == []


def test_checks_signature_timeout_is_stable():
    assert cj.checks_signature([], timed_out=True) == "ci:timeout"


def test_checks_signature_fail_uses_names_only():
    sig1 = cj.checks_signature([
        {"name": "unit", "state": "FAILURE"},
        {"name": "lint", "state": "PENDING"},
    ])
    sig2 = cj.checks_signature([
        {"name": "unit", "state": "FAILURE"},
        {"name": "lint", "state": "IN_PROGRESS"},
    ])
    assert sig1 == sig2
    assert sig1.startswith("ci:fail|")


def test_run_ci_judge_missing_gh(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    verdict = cj.run_ci_judge(_profile(), cwd=Path(tmp_path))
    assert verdict.passed is False
    assert verdict.signature.startswith("unavailable|")


def test_run_gh_file_not_found(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(cj.subprocess, "run", boom)
    out, err, code = cj._run_gh(Path(tmp_path), ["auth", "status"], timeout=1)
    assert code == 127
    assert "gh not found" in err
    assert out == ""


def test_run_ci_judge_no_pr(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/gh")

    def fake_gh(cwd, args, timeout=60):
        if args[:2] == ["auth", "status"]:
            return "", "", 0
        if args[:2] == ["pr", "view"]:
            return "", "no pull requests", 1
        return "", "unexpected", 1

    monkeypatch.setattr(cj, "_run_gh", fake_gh)
    verdict = cj.run_ci_judge(_profile(), cwd=Path(tmp_path))
    assert verdict.passed is False
    assert verdict.signature.startswith("unavailable|")


def test_run_ci_judge_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(cj.time, "sleep", lambda _s: None)

    checks = [{"name": "ci/test", "state": "SUCCESS", "workflow": "test"}]

    def fake_gh(cwd, args, timeout=60):
        if args[:2] == ["auth", "status"]:
            return "", "", 0
        if args[:2] == ["pr", "view"]:
            return json.dumps({
                "number": 7,
                "headRefName": "feat",
                "url": "https://example/pr/7",
                "state": "OPEN",
            }), "", 0
        if args[:2] == ["pr", "checks"]:
            return json.dumps(checks), "", 0
        return "", f"unexpected {args}", 1

    monkeypatch.setattr(cj, "_run_gh", fake_gh)
    prof = _profile(grace_after_push_s=0, poll_interval_s=1, timeout_s=5)
    verdict = cj.run_ci_judge(prof, cwd=Path(tmp_path), run_gh=fake_gh)
    assert verdict.passed is True
    assert "passed" in verdict.summary.lower()


def test_run_ci_judge_empty_required_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(cj.time, "sleep", lambda _s: None)
    calls: list[bool] = []

    def fake_gh(cwd, args, timeout=60):
        if args[:2] == ["auth", "status"]:
            return "", "", 0
        if args[:2] == ["pr", "view"]:
            return json.dumps({
                "number": 1,
                "headRefName": "feat",
                "url": "https://example/pr/1",
                "state": "OPEN",
            }), "", 0
        if args[:2] == ["pr", "checks"]:
            required = "--required" in args
            calls.append(required)
            if required:
                return "[]", "", 0
            return json.dumps([{"name": "unit", "state": "SUCCESS"}]), "", 0
        return "", f"unexpected {args}", 1

    monkeypatch.setattr(cj, "_run_gh", fake_gh)
    prof = _profile(grace_after_push_s=0, poll_interval_s=1, timeout_s=5)
    verdict = cj.run_ci_judge(prof, cwd=Path(tmp_path), run_gh=fake_gh)
    assert verdict.passed is True
    assert calls == [True, False]


def test_run_ci_judge_fail_with_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(cj.time, "sleep", lambda _s: None)

    def fake_gh(cwd, args, timeout=60):
        if args[:2] == ["auth", "status"]:
            return "", "", 0
        if args[:2] == ["pr", "view"]:
            return json.dumps({
                "number": 3,
                "headRefName": "main",
                "url": "https://example/pr/3",
                "state": "OPEN",
            }), "", 0
        if args[:2] == ["pr", "checks"]:
            payload = [{
                "name": "unit",
                "state": "FAILURE",
                "link": "https://github.com/o/r/actions/runs/99",
            }]
            return json.dumps(payload), "", 0
        if args[:3] == ["run", "view", "99"]:
            return "FAILED test_foo\nassert 1 == 2\n", "", 0
        return "", f"unexpected {args}", 1

    monkeypatch.setattr(cj, "_run_gh", fake_gh)
    prof = _profile(grace_after_push_s=0, poll_interval_s=1, timeout_s=5)
    verdict = cj.run_ci_judge(prof, cwd=Path(tmp_path), run_gh=fake_gh)
    assert verdict.passed is False
    assert verdict.findings
    assert "assert" in verdict.raw


def test_run_ci_judge_push_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/gh")

    def fake_gh(cwd, args, timeout=60):
        if args[:2] == ["auth", "status"]:
            return "", "", 0
        if args[:2] == ["pr", "view"]:
            return json.dumps({
                "number": 2,
                "headRefName": "feat",
                "url": "https://example/pr/2",
                "state": "OPEN",
            }), "", 0
        return "", "", 0

    monkeypatch.setattr(cj, "_run_gh", fake_gh)
    monkeypatch.setattr(
        cj,
        "try_git_push",
        lambda _cwd, pr_branch=None, run_gh=None: (False, "rejected"),
    )
    verdict = cj.run_ci_judge(_profile(), cwd=Path(tmp_path), do_push=True, run_gh=fake_gh)
    assert verdict.passed is False
    assert verdict.signature.startswith("unavailable|")


def test_poll_retries_transient_gh_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(cj.time, "sleep", lambda _s: None)
    attempts = {"n": 0}

    def fake_gh(cwd, args, timeout=60):
        if args[:2] == ["pr", "checks"]:
            attempts["n"] += 1
            if attempts["n"] < 3:
                return "", "network blip", 1
            return json.dumps([{"name": "unit", "state": "SUCCESS"}]), "", 0
        return "", "unexpected", 1

    passed, checks, tail, timed_out, infra_failed = cj.poll_pr_checks(
        Path(tmp_path),
        1,
        poll_interval_s=1,
        timeout_s=10,
        grace_s=0,
        run_gh=fake_gh,
    )
    assert passed is True
    assert attempts["n"] == 3
    assert not timed_out
    assert not infra_failed


def test_poll_infra_failure_is_flagged(tmp_path, monkeypatch):
    monkeypatch.setattr(cj.time, "sleep", lambda _s: None)

    def fake_gh(cwd, args, timeout=60):
        return "", "network down", 1

    passed, _checks, tail, timed_out, infra_failed = cj.poll_pr_checks(
        Path(tmp_path),
        1,
        poll_interval_s=1,
        timeout_s=10,
        grace_s=0,
        run_gh=fake_gh,
    )
    assert passed is False
    assert infra_failed is True
    assert not timed_out


def test_try_git_push_refuses_devel_default(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo, branch="devel")
    remote = tmp_path / "origin.git"
    remote.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare"], cwd=remote, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)
    monkeypatch.setattr(
        cj,
        "default_remote_branch",
        lambda cwd, run_gh=None: ("devel", None),
    )
    ok, detail = cj.try_git_push(repo)
    assert ok is False
    assert "default" in detail.lower()


def test_try_git_push_refuses_when_default_unknown(tmp_path, monkeypatch):
    _init_git_repo(tmp_path, branch="feature")
    real_which = shutil.which

    def fake_which(name):
        if name == "gh":
            return None
        return real_which(name)

    monkeypatch.setattr(shutil, "which", fake_which)
    ok, detail = cj.try_git_push(tmp_path)
    assert ok is False
    assert "default branch" in detail.lower()


def test_try_git_push_succeeds_with_bare_remote(tmp_path, monkeypatch):
    remote = tmp_path / "origin.git"
    remote.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare"], cwd=remote, check=True)
    repo = tmp_path / "repo"
    _init_git_repo(repo, branch="main")
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=repo, check=True)
    (repo / "feat").write_text("f\n")
    subprocess.run(["git", "add", "feat"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "feat"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=repo, check=True)
    monkeypatch.setattr(
        cj,
        "default_remote_branch",
        lambda cwd, run_gh=None: ("main", None),
    )

    ok, detail = cj.try_git_push(repo, pr_branch="feature")
    assert ok is True
    assert detail == "feature"
    head, herr = git_cmd(repo, ["rev-parse", "HEAD"])
    remote_sha, rerr = git_cmd(repo, ["rev-parse", "origin/feature"])
    assert not herr and not rerr
    assert head.strip() == remote_sha.strip()


def test_git_push_verify_rejects_sha_mismatch(tmp_path, monkeypatch):
    """_git_push fails closed when origin/<branch> != HEAD after a 'successful' push."""

    class _OkProc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(cj.subprocess, "run", lambda *a, **k: _OkProc())

    def fake_git_cmd(cwd, args, **kw):
        if args[:1] == ["rev-parse"]:
            return ("a" * 40 + "\n", None) if args[1].startswith("origin/") else ("b" * 40 + "\n", None)
        return "", None

    monkeypatch.setattr(cj, "git_cmd", fake_git_cmd)
    ok, detail = cj._git_push(tmp_path, "feature")
    assert ok is False
    assert "does not match head" in detail.lower()


def test_git_push_verify_fails_closed_on_rev_parse_error(tmp_path, monkeypatch):
    """_git_push fails closed when the post-push rev-parse can't be read."""

    class _OkProc:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(cj.subprocess, "run", lambda *a, **k: _OkProc())
    monkeypatch.setattr(cj, "git_cmd", lambda cwd, args, **kw: ("", "fatal: bad revision"))
    ok, detail = cj._git_push(tmp_path, "feature")
    assert ok is False
    assert "verification failed" in detail.lower()


def test_default_remote_branch_non_object_gh_json_fails_closed(tmp_path, monkeypatch):
    """A valid-but-non-object gh body stays fail-closed instead of raising."""
    monkeypatch.setattr(cj.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(
        cj,
        "git_cmd",
        lambda cwd, args, **kw: ("", "fatal: no origin/HEAD"),
    )
    for body in ("null", "[]", '"main"', "42"):
        default, err = cj.default_remote_branch(
            tmp_path, run_gh=lambda cwd, a, timeout=60, _b=body: (_b, "", 0)
        )
        assert default is None
        assert err  # fail-closed: error set, no exception


def test_try_git_push_refuses_main(tmp_path):
    _init_git_repo(tmp_path, branch="main")
    ok, detail = cj.try_git_push(tmp_path)
    assert ok is False
    assert "main" in detail.lower()


def test_try_git_push_refuses_dirty_tree(tmp_path, monkeypatch):
    _init_git_repo(tmp_path, branch="feature")
    monkeypatch.setattr(
        cj,
        "is_protected_branch",
        lambda cwd, branch, run_gh=None: (False, ""),
    )
    (tmp_path / "dirty").write_text("x\n")
    ok, detail = cj.try_git_push(tmp_path)
    assert ok is False
    assert "uncommitted" in detail.lower()


def test_resolve_push_mode_defaults():
    assert resolve_push_mode(None, ["tests"], {}) == "never"
    assert resolve_push_mode(None, ["tests", "github-ci"], {}) == "never"
    assert resolve_push_mode("each", ["tests", "github-ci"], {}) == "each"


def test_resolve_commit_mode_implies_each_when_push():
    assert resolve_commit_mode(None, "each", loop_defaults=None) == "each"
    assert resolve_commit_mode("final", "each", loop_defaults=None) == "final"
    assert resolve_commit_mode(None, "never", loop_defaults=None) == "never"
