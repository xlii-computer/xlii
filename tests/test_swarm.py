"""Writer swarm — worktree lifecycle, integration, and loop wiring."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from xlii.loop import LoopController, run_loop_cli
from xlii.swarm import (
    WorktreeManager,
    WorkerSpec,
    SwarmState,
    dispatch_writers,
    integrate_workers,
    land_integration,
    project_for_worktree,
    writer_tasks,
)
from xlii.tools import WRITER_REGISTRY, worker_tool_schemas
from tests.helpers import FakeConsole, make_tool_ctx


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True, capture_output=True)
    (repo / "README").write_text("base\n")
    subprocess.run(["git", "add", "README"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def test_writer_tool_schemas_include_write_tools():
    read_schemas = {s["function"]["name"] for s in worker_tool_schemas(writer=False)}
    write_schemas = {s["function"]["name"] for s in worker_tool_schemas(writer=True)}
    assert "write_file" not in read_schemas
    assert "edit_file" not in read_schemas
    assert "write_file" in write_schemas
    assert "edit_file" in write_schemas


def test_writer_registry_has_write_tools():
    assert "write_file" in WRITER_REGISTRY
    assert "edit_file" in WRITER_REGISTRY


def test_writer_worker_bash_allows_modifies_project(tmp_path):
    from xlii import tools

    (tmp_path / "f.txt").write_text("a")
    ctx = make_tool_ctx(tmp_path, is_worker=True)
    ctx.worker_writes = True
    r = tools.t_bash(ctx, {"command": "echo b >> f.txt", "intent": "modifies-project"})
    assert not r.is_error


def test_writer_worker_bash_blocks_network_without_yolo(tmp_path):
    from xlii import tools

    ctx = make_tool_ctx(tmp_path, is_worker=True)
    ctx.worker_writes = True
    r = tools.t_bash(ctx, {"command": "curl example.com", "intent": "network"})
    assert r.is_error and "writer-workers" in r.content


def test_worktree_create_and_remove(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    wt_base = tmp_path / "wt_store"
    monkeypatch.setattr("xlii.swarm.worktrees_root", lambda: wt_base)

    mgr = WorktreeManager(repo_root=repo, loop_id="loop1", project_slug="repo")
    wt, branch = mgr.create_worker(0, "HEAD")
    assert wt.is_dir()
    assert (wt / "README").read_text() == "base\n"
    mgr.remove(wt)
    assert not wt.exists()


def test_clean_merge_integration(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    wt_base = tmp_path / "wt_store"
    monkeypatch.setattr("xlii.swarm.worktrees_root", lambda: wt_base)

    mgr = WorktreeManager(repo_root=repo, loop_id="loop2", project_slug="repo")
    base_out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    w0, b0 = mgr.create_worker(0, base_out)
    (w0 / "a.py").write_text("a = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=w0, check=True)
    subprocess.run(["git", "commit", "-m", "w0"], cwd=w0, check=True, capture_output=True)

    w1, b1 = mgr.create_worker(1, base_out)
    (w1 / "b.py").write_text("b = 2\n")
    subprocess.run(["git", "add", "b.py"], cwd=w1, check=True)
    subprocess.run(["git", "commit", "-m", "w1"], cwd=w1, check=True, capture_output=True)

    int_wt, int_branch = mgr.create_integration(base_out)

    swarm = SwarmState(
        base=base_out,
        integration_branch=int_branch,
        integration_worktree=int_wt,
        workers=[
            WorkerSpec(0, "", w0, b0, status="done"),
            WorkerSpec(1, "", w1, b1, status="done"),
        ],
    )
    ok, msg, _ = integrate_workers(
        repo_root=repo,
        swarm=swarm,
        tasks=["t0", "t1"],
        merge_mode="auto",
        merge_judge_profile=None,
        test_command="true",
        pool=None,
        project=MagicMock(project_root=repo),
        cfg=MagicMock(),
        xli_dir=tmp_path / ".xlii",
        pricing=None,
        read_budget=0,
    )
    assert ok, msg
    assert (int_wt / "a.py").is_file()
    assert (int_wt / "b.py").is_file()

    landed, _ = land_integration(repo, int_branch)
    assert landed
    assert (repo / "a.py").is_file()
    assert (repo / "b.py").is_file()


def test_clean_merge_blocked_by_failing_final_gate(tmp_path, monkeypatch):
    """All-clean merges must still clear the whole-tree oracle before ok=True.
    A red tree must NOT report success, so run_swarm_build never lands it —
    the working tree stays pristine (atomic landing)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    wt_base = tmp_path / "wt_store"
    monkeypatch.setattr("xlii.swarm.worktrees_root", lambda: wt_base)

    mgr = WorktreeManager(repo_root=repo, loop_id="loop3", project_slug="repo")
    base_out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    w0, b0 = mgr.create_worker(0, base_out)
    (w0 / "a.py").write_text("a = 1\n")
    subprocess.run(["git", "add", "a.py"], cwd=w0, check=True)
    subprocess.run(["git", "commit", "-m", "w0"], cwd=w0, check=True, capture_output=True)

    int_wt, int_branch = mgr.create_integration(base_out)
    from xlii.swarm import SwarmState, WorkerSpec

    swarm = SwarmState(
        base=base_out,
        integration_branch=int_branch,
        integration_worktree=int_wt,
        workers=[WorkerSpec(0, "", w0, b0, status="done")],
    )
    ok, msg, failed = integrate_workers(
        repo_root=repo,
        swarm=swarm,
        tasks=["t0"],
        merge_mode="auto",
        merge_judge_profile=None,
        test_command="false",  # red tree — the whole-tree gate must catch it
        pool=None,
        project=MagicMock(project_root=repo),
        cfg=MagicMock(),
        xli_dir=tmp_path / ".xlii",
        pricing=None,
        read_budget=0,
    )
    assert not ok
    assert "verification" in msg
    assert failed is None  # whole-tree failure, not attributable to one writer
    # nothing landed: land was never reached, real working tree is untouched
    assert not (repo / "a.py").exists()


def test_conflict_fail_closed(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    wt_base = tmp_path / "wt_store"
    monkeypatch.setattr("xlii.swarm.worktrees_root", lambda: wt_base)

    mgr = WorktreeManager(repo_root=repo, loop_id="loop3", project_slug="repo")
    base_out = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    w0, b0 = mgr.create_worker(0, base_out)
    (w0 / "shared.py").write_text("from w0\n")
    subprocess.run(["git", "add", "shared.py"], cwd=w0, check=True)
    subprocess.run(["git", "commit", "-m", "w0"], cwd=w0, check=True, capture_output=True)

    w1, b1 = mgr.create_worker(1, base_out)
    (w1 / "shared.py").write_text("from w1\n")
    subprocess.run(["git", "add", "shared.py"], cwd=w1, check=True)
    subprocess.run(["git", "commit", "-m", "w1"], cwd=w1, check=True, capture_output=True)

    int_wt, int_branch = mgr.create_integration(base_out)
    from xlii.swarm import SwarmState, WorkerSpec

    swarm = SwarmState(
        base=base_out,
        integration_branch=int_branch,
        integration_worktree=int_wt,
        workers=[
            WorkerSpec(0, "", w0, b0, status="done"),
            WorkerSpec(1, "", w1, b1, status="done"),
        ],
    )
    ok, msg, failed = integrate_workers(
        repo_root=repo,
        swarm=swarm,
        tasks=["t0", "t1"],
        merge_mode="auto",
        merge_judge_profile=None,
        test_command="true",
        pool=None,
        project=MagicMock(project_root=repo),
        cfg=MagicMock(),
        xli_dir=tmp_path / ".xlii",
        pricing=None,
        read_budget=0,
    )
    assert not ok
    assert "conflict" in msg
    assert failed is not None


def test_writer_tasks_fan_out():
    tasks = writer_tasks("goal", 3)
    assert len(tasks) == 3
    assert all("goal" in t for t in tasks)


def test_project_for_worktree_repoints_jail(tmp_path):
    from xlii.config import ProjectConfig

    main = tmp_path / "main"
    wt = tmp_path / "wt"
    main.mkdir()
    wt.mkdir()
    proj = ProjectConfig(
        project_root=main,
        name="p",
        collection_id="c",
        created_at="now",
    )
    repointed = project_for_worktree(proj, wt)
    assert repointed.project_root == wt.resolve()


def test_dispatch_writers_uses_explicit_loop_id(monkeypatch, tmp_path):
    captured: dict[str, str] = {}
    monkeypatch.setattr("xlii.swarm.run_writer_worker", lambda **_kw: ("ok", 0.0))

    def _capture_commit(_worktree, *, loop_id, index, commit_mode):
        captured["loop_id"] = loop_id
        captured["index"] = str(index)
        captured["commit_mode"] = commit_mode

    monkeypatch.setattr("xlii.swarm._worker_commit", _capture_commit)

    swarm = SwarmState(
        base="b",
        integration_branch="swarm/loop/with/slash/integration",
        integration_worktree=tmp_path,
        workers=[
            WorkerSpec(index=0, task="t0", worktree=tmp_path, branch="swarm/loop/with/slash/0"),
        ],
    )
    errors = dispatch_writers(
        pool=MagicMock(),
        project=MagicMock(),
        cfg=MagicMock(),
        swarm=swarm,
        loop_id="loop/with/slash",
        tasks=["task"],
        lock_tests=False,
        yolo=False,
        commit_mode="each",
        max_workers=1,
        subscribed_plugins=[],
    )

    assert errors == []
    assert captured["loop_id"] == "loop/with/slash"
    assert captured["index"] == "0"
    assert captured["commit_mode"] == "each"


def test_loop_swarm_size_in_state(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    ctrl = LoopController.start(
        xli_dir=xli,
        goal="g",
        judges=["tests"],
        max_cycles=2,
        test_command="true",
        swarm_size=4,
        merge_mode="auto",
    )
    assert ctrl.state.swarm_size == 4
    assert ctrl.state.merge_mode == "auto"


def test_run_loop_cli_swarm_skips_orchestrator_on_cycle1(tmp_path, monkeypatch):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    _git_init(tmp_path)

    ctrl = LoopController.start(
        xli_dir=xli,
        goal="add feature",
        judges=["tests"],
        max_cycles=2,
        test_command="true",
        swarm_size=2,
    )
    run_turn = MagicMock(return_value=("", set(), MagicMock(total_cost=0.0)))
    agent = MagicMock()
    agent.cfg.max_parallel_workers = 4
    agent.cfg.pricing = {}
    agent.pool = MagicMock()
    agent.project = MagicMock(project_root=tmp_path, xli_dir=xli)
    agent.attached_refs = []
    agent.session.yolo = True

    swarm_ok = MagicMock(return_value=(MagicMock(ok=True, message="ok", builder_cost=0.1), MagicMock(to_dict=lambda: {})))

    with patch("xlii.swarm.run_swarm_build", swarm_ok):
        outcome = run_loop_cli(
            controller=ctrl,
            project_root=tmp_path,
            run_turn=run_turn,
            console=FakeConsole(),
            agent=agent,
        )
    assert outcome == "LOOP_PASS"
    swarm_ok.assert_called_once()
    run_turn.assert_not_called()


def _make_project(repo):
    from xlii.config import ProjectConfig

    return ProjectConfig(project_root=repo, name="p", collection_id="c", created_at="now")


def _fake_writer_factory(filename="feat.py"):
    def fake_writer(*, worktree, task, **kw):
        (worktree / filename).write_text("x = 1\n")
        return ("wrote " + filename, 0.0)

    return fake_writer


def test_pre_land_gate_blocks_land_on_judge_fail(tmp_path, monkeypatch):
    """A failing pre-land panel (verify_tree) stops the land even when writers
    succeeded and merges were clean — nothing un-green reaches the working tree,
    and the integration branch is kept for inspection."""
    import xlii.swarm as swarm_mod

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    monkeypatch.setattr("xlii.swarm.worktrees_root", lambda: tmp_path / "wt_store")
    monkeypatch.setattr("xlii.swarm.run_writer_worker", _fake_writer_factory())

    cfg = MagicMock()
    cfg.max_parallel_workers = 4
    calls = {"n": 0}

    def gate(_tree_root):
        calls["n"] += 1
        return False, "merge-judge: dropped a side"

    result, _state = swarm_mod.run_swarm_build(
        repo_root=repo,
        project=_make_project(repo),
        cfg=cfg,
        pool=MagicMock(),
        loop_id="gate1",
        goal="add feature",
        swarm_size=1,
        merge_mode="auto",
        merge_judge_profile=None,
        test_command="true",
        commit_mode="each",
        lock_tests=False,
        yolo=False,
        xli_dir=tmp_path / ".xlii",
        pricing=None,
        read_budget=0,
        subscribed_plugins=[],
        verify_tree=gate,
    )

    assert not result.ok
    assert "pre-land verification failed" in result.message
    assert calls["n"] == 1  # the panel actually ran
    assert not (repo / "feat.py").exists()  # nothing landed — working tree pristine
    branches, _ = swarm_mod.git_cmd(repo, ["branch", "--list", "swarm/gate1/integration"])
    assert "swarm/gate1/integration" in branches  # kept for inspection


def test_pre_land_gate_passes_and_lands(tmp_path, monkeypatch):
    """When the pre-land panel passes, the verified integration tree lands."""
    import xlii.swarm as swarm_mod

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    monkeypatch.setattr("xlii.swarm.worktrees_root", lambda: tmp_path / "wt_store")
    monkeypatch.setattr("xlii.swarm.run_writer_worker", _fake_writer_factory())

    cfg = MagicMock()
    cfg.max_parallel_workers = 4

    result, _state = swarm_mod.run_swarm_build(
        repo_root=repo,
        project=_make_project(repo),
        cfg=cfg,
        pool=MagicMock(),
        loop_id="gate2",
        goal="add feature",
        swarm_size=1,
        merge_mode="auto",
        merge_judge_profile=None,
        test_command="true",
        commit_mode="each",
        lock_tests=False,
        yolo=False,
        xli_dir=tmp_path / ".xlii",
        pricing=None,
        read_budget=0,
        subscribed_plugins=[],
        verify_tree=lambda _tree: (True, "all judges passed"),
    )

    assert result.ok, result.message
    assert (repo / "feat.py").exists()  # the verified tree landed


def _swarm_build_with(tmp_path, monkeypatch, *, loop_id, verify_tree, fix_tree, max_fix_attempts):
    """Drive a real run_swarm_build with a stubbed (no-LLM) writer."""
    import xlii.swarm as swarm_mod

    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    monkeypatch.setattr("xlii.swarm.worktrees_root", lambda: tmp_path / "wt_store")
    monkeypatch.setattr("xlii.swarm.run_writer_worker", _fake_writer_factory())

    cfg = MagicMock()
    cfg.max_parallel_workers = 4
    result, _state = swarm_mod.run_swarm_build(
        repo_root=repo,
        project=_make_project(repo),
        cfg=cfg,
        pool=MagicMock(),
        loop_id=loop_id,
        goal="add feature",
        swarm_size=1,
        merge_mode="auto",
        merge_judge_profile=None,
        test_command="true",
        commit_mode="each",
        lock_tests=False,
        yolo=False,
        xli_dir=tmp_path / ".xlii",
        pricing=None,
        read_budget=0,
        subscribed_plugins=[],
        verify_tree=verify_tree,
        fix_tree=fix_tree,
        max_fix_attempts=max_fix_attempts,
    )
    return repo, result


def test_pre_land_fix_loop_converges_and_lands(tmp_path, monkeypatch):
    """Gate red once, fix, gate green → the fixed tree lands after one attempt."""
    seq = {"i": 0}

    def gate(_wt):
        seq["i"] += 1
        return (seq["i"] >= 2), ("ok" if seq["i"] >= 2 else "judge: red")

    fix_calls = {"n": 0}

    def fix(_wt, _attempt):
        fix_calls["n"] += 1
        return True

    repo, result = _swarm_build_with(
        tmp_path, monkeypatch, loop_id="fix1",
        verify_tree=gate, fix_tree=fix, max_fix_attempts=3,
    )
    assert result.ok, result.message
    assert fix_calls["n"] == 1
    assert (repo / "feat.py").exists()  # converged → landed


def test_pre_land_fix_loop_exhausts_attempts(tmp_path, monkeypatch):
    """Gate always red → exactly max_fix_attempts fixes, then give up (no land)."""
    fix_calls = {"n": 0}

    def fix(_wt, _attempt):
        fix_calls["n"] += 1
        return True

    repo, result = _swarm_build_with(
        tmp_path, monkeypatch, loop_id="fix2",
        verify_tree=lambda _wt: (False, "judge: red"), fix_tree=fix, max_fix_attempts=2,
    )
    assert not result.ok
    assert "after 2 fix attempt(s)" in result.message
    assert fix_calls["n"] == 2
    assert not (repo / "feat.py").exists()  # never landed
    import xlii.swarm as swarm_mod
    branches, _ = swarm_mod.git_cmd(repo, ["branch", "--list", "swarm/fix2/integration"])
    assert "swarm/fix2/integration" in branches  # kept for inspection


def test_pre_land_fix_gives_up_when_unfixable(tmp_path, monkeypatch):
    """fix_tree returning False (infra failure / no progress) stops immediately."""
    fix_calls = {"n": 0}

    def fix(_wt, _attempt):
        fix_calls["n"] += 1
        return False

    repo, result = _swarm_build_with(
        tmp_path, monkeypatch, loop_id="fix3",
        verify_tree=lambda _wt: (False, "judge: red"), fix_tree=fix, max_fix_attempts=3,
    )
    assert not result.ok
    assert "unfixable" in result.message
    assert fix_calls["n"] == 1  # gave up after one non-viable attempt
    assert not (repo / "feat.py").exists()
