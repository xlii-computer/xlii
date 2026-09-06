"""CI judge — poll required PR checks via the GitHub CLI (``gh``).

Forge-agnostic by design: no GitHub REST client in core. The ``gh`` binary is the
only integration surface, matching xlii's local-git philosophy (see
``proposals/done/project-browser.md``).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

from xlii.loop_bundle import git_cmd
from xlii.loop_judge import JudgeProfile
from xlii.loop_verdict import Finding, Verdict

# Test seam — patch ``_run_gh`` in unit tests.
RunGh = Callable[[Path, list[str], int], tuple[str, str, int]]

_PASS_STATES = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
_FAIL_STATES = frozenset({
    "FAILURE",
    "CANCELLED",
    "ACTION_REQUIRED",
    "TIMED_OUT",
    "STARTUP_FAILURE",
    "ERROR",
})
_PENDING_STATES = frozenset({"PENDING", "IN_PROGRESS", "QUEUED", "WAITING", "REQUESTED"})

DEFAULT_POLL_INTERVAL_S = 30
DEFAULT_TIMEOUT_S = 1800
DEFAULT_GRACE_AFTER_PUSH_S = 120
LOG_TAIL_LINES = 80
MAX_CONSECUTIVE_FETCH_ERRORS = 5
MAX_GRACE_S = 600


def _run_gh(cwd: Path, args: list[str], timeout: int = 60) -> tuple[str, str, int]:
    try:
        proc = subprocess.run(
            ["gh", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.stdout or "", proc.stderr or "", proc.returncode
    except subprocess.TimeoutExpired:
        return "", "gh command timed out", 124
    except FileNotFoundError:
        return "", "gh not found on PATH", 127


def _unavailable(profile: JudgeProfile, reason: str) -> Verdict:
    short = reason[:40]
    return Verdict(
        passed=False,
        summary=f"ci judge unavailable: {reason}",
        signature=f"unavailable|{short}",
        raw=reason,
        judge=profile.name,
        exit_code=127,
    )


def _gh_runner(run_gh: RunGh | None) -> RunGh:
    return run_gh if run_gh is not None else _run_gh


def gh_preflight(cwd: Path, *, run_gh: RunGh | None = None) -> Optional[str]:
    """Return an error string when ``gh`` is missing or not authed, else None."""
    runner = _gh_runner(run_gh)
    if shutil.which("gh") is None:
        return "gh not found on PATH — install GitHub CLI and run `gh auth login`"
    out, err, code = runner(cwd, ["auth", "status"], timeout=30)
    if code != 0:
        detail = (err or out or "").strip() or f"exit {code}"
        return f"gh auth status failed: {detail}"
    return None


def resolve_pr_number(
    cwd: Path,
    *,
    pr_number: int = 0,
    run_gh: RunGh | None = None,
) -> tuple[int, str, str] | None:
    """Return ``(number, head_branch, url)`` for an OPEN PR, or None."""
    runner = _gh_runner(run_gh)
    fields = "number,headRefName,url,state"
    if pr_number > 0:
        args = ["pr", "view", str(pr_number), "--json", fields]
    else:
        args = ["pr", "view", "--json", fields]
    out, err, code = runner(cwd, args, timeout=60)
    if code != 0:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    num = int(data.get("number") or 0)
    branch = str(data.get("headRefName") or "").strip()
    url = str(data.get("url") or "").strip()
    state = str(data.get("state") or "").upper()
    if state and state != "OPEN":
        return None
    if num <= 0 or not branch:
        return None
    return num, branch, url


def _fetch_checks(
    cwd: Path,
    pr_number: int,
    *,
    required_only: bool,
    run_gh: RunGh,
) -> tuple[list[dict[str, Any]], str]:
    args = [
        "pr",
        "checks",
        str(pr_number),
        "--json",
        "name,state,link,workflow",
    ]
    if required_only:
        args.insert(4, "--required")
    out, err, code = run_gh(cwd, args, timeout=120)
    if code != 0:
        detail = (err or out or "").strip() or f"exit {code}"
        return [], detail
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return [], f"invalid JSON from gh pr checks: {e}"
    if not isinstance(data, list):
        return [], "gh pr checks returned unexpected payload"
    return data, ""


def fetch_pr_checks(
    cwd: Path,
    pr_number: int,
    *,
    run_gh: RunGh | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """Fetch checks for a PR. Tries required first, then all checks if required is empty."""
    runner = _gh_runner(run_gh)
    checks, err = _fetch_checks(cwd, pr_number, required_only=True, run_gh=runner)
    if err:
        return [], err
    if not checks:
        checks, err = _fetch_checks(cwd, pr_number, required_only=False, run_gh=runner)
    return checks, err


def classify_checks(checks: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Classify check rollup as ``pending``, ``pass``, or ``fail``.

    Returns ``(status, failing_checks)``.
    """
    if not checks:
        return "pass", []

    failing: list[dict[str, Any]] = []
    for chk in checks:
        state = str(chk.get("state") or "").upper()
        if state in _PENDING_STATES or not state:
            return "pending", []
        if state in _FAIL_STATES:
            failing.append(chk)
        elif state not in _PASS_STATES:
            return "pending", []

    if failing:
        return "fail", failing
    return "pass", []


def checks_signature(
    checks: list[dict[str, Any]],
    *,
    timed_out: bool = False,
) -> str:
    if timed_out:
        return "ci:timeout"
    failing = [c for c in checks if str(c.get("state") or "").upper() in _FAIL_STATES]
    if failing:
        parts = sorted(str(c.get("name") or "?") for c in failing)
        digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
        return f"ci:fail|{digest}"
    if not checks:
        return "ci:empty"
    parts = sorted(str(c.get("name") or "?") for c in checks)
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return f"ci:pass|{digest}"


def _run_id_from_check_link(link: str) -> int | None:
    """Extract a workflow run id from a GitHub check details URL when present."""
    if not link:
        return None
    m = re.search(r"/actions/runs/(\d+)", link)
    if m:
        return int(m.group(1))
    return None


def fetch_failed_log_tail(
    cwd: Path,
    branch: str,
    *,
    failing: list[dict[str, Any]],
    run_gh: RunGh | None = None,
    tail_lines: int = LOG_TAIL_LINES,
) -> str:
    """Best-effort failed workflow log tail for the first failing check."""
    runner = _gh_runner(run_gh)
    if not failing:
        return ""
    target = failing[0]
    run_id = _run_id_from_check_link(str(target.get("link") or ""))
    if run_id is None:
        name = str(target.get("name") or "").strip()
        out, err, code = runner(
            cwd,
            [
                "run",
                "list",
                "--branch",
                branch,
                "--limit",
                "10",
                "--json",
                "databaseId,conclusion,displayTitle,workflowName",
            ],
            timeout=120,
        )
        if code != 0:
            return (err or out or "").strip()
        try:
            runs = json.loads(out)
        except json.JSONDecodeError:
            return (out or err or "").strip()
        if isinstance(runs, list):
            for run in runs:
                if not isinstance(run, dict):
                    continue
                conclusion = str(run.get("conclusion") or "").lower()
                title = str(run.get("displayTitle") or "")
                wf = str(run.get("workflowName") or "")
                if conclusion == "failure" and (not name or name in title or name in wf):
                    run_id = int(run.get("databaseId") or 0) or None
                    break

    if not run_id:
        names = ", ".join(str(c.get("name") or "?") for c in failing[:5])
        return f"CI checks failed ({names}) — could not resolve workflow run id for logs"

    log_out, log_err, log_code = runner(cwd, ["run", "view", str(run_id), "--log-failed"], timeout=300)
    combined = (log_out or "") + (log_err or "")
    if log_code != 0 and not combined.strip():
        return f"gh run view {run_id} --log-failed failed (exit {log_code})"
    lines = combined.splitlines()
    return "\n".join(lines[-tail_lines:]) if lines else combined.strip()


def current_branch(cwd: Path) -> tuple[Optional[str], Optional[str]]:
    out, err = git_cmd(cwd, ["rev-parse", "--abbrev-ref", "HEAD"])
    if err:
        return None, err
    branch = (out or "").strip()
    if not branch or branch == "HEAD":
        return None, "detached HEAD — cannot push"
    return branch, None


def default_remote_branch(cwd: Path, *, run_gh: RunGh | None = None) -> tuple[Optional[str], Optional[str]]:
    """Return ``(default_branch, error)``. Error is set when the default is unknown."""
    out, err = git_cmd(cwd, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    if not err and out.strip():
        ref = out.strip()
        prefix = "refs/remotes/origin/"
        if ref.startswith(prefix):
            return ref[len(prefix):], None

    runner = _gh_runner(run_gh)
    if shutil.which("gh") is None:
        return None, (
            "could not determine default branch (origin/HEAD unset) — "
            "run `git remote set-head origin -a` or install `gh`"
        )
    gh_out, gh_err, code = runner(cwd, ["repo", "view", "--json", "defaultBranchRef"], timeout=60)
    if code != 0:
        detail = (gh_err or gh_out or "").strip() or f"gh exit {code}"
        return None, (
            "could not determine default branch — run `git remote set-head origin -a` "
            f"or fix gh auth ({detail})"
        )
    try:
        data = json.loads(gh_out)
    except json.JSONDecodeError:
        return None, "could not parse gh repo view defaultBranchRef"
    # gh returns an object on exit 0, but guard against a non-object body
    # (null/array/scalar) so a malformed response stays fail-closed via the
    # ``(None, error)`` contract instead of raising AttributeError.
    if not isinstance(data, dict):
        return None, "could not parse gh repo view defaultBranchRef"
    ref = data.get("defaultBranchRef")
    name = ref.get("name") if isinstance(ref, dict) else None
    if not name:
        return None, "could not determine default branch from gh repo view"
    return str(name), None


def is_dirty(cwd: Path) -> bool:
    out, err = git_cmd(cwd, ["status", "--porcelain"])
    return not err and bool((out or "").strip())


def is_protected_branch(
    cwd: Path,
    branch: str,
    *,
    run_gh: RunGh | None = None,
) -> tuple[bool, str]:
    lowered = branch.lower()
    if lowered in {"main", "master"}:
        return True, f"refusing to auto-push protected branch {branch}"
    default, derr = default_remote_branch(cwd, run_gh=run_gh)
    if derr:
        return True, f"refusing to auto-push: {derr}"
    if default and branch == default:
        return True, f"refusing to auto-push default branch {branch}"
    return False, ""


def _git_push(cwd: Path, branch: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            ["git", "push", "-u", "origin", branch],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "git push timed out"
    except FileNotFoundError:
        return False, "git not found on PATH"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip() or f"git push exit {proc.returncode}"
        return False, detail
    remote_sha, err = git_cmd(cwd, ["rev-parse", f"origin/{branch}"])
    head_sha, err2 = git_cmd(cwd, ["rev-parse", "HEAD"])
    if err or err2 or not remote_sha.strip() or not head_sha.strip():
        return False, f"push verification failed: could not read origin/{branch} or HEAD"
    if remote_sha.strip() != head_sha.strip():
        return False, f"push completed but origin/{branch} does not match HEAD"
    return True, branch


def try_git_push(
    cwd: Path,
    *,
    pr_branch: Optional[str] = None,
    run_gh: RunGh | None = None,
) -> tuple[bool, str]:
    """Push current branch to ``origin`` when safe. Returns ``(ok, detail)``."""
    branch, err = current_branch(cwd)
    if err or branch is None:
        return False, err or "unknown branch"

    protected, reason = is_protected_branch(cwd, branch, run_gh=run_gh)
    if protected:
        return False, reason

    if pr_branch and branch != pr_branch:
        return False, f"current branch {branch} does not match PR head {pr_branch}"

    if is_dirty(cwd):
        return False, "refusing to push with uncommitted changes — use --commit each"

    return _git_push(cwd, branch)


def poll_pr_checks(
    cwd: Path,
    pr_number: int,
    *,
    poll_interval_s: int,
    timeout_s: int,
    grace_s: int,
    console: Any | None = None,
    run_gh: RunGh | None = None,
) -> tuple[bool, list[dict[str, Any]], str, bool, bool]:
    """Poll checks until pass, fail, timeout, or repeated fetch errors.

    Returns ``(passed, checks, log_tail, timed_out, infra_failed)``.
    """
    runner = _gh_runner(run_gh)
    grace_s = min(max(0, grace_s), MAX_GRACE_S)
    if grace_s > 0:
        time.sleep(grace_s)

    deadline = time.monotonic() + max(1, timeout_s)
    last_checks: list[dict[str, Any]] = []
    consecutive_errors = 0
    while time.monotonic() < deadline:
        checks, err = fetch_pr_checks(cwd, pr_number, run_gh=runner)
        if err:
            consecutive_errors += 1
            if consecutive_errors >= MAX_CONSECUTIVE_FETCH_ERRORS:
                return False, last_checks, err, False, True
            if console is not None:
                console.print(f"[dim][loop] ci: fetch error ({consecutive_errors}) — retrying[/dim]")
            time.sleep(max(1, poll_interval_s))
            continue
        consecutive_errors = 0
        last_checks = checks
        status, failing = classify_checks(checks)
        if console is not None and status == "pending":
            names = ", ".join(
                f"{c.get('name', '?')}:{c.get('state', '?')}" for c in checks[:6]
            )
            console.print(f"[dim][loop] ci: waiting — {names}[/dim]")
        if status == "pass":
            return True, checks, "", False, False
        if status == "fail":
            pr = resolve_pr_number(cwd, pr_number=pr_number, run_gh=runner)
            branch = pr[1] if pr else ""
            tail = fetch_failed_log_tail(cwd, branch, failing=failing, run_gh=runner)
            return False, checks, tail, False, False
        time.sleep(max(1, poll_interval_s))

    names = ", ".join(f"{c.get('name', '?')}:{c.get('state', '?')}" for c in last_checks[:8])
    return False, last_checks, f"CI poll timed out after {timeout_s}s — last: {names or '(no checks)'}", True, False


def run_ci_judge(
    profile: JudgeProfile,
    *,
    cwd: Path,
    console: Any | None = None,
    do_push: bool = False,
    run_gh: RunGh | None = None,
) -> Verdict:
    """Poll required PR checks and return a machine verdict."""
    runner = _gh_runner(run_gh)
    auth_err = gh_preflight(cwd, run_gh=runner)
    if auth_err:
        return _unavailable(profile, auth_err)

    pr = resolve_pr_number(cwd, pr_number=profile.pr_number, run_gh=runner)
    if pr is None:
        msg = (
            "no open PR for the current branch — create one first "
            "(`gh pr create`) or set pr_number in the judge profile"
        )
        return _unavailable(profile, msg)

    pr_number, branch, url = pr

    if do_push:
        ok, detail = try_git_push(cwd, pr_branch=branch, run_gh=runner)
        if not ok:
            return _unavailable(profile, f"git push failed: {detail}")
        if console is not None:
            console.print(f"[dim][loop] ci: pushed {detail}[/dim]")

    if console is not None:
        console.print(f"[dim][loop] ci: watching PR #{pr_number} ({branch}) {url}[/dim]")

    passed, checks, tail, timed_out, infra_failed = poll_pr_checks(
        cwd,
        pr_number,
        poll_interval_s=profile.poll_interval_s,
        timeout_s=profile.timeout_s,
        grace_s=profile.grace_after_push_s if do_push else 0,
        console=console,
        run_gh=runner,
    )

    if infra_failed:
        return _unavailable(profile, tail or "CI fetch failed repeatedly")

    sig = checks_signature(checks, timed_out=timed_out)
    if passed:
        names = ", ".join(str(c.get("name") or "?") for c in checks[:8])
        summary = f"CI checks passed ({names})" if names else "CI checks passed (no checks on PR)"
        return Verdict(
            passed=True,
            summary=summary,
            signature=sig,
            raw=summary,
            judge=profile.name,
            exit_code=0,
        )

    if timed_out:
        return Verdict(
            passed=False,
            summary=tail[:120] if tail else "CI poll timed out",
            signature=sig,
            raw=tail,
            judge=profile.name,
            exit_code=1,
        )

    if tail and not checks:
        return _unavailable(profile, tail)

    findings: list[Finding] = []
    failing = [c for c in checks if str(c.get("state") or "").upper() in _FAIL_STATES]
    for chk in failing[:10]:
        name = str(chk.get("name") or "check")
        state = str(chk.get("state") or "FAILURE")
        link = str(chk.get("link") or "")
        text = f"{name} — {state}"
        if link:
            text += f" ({link})"
        findings.append(Finding(text=text, tag="ci"))

    summary = (
        f"CI checks failed: {', '.join(str(c.get('name') or '?') for c in failing[:5])}"
        if failing
        else (tail[:120] if tail else "CI checks failed")
    )
    return Verdict(
        passed=False,
        summary=summary,
        signature=sig,
        raw=tail or summary,
        judge=profile.name,
        exit_code=1,
        findings=findings,
    )
