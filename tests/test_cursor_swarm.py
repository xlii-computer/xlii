"""Vector C — Tier 4: the build-plan swarm (harness/swarm.py).

Parsing is pure; the per-vector runner and judge are injected, so the
orchestration is exercised without a harness binary or network. One test drives
the real ``git worktree`` path against a throwaway repo to prove isolation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

from xlii.harness import swarm
from xlii.harness.swarm import (
    SwarmReport,
    VectorSpec,
    build_vector_brief,
    parse_build_plan,
    run_build_swarm,
)

SAMPLE_PLAN = """# Demo Build Plan

Intro prose that is not a vector.

## Vector A1 — Input Surface
Make the input nice.
**Owns:** `tui_textual.py`, `tui/status.py`

## Vector B — Capture
Capture harness output.
**Owns:** `shell_toolkit.py`

---

## Not A Vector
ignored
"""


def _fake_runner(record=None):
    def runner(spec, worktree, *, harness, mode, model, timeout_s, plan_title="", total=0, index=0):
        if record is not None:
            record.append((spec.name, str(worktree), harness, mode))
        return SimpleNamespace(text=f"did {spec.name}", files_touched=[f"{spec.name}.py"], error=None)
    return runner


# --------------------------------------------------------------------------- #
#  Parsing
# --------------------------------------------------------------------------- #

def test_parse_build_plan_basic():
    specs = parse_build_plan(SAMPLE_PLAN)
    assert [s.name for s in specs] == ["A1", "B"]
    assert specs[0].title == "Input Surface"
    assert "Make the input nice." in specs[0].body
    assert "tui_textual.py" in specs[0].owns
    assert "tui/status.py" in specs[0].owns
    assert specs[1].owns == ["shell_toolkit.py"]


# In-test stand-in for a six-vector parallel-build plan. Lab design docs are
# not in the public tree; this fixture keeps the parser contract executable.
SIX_VECTOR_PLAN = """# Parallel Build Plan

## Vector A1 — Context Tabs
**Owns:** `tui/tabs.py`

## Vector A2 — Kinds
**Owns:** `tui/kinds.py`

## Vector B — Capture
**Owns:** `shell_toolkit.py`

## Vector C — Harness Modes
Drive one harness session per vector.
**Owns:** `harness/swarm.py`, `harness/delegate.py`

## Vector D — Judge
**Owns:** `harness/brief.py`

## Vector F — Cleanup
**Owns:** `swarm.py`
"""


def test_parse_multi_vector_plan_is_executable():
    specs = parse_build_plan(SIX_VECTOR_PLAN)
    assert [s.name for s in specs] == ["A1", "A2", "B", "C", "D", "F"]
    by_name = {s.name: s for s in specs}
    assert "Harness Modes" in by_name["C"].title
    assert by_name["C"].owns


def test_build_vector_brief_wraps_contract():
    spec = VectorSpec(name="C", title="Harness Modes", body="Do the harness work.")
    brief = build_vector_brief(spec, plan_title="Demo", total=6, index=4)
    assert "Vector C" in brief
    assert "Own ONLY the files" in brief
    assert "Do the harness work." in brief
    assert "Demo" in brief


# --------------------------------------------------------------------------- #
#  Orchestration (injected runner / judge, no git)
# --------------------------------------------------------------------------- #

def test_run_swarm_injected_runner(tmp_path):
    record: list = []
    report = run_build_swarm(
        SAMPLE_PLAN, project_root=tmp_path, harness="cursor",
        runner=_fake_runner(record), use_worktrees=False,
    )
    assert isinstance(report, SwarmReport)
    assert report.plan_title == "Demo Build Plan"
    assert [o.vector for o in report.outcomes] == ["A1", "B"]
    assert report.outcomes[0].text == "did A1"
    assert report.outcomes[0].files_touched == ["A1.py"]
    assert report.ok is True
    # one runner call per vector, each pointed at the project root (no worktrees)
    assert [r[0] for r in record] == ["A1", "B"]
    assert all(r[1] == str(tmp_path) for r in record)


def test_run_swarm_vector_filter(tmp_path):
    report = run_build_swarm(
        SAMPLE_PLAN, project_root=tmp_path, runner=_fake_runner(),
        use_worktrees=False, vectors=["b"],
    )
    assert [o.vector for o in report.outcomes] == ["B"]


def test_run_swarm_with_judge(tmp_path):
    def judge(spec, worktree, result):
        return (spec.name == "A1", f"verdict for {spec.name}")

    report = run_build_swarm(
        SAMPLE_PLAN, project_root=tmp_path, runner=_fake_runner(),
        use_worktrees=False, judge=judge,
    )
    a1, b = report.outcomes
    assert a1.passed is True and a1.verdict == "verdict for A1"
    assert b.passed is False
    assert report.passed is False  # B failed the judge


def test_run_swarm_runner_error_is_captured(tmp_path):
    def boom(spec, worktree, **kw):
        if spec.name == "B":
            raise RuntimeError("kaboom")
        return SimpleNamespace(text="ok", files_touched=[], error=None)

    report = run_build_swarm(SAMPLE_PLAN, project_root=tmp_path, runner=boom, use_worktrees=False)
    assert report.outcomes[0].error is None
    assert "kaboom" in report.outcomes[1].error
    assert report.ok is False


def test_run_swarm_no_vectors_returns_empty(tmp_path):
    report = run_build_swarm("no vectors here", project_root=tmp_path, use_worktrees=False)
    assert report.outcomes == []


def test_read_plan_accepts_path_and_text(tmp_path):
    p = tmp_path / "plan.md"
    p.write_text(SAMPLE_PLAN)
    assert swarm._read_plan(p).startswith("# Demo Build Plan")
    assert swarm._read_plan(str(p)).startswith("# Demo Build Plan")
    assert swarm._read_plan("## Vector X — inline") == "## Vector X — inline"


# --------------------------------------------------------------------------- #
#  The real git-worktree path
# --------------------------------------------------------------------------- #

def _init_repo(root: Path) -> None:
    env_flags = ["-c", "user.email=t@t.t", "-c", "user.name=t"]
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", *env_flags, "commit", "--allow-empty", "-q", "-m", "base"], cwd=root, check=True)


def test_run_swarm_real_worktrees(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    seen: list = []

    def runner(spec, worktree, *, harness, mode, model, timeout_s, **kw):
        wt = Path(worktree)
        # Prove the worktree is a real, isolated checkout we can write into.
        assert wt.exists() and wt != repo
        (wt / f"{spec.name}.txt").write_text("work")
        seen.append((spec.name, wt))
        return SimpleNamespace(text=f"did {spec.name}", files_touched=[f"{spec.name}.txt"], error=None)

    report = run_build_swarm(
        SAMPLE_PLAN, project_root=repo, harness="cursor",
        runner=runner, use_worktrees=True, swarm_id="t1", cleanup=True,
    )
    assert [o.vector for o in report.outcomes] == ["A1", "B"]
    assert report.outcomes[0].branch == "swarm/t1/A1"
    assert report.outcomes[1].branch == "swarm/t1/B"
    # each vector got its own worktree and they differed
    assert len({wt for _, wt in seen}) == 2
    # cleanup removed the worktrees
    for _, wt in seen:
        assert not wt.exists()
    # branches remain for inspection
    branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
    assert "swarm/t1/A1" in branches


def test_run_swarm_keep_worktrees(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    kept: list = []

    def runner(spec, worktree, **kw):
        kept.append(Path(worktree))
        return SimpleNamespace(text="ok", files_touched=[], error=None)

    run_build_swarm(
        SAMPLE_PLAN, project_root=repo, runner=runner,
        use_worktrees=True, swarm_id="keep1", cleanup=False,
    )
    assert all(wt.exists() for wt in kept)  # left in place for inspection
    # clean them up so the test leaves no worktrees behind
    from xlii.harness.swarm import remove_vector_worktree

    for wt in kept:
        remove_vector_worktree(repo, wt)
