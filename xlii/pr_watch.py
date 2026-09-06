"""PR-event producer for the goal inbox (pr-watch P0 + P1).

Polls four ``gh`` reads, translates actionable events into atomic inbox files,
and leaves execution to ``xlii loop --drain-inbox``. P1: the on-loop-cycle hook
is the signal (outbox marker); the watcher flushes push + stamped reply.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from urllib.parse import quote

from xlii.atomicio import write_text_atomic
from xlii.ci_judge import (
    DEFAULT_GRACE_AFTER_PUSH_S,
    DEFAULT_POLL_INTERVAL_S,
    DEFAULT_TIMEOUT_S,
    RunGh,
    checks_signature,
    classify_checks,
    fetch_failed_log_tail,
    fetch_pr_checks,
    gh_preflight,
    poll_pr_checks,
    try_git_push,
)
from xlii.inbox import (
    enqueue_inbox_atomic,
    inbox_dir,
    list_inbox,
    safe_stem,
)
from xlii.loop_bundle import git_cmd

OWN_STAMP = "<!-- xlii-pr-watch -->"
DEFAULT_MARKER = "@xlii"
DEFAULT_INTERVAL_S = 45
MIN_INTERVAL_S = 30
MAX_ENQUEUE_PER_SWEEP = 8
STATE_DIR = "pr-watch"
OUTBOX_DIR = "outbox"
HOOK_NAME = "pr-watch-outbox"
REBUILD_WINDOW_S = 24 * 3600
TOKEN_RE = re.compile(
    r"\[pr-watch pr=(?P<pr>\d+) thread=(?P<thread>\S+) head=(?P<head>[0-9a-fA-F]+)\]"
)
STEM_RE = re.compile(
    r"^(?:\d+-)?pr-(?P<pr>\d+)-(?P<kind>review|comment|ci)-(?P<id>[a-z0-9_-]+)$"
)
# Head refs come from GitHub, i.e. from whoever opened the PR. `git
# check-ref-format` accepts `|`, `>`, and Unicode line separators, all of which
# would re-parse as a different (or empty) `branch:` key and fail *open* on the
# drain-side hold. Only plain ref characters are allowed into frontmatter.
SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
SAFE_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


@dataclass
class SweepResult:
    enqueued: list[Path] = field(default_factory=list)
    disarmed: bool = False
    error: str = ""
    message: str = ""
    pr_number: int = 0
    flush: Optional["FlushResult"] = None


@dataclass
class FlushResult:
    pushed: bool = False
    sha: str = ""
    replied: list[str] = field(default_factory=list)
    skipped: str = ""
    error: str = ""


def _runner(run_gh: RunGh | None) -> RunGh:
    if run_gh is not None:
        return run_gh
    from xlii.ci_judge import _run_gh

    return _run_gh


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_pr_ref(raw: str | int | None) -> int:
    """``142``, a github PR URL, or 0 (``gh pr view`` for the current branch)."""
    if raw is None or raw == "":
        return 0
    if isinstance(raw, int):
        return raw
    s = str(raw).strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"/pull/(\d+)", s)
    if m:
        return int(m.group(1))
    return 0


def state_path(xli_dir: Path) -> Path:
    return xli_dir / STATE_DIR / "state.json"


@contextmanager
def pr_watch_sweep_lock(xli_dir: Path) -> Iterator[bool]:
    """Non-blocking exclusive flock for one ``sweep`` pass.

    Overlapping ``pr sweep`` / ``pr watch`` polls on the same tree used to
    race on ``_next_seq``, ``enqueue_inbox_atomic``, and ``state.json`` — last
    writer could drop ``seen`` entries (silent event loss) or raise on
    ``os.replace``. Mirrors ``inbox_drain_lock``.
    """
    lock_dir = xli_dir / STATE_DIR
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = lock_dir / ".sweep.lock"
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        yield False
        return
    try:
        yield True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load_state(xli_dir: Path) -> dict[str, Any]:
    path = state_path(xli_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return {"prs": {}}
    if not isinstance(raw, dict):
        return {"prs": {}}
    prs = raw.get("prs")
    if not isinstance(prs, dict):
        raw["prs"] = {}
    return raw


def save_state(xli_dir: Path, state: dict[str, Any]) -> None:
    path = state_path(xli_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def outbox_dir(xli_dir: Path) -> Path:
    return xli_dir / STATE_DIR / OUTBOX_DIR


def list_outbox(xli_dir: Path) -> list[Path]:
    d = outbox_dir(xli_dir)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.json") if p.is_file())


def outbox_path(xli_dir: Path, pr: str | int, thread: str, head: str) -> Path:
    stem = re.sub(
        r"[^a-z0-9_-]+", "-",
        f"pr-{pr}-thread-{thread}-head-{head}".lower(),
    ).strip("-")[:80]
    return outbox_dir(xli_dir) / f"{stem}.json"


def write_outbox_from_hook(payload: dict[str, Any]) -> Optional[Path]:
    """on-loop-cycle observer: outcome==done + [pr-watch …] token → marker.

    Never pushes, never talks to GitHub. HOOK_TIMEOUT_S = 10 is the wall.
    """
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None
    if str(data.get("outcome") or "") != "done":
        return None
    goal = str(data.get("goal") or "")
    m = TOKEN_RE.search(goal)
    if not m:
        return None
    root = Path(str(payload.get("project_root") or "") or ".")
    xli = root / ".xlii"
    pr, thread, head = m.group("pr"), m.group("thread"), m.group("head")
    dest = outbox_path(xli, pr, thread, head)
    dest.parent.mkdir(parents=True, exist_ok=True)
    marker = {
        "pr": int(pr),
        "thread": thread,
        "head": head,
        "token": m.group(0),
        "goal": goal,
        "outcome": "done",
    }
    write_text_atomic(dest, json.dumps(marker, indent=2, sort_keys=True) + "\n")
    return dest


def install_outbox_hook(xli_dir: Path) -> Path:
    """Install the on-loop-cycle signal hook. Idempotent; never the executor."""
    hook_dir = xli_dir / "hooks" / "on-loop-cycle"
    hook_dir.mkdir(parents=True, exist_ok=True)
    path = hook_dir / HOOK_NAME
    # Bake the package parent onto sys.path so a drain subprocess can import
    # xlii even when the project venv did not set PYTHONPATH.
    pkg_parent = str(Path(__file__).resolve().parents[1])
    source = (
        f"#!{sys.executable}\n"
        '"""pr-watch P1: on-loop-cycle signal → outbox marker. Never pushes."""\n'
        "import json\n"
        "import sys\n"
        f"sys.path.insert(0, {pkg_parent!r})\n"
        "from xlii.pr_watch import write_outbox_from_hook\n"
        "try:\n"
        "    payload = json.load(sys.stdin)\n"
        "except Exception:\n"
        "    raise SystemExit(0)\n"
        "write_outbox_from_hook(payload)\n"
    )
    path.write_text(source, encoding="utf-8")
    os.chmod(path, path.stat().st_mode | 0o111)
    return path


def write_scope_preflight(cwd: Path, repo: str, *, run_gh: RunGh) -> str:
    """Return an error if authed ``gh`` cannot push/comment on ``repo``.

    ``gh auth status`` is not enough — it does not prove rights on a given
    repo. Probe ``GET /repos/{repo}`` for ``permissions.push``. Fail closed.
    """
    if not repo or "/" not in repo:
        return "write-scope preflight failed: missing owner/repo"
    out, err, code = run_gh(cwd, ["api", f"repos/{repo}"], 30)
    if code != 0:
        detail = (err or out or f"exit {code}").strip()
        return f"write-scope preflight failed: {detail}"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return "write-scope preflight failed: invalid JSON from gh api repos"
    if not isinstance(data, dict):
        return "write-scope preflight failed: unexpected repo payload"
    perms = data.get("permissions")
    if not isinstance(perms, dict):
        return f"write-scope preflight: missing permissions on {repo} — reply-back not armed"
    if perms.get("push") or perms.get("admin"):
        return ""
    return f"write-scope preflight: no push permission on {repo} — reply-back not armed"


def _head_sha(cwd: Path) -> str:
    out, err = git_cmd(cwd, ["rev-parse", "HEAD"])
    if err:
        return ""
    return (out or "").strip()


def _reply_body(sha: str) -> str:
    return f"fixed in {sha}\n{OWN_STAMP}\n"


def _post_reply(
    cwd: Path,
    repo: str,
    pr: int,
    thread: str,
    sha: str,
    run_gh: RunGh,
) -> tuple[str, str]:
    """Post a stamped reply. Returns ``(posted_id, error)``."""
    body = _reply_body(sha)
    paths: list[str] = []
    if thread.isdigit():
        paths.append(f"repos/{repo}/pulls/{pr}/comments/{thread}/replies")
    paths.append(f"repos/{repo}/issues/{pr}/comments")
    last_err = "no reply endpoint"
    for api_path in paths:
        out, err, code = run_gh(
            cwd, ["api", "--method", "POST", api_path, "-f", f"body={body}"], 60,
        )
        if code != 0:
            last_err = (err or out or f"exit {code}").strip()
            continue
        try:
            data = json.loads(out)
        except json.JSONDecodeError:
            last_err = "invalid JSON from comment POST"
            continue
        if not isinstance(data, dict):
            last_err = "comment POST: unexpected payload"
            continue
        pid = str(data.get("id") or "").strip()
        if not pid:
            last_err = "comment POST missing id"
            continue
        return pid, ""
    return "", last_err


def _record_own_push(
    xli_dir: Path,
    pr: int,
    sha: str,
    cwd: Path,
    run_gh: RunGh,
) -> None:
    """Write new head SHA + checks signature so the push is not a fresh event."""
    st = load_state(xli_dir)
    slot = st.setdefault("prs", {}).setdefault(str(pr), {})
    slot["head_sha"] = sha
    checks, err = fetch_pr_checks(cwd, pr, run_gh=run_gh)
    if not err:
        sig = checks_signature(checks)
        slot["checks_signature"] = sig
        seen = set(slot.get("seen") or [])
        seen.add(f"ci:{sha[:12]}|{sig}")
        slot["seen"] = sorted(seen)
    save_state(xli_dir, st)


def _record_posted_id(xli_dir: Path, pr: int, posted_id: str) -> None:
    st = load_state(xli_dir)
    slot = st.setdefault("prs", {}).setdefault(str(pr), {})
    ids = [str(x) for x in (slot.get("posted_ids") or [])]
    if posted_id not in ids:
        ids.append(posted_id)
    slot["posted_ids"] = ids
    save_state(xli_dir, st)


def _load_marker(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return raw


def flush_outbox(
    *,
    cwd: Path,
    xli_dir: Path,
    run_gh: RunGh | None = None,
    push_fn: Callable[..., tuple[bool, str]] | None = None,
    poll_fn: Callable[..., tuple[bool, list, str, bool, bool]] | None = None,
    hold_lock: bool = True,
) -> FlushResult:
    """Watcher-side executor: safe push, poll checks, stamped reply.

    The hook only wrote the marker. Push stays here — never an inbox key.
    """
    if hold_lock:
        with pr_watch_sweep_lock(xli_dir) as got:
            if not got:
                return FlushResult(skipped="pr-watch sweep already running")
            return _flush_outbox_locked(
                cwd=cwd, xli_dir=xli_dir, run_gh=run_gh,
                push_fn=push_fn, poll_fn=poll_fn,
            )
    return _flush_outbox_locked(
        cwd=cwd, xli_dir=xli_dir, run_gh=run_gh,
        push_fn=push_fn, poll_fn=poll_fn,
    )


def _flush_outbox_locked(
    *,
    cwd: Path,
    xli_dir: Path,
    run_gh: RunGh | None,
    push_fn: Callable[..., tuple[bool, str]] | None,
    poll_fn: Callable[..., tuple[bool, list, str, bool, bool]] | None,
) -> FlushResult:
    markers = list_outbox(xli_dir)
    if not markers:
        return FlushResult()
    runner = _runner(run_gh)
    repo, repo_err = _repo_name(cwd, runner)
    if repo_err:
        return FlushResult(error=repo_err)
    scope_err = write_scope_preflight(cwd, repo, run_gh=runner)
    if scope_err:
        return FlushResult(skipped=scope_err)

    parsed: list[tuple[Path, dict[str, Any]]] = []
    for path in markers:
        raw = _load_marker(path)
        if raw is None:
            continue
        try:
            pr = int(raw.get("pr") or 0)
        except (TypeError, ValueError):
            continue
        thread = str(raw.get("thread") or "").strip()
        if pr <= 0 or not thread:
            continue
        parsed.append((path, raw))
    if not parsed:
        return FlushResult()

    pr_branch = ""
    first_pr = int(parsed[0][1]["pr"])
    view, _ = _view_pr(cwd, first_pr, runner)
    if view is not None:
        pr_branch = str(view.get("headRefName") or "").strip()
    if not pr_branch:
        return FlushResult(skipped="could not resolve PR head branch for push gate")

    if push_fn is not None:
        ok, detail = push_fn(cwd, pr_branch=pr_branch, run_gh=runner)
    else:
        ok, detail = try_git_push(cwd, pr_branch=pr_branch or None, run_gh=runner)
    if not ok:
        return FlushResult(skipped=detail or "push refused")

    sha = _head_sha(cwd)
    if not sha:
        return FlushResult(error="git rev-parse HEAD failed after push")

    result = FlushResult(pushed=True, sha=sha)
    seen_pr: set[int] = set()
    polled_ok: dict[int, bool] = {}
    for path, raw in parsed:
        pr = int(raw["pr"])
        thread = str(raw["thread"])
        if pr not in seen_pr:
            _record_own_push(xli_dir, pr, sha, cwd, runner)
            seen_pr.add(pr)
        if pr not in polled_ok:
            if poll_fn is not None:
                passed, _checks, _tail, _timed_out, infra = poll_fn(cwd, pr)
            else:
                passed, _checks, _tail, _timed_out, infra = poll_pr_checks(
                    cwd,
                    pr,
                    poll_interval_s=DEFAULT_POLL_INTERVAL_S,
                    timeout_s=DEFAULT_TIMEOUT_S,
                    grace_s=DEFAULT_GRACE_AFTER_PUSH_S,
                    run_gh=runner,
                )
            polled_ok[pr] = bool(passed) and not infra
        if not polled_ok[pr]:
            continue
        posted_id, post_err = _post_reply(cwd, repo, pr, thread, sha, runner)
        if post_err or not posted_id:
            if not result.error:
                result.error = post_err or "comment POST failed"
            continue
        _record_posted_id(xli_dir, pr, posted_id)
        path.unlink(missing_ok=True)
        result.replied.append(posted_id)
    return result


def _sanitize_goal(text: str, cap: int = 140) -> str:
    s = " ".join((text or "").split())
    s = s.replace("---", "—")
    return s[:cap]


def safe_branch(name: str) -> str:
    """The head ref if it is safe to interpolate into frontmatter, else ``""``.

    Fail-closed: callers refuse the event rather than emit a `branch:` value the
    frontmatter parser could read back as empty (= no branch restriction) or as
    a different branch.
    """
    s = (name or "").strip()
    if not s or ".." in s or not SAFE_BRANCH_RE.match(s):
        return ""
    return s


def _has_marker(body: str, marker: str) -> bool:
    if not marker:
        return True
    return marker.lower() in (body or "").lower()


def _is_own(body: str, event_id: str, posted_ids: set[str]) -> bool:
    if event_id and event_id in posted_ids:
        return True
    return OWN_STAMP in (body or "")


def _repo_name(cwd: Path, run_gh: RunGh) -> tuple[str, str]:
    out, err, code = run_gh(cwd, ["repo", "view", "--json", "nameWithOwner"], 30)
    if code != 0:
        return "", (err or out or f"exit {code}").strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return "", f"invalid JSON from gh repo view: {e}"
    name = str(data.get("nameWithOwner") or "").strip()
    if not name or "/" not in name:
        return "", "gh repo view: missing nameWithOwner"
    return name, ""


def _parse_json_list(raw: str) -> list[Any]:
    text = (raw or "").strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        return [data]
    except json.JSONDecodeError:
        pass
    # ``gh api --paginate`` can concatenate arrays: `][`.
    glued = "[" + text.replace("][", ",") + "]"
    try:
        data = json.loads(glued)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def _api(
    cwd: Path,
    path: str,
    run_gh: RunGh,
    *,
    paginate: bool = False,
) -> tuple[list[Any], str]:
    args = ["api", path]
    if paginate:
        args = ["api", "--paginate", path]
    out, err, code = run_gh(cwd, args, 60)
    if code != 0:
        detail = (err or out or "").strip() or f"exit {code}"
        return [], detail
    return _parse_json_list(out), ""


def _view_pr(
    cwd: Path, pr_number: int, run_gh: RunGh,
) -> tuple[dict[str, Any] | None, str]:
    fields = "number,headRefName,headRefOid,url,state"
    args = ["pr", "view", "--json", fields]
    if pr_number > 0:
        args = ["pr", "view", str(pr_number), "--json", fields]
    out, err, code = run_gh(cwd, args, 60)
    if code != 0:
        return None, (err or out or f"exit {code}").strip()
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return None, f"invalid JSON from gh pr view: {e}"
    if not isinstance(data, dict):
        return None, "gh pr view: unexpected payload"
    return data, ""


def _next_seq(xli_dir: Path) -> int:
    n = 0
    d = inbox_dir(xli_dir)
    for folder in (d, d / "done"):
        if not folder.is_dir():
            continue
        for p in folder.glob("*.md"):
            m = re.match(r"^(\d+)-", p.name)
            if m:
                n = max(n, int(m.group(1)))
    return n + 1


def _problem_glob(xli_dir: Path, pr: int, kind: str, ident: str) -> Optional[Path]:
    d = inbox_dir(xli_dir)
    if not d.is_dir():
        return None
    needle = f"-pr-{pr}-{kind}-{ident}.md"
    matches = sorted(p for p in d.glob("*.md") if p.name.endswith(needle) or p.name == f"pr-{pr}-{kind}-{ident}.md")
    return matches[0] if matches else None


def _rebuild_seen(xli_dir: Path, pr: int) -> set[str]:
    seen: set[str] = set()
    d = inbox_dir(xli_dir)
    for folder in (d, d / "done"):
        if not folder.is_dir():
            continue
        for p in folder.glob("*.md"):
            m = STEM_RE.search(p.stem)
            if not m or int(m.group("pr")) != pr:
                continue
            kind, ident = m.group("kind"), m.group("id")
            if kind == "comment":
                seen.add(f"comment:{ident}")
            elif kind == "review":
                seen.add(f"review:{ident}")
            elif kind == "ci":
                seen.add(f"ci:{ident}")
    return seen


def _comment_seen(seen: set[str], cid: str, updated: str) -> bool:
    if f"comment:{cid}:{updated}" in seen:
        return True
    if f"comment:{cid}" in seen:
        return True
    return False


def prune_stale(xli_dir: Path, pr: int, head_sha: str) -> list[Path]:
    """Drop pending pr-watch files pinned to a superseded head SHA."""
    dropped: list[Path] = []
    for path in list_inbox(xli_dir):
        m = STEM_RE.search(path.stem)
        if not m or int(m.group("pr")) != pr:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        tok = TOKEN_RE.search(text)
        if tok and tok.group("head").lower() != head_sha.lower()[: len(tok.group("head"))]:
            path.unlink(missing_ok=True)
            dropped.append(path)
    return dropped


def _token(pr: int, thread: str, head: str) -> str:
    return f"[pr-watch pr={pr} thread={thread} head={head[:12]}]"


def _render_item(
    *,
    goal: str,
    token: str,
    branch: str,
    body: str,
    max_cycles: int = 5,
    test: str = "pytest -q",
    commit: str = "each",
) -> str:
    # Watcher-synthesized frontmatter only — reviewer text stays in the body.
    safe = safe_branch(branch)
    if not safe:
        raise ValueError(f"unsafe head branch for inbox frontmatter: {branch!r}")
    goal_line = _sanitize_goal(f"{goal} {token}")
    fm = (
        "---\n"
        f"goal: {goal_line}\n"
        f"max_cycles: {max_cycles}\n"
        f"test: {test}\n"
        f"commit: {commit}\n"
        f"branch: {safe}\n"
        "---\n"
    )
    return fm + body.rstrip() + "\n"


def _enqueue(
    xli_dir: Path,
    *,
    pr: int,
    kind: str,
    ident: str,
    body: str,
    seq: int,
) -> Path:
    existing = _problem_glob(xli_dir, pr, kind, ident)
    if existing is not None:
        return enqueue_inbox_atomic(xli_dir, body, stem=existing.stem, dest=existing)
    stem = safe_stem(f"{seq:03d}-pr-{pr}-{kind}-{ident}")
    return enqueue_inbox_atomic(xli_dir, body, stem=stem)


def sweep(
    *,
    cwd: Path,
    xli_dir: Path,
    pr: str | int | None = None,
    marker: str = DEFAULT_MARKER,
    all_comments: bool = False,
    max_enqueue: int = MAX_ENQUEUE_PER_SWEEP,
    run_gh: RunGh | None = None,
    now: float | None = None,
    push_fn: Callable[..., tuple[bool, str]] | None = None,
    poll_fn: Callable[..., tuple[bool, list, str, bool, bool]] | None = None,
) -> SweepResult:
    """One poll pass. Failed reads do not advance cursors."""
    if run_gh is None:
        pre = gh_preflight(cwd)
        if pre:
            return SweepResult(error=pre)
    with pr_watch_sweep_lock(xli_dir) as got:
        if not got:
            return SweepResult(error="pr-watch sweep already running")
        return _sweep_locked(
            cwd=cwd,
            xli_dir=xli_dir,
            pr=pr,
            marker=marker,
            all_comments=all_comments,
            max_enqueue=max_enqueue,
            run_gh=run_gh,
            now=now,
            push_fn=push_fn,
            poll_fn=poll_fn,
        )


def _sweep_locked(
    *,
    cwd: Path,
    xli_dir: Path,
    pr: str | int | None = None,
    marker: str = DEFAULT_MARKER,
    all_comments: bool = False,
    max_enqueue: int = MAX_ENQUEUE_PER_SWEEP,
    run_gh: RunGh | None = None,
    now: float | None = None,
    push_fn: Callable[..., tuple[bool, str]] | None = None,
    poll_fn: Callable[..., tuple[bool, list, str, bool, bool]] | None = None,
) -> SweepResult:
    runner = _runner(run_gh)
    clock = time.time() if now is None else now
    n = parse_pr_ref(pr)

    data, err = _view_pr(cwd, n, runner)
    if err and data is None:
        # Closed/merged ``gh pr view`` exits nonzero — treat as disarm when
        # we asked for a specific number; otherwise it's a real error.
        low = err.lower()
        if n > 0 and any(w in low for w in ("closed", "merged", "not found", "no pull requests")):
            st = load_state(xli_dir)
            slot = st.setdefault("prs", {}).setdefault(str(n), {})
            slot["disarmed"] = True
            save_state(xli_dir, st)
            return SweepResult(
                disarmed=True, pr_number=n,
                message=f"PR #{n} closed/merged — disarming",
            )
        return SweepResult(error=err or "gh pr view failed")
    assert data is not None
    state = str(data.get("state") or "").upper()
    num = int(data.get("number") or 0)
    branch = str(data.get("headRefName") or "").strip()
    head = str(data.get("headRefOid") or "").strip()
    if state and state != "OPEN":
        st = load_state(xli_dir)
        slot = st.setdefault("prs", {}).setdefault(str(num or n), {})
        slot["disarmed"] = True
        save_state(xli_dir, st)
        return SweepResult(
            disarmed=True, pr_number=num or n,
            message=f"PR #{num or n} closed/merged — disarming",
        )
    if num <= 0 or not branch or not head:
        return SweepResult(error="gh pr view: missing number/branch/head")
    if not safe_branch(branch):
        return SweepResult(
            error=f"refusing PR #{num}: head branch {branch!r} is not a plain ref name",
            pr_number=num,
        )
    if not SAFE_SHA_RE.match(head):
        return SweepResult(
            error=f"refusing PR #{num}: head oid {head!r} is not a hex sha",
            pr_number=num,
        )

    repo, repo_err = _repo_name(cwd, runner)
    if repo_err:
        return SweepResult(error=repo_err)

    install_outbox_hook(xli_dir)
    flushed = flush_outbox(
        cwd=cwd, xli_dir=xli_dir, run_gh=runner,
        push_fn=push_fn, poll_fn=poll_fn, hold_lock=False,
    )
    if flushed.pushed:
        data, err = _view_pr(cwd, num, runner)
        if data is not None:
            branch = str(data.get("headRefName") or branch).strip()
            head = str(data.get("headRefOid") or head).strip()

    st = load_state(xli_dir)
    slot = st.setdefault("prs", {}).setdefault(str(num), {})
    rebuilt = False
    if not slot.get("seen") and not state_path(xli_dir).is_file():
        slot["seen"] = sorted(_rebuild_seen(xli_dir, num))
        rebuilt = True
    seen = set(slot.get("seen") or [])
    if not seen:
        extra = _rebuild_seen(xli_dir, num)
        if extra:
            seen |= extra
            rebuilt = True
    posted_ids = {str(x) for x in (slot.get("posted_ids") or [])}
    since = str(slot.get("since") or "")
    if not since or rebuilt:
        since = _iso(clock - REBUILD_WINDOW_S)
    high_water = int(slot.get("review_high_water") or 0)

    prune_stale(xli_dir, num, head)

    since_q = quote(since, safe=":-")
    issue_path = f"repos/{repo}/issues/{num}/comments?since={since_q}"
    review_c_path = f"repos/{repo}/pulls/{num}/comments?since={since_q}"
    reviews_path = f"repos/{repo}/pulls/{num}/reviews"

    issue_comments, e1 = _api(cwd, issue_path, runner)
    if e1:
        return SweepResult(error=f"issue comments: {e1}", pr_number=num)
    review_comments, e2 = _api(cwd, review_c_path, runner)
    if e2:
        return SweepResult(error=f"review comments: {e2}", pr_number=num)
    reviews, e3 = _api(cwd, reviews_path, runner, paginate=True)
    if e3:
        return SweepResult(error=f"reviews: {e3}", pr_number=num)
    checks, e4 = fetch_pr_checks(cwd, num, run_gh=runner)
    if e4:
        return SweepResult(error=f"checks: {e4}", pr_number=num)

    cr_reviews: dict[str, dict[str, Any]] = {}
    new_high = high_water
    for rev in reviews:
        rid = str(rev.get("id") or "")
        if not rid:
            continue
        try:
            iid = int(rid)
        except (TypeError, ValueError):
            iid = 0
        if iid > new_high:
            new_high = iid
        if iid and iid <= high_water:
            continue
        body = str(rev.get("body") or "")
        if _is_own(body, rid, posted_ids):
            continue
        if str(rev.get("state") or "").upper() != "CHANGES_REQUESTED":
            continue
        if f"review:{rid}" in seen:
            continue
        cr_reviews[rid] = rev

    folded_review_ids = set(cr_reviews) | {
        k.split(":", 1)[1] for k in seen if k.startswith("review:")
    }

    actionable_comments: list[dict[str, Any]] = []
    for c in list(issue_comments) + list(review_comments):
        cid = str(c.get("id") or "")
        if not cid:
            continue
        body = str(c.get("body") or "")
        updated = str(c.get("updated_at") or c.get("created_at") or "")
        if _is_own(body, cid, posted_ids):
            continue
        rid = str(c.get("pull_request_review_id") or "")
        if rid and rid in folded_review_ids:
            # Fold into the review file — attach text for a new CR review.
            if rid in cr_reviews:
                cr_reviews[rid].setdefault("_folded", []).append(c)
            continue
        if not all_comments and not _has_marker(body, marker):
            continue
        if _comment_seen(seen, cid, updated):
            continue
        actionable_comments.append(c)

    ci_status, failing = classify_checks(checks)
    ci_sig = checks_signature(checks)
    ci_key = f"ci:{head[:12]}|{ci_sig}"
    ci_ident = re.sub(r"[^a-z0-9_-]+", "-", ci_sig.lower())[:40]
    want_ci = ci_status == "fail" and ci_key not in seen and f"ci:{ci_ident}" not in seen

    planned: list[tuple[str, str, str]] = []  # kind, ident, rendered
    for rid, rev in cr_reviews.items():
        author = str((rev.get("user") or {}).get("login") or "reviewer")
        folded = rev.get("_folded") or []
        hunks = []
        for fc in folded:
            path = str(fc.get("path") or "")
            line = fc.get("line") or fc.get("original_line") or ""
            hunk = str(fc.get("diff_hunk") or "")
            block = (
                f"File: {path}" + (f", line {line}" if line else "") + "\n"
                f"> {_sanitize_goal(str(fc.get('body') or ''), 400)}"
            )
            if hunk:
                block += f"\n<diff hunk>\n{hunk}"
            hunks.append(block)
        body = (
            f"PR #{num} · head {head[:12]} · review {rid} · author {author}\n"
            f"> {_sanitize_goal(str(rev.get('body') or 'changes requested'), 400)}\n"
        )
        if hunks:
            body += "\n" + "\n\n".join(hunks) + "\n"
        token = _token(num, f"review-{rid}", head)
        goal = f"address review on PR #{num}"
        planned.append((
            "review", rid,
            _render_item(goal=goal, token=token, branch=branch, body=body),
        ))

    for c in actionable_comments:
        cid = str(c.get("id") or "")
        author = str((c.get("user") or {}).get("login") or "commenter")
        path = str(c.get("path") or "")
        line = c.get("line") or c.get("original_line") or ""
        loc = f" on {path}:{line}" if path else ""
        token = _token(num, cid, head)
        goal = f"address review comment{loc}"
        body = (
            f"PR #{num} · head {head[:12]} · thread {cid} · author {author}\n"
            f"> {_sanitize_goal(str(c.get('body') or ''), 800)}\n"
        )
        if path:
            body += f"File: {path}" + (f", line {line}" if line else "") + "\n"
        hunk = str(c.get("diff_hunk") or "")
        if hunk:
            body += f"<diff hunk>\n{hunk}\n"
        planned.append((
            "comment", cid,
            _render_item(goal=goal, token=token, branch=branch, body=body),
        ))

    if want_ci:
        names = ", ".join(str(c.get("name") or "?") for c in failing) or "required checks"
        token = _token(num, "ci", head)
        goal = f"fix failing checks: {names}"
        body = (
            f"PR #{num} · head {head[:12]} · CI {ci_sig}\n"
            + "\n".join(f"- {c.get('name')}: {c.get('state')}" for c in failing)
            + "\n"
        )
        tail = fetch_failed_log_tail(cwd, branch, failing=failing, run_gh=runner)
        if tail:
            body += f"\n{tail.rstrip()}\n"
        planned.append((
            "ci", ci_ident,
            _render_item(goal=goal, token=token, branch=branch, body=body),
        ))

    cap = max(1, int(max_enqueue))
    truncated = len(planned) > cap
    planned = planned[:cap]
    enqueued_review_ids = {ident for kind, ident, _ in planned if kind == "review"}
    enqueued_comment_ids = {ident for kind, ident, _ in planned if kind == "comment"}
    seq = _next_seq(xli_dir)
    enqueued: list[Path] = []
    new_seen = set(seen)
    for kind, ident, rendered in planned:
        path = _enqueue(xli_dir, pr=num, kind=kind, ident=ident, body=rendered, seq=seq)
        seq += 1
        enqueued.append(path)
        if kind == "review":
            new_seen.add(f"review:{ident}")
        elif kind == "comment":
            # Find updated_at from the comment we just wrote.
            updated = ""
            for c in list(issue_comments) + list(review_comments):
                if str(c.get("id") or "") == ident:
                    updated = str(c.get("updated_at") or c.get("created_at") or "")
                    break
            new_seen.add(f"comment:{ident}:{updated}")
        elif kind == "ci":
            new_seen.add(ci_key)
            new_seen.add(f"ci:{ident}")

    # Advance cursors only after a complete successful read + enqueue attempt.
    # When the per-sweep cap truncates ``planned``, do not advance past events we
    # skipped — otherwise GitHub's ``since`` filter and ``review_high_water``
    # would permanently drop the overflow.
    if truncated:
        newest = since
        for c in list(issue_comments) + list(review_comments):
            cid = str(c.get("id") or "")
            if not cid:
                continue
            u = str(c.get("updated_at") or c.get("created_at") or "")
            body = str(c.get("body") or "")
            updated = str(c.get("updated_at") or c.get("created_at") or "")
            if _is_own(body, cid, posted_ids):
                if u > newest:
                    newest = u
                continue
            rid = str(c.get("pull_request_review_id") or "")
            if rid and rid in folded_review_ids:
                if rid in enqueued_review_ids or f"review:{rid}" in seen:
                    if u > newest:
                        newest = u
                continue
            if not all_comments and not _has_marker(body, marker):
                if u > newest:
                    newest = u
                continue
            if _comment_seen(seen, cid, updated):
                if u > newest:
                    newest = u
                continue
            if cid in enqueued_comment_ids and u > newest:
                newest = u
        new_high = high_water
        for rev in reviews:
            rid = str(rev.get("id") or "")
            if not rid:
                continue
            try:
                iid = int(rid)
            except (TypeError, ValueError):
                iid = 0
            if not iid or iid <= high_water:
                continue
            body = str(rev.get("body") or "")
            if _is_own(body, rid, posted_ids):
                new_high = max(new_high, iid)
                continue
            if str(rev.get("state") or "").upper() != "CHANGES_REQUESTED":
                new_high = max(new_high, iid)
                continue
            if f"review:{rid}" in seen:
                new_high = max(new_high, iid)
                continue
            if rid in enqueued_review_ids:
                new_high = max(new_high, iid)
    else:
        newest = since
        for c in list(issue_comments) + list(review_comments):
            u = str(c.get("updated_at") or "")
            if u > newest:
                newest = u
    slot["since"] = newest
    slot["review_high_water"] = new_high
    slot["seen"] = sorted(new_seen)
    slot["head_sha"] = head
    slot["disarmed"] = False
    save_state(xli_dir, st)
    msg = f"PR #{num}: enqueued {len(enqueued)}" if enqueued else f"PR #{num}: no new events"
    return SweepResult(enqueued=enqueued, pr_number=num, message=msg, flush=flushed)


def drain_inbox_subprocess(workspace: Path) -> int:
    """Run ``xlii loop --drain-inbox`` as a child so a crash can't take the watcher."""
    proc = __import__("subprocess").run(
        [sys.executable, "-m", "xlii", "loop", "--drain-inbox",
         "--workspace", str(workspace)],
        cwd=str(workspace),
    )
    return int(proc.returncode)


def watch(
    *,
    cwd: Path,
    xli_dir: Path,
    pr: str | int | None = None,
    marker: str = DEFAULT_MARKER,
    all_comments: bool = False,
    interval: int = DEFAULT_INTERVAL_S,
    drain: bool = True,
    run_gh: RunGh | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    drain_fn: Callable[[Path], int] | None = None,
    should_stop: Callable[[], bool] | None = None,
    push_fn: Callable[..., tuple[bool, str]] | None = None,
    poll_fn: Callable[..., tuple[bool, list, str, bool, bool]] | None = None,
) -> int:
    """Foreground loop over :func:`sweep`. Ctrl-C / disarm → 0."""
    wait = max(MIN_INTERVAL_S, int(interval))
    backoff = wait
    n = parse_pr_ref(pr)
    try:
        while True:
            if should_stop is not None and should_stop():
                return 0
            result = sweep(
                cwd=cwd, xli_dir=xli_dir, pr=pr, marker=marker,
                all_comments=all_comments, run_gh=run_gh,
                push_fn=push_fn, poll_fn=poll_fn,
            )
            if result.message:
                print(result.message)
            if result.disarmed:
                print(f"PR #{result.pr_number or n} closed/merged — disarming")
                return 0
            if result.error:
                print(f"pr watch: {result.error}", file=sys.stderr)
                sleep_fn(backoff)
                backoff = min(backoff * 2, 600)
                continue
            backoff = wait
            if drain and result.enqueued:
                fn = drain_fn or drain_inbox_subprocess
                code = fn(cwd)
                print(f"pr watch: drain exited {code}")
                # Tail of --drain: hook wrote markers during the child loop.
                flushed = flush_outbox(
                    cwd=cwd, xli_dir=xli_dir, run_gh=run_gh,
                    push_fn=push_fn, poll_fn=poll_fn,
                )
                if flushed.error or flushed.skipped:
                    print(
                        f"pr watch: flush {flushed.error or flushed.skipped}",
                        file=sys.stderr,
                    )
                elif flushed.replied:
                    print(f"pr watch: replied {len(flushed.replied)}")
            if should_stop is not None:
                # Test hook: one successful pass then the caller decides.
                pass
            sleep_fn(wait)
    except KeyboardInterrupt:
        print("\npr watch: stopped")
        return 0


def hook_main() -> int:
    """``python -m xlii.pr_watch --hook`` — stdin JSON → outbox marker."""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    write_outbox_from_hook(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(hook_main())
