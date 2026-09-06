"""Writer swarm — parallel writer-workers in git worktrees, sequential integration."""

from __future__ import annotations

import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Optional

from xlii.agent import WorkerAgent, load_prompt
from xlii.config import GlobalConfig, ProjectConfig
from xlii.loop_bundle import git_cmd
from xlii.loop_judge import JudgeProfile, run_merge_judge, run_shell_judge
from xlii.loop_verdict import Verdict
from xlii.pool import ClientPool, is_auth_failure


def worktrees_root() -> Path:
    """Base dir for swarm worktrees — outside any project_root so sync can't see them."""
    return Path.home() / ".xlii" / "worktrees"


def _project_slug(project_root: Path) -> str:
    name = project_root.name or "project"
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", name).strip("-") or "project"
    return slug


def _branch_name(loop_id: str, index: int) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._/-]+", "-", loop_id).strip("-")
    return f"swarm/{safe}/{index}"


def integration_branch_name(loop_id: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._/-]+", "-", loop_id).strip("-")
    return f"swarm/{safe}/integration"


@dataclass
class WorkerSpec:
    index: int
    task: str
    worktree: Path
    branch: str
    status: str = "pending"  # pending | done | merged | conflict | failed


@dataclass
class SwarmState:
    base: str = ""
    integration_branch: str = ""
    integration_worktree: Path = field(default_factory=Path)
    workers: list[WorkerSpec] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "base": self.base,
            "integration_branch": self.integration_branch,
            "integration_worktree": str(self.integration_worktree),
            "workers": [
                {
                    "i": w.index,
                    "worktree": str(w.worktree),
                    "branch": w.branch,
                    "status": w.status,
                }
                for w in self.workers
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SwarmState:
        workers = [
            WorkerSpec(
                index=int(w["i"]),
                task="",
                worktree=Path(w["worktree"]),
                branch=str(w["branch"]),
                status=str(w.get("status", "pending")),
            )
            for w in data.get("workers") or []
        ]
        return cls(
            base=str(data.get("base") or ""),
            integration_branch=str(data.get("integration_branch") or ""),
            integration_worktree=Path(data.get("integration_worktree") or "."),
            workers=workers,
        )


class WorktreeManager:
    """Lifecycle for writer worktrees outside project_root."""

    def __init__(self, *, repo_root: Path, loop_id: str, project_slug: str) -> None:
        self.repo_root = repo_root.resolve()
        self.loop_id = loop_id
        self.base_dir = worktrees_root() / project_slug / loop_id

    def worker_path(self, index: int) -> Path:
        return self.base_dir / f"worker-{index}"

    def integration_path(self) -> Path:
        return self.base_dir / "integration"

    def create_worker(self, index: int, base_commit: str) -> tuple[Path, str]:
        wt = self.worker_path(index)
        branch = _branch_name(self.loop_id, index)
        if wt.exists():
            self.remove(wt)
        wt.parent.mkdir(parents=True, exist_ok=True)
        _, err = git_cmd(
            self.repo_root,
            ["worktree", "add", "-B", branch, str(wt), base_commit],
        )
        if err:
            raise RuntimeError(f"worktree add worker {index}: {err}")
        return wt, branch

    def create_integration(self, base_commit: str) -> tuple[Path, str]:
        wt = self.integration_path()
        branch = integration_branch_name(self.loop_id)
        if wt.exists():
            self.remove(wt)
        wt.parent.mkdir(parents=True, exist_ok=True)
        _, err = git_cmd(
            self.repo_root,
            ["worktree", "add", "-B", branch, str(wt), base_commit],
        )
        if err:
            raise RuntimeError(f"worktree add integration: {err}")
        return wt, branch

    def remove(self, worktree: Path) -> None:
        git_cmd(self.repo_root, ["worktree", "remove", "--force", str(worktree)])
        if worktree.exists():
            shutil.rmtree(worktree, ignore_errors=True)

    def cleanup_all(self, swarm: SwarmState) -> None:
        seen: set[Path] = set()
        for w in swarm.workers:
            if w.worktree not in seen:
                seen.add(w.worktree)
                try:
                    self.remove(w.worktree)
                except Exception:
                    # One worktree that won't remove must not stop cleanup of the rest.
                    pass
        if swarm.integration_worktree and swarm.integration_worktree not in seen:
            try:
                self.remove(swarm.integration_worktree)
            except Exception:
                # Same -- a stuck integration worktree is left for manual cleanup.
                pass
        git_cmd(self.repo_root, ["worktree", "prune"])
        if self.base_dir.exists() and not any(self.base_dir.iterdir()):
            shutil.rmtree(self.base_dir, ignore_errors=True)

    def prune_dangling(self) -> None:
        git_cmd(self.repo_root, ["worktree", "prune"])


def project_for_worktree(project: ProjectConfig, worktree: Path) -> ProjectConfig:
    """Repoint project_root at a worktree — the path jail seam."""
    return replace(project, project_root=worktree.resolve())


def writer_tasks(goal: str, n: int) -> list[str]:
    """Fan-out tasks — full overlap allowed; each writer gets the goal + slice hint."""
    if n <= 1:
        return [goal]
    return [
        (
            f"{goal}\n\n"
            f"[Writer {i + 1}/{n} — parallel slice. Prefer disjoint files; "
            f"coordinate via non-overlapping modules where possible.]"
        )
        for i in range(n)
    ]


def _count_changed_files(repo_root: Path, branch: str, base: str) -> int:
    names, _ = git_cmd(repo_root, ["diff", "--name-only", base, branch])
    return len([ln for ln in (names or "").splitlines() if ln.strip()])


def _integration_order(workers: list[WorkerSpec], repo_root: Path, base: str) -> list[WorkerSpec]:
    """Fewest-files-touched first — stable integration base."""
    return sorted(
        workers,
        key=lambda w: (_count_changed_files(repo_root, w.branch, base), w.index),
    )


def _worker_commit(
    worktree: Path,
    *,
    loop_id: str,
    index: int,
    commit_mode: str,
) -> None:
    if commit_mode == "never":
        return
    status, err = git_cmd(worktree, ["status", "--porcelain"])
    if err or not (status or "").strip():
        return
    git_cmd(worktree, ["add", "-A"])
    git_cmd(worktree, ["commit", "-m", f"swarm: {loop_id} worker {index}"])


def commit_tree(worktree: Path, message: str) -> bool:
    """Stage + commit everything in a worktree. Returns True if a commit was
    made, False if the tree was already clean. The pre-land fix loop uses this to
    durably record each fix on the integration branch before the gate re-runs —
    so the gate always judges committed state and the worktree stays clean."""
    status, err = git_cmd(worktree, ["status", "--porcelain"])
    if err or not (status or "").strip():
        return False
    git_cmd(worktree, ["add", "-A"])
    git_cmd(worktree, ["commit", "-m", message])
    return True


def run_writer_worker(
    *,
    pool: ClientPool,
    project: ProjectConfig,
    cfg: GlobalConfig,
    worktree: Path,
    task: str,
    lock_tests: bool,
    yolo: bool,
    subscribed_plugins: list[str],
) -> tuple[str, float]:
    """Run one write-capable worker in its worktree. Returns (summary, cost_usd)."""
    wt_project = project_for_worktree(project, worktree)
    clients = pool.acquire()
    worker = WorkerAgent(
        clients=clients,
        project=wt_project,
        cfg=cfg,
        subscribed_plugins=subscribed_plugins,
        worker_writes=True,
        loop_lock_tests=lock_tests,
        yolo=yolo,
    )
    try:
        text, call = worker.run(task)
        pool.report_success(clients)
        return text, call.cost_usd or 0.0
    except Exception as e:
        if is_auth_failure(e):
            pool.report_auth_failure(clients)
        raise


def dispatch_writers(
    *,
    pool: ClientPool,
    project: ProjectConfig,
    cfg: GlobalConfig,
    swarm: SwarmState,
    loop_id: str,
    tasks: list[str],
    lock_tests: bool,
    yolo: bool,
    commit_mode: str,
    max_workers: int,
    subscribed_plugins: list[str],
    console: Any = None,
) -> list[str]:
    """Run N writer-workers in parallel. Returns error messages (empty = ok)."""
    errors: list[str] = []

    def _one(spec: WorkerSpec, task: str) -> None:
        if console is not None:
            console.print(f"[dim][swarm] writer {spec.index} starting[/dim]")
        run_writer_worker(
            pool=pool,
            project=project,
            cfg=cfg,
            worktree=spec.worktree,
            task=task,
            lock_tests=lock_tests,
            yolo=yolo,
            subscribed_plugins=subscribed_plugins,
        )
        _worker_commit(spec.worktree, loop_id=loop_id, index=spec.index, commit_mode=commit_mode)
        spec.status = "done"

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {
            ex.submit(_one, spec, tasks[spec.index]): spec
            for spec in swarm.workers
        }
        for fut in as_completed(futures):
            spec = futures[fut]
            try:
                fut.result()
            except Exception as e:
                spec.status = "failed"
                errors.append(f"writer {spec.index}: {type(e).__name__}: {e}")

    return errors


def _branch_integrated(integration_wt: Path, branch: str) -> bool:
    """True iff ``branch``'s tip is an ancestor of HEAD — i.e. the merge actually
    landed it. Uses the returncode directly because ``merge-base --is-ancestor``
    signals its answer purely via exit 0 (ancestor) vs 1 (not), and git_cmd collapses
    exit 1 into success — so it can't distinguish the two."""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", branch, "HEAD"],
            cwd=str(integration_wt), capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0


def _try_merge(integration_wt: Path, branch: str) -> str:
    """Attempt merge. Returns 'clean', 'conflict', or an 'error: …' message.

    A path conflict exits 1 with unmerged paths present. A non-conflict failure —
    dirty worktree (exit 128), untracked-would-be-overwritten (exit 1), not-a-commit
    (exit 128) — leaves no unmerged paths and does NOT integrate the branch. git_cmd
    hides both exit 1 and 128 as "success", so we can't trust it: instead confirm the
    branch tip actually became an ancestor of HEAD. We must NOT report a non-integrating
    merge as 'clean' — integrate_workers would mark the writer 'merged' and land a tree
    missing its work (silent data loss).
    """
    _, merge_err = git_cmd(integration_wt, ["merge", "--no-edit", branch])
    status, _ = git_cmd(integration_wt, ["diff", "--name-only", "--diff-filter=U"])
    if (status or "").strip():
        return "conflict"
    if not _branch_integrated(integration_wt, branch):
        return f"error: {merge_err or 'branch did not integrate (dirty or blocked worktree)'}"
    return "clean"


def _conflicted_files(integration_wt: Path) -> list[str]:
    status, _ = git_cmd(integration_wt, ["diff", "--name-only", "--diff-filter=U"])
    return [ln.strip() for ln in (status or "").splitlines() if ln.strip()]


def _file_at_commit(repo: Path, commit: str, relpath: str) -> str:
    out, err = git_cmd(repo, ["show", f"{commit}:{relpath}"])
    if err:
        return ""
    return out or ""


def _resolve_with_merge_agent(
    *,
    integration_wt: Path,
    relpath: str,
    base: str,
    ours_commit: str,
    theirs_branch: str,
    intent_ours: str,
    intent_theirs: str,
    pool: ClientPool,
    project: ProjectConfig,
    cfg: GlobalConfig,
) -> Optional[str]:
    """LLM merge resolver — returns resolved content or None on failure."""
    base_text = _file_at_commit(integration_wt, base, relpath)
    ours_text = _file_at_commit(integration_wt, ours_commit, relpath) if ours_commit else base_text
    theirs_text = _file_at_commit(integration_wt, theirs_branch, relpath)
    conflict_path = integration_wt / relpath
    conflict_text = conflict_path.read_text(errors="replace") if conflict_path.is_file() else ""

    brief = (
        f"File: {relpath}\n\n"
        f"Intent (already integrated):\n{intent_ours}\n\n"
        f"Intent (incoming writer):\n{intent_theirs}\n\n"
        f"--- BASE (merge ancestor) ---\n{base_text}\n\n"
        f"--- OURS (integration tip before this merge) ---\n{ours_text}\n\n"
        f"--- THEIRS (incoming branch) ---\n{theirs_text}\n\n"
        f"--- CONFLICT MARKERS ---\n{conflict_text}\n\n"
        "Output the resolved file content only."
    )
    clients = pool.acquire()
    agent = WorkerAgent(
        clients=clients,
        project=project_for_worktree(project, integration_wt),
        cfg=cfg,
        worker_writes=False,
    )
    try:
        text, _call = agent.run(brief, system_prompt_override=load_prompt("merge-agent"))
        pool.report_success(clients)
    except Exception as e:
        if is_auth_failure(e):
            pool.report_auth_failure(clients)
        return None
    resolved = text.strip()
    if not resolved or "<<<<<<<" in resolved or "=======" in resolved or ">>>>>>>" in resolved:
        return None
    return resolved


def _verify_integration_tree(
    integration_wt: Path,
    test_command: str,
) -> Verdict:
    profile = JudgeProfile(name="tests", kind="shell", command=test_command)
    return run_shell_judge(profile, cwd=integration_wt)


def integrate_workers(
    *,
    repo_root: Path,
    swarm: SwarmState,
    tasks: list[str],
    merge_mode: str,
    merge_judge_profile: Optional[JudgeProfile],
    test_command: str,
    pool: Optional[ClientPool],
    project: ProjectConfig,
    cfg: GlobalConfig,
    xli_dir: Path,
    pricing: Optional[dict],
    read_budget: int,
    final_gate: bool = True,
    console: Any = None,
) -> tuple[bool, str, Optional[str]]:
    """Sequential integration into branch I. Returns (ok, message, failed_worker_index).

    ``final_gate`` runs the cheap shell oracle over the whole integrated tree
    before returning ok. The loop disables it (passes a full judge panel via
    ``run_swarm_build``'s ``verify_tree`` instead), so the shell command isn't
    run twice."""
    integration_wt = swarm.integration_worktree
    base = swarm.base
    merged_intents: list[str] = []
    ordered = _integration_order(
        [w for w in swarm.workers if w.status not in ("merged", "failed", "pending")],
        repo_root,
        base,
    )

    for spec in ordered:
        tip_before, _ = git_cmd(integration_wt, ["rev-parse", "HEAD"])
        merge_result = _try_merge(integration_wt, spec.branch)
        if merge_result == "clean":
            spec.status = "merged"
            merged_intents.append(tasks[spec.index])
            if console is not None:
                console.print(f"[dim][swarm] merged writer {spec.index} (clean)[/dim]")
            continue

        if merge_result != "conflict":
            return False, f"merge failed for writer {spec.index}: {merge_result}", str(spec.index)

        spec.status = "conflict"
        if merge_mode != "llm" or pool is None:
            git_cmd(integration_wt, ["merge", "--abort"])
            return False, f"merge conflict with writer {spec.index} (merge mode={merge_mode})", str(spec.index)

        # LLM resolution path (W3)
        conflicted = _conflicted_files(integration_wt)
        intent_ours = "\n".join(merged_intents) or "(empty integration)"
        intent_theirs = tasks[spec.index]
        for relpath in conflicted:
            resolved = _resolve_with_merge_agent(
                integration_wt=integration_wt,
                relpath=relpath,
                base=base,
                ours_commit=tip_before.strip() if tip_before else base,
                theirs_branch=spec.branch,
                intent_ours=intent_ours,
                intent_theirs=intent_theirs,
                pool=pool,
                project=project,
                cfg=cfg,
            )
            if resolved is None:
                git_cmd(integration_wt, ["merge", "--abort"])
                return False, f"merge-agent failed on {relpath} (writer {spec.index})", str(spec.index)
            fp = integration_wt / relpath
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(resolved, encoding="utf-8")
            git_cmd(integration_wt, ["add", relpath])

        git_cmd(integration_wt, ["commit", "--no-edit"])

        if merge_judge_profile is not None:
            for relpath in conflicted:
                base_text = _file_at_commit(integration_wt, base, relpath)
                ours_text = _file_at_commit(integration_wt, tip_before.strip(), relpath) if tip_before else base_text
                theirs_text = _file_at_commit(integration_wt, spec.branch, relpath)
                resolved_text = (integration_wt / relpath).read_text(errors="replace")
                verdict = run_merge_judge(
                    merge_judge_profile,
                    xli_dir=xli_dir,
                    base=base_text,
                    ours=ours_text,
                    theirs=theirs_text,
                    resolved=resolved_text,
                    intent_ours=intent_ours,
                    intent_theirs=intent_theirs,
                    relpath=relpath,
                    pricing=pricing,
                    read_budget=read_budget,
                )
                if not verdict.passed:
                    git_cmd(integration_wt, ["reset", "--hard", tip_before.strip()])
                    return (
                        False,
                        f"merge-judge rejected resolution for {relpath} (writer {spec.index}): "
                        f"{verdict.summary}",
                        str(spec.index),
                    )

        test_verdict = _verify_integration_tree(integration_wt, test_command)
        if not test_verdict.passed:
            git_cmd(integration_wt, ["reset", "--hard", tip_before.strip()])
            return (
                False,
                f"tests failed after conflicted merge (writer {spec.index}): {test_verdict.summary}",
                str(spec.index),
            )

        spec.status = "merged"
        merged_intents.append(tasks[spec.index])
        if console is not None:
            console.print(f"[dim][swarm] merged writer {spec.index} (resolved)[/dim]")

    # Final whole-tree gate. Mid-loop verification only runs after *conflicted*
    # merges — a run of all-clean merges would otherwise reach landing having
    # never run the oracle. Verify the FULL integrated tree here so that ok=True
    # means "verified green": run_swarm_build lands on ok, and atomic landing
    # (never ship un-green code) depends on this being the authoritative gate.
    # A whole-tree failure is not attributable to one writer, so failed_worker
    # is None — no serial re-dispatch; the loop fails honestly and the
    # integration branch is kept for inspection.
    if final_gate and test_command and test_command.strip():
        final = _verify_integration_tree(integration_wt, test_command)
        if not final.passed:
            if console is not None:
                console.print("[red][swarm][/red] integration tree failed verification — not landing")
            return False, f"integration tree failed verification: {final.summary}", None

    return True, "all workers integrated", None


def land_integration(
    repo_root: Path,
    integration_branch: str,
    *,
    target_branch: Optional[str] = None,
) -> tuple[bool, str]:
    """Fast-forward target branch to integration tip. Working tree must be clean."""
    status, err = git_cmd(repo_root, ["status", "--porcelain"])
    if err:
        return False, err
    if (status or "").strip():
        return False, "working tree not clean — refusing to land"

    if target_branch is None:
        ref, _ = git_cmd(repo_root, ["symbolic-ref", "--short", "HEAD"])
        target_branch = (ref or "HEAD").strip()

    tip, err = git_cmd(repo_root, ["rev-parse", integration_branch])
    if err or not tip:
        return False, f"cannot resolve integration branch {integration_branch!r}"

    _, err = git_cmd(repo_root, ["merge", "--ff-only", integration_branch])
    if err:
        # May need to checkout target first
        git_cmd(repo_root, ["checkout", target_branch])
        _, err = git_cmd(repo_root, ["merge", "--ff-only", integration_branch])
        if err:
            return False, f"ff-merge failed: {err}"
    return True, tip.strip()


def gc_swarm_branches(repo_root: Path, loop_id: str) -> None:
    """Remove swarm/* branches after successful land."""
    safe = re.sub(r"[^a-zA-Z0-9._/-]+", "-", loop_id).strip("-")
    prefix = f"swarm/{safe}/"
    branches, _ = git_cmd(repo_root, ["branch", "--list", f"{prefix}*"])
    for ln in (branches or "").splitlines():
        name = ln.strip().lstrip("* ").strip()
        if name.startswith(prefix):
            git_cmd(repo_root, ["branch", "-D", name])


@dataclass
class SwarmBuildResult:
    ok: bool
    message: str
    builder_cost: float = 0.0
    failed_worker: Optional[int] = None


def run_swarm_build(
    *,
    repo_root: Path,
    project: ProjectConfig,
    cfg: GlobalConfig,
    pool: ClientPool,
    loop_id: str,
    goal: str,
    swarm_size: int,
    merge_mode: str,
    merge_judge_profile: Optional[JudgeProfile],
    test_command: str,
    commit_mode: str,
    lock_tests: bool,
    yolo: bool,
    xli_dir: Path,
    pricing: Optional[dict],
    read_budget: int,
    subscribed_plugins: list[str],
    console: Any = None,
    serial_redispatch: Optional[Callable[[int, Path], None]] = None,
    verify_tree: Optional[Callable[[Path], tuple[bool, str]]] = None,
    fix_tree: Optional[Callable[[Path, int], bool]] = None,
    max_fix_attempts: int = 0,
) -> tuple[SwarmBuildResult, SwarmState]:
    """Full swarm build: worktrees → writers → integrate → verify → fix → land.

    ``verify_tree(integration_worktree) -> (ok, summary)`` is the authoritative
    pre-land gate. The loop wires it to the full judge panel (shell oracle +
    LLM/cross-vendor judges + collusion) run against the integration tree, so
    nothing un-green ever lands. When omitted, integrate_workers' own shell gate
    is the only check (CLI without judges / tests).

    ``fix_tree(integration_worktree, attempt) -> applied`` (W4) is called when
    the gate fails: it patches the integration tree in place (a fix-writer +
    commit) and returns True if a retry is worth running, False to give up
    (infrastructure failure, or no progress). Up to ``max_fix_attempts`` fixes
    are tried — "walk away to green" entirely pre-land. ``max_fix_attempts=0``
    (or no ``fix_tree``) reduces to one-shot verify-then-land."""
    repo_root = repo_root.resolve()
    base_out, err = git_cmd(repo_root, ["rev-parse", "HEAD"])
    if err or not base_out:
        return SwarmBuildResult(False, f"cannot resolve HEAD: {err}"), SwarmState()
    base = base_out.strip()

    mgr = WorktreeManager(
        repo_root=repo_root,
        loop_id=loop_id,
        project_slug=_project_slug(repo_root),
    )
    mgr.prune_dangling()

    tasks = writer_tasks(goal, swarm_size)
    workers: list[WorkerSpec] = []
    for i in range(swarm_size):
        wt, branch = mgr.create_worker(i, base)
        workers.append(WorkerSpec(index=i, task=tasks[i], worktree=wt, branch=branch))

    int_wt, int_branch = mgr.create_integration(base)
    swarm = SwarmState(
        base=base,
        integration_branch=int_branch,
        integration_worktree=int_wt,
        workers=workers,
    )

    if console is not None:
        console.print(
            f"[cyan][swarm][/cyan] {swarm_size} writer(s) @ {base[:12]} · "
            f"merge={merge_mode}"
        )

    dispatch_errors = dispatch_writers(
        pool=pool,
        project=project,
        cfg=cfg,
        swarm=swarm,
        loop_id=loop_id,
        tasks=tasks,
        lock_tests=lock_tests,
        yolo=yolo,
        commit_mode=commit_mode,
        max_workers=min(swarm_size, cfg.max_parallel_workers),
        subscribed_plugins=subscribed_plugins,
        console=console,
    )
    if dispatch_errors:
        mgr.cleanup_all(swarm)
        return SwarmBuildResult(False, "; ".join(dispatch_errors)), swarm

    ok, msg, failed_idx = integrate_workers(
        repo_root=repo_root,
        swarm=swarm,
        tasks=tasks,
        merge_mode=merge_mode,
        merge_judge_profile=merge_judge_profile,
        test_command=test_command,
        pool=pool,
        project=project,
        cfg=cfg,
        xli_dir=xli_dir,
        pricing=pricing,
        read_budget=read_budget,
        final_gate=(verify_tree is None),
        console=console,
    )
    if not ok:
        if failed_idx is not None and serial_redispatch is not None:
            try:
                idx = int(failed_idx)
                if console is not None:
                    console.print(
                        f"[yellow][swarm][/yellow] re-dispatching writer {idx} serially"
                    )
                serial_redispatch(idx, swarm.integration_worktree)
                spec = next(w for w in swarm.workers if w.index == idx)
                spec.status = "done"
                ok2, msg2, _ = integrate_workers(
                    repo_root=repo_root,
                    swarm=swarm,
                    tasks=tasks,
                    merge_mode="auto",
                    merge_judge_profile=None,
                    test_command=test_command,
                    pool=pool,
                    project=project,
                    cfg=cfg,
                    xli_dir=xli_dir,
                    pricing=pricing,
                    read_budget=read_budget,
                    console=console,
                )
                if not ok2:
                    return SwarmBuildResult(False, msg2, failed_worker=idx), swarm
            except Exception as e:
                return SwarmBuildResult(False, f"{msg}; serial re-dispatch failed: {e}", failed_worker=int(failed_idx) if failed_idx else None), swarm
        else:
            return SwarmBuildResult(False, msg, failed_worker=int(failed_idx) if failed_idx else None), swarm

    # Authoritative pre-land gate: the full judge panel (shell oracle + LLM /
    # cross-vendor judges + collusion) over the WHOLE integration tree. On
    # failure, iterate (W4): fix_tree patches the integration worktree, commits,
    # and the gate re-runs — "walk away to green" entirely pre-land. Land only
    # when the panel passes; exhaust the attempts → do NOT land, keep the
    # integration branch + worktrees for inspection (a non-converged run is
    # honest, never half-applied).
    if verify_tree is not None:
        attempts = 0
        while True:
            gate_ok, gate_summary = verify_tree(swarm.integration_worktree)
            if gate_ok:
                break
            if fix_tree is None or attempts >= max_fix_attempts:
                if console is not None:
                    console.print(f"[red][swarm][/red] pre-land judges FAIL — not landing: {gate_summary}")
                suffix = f" after {attempts} fix attempt(s)" if attempts else ""
                return SwarmBuildResult(False, f"pre-land verification failed{suffix}: {gate_summary}"), swarm
            if console is not None:
                console.print(
                    f"[yellow][swarm][/yellow] pre-land gate failed — fix attempt "
                    f"{attempts + 1}/{max_fix_attempts}: {gate_summary}"
                )
            if not fix_tree(swarm.integration_worktree, attempts):
                if console is not None:
                    console.print(f"[red][swarm][/red] fix not viable — not landing: {gate_summary}")
                return SwarmBuildResult(False, f"pre-land verification failed (unfixable): {gate_summary}"), swarm
            attempts += 1

    landed, land_msg = land_integration(repo_root, int_branch)
    if not landed:
        return SwarmBuildResult(False, f"integration ok but land failed: {land_msg}"), swarm

    mgr.cleanup_all(swarm)
    gc_swarm_branches(repo_root, loop_id)

    if console is not None:
        console.print(f"[green][swarm][/green] landed @ {land_msg[:12]}")

    return SwarmBuildResult(True, msg), swarm
