"""VerdictBundle assembly for autonomous loop judges."""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from xlii.atomicio import write_text_atomic


def git_cmd(cwd: Path, args: list[str], timeout: int = 30) -> tuple[str, Optional[str]]:
    """Return (stdout, error). error is None on success."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return "", str(e)
    if proc.returncode not in (0, 1):
        err = (proc.stderr or proc.stdout or "").strip() or f"git exit {proc.returncode}"
        return "", err
    return proc.stdout, None


@dataclass
class VerdictBundle:
    bundle_version: int = 1
    loop_id: str = ""
    cycle: int = 1
    mode: str = "verify"
    task: dict[str, Any] = field(default_factory=dict)
    artifact: dict[str, Any] = field(default_factory=dict)
    oracle: dict[str, Any] = field(default_factory=dict)
    history: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def save_audit(self, xli_dir: Path) -> None:
        path = xli_dir / f"loop-bundle-{self.cycle}.json"
        write_text_atomic(path, json.dumps(self.to_dict(), indent=2) + "\n")


def _tail_text(text: str, max_lines: int) -> str:
    lines = (text or "").splitlines()
    if len(lines) <= max_lines:
        return text or ""
    return "\n".join(lines[-max_lines:])


def _files_block(files: list[str]) -> str:
    return "\n".join(f"  - {f}" for f in files) if files else "  (none enumerated)"


def render_bundle_brief(bundle: VerdictBundle) -> str:
    """Render a VerdictBundle as markdown for LLM judges (cold context)."""
    art = bundle.artifact
    files = art.get("files_changed") or []
    files_txt = _files_block(files)
    diff = art.get("diff") or ""
    if art.get("diff_truncated"):
        diff += "\n[diff truncated]"

    if bundle.mode == "peer":
        return (
            f"Range under review: {art.get('commit_range', '?')}\n\n"
            f"Files changed:\n{files_txt}\n\n"
            f"Commit log:\n{art.get('commit_log', '(no commits)')}\n\n"
            f"Full diff:\n{diff}"
        )

    task = bundle.task
    oracle = bundle.oracle
    criteria = task.get("success_criteria") or []
    crit_block = ""
    if criteria:
        crit_block = "\nSuccess criteria:\n" + "\n".join(f"- {c}" for c in criteria) + "\n"

    return (
        f"Original task:\n{task.get('goal', '')}\n"
        f"{crit_block}\n"
        f"Test command: {oracle.get('test_command', '')}\n"
        f"Test exit code: {oracle.get('test_exit_code', '?')}\n"
        f"Test output (tail):\n{oracle.get('test_output_tail', '')}\n\n"
        f"Files changed (uncommitted vs HEAD):\n{files_txt}\n\n"
        f"Diff:\n{diff}"
    )


def assemble_peer_bundle(
    *,
    project_root: Path,
    loop_id: str,
    cycle: int,
    since: str = "HEAD~1",
    diff_max_bytes: int = 120_000,
    prior_findings: Optional[list[dict[str, Any]]] = None,
) -> VerdictBundle:
    """Build a blind peer bundle (no task brief)."""
    base, err = git_cmd(project_root, ["rev-parse", since])
    if err:
        baseline = since
        diff = ""
        log = f"(cannot resolve {since}: {err})"
        names = ""
    else:
        baseline = base.strip()
        rng = f"{baseline}..HEAD"
        diff, _ = git_cmd(project_root, ["diff", rng])
        log, _ = git_cmd(project_root, ["log", "--format=%h %s%n%b", rng])
        names, _ = git_cmd(project_root, ["diff", rng, "--name-only"])

    files_changed = [ln.strip() for ln in (names or "").splitlines() if ln.strip()]
    diff_truncated = False
    if diff and len(diff.encode()) > diff_max_bytes:
        diff = diff.encode()[:diff_max_bytes].decode("utf-8", errors="replace") + "\n…[truncated]"
        diff_truncated = True

    return VerdictBundle(
        loop_id=loop_id,
        cycle=cycle,
        mode="peer",
        task={},
        artifact={
            "files_changed": files_changed,
            "diff": diff or "",
            "diff_truncated": diff_truncated,
            "commit_range": f"{baseline[:12]}..HEAD" if baseline else since,
            "commit_log": (log or "").strip() or "(no commits)",
        },
        oracle={},
        history={"prior_findings": list(prior_findings or [])},
    )


def _collect_changes(
    project_root: Path, base: str, diff_max_bytes: int
) -> tuple[str, list[str], bool]:
    """The builder's full change-set for the judge: everything since the loop's
    baseline commit (committed AND uncommitted), plus untracked new files.

    The old `git diff HEAD` missed two whole classes of change — work the builder
    committed (HEAD moves, the working tree goes clean) and brand-new files
    (untracked files never appear in `git diff`) — which handed the judge an empty
    diff and an unbreakable "zero change" FAIL loop. Diffing against the recorded
    base commit catches committed work; appending untracked files catches new code.
    """
    ref = base or "HEAD"
    diff, _ = git_cmd(project_root, ["diff", ref])
    names, _ = git_cmd(project_root, ["diff", ref, "--name-only"])
    files = [ln.strip() for ln in (names or "").splitlines() if ln.strip()]

    untracked, _ = git_cmd(project_root, ["ls-files", "--others", "--exclude-standard"])
    for rel in [ln.strip() for ln in (untracked or "").splitlines() if ln.strip()]:
        fp = project_root / rel
        if not fp.is_file():
            continue
        try:
            body = fp.read_text(errors="replace")
        except OSError:
            continue
        files.append(rel)
        # Synthesize a "new file" hunk so the judge sees freshly-created code.
        diff += (
            f"\ndiff --git a/{rel} b/{rel}\nnew file mode 100644\n"
            f"--- /dev/null\n+++ b/{rel}\n"
        )
        diff += "".join(f"+{ln}\n" for ln in body.splitlines())

    truncated = False
    if diff and len(diff.encode()) > diff_max_bytes:
        diff = diff.encode()[:diff_max_bytes].decode("utf-8", errors="replace") + "\n…[truncated]"
        truncated = True
    return diff, files, truncated


def assemble_bundle(
    *,
    project_root: Path,
    loop_id: str,
    cycle: int,
    goal: str,
    success_criteria: list[str],
    test_command: str,
    test_exit_code: int,
    test_output: str,
    base: str = "",
    diff_max_bytes: int = 120_000,
    test_output_tail_lines: int = 80,
    prior_findings: Optional[list[dict[str, Any]]] = None,
) -> VerdictBundle:
    """Build a cold artifact bundle (audit trail + LLM judges). `base` is the
    loop's baseline commit; the diff is everything since then (committed +
    uncommitted) plus untracked files — see _collect_changes."""
    diff, files_changed, diff_truncated = _collect_changes(project_root, base, diff_max_bytes)

    test_files: dict[str, str] = {}
    for rel in files_changed:
        if rel.startswith("tests/") or rel.endswith("_test.py") or "/test_" in rel:
            fp = project_root / rel
            if fp.is_file():
                try:
                    test_files[rel] = fp.read_text(errors="replace")
                except OSError:
                    # An unreadable test file is left out of the bundle rather than failing assembly.
                    pass

    return VerdictBundle(
        loop_id=loop_id,
        cycle=cycle,
        mode="verify",
        task={
            "goal": goal,
            "success_criteria": list(success_criteria),
        },
        artifact={
            "files_changed": files_changed,
            "diff": diff or "",
            "diff_truncated": diff_truncated,
        },
        oracle={
            "test_command": test_command,
            "test_exit_code": test_exit_code,
            "test_output_tail": _tail_text(test_output, test_output_tail_lines),
            "test_files": test_files,
        },
        history={
            "prior_findings": list(prior_findings or []),
        },
    )


def new_loop_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    return ts


def fetch_read_excerpts(
    project_root: Path,
    requests: list[Any],
    *,
    max_files: int = 3,
    max_lines_per_file: int = 80,
) -> str:
    """Fetch line-range excerpts for a judge READ_REQUEST (L3)."""
    from xlii.loop_verdict import ReadRequest

    parts: list[str] = []
    for req in requests[:max_files]:
        if not isinstance(req, ReadRequest):
            continue
        rel = req.path.lstrip("./")
        fp = project_root / rel
        if not fp.is_file():
            parts.append(f"### {rel} (not found)\n")
            continue
        try:
            lines = fp.read_text(errors="replace").splitlines()
        except OSError:
            parts.append(f"### {rel} (unreadable)\n")
            continue
        start = max(1, req.start_line) - 1
        end = req.end_line if req.end_line > 0 else min(len(lines), start + max_lines_per_file)
        end = min(end, len(lines))
        if start >= len(lines):
            excerpt = "(start line past end of file)"
        else:
            excerpt = "\n".join(f"{i+1:6}\t{ln}" for i, ln in enumerate(lines[start:end], start=start))
        parts.append(f"### {rel}:{req.start_line}-{end or req.start_line}\n{excerpt}")
    return "\n\n".join(parts)
