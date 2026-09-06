"""Build-plan swarm — make a parallel-build doc executable (Vector C, Tier 4).

The recursion the proposal calls out: a parallel-build plan (this very document)
decomposes work into independent **vectors**; this module *runs* it. It parses
the plan into vectors, drives one harness session per vector — each in its own
git worktree so they can't collide — collects the results, and optionally judges
each (the existing Cursor judge). The build plan that describes building the
thing becomes the thing that runs the build plan.

Design for isolation + testability:
  • ``parse_build_plan`` is pure text → ``list[VectorSpec]`` (no IO);
  • the per-vector ``runner`` and the ``judge`` are **injected** (default: a real
    ``run_delegate`` against the worktree; judge off by default), so the
    orchestration is unit-testable without git, a harness binary, or a network;
  • worktrees reuse the same ``git worktree`` plumbing as ``xlii.swarm``.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from xlii.loop_bundle import git_cmd
from xlii.swarm import worktrees_root

# Injected per-vector runner: (spec, worktree, *, harness, mode, model, timeout_s)
# -> an object with .text / .files_touched / .error (a DelegateResult by default).
Runner = Callable[..., Any]
# Injected judge: (spec, worktree, run_result) -> (passed: bool, verdict: str).
Judge = Callable[..., "tuple[bool, str]"]
ProgressCallback = Callable[[str, str, Optional["VectorOutcome"]], None]

# `## Vector A1 — Context Tabs & Kinds` / `## Vector C - Harness Modes`. Accepts
# em dash, en dash, or hyphen as the name↔title separator.
_VECTOR_RE = re.compile(r"^##\s+Vector\s+([A-Za-z0-9]+)\b\s*[—–-]?\s*(.*)$")
_SECTION_BREAK_RE = re.compile(r"^(##\s|#\s|---\s*$)")
_OWNS_RE = re.compile(r"\*\*Owns:\*\*\s*(.+)", re.IGNORECASE | re.DOTALL)


@dataclass
class VectorSpec:
    """One vector parsed out of a build-plan doc."""

    name: str
    title: str = ""
    body: str = ""
    owns: list[str] = field(default_factory=list)


@dataclass
class VectorOutcome:
    """The result of running (and maybe judging) one vector."""

    vector: str
    title: str = ""
    branch: str = ""
    worktree: str = ""
    text: str = ""
    files_touched: list[str] = field(default_factory=list)
    error: Optional[str] = None
    passed: Optional[bool] = None
    verdict: str = ""


@dataclass
class SwarmReport:
    plan_title: str = ""
    harness: str = ""
    swarm_id: str = ""
    outcomes: list[VectorOutcome] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(o.error is None for o in self.outcomes)

    @property
    def passed(self) -> bool:
        """True when every judged vector passed (and none errored)."""
        if not self.ok:
            return False
        return all(o.passed is not False for o in self.outcomes)


# --------------------------------------------------------------------------- #
#  Parsing — the build-plan doc → vectors
# --------------------------------------------------------------------------- #

def _plan_title(text: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# ") and not s.startswith("## "):
            return s[2:].strip()
    return ""


def _extract_owns(body: str) -> list[str]:
    """Best-effort file list from an `**Owns:** a.py, b.py` line in the body."""
    m = _OWNS_RE.search(body)
    if not m:
        return []
    chunk = m.group(1)
    # Stop at the next bold field (e.g. **Reads only:**) or a blank-line break.
    chunk = re.split(r"\n\s*\n|\*\*[A-Z]", chunk)[0]
    files: list[str] = []
    for tok in re.findall(r"`([^`]+)`", chunk):
        for part in tok.split(","):
            part = part.strip()
            if part:
                files.append(part)
    return files


def parse_build_plan(text: str) -> list[VectorSpec]:
    """Parse a build-plan doc into its vectors (pure; no IO).

    Recognises ``## Vector <name> — <title>`` headers and captures each section's
    body up to the next heading / ``---`` rule, plus a best-effort `**Owns:**`
    file list."""
    lines = text.splitlines()
    specs: list[VectorSpec] = []
    i = 0
    n = len(lines)
    while i < n:
        m = _VECTOR_RE.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1).strip()
        title = m.group(2).strip()
        j = i + 1
        body_lines: list[str] = []
        while j < n and not _SECTION_BREAK_RE.match(lines[j]):
            body_lines.append(lines[j])
            j += 1
        body = "\n".join(body_lines).strip()
        specs.append(VectorSpec(name=name, title=title, body=body, owns=_extract_owns(body)))
        i = j
    return specs


def build_vector_brief(spec: VectorSpec, *, plan_title: str = "", total: int = 0, index: int = 0) -> str:
    """Wrap a vector's section in the launching-wrapper contract (the prepend the
    proposal specifies) so a harness session gets the same brief a human session
    would: own only your files, meet other surfaces through their seams, ship
    green tests for your vector."""
    seq = f" of {total}" if total else ""
    header = (
        f"You are building **Vector {spec.name}**{(' — ' + spec.title) if spec.title else ''}"
        f"{(', session ' + str(index) + seq) if total else ''}, one of several vectors built in "
        "parallel. Read your vector below. Own ONLY the files in your Owns list; contribute to "
        "another vector's surface through its published seam (register a provider/tab/mode), never "
        "by editing its files. Ship green tests for your vector. If you need something outside your "
        "lane, stop and surface it."
    )
    parts = [header]
    if plan_title:
        parts.append(f"Build plan: {plan_title}")
    parts.append(f"## Vector {spec.name}{(' — ' + spec.title) if spec.title else ''}\n\n{spec.body}")
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
#  Worktrees — one per vector, reusing the git worktree plumbing
# --------------------------------------------------------------------------- #

def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-") or "x"


def new_swarm_id() -> str:
    return time.strftime("build-%Y%m%d-%H%M%S")


def _head_commit(repo_root: Path) -> Optional[str]:
    out, err = git_cmd(repo_root, ["rev-parse", "HEAD"])
    if err or not out.strip():
        return None
    return out.strip()


def _swarm_base_dir(repo_root: Path, swarm_id: str) -> Path:
    return worktrees_root() / _slug(repo_root.name) / swarm_id


def create_vector_worktree(
    repo_root: Path, swarm_id: str, vector_name: str, base_commit: str
) -> tuple[Path, str]:
    """`git worktree add -B swarm/<id>/<vector> <wt> <base>` → (worktree, branch)."""
    branch = f"swarm/{_slug(swarm_id)}/{_slug(vector_name)}"
    wt = _swarm_base_dir(repo_root, swarm_id) / _slug(vector_name)
    if wt.exists():
        remove_vector_worktree(repo_root, wt)
    wt.parent.mkdir(parents=True, exist_ok=True)
    _, err = git_cmd(repo_root, ["worktree", "add", "-B", branch, str(wt), base_commit])
    if err:
        raise RuntimeError(f"worktree add {vector_name}: {err}")
    return wt, branch


def remove_vector_worktree(repo_root: Path, worktree: Path) -> None:
    git_cmd(repo_root, ["worktree", "remove", "--force", str(worktree)])
    if worktree.exists():
        import shutil

        shutil.rmtree(worktree, ignore_errors=True)


# --------------------------------------------------------------------------- #
#  The swarm runner
# --------------------------------------------------------------------------- #

def _default_runner(spec: VectorSpec, worktree: Path, *, harness: str, mode: str,
                    model: str | None, timeout_s: float, plan_title: str = "", total: int = 0,
                    index: int = 0) -> Any:
    from xlii.harness.delegate import run_delegate

    brief = build_vector_brief(spec, plan_title=plan_title, total=total, index=index)
    return run_delegate(
        harness,
        brief,
        mode=mode,
        model=model,
        project_root=Path(worktree),
        timeout_s=timeout_s,
    )


def harness_judge(harness: str = "cursor", *, model: str | None = None, mode: str = "ask") -> Judge:
    """The 'existing Cursor judge' as a swarm judge: diff the vector's worktree and
    ask ``harness`` whether the work satisfies its brief, parsing PASS/FAIL.

    Returned as a closure so callers can swap the judging harness (or inject a
    fake in tests). Never raises — a judge transport failure is a FAIL verdict."""
    from xlii.harness.brief import HarnessBrief
    from xlii.harness.runner import run_ask

    def _judge(spec: VectorSpec, worktree: Path, run_result: Any) -> tuple[bool, str]:
        diff, _ = git_cmd(Path(worktree), ["diff", "HEAD"])
        prompt = (
            f"You are judging one vector of a parallel build. Vector {spec.name}"
            f"{(' — ' + spec.title) if spec.title else ''}.\n\n"
            f"Its brief:\n{spec.body[:4000]}\n\n"
            f"The diff it produced:\n{(diff or '(no changes)')[:8000]}\n\n"
            "Did it satisfy the brief without reaching outside its lane? "
            "Answer with PASS or FAIL on the first line, then one sentence why."
        )
        result = run_ask(
            harness,
            HarnessBrief(kind="judge", question=prompt, project_root=Path(worktree)),
            model=model,
            mode=mode,
        )
        if result.error:
            return False, f"judge unavailable: {result.error}"
        verdict = (result.text or "").strip()
        passed = verdict.upper().lstrip().startswith("PASS")
        return passed, verdict

    return _judge


def run_build_swarm(
    plan: str | Path,
    *,
    project_root: str | Path,
    harness: str = "cursor",
    mode: str = "agent",
    model: str | None = None,
    vectors: list[str] | None = None,
    runner: Runner | None = None,
    judge: Judge | None = None,
    use_worktrees: bool = True,
    swarm_id: str | None = None,
    timeout_s: float = 600.0,
    cleanup: bool = True,
    on_progress: ProgressCallback | None = None,
) -> SwarmReport:
    """Run a build-plan doc as a swarm: one harness session per vector, each in a
    worktree, collected (and optionally judged) into a :class:`SwarmReport`.

    ``plan`` is the doc text or a path to it. ``vectors`` filters to a subset of
    vector names. ``runner`` / ``judge`` are injectable for testing; the defaults
    drive a real ``run_delegate`` in each worktree and skip judging."""
    text = _read_plan(plan)
    specs = parse_build_plan(text)
    if vectors:
        want = {v.strip().lower() for v in vectors}
        specs = [s for s in specs if s.name.lower() in want]

    report = SwarmReport(plan_title=_plan_title(text), harness=harness)
    if not specs:
        return report

    runner = runner or _default_runner
    project_root = Path(project_root)
    total = len(specs)

    repo_root: Optional[Path] = None
    base_commit: Optional[str] = None
    created: list[Path] = []
    if use_worktrees:
        from xlii.git_status import find_repo_root

        repo_root = find_repo_root(project_root) or project_root
        base_commit = _head_commit(repo_root)
        if base_commit is None:
            use_worktrees = False  # not a git repo — fall back to in-place runs
    swarm_id = swarm_id or new_swarm_id()
    report.swarm_id = swarm_id

    try:
        for index, spec in enumerate(specs, start=1):
            if on_progress:
                on_progress("start", spec.name, None)
            worktree: Path = project_root
            branch = ""
            outcome = VectorOutcome(vector=spec.name, title=spec.title)
            if use_worktrees and repo_root is not None and base_commit is not None:
                try:
                    worktree, branch = create_vector_worktree(repo_root, swarm_id, spec.name, base_commit)
                    created.append(worktree)
                except RuntimeError as e:
                    outcome.error = str(e)
                    report.outcomes.append(outcome)
                    if on_progress:
                        on_progress("done", spec.name, outcome)
                    continue
            outcome.branch = branch
            outcome.worktree = str(worktree)

            try:
                result = runner(
                    spec, worktree, harness=harness, mode=mode, model=model,
                    timeout_s=timeout_s, plan_title=report.plan_title, total=total, index=index,
                )
            except Exception as e:
                outcome.error = f"{type(e).__name__}: {e}"
                report.outcomes.append(outcome)
                if on_progress:
                    on_progress("done", spec.name, outcome)
                continue

            outcome.text = getattr(result, "text", "") or ""
            outcome.files_touched = list(getattr(result, "files_touched", []) or [])
            outcome.error = getattr(result, "error", None)

            if judge is not None and outcome.error is None:
                try:
                    passed, verdict = judge(spec, worktree, result)
                    outcome.passed, outcome.verdict = bool(passed), str(verdict)
                except Exception as e:
                    outcome.verdict = f"judge error: {type(e).__name__}: {e}"

            report.outcomes.append(outcome)
            if on_progress:
                on_progress("done", spec.name, outcome)
    finally:
        if cleanup and repo_root is not None:
            for wt in created:
                try:
                    remove_vector_worktree(repo_root, wt)
                except Exception:
                    # One worktree that won't remove must not stop cleanup of the rest.
                    pass
            git_cmd(repo_root, ["worktree", "prune"])

    return report


def _read_plan(plan: str | Path) -> str:
    """Accept a plan as raw text or a path. A short string that resolves to a file
    is read; otherwise it's treated as the plan text itself."""
    if isinstance(plan, Path):
        return plan.read_text(errors="replace")
    if isinstance(plan, str) and "\n" not in plan and len(plan) < 4096:
        p = Path(plan).expanduser()
        if p.exists() and p.is_file():
            return p.read_text(errors="replace")
    return plan


__all__ = [
    "VectorSpec",
    "VectorOutcome",
    "SwarmReport",
    "parse_build_plan",
    "build_vector_brief",
    "run_build_swarm",
    "harness_judge",
    "create_vector_worktree",
    "remove_vector_worktree",
    "new_swarm_id",
]
