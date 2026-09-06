"""Autonomous loop controller — macro-loop over full turns."""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from xlii.atomicio import write_text_atomic
from xlii.loop_bundle import assemble_bundle, assemble_peer_bundle, git_cmd, new_loop_id
from xlii.loop_collusion import (
    detect_weakening,
    is_test_path,
    snapshot_test_files,
    write_collusion_alarm,
)
from xlii.loop_judge import JudgeProfile, resolve_judges, run_llm_judge, run_shell_judge
from xlii.ci_judge import MAX_GRACE_S, current_branch, run_ci_judge
from xlii.loop_verdict import Verdict

ACTIVE_FILE = "loop-active.json"
PLAN_LAST_FILE = "plan-last.md"
TERMINAL_STATUSES = frozenset({"done", "failed", "capped", "cancelled"})


@dataclass
class LoopJudgeContext:
    """Runtime context for LLM judges (same-vendor / cross-vendor)."""

    agent: Any
    console: Any = None


@dataclass
class LoopAdvanceResult:
    """Result of advancing the loop after a build/fix turn or test phase."""

    status: str
    next_prompt: Optional[str] = None
    message: Optional[str] = None
    verdict: Optional[Verdict] = None


@dataclass
class LoopState:
    version: int = 1
    loop_id: str = ""
    goal: str = ""
    success_criteria: list[str] = field(default_factory=list)
    judges: list[str] = field(default_factory=lambda: ["tests"])
    max_cycles: int = 5
    cycle: int = 1
    phase: str = "build"
    status: str = "active"
    test_command: str = "pytest -q"
    commit_mode: str = "never"
    push_mode: str = "never"
    pushed_for_ci: bool = False
    read_budget: int = 3
    budget_usd: Optional[float] = None
    swarm_size: int = 1
    merge_mode: str = "auto"  # auto | llm
    merge_judge: str = "merge"
    swarm: dict[str, Any] = field(default_factory=dict)
    cost: dict[str, float] = field(default_factory=lambda: {"builder_usd": 0.0, "judges_usd": 0.0})
    lock_tests: bool = False
    test_files_hash_by_cycle: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    finding_sigs_seen: list[str] = field(default_factory=list)
    last_verdicts: list[dict[str, Any]] = field(default_factory=list)
    pinned_at: str = ""
    # Git commit the loop started from. The judge diffs against this so it sees
    # ALL of the builder's work — including changes the builder committed and
    # brand-new files — not just `git diff HEAD` (which misses both). Empty for
    # non-git projects or pre-existing loops; _collect_changes falls back to HEAD.
    base_commit: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopState:
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})


class LoopController:
    """Owns loop state, persistence, and the build→test→verify→fix cycle."""

    def __init__(self, state: LoopState, xli_dir: Path, profiles: list[JudgeProfile]) -> None:
        self.state = state
        self.xli_dir = xli_dir
        self.profiles = profiles

    @property
    def is_terminal(self) -> bool:
        return self.state.status in TERMINAL_STATUSES

    @property
    def is_active(self) -> bool:
        return self.state.status == "active" and not self.is_terminal

    @property
    def has_llm_judges(self) -> bool:
        return any(p.kind in ("same_vendor", "cross_vendor") for p in self.profiles)

    def has_cross_vendor_judges(self) -> bool:
        return any(p.kind == "cross_vendor" for p in self.profiles)

    @classmethod
    def load(cls, xli_dir: Path, config_judges: dict[str, Any] | None = None) -> Optional[LoopController]:
        path = xli_dir / ACTIVE_FILE
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            state = LoopState.from_dict(data)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return None
        if state.status in TERMINAL_STATUSES | {"cancelled"}:
            return None
        try:
            profiles = resolve_judges(state.judges, config_judges)
        except ValueError:
            return None
        return cls(state, xli_dir, profiles)

    @staticmethod
    def load_plan_goal(xli_dir: Path) -> str:
        """Read goal text from the last approved plan (L3)."""
        path = xli_dir / PLAN_LAST_FILE
        if not path.exists():
            raise ValueError(
                f"no {PLAN_LAST_FILE} — run /plan then /execute first, or pass an explicit goal"
            )
        text = path.read_text(errors="replace").strip()
        if not text:
            raise ValueError(f"{PLAN_LAST_FILE} is empty")
        return text

    def save(self) -> None:
        write_text_atomic(self.xli_dir / ACTIVE_FILE, json.dumps(self.state.to_dict(), indent=2) + "\n")

    def clear(self) -> None:
        path = self.xli_dir / ACTIVE_FILE
        try:
            path.unlink()
        except OSError:
            # No active-loop file to remove is exactly the state clear() wants.
            pass

    @classmethod
    def start(
        cls,
        *,
        xli_dir: Path,
        goal: str,
        judges: list[str],
        max_cycles: int,
        test_command: str,
        success_criteria: Optional[list[str]] = None,
        budget_usd: Optional[float] = None,
        commit_mode: str = "never",
        push_mode: str = "never",
        read_budget: int = 3,
        config_judges: dict[str, Any] | None = None,
        swarm_size: int = 1,
        merge_mode: str = "auto",
        merge_judge: str = "merge",
        project_root: Optional[Path] = None,
    ) -> LoopController:
        profiles = resolve_judges(judges, config_judges)
        # Pin the baseline commit now (before any building), so the judge can diff
        # the cumulative change-set against it regardless of whether the builder
        # commits its work. Best-effort: non-git roots leave it empty.
        base_commit = ""
        if project_root is not None:
            sha, err = git_cmd(project_root, ["rev-parse", "HEAD"])
            if not err and sha.strip():
                base_commit = sha.strip()
        if commit_mode not in ("never", "each", "final"):
            raise ValueError(f"invalid commit_mode: {commit_mode!r} (use never, each, or final)")
        if push_mode not in ("never", "each", "final"):
            raise ValueError(f"invalid push_mode: {push_mode!r} (use never, each, or final)")
        if push_mode != "never" and commit_mode == "never":
            raise ValueError(
                "push_mode requires commit_mode each or final — "
                "cannot auto-push without committing local fixes first"
            )
        if merge_mode not in ("auto", "llm"):
            raise ValueError(f"invalid merge_mode: {merge_mode!r} (use auto or llm)")
        if swarm_size < 1:
            raise ValueError("swarm_size must be >= 1")

        lid = new_loop_id() + "-" + uuid.uuid4().hex[:4]
        state = LoopState(
            loop_id=lid,
            goal=goal.strip(),
            success_criteria=list(success_criteria or []),
            judges=list(judges),
            max_cycles=max(1, max_cycles),
            cycle=1,
            phase="build",
            status="active",
            test_command=test_command,
            commit_mode=commit_mode,
            push_mode=push_mode,
            read_budget=max(0, read_budget),
            budget_usd=budget_usd,
            swarm_size=max(1, swarm_size),
            merge_mode=merge_mode,
            merge_judge=merge_judge,
            lock_tests=any(p.kind == "cross_vendor" for p in profiles),
            test_files_hash_by_cycle={},
            pinned_at=lid.split("-")[0] if "-" in lid else lid,
            base_commit=base_commit,
        )
        ctrl = cls(state, xli_dir, profiles)
        ctrl.save()
        return ctrl

    def cancel(self) -> None:
        self.state.status = "cancelled"
        self.clear()

    def pause(self) -> None:
        if self.is_active:
            self.state.status = "paused"
            self.save()

    def resume(self) -> None:
        if self.state.status in ("paused", "interrupted"):
            self.state.status = "active"
            self.state.phase = "build"
            self.save()

    def mark_interrupted(self) -> None:
        if self.is_active:
            self.state.status = "interrupted"
            self.save()

    def initial_build_prompt(self) -> str:
        s = self.state
        criteria = ""
        if s.success_criteria:
            criteria = "\nSuccess criteria:\n" + "\n".join(f"- {c}" for c in s.success_criteria)
        judge_note = ""
        if self.has_llm_judges:
            llm_names = [p.name for p in self.profiles if p.kind in ("same_vendor", "cross_vendor")]
            if llm_names:
                judge_note = f"\nAfter tests pass, judges will run: {', '.join(llm_names)}."
            if self.state.lock_tests:
                judge_note += "\nTest files are locked (cross-vendor judges) — do not weaken tests."
        return (
            f"{s.goal}\n\n"
            f"[autonomous loop · cycle {s.cycle}/{s.max_cycles}]\n"
            f"Test command: {s.test_command}\n"
            f"Work until tests pass. The loop will re-run tests and judges automatically."
            f"{criteria}{judge_note}"
        )

    def resume_build_prompt(self) -> str:
        return self.initial_build_prompt()

    def _shell_profiles(self) -> list[JudgeProfile]:
        return [p for p in self.profiles if p.kind == "shell"]

    def _llm_profiles(self) -> list[JudgeProfile]:
        llm = [p for p in self.profiles if p.kind in ("same_vendor", "cross_vendor")]
        # Run the most independent judge first. The cycle routes back on the FIRST
        # failing judge (short-circuit, advance_after_build), so without this a weak
        # same-vendor judge — a model grading its own output — could veto the loop
        # before the authoritative cross-vendor judge is ever consulted. sorted() is
        # stable, so the user's order is preserved within each tier.
        return sorted(llm, key=lambda p: 0 if p.kind == "cross_vendor" else 1)

    def _ci_profiles(self) -> list[JudgeProfile]:
        return [p for p in self.profiles if p.kind == "ci"]

    def _should_push_before_ci(self) -> bool:
        mode = self.state.push_mode
        if mode == "never":
            return False
        if mode == "each":
            return True
        if mode == "final":
            # `final` pushes exactly once by design (per-cycle remote CI is `each`);
            # pushed_for_ci is intentionally never re-armed. KNOWN FOOTGUN: pairing a CI
            # judge with `final` means routed-back cycles poll a stale remote commit, so
            # the CI verdict can never reflect later local fixes and the no-progress guard
            # may cap the loop. A future guard should skip/neutralize the CI judge on
            # cycles where nothing new was pushed under `final` (tracked separately).
            if self.state.pushed_for_ci:
                return False
            self.state.pushed_for_ci = True
            self.save()
            return True
        return False

    def _judge_budget_exceeded(self) -> bool:
        if self.state.budget_usd is None:
            return False
        return self.state.cost.get("judges_usd", 0.0) >= self.state.budget_usd

    def _collect_test_files(self, project_root: Path) -> dict[str, str]:
        """Read test file contents for collusion tracking (changed + prior paths)."""
        paths: set[str] = set()
        for per_cycle in self.state.test_files_hash_by_cycle.values():
            paths.update(per_cycle.keys())
        names, _ = git_cmd(project_root, ["diff", "HEAD", "--name-only"])
        for rel in (names or "").splitlines():
            rel = rel.strip()
            if rel and is_test_path(rel):
                paths.add(rel)

        tests_root = project_root / "tests"
        if tests_root.is_dir():
            for fp in tests_root.rglob("*.py"):
                rel = fp.relative_to(project_root).as_posix()
                if is_test_path(rel):
                    paths.add(rel)

        out: dict[str, str] = {}
        for rel in sorted(paths):
            fp = project_root / rel
            if fp.is_file():
                try:
                    out[rel] = fp.read_text(errors="replace")
                except OSError:
                    # An unreadable test file is left out of the bundle rather than failing collection.
                    pass
        return out

    def _check_collusion(self, project_root: Path) -> Optional[str]:
        if not self.state.lock_tests:
            return None
        current = self._collect_test_files(project_root)
        if not current:
            return None
        prior = {
            k: v for k, v in self.state.test_files_hash_by_cycle.items()
            if k != str(self.state.cycle)
        }
        alarms = detect_weakening(prior, current)
        if not alarms:
            self.state.test_files_hash_by_cycle[str(self.state.cycle)] = snapshot_test_files(current)
            return None
        write_collusion_alarm(self.xli_dir, cycle=self.state.cycle, alarms=alarms)
        return "; ".join(alarms[:3])

    def sync_test_lock(self, session: Any) -> None:
        """Push loop test-lock flag into the live session for tool enforcement."""
        lock = bool(self.state.lock_tests and self.is_active)
        session.loop_lock_tests = lock

    def record_builder_cost(self, usd: float) -> None:
        if usd <= 0:
            return
        self.state.cost["builder_usd"] = self.state.cost.get("builder_usd", 0.0) + usd
        self.save()

    def _commit_message(self) -> str:
        g = self.state.goal.replace("\n", " ").strip()
        if len(g) > 72:
            g = g[:72] + "…"
        return f"loop: {g} (cycle {self.state.cycle})"

    def _try_git_commit(self, project_root: Path) -> Optional[str]:
        if self.state.commit_mode == "never":
            return None
        status, err = git_cmd(project_root, ["status", "--porcelain"])
        if err or not (status or "").strip():
            return None
        git_cmd(project_root, ["add", "-A"])
        msg = self._commit_message()
        out, cerr = git_cmd(project_root, ["commit", "-m", msg])
        if cerr:
            return None
        return msg

    def _maybe_commit_after_tests(self, project_root: Path) -> Optional[str]:
        if self.state.commit_mode == "each":
            return self._try_git_commit(project_root)
        return None

    def _maybe_commit_on_done(self, project_root: Path) -> Optional[str]:
        if self.state.commit_mode == "final":
            return self._try_git_commit(project_root)
        return None

    def _maybe_commit_before_ci(self, project_root: Path) -> Optional[str]:
        """Commit pending work before a CI push when commit_mode is ``final``.

        Returns the commit message so the caller can surface a ``committed
        (final)`` status line — otherwise the work committed here is invisible,
        since ``_maybe_commit_on_done`` then sees a clean tree and reports nothing.
        """
        if self.state.commit_mode == "final":
            return self._try_git_commit(project_root)
        return None

    def emit_cycle_hook(
        self,
        project_root: Path,
        *,
        outcome: str,
        console: Any = None,
    ) -> None:
        from xlii.hooks import run_hooks

        run_hooks(
            self.xli_dir,
            "on-loop-cycle",
            {
                "loop_id": self.state.loop_id,
                "cycle": self.state.cycle,
                "max_cycles": self.state.max_cycles,
                "phase": self.state.phase,
                "outcome": outcome,
                "status": self.state.status,
                "judges": list(self.state.judges),
                "goal": self.state.goal,
                "last_verdicts": list(self.state.last_verdicts[-5:]),
            },
            console=console,
        )

    def _record_verdict(self, verdict: Verdict) -> None:
        self.state.last_verdicts.append(
            {
                "judge": verdict.judge,
                "passed": verdict.passed,
                "cycle": self.state.cycle,
                "signature": verdict.signature,
                "summary": verdict.summary,
            }
        )
        if verdict.cost_usd:
            self.state.cost["judges_usd"] = self.state.cost.get("judges_usd", 0.0) + verdict.cost_usd

    def _run_shell_oracle(self, project_root: Path) -> Verdict:
        profile = self._shell_profiles()[0] if self._shell_profiles() else JudgeProfile(
            name="tests", kind="shell", command=self.state.test_command
        )
        cmd_profile = JudgeProfile(
            name=profile.name,
            kind="shell",
            command=self.state.test_command,
        )
        self.state.phase = "test"
        self.save()
        return run_shell_judge(cmd_profile, cwd=project_root)

    def _fix_prompt_tests(self, verdict: Verdict) -> str:
        s = self.state
        return (
            f"The autonomous loop failed tests (cycle {s.cycle}/{s.max_cycles}).\n\n"
            f"Test command: {s.test_command}\n"
            f"Exit code: {verdict.exit_code}\n"
            f"Output (tail):\n{verdict.raw}\n\n"
            f"Goal: {s.goal}\n"
            f"Fix the code so tests pass. The loop will re-run tests and judges automatically."
        )

    def _fix_prompt_ci(self, verdict: Verdict) -> str:
        s = self.state
        lines: list[str] = []
        if verdict.findings:
            for i, f in enumerate(verdict.findings, 1):
                lines.append(f"{i}. {f.text}")
        elif verdict.raw:
            lines.append(verdict.raw[:2000])
        detail = "\n".join(lines) or verdict.summary
        extra = ""
        if self.has_llm_judges:
            extra = "reviewers, and "
        return (
            f"The autonomous loop failed remote CI checks (cycle {s.cycle}/{s.max_cycles}).\n\n"
            f"Judge: {verdict.judge}\n"
            f"Details:\n{detail}\n\n"
            f"Goal: {s.goal}\n"
            f"Fix the code so required PR checks pass. The loop will re-run tests, "
            f"{extra}CI automatically."
        )

    def _judges_done_label(self) -> str:
        """Judge names for completion banners — CI is omitted in swarm mode."""
        if self.state.swarm_size <= 1:
            return ", ".join(self.state.judges)
        ci_names = {p.name for p in self.profiles if p.kind == "ci"}
        return ", ".join(j for j in self.state.judges if j not in ci_names)

    def _fix_prompt_llm(self, verdict: Verdict) -> str:
        s = self.state
        lines: list[str] = []
        if verdict.findings:
            for i, f in enumerate(verdict.findings, 1):
                loc = f"{f.file}:{f.line}" if f.file else "finding"
                tag = f" [{f.tag}]" if f.tag else ""
                lines.append(f"{i}. {loc} — {f.text}{tag}")
        elif verdict.raw:
            lines.append(verdict.raw[:2000])
        findings_txt = "\n".join(lines) or verdict.summary
        return (
            f"The autonomous loop failed verification (cycle {s.cycle}/{s.max_cycles}).\n\n"
            f"Judge: {verdict.judge}\n"
            f"Findings:\n{findings_txt}\n\n"
            f"Goal: {s.goal}\n"
            f"Fix these issues. Do not modify test files or success criteria.\n"
            f"The loop will re-run tests and judges automatically."
        )

    def _route_back(self, verdict: Verdict, *, failure_kind: str) -> LoopAdvanceResult:
        sig = verdict.signature
        # Verifier starvation is inconclusive about the code — never route the
        # builder to "fix" a verdict that said nothing. Pause (or halt on repeat)
        # instead of burning cycles on unrelated mutations.
        if sig.startswith("inconclusive|"):
            low_signal = failure_kind == "llm" and not verdict.findings
            seen = self.state.finding_sigs_seen.count(sig)
            if seen >= (2 if low_signal else 1):
                self.state.status = "failed"
                self.state.phase = "done"
                self.save()
                return LoopAdvanceResult(
                    status="failed",
                    message=(
                        f"[red]loop halted[/red] — judge {verdict.judge} could not finish "
                        f"{seen + 1}× (verifier starvation, NOT a code failure). "
                        "Raise max_worker_iterations / the judge read budget, or rerun "
                        "without that judge."
                    ),
                    verdict=verdict,
                )
            self.state.finding_sigs_seen.append(sig)
            self.state.status = "paused"
            self.state.phase = "verify"
            self.save()
            return LoopAdvanceResult(
                status="paused",
                message=(
                    f"[yellow]loop paused[/yellow] — judge {verdict.judge} could not finish "
                    "(verifier starvation, NOT a code failure). "
                    "Raise max_worker_iterations / the judge read budget, then /loop resume."
                ),
                verdict=verdict,
            )

        # A repeated signature normally means "not progressing" → stop, so the loop
        # doesn't burn cycles (and budget) re-failing the same way. But a
        # findings-less LLM FAIL is low-signal: its signature is just a hash of the
        # reviewer's prose (loop_verdict), so a reworded rejection slips past while
        # an unchanged one trips it — neither reliably means "stuck". Give such a
        # verdict one extra cycle of grace before declaring no-progress; concrete
        # failures (test exit, structured findings) still stop on the first repeat.
        low_signal = failure_kind == "llm" and not verdict.findings
        seen = self.state.finding_sigs_seen.count(sig)
        if seen >= (2 if low_signal else 1):
            self.state.status = "failed"
            self.state.phase = "done"
            self.save()
            message = (
                f"[red]loop failed[/red] — same failure {seen + 1}× (not progressing). "
                f"Signature: {sig[:60]}"
            )
            return LoopAdvanceResult(
                status="failed",
                message=message,
                verdict=verdict,
            )

        self.state.finding_sigs_seen.append(sig)

        if self.state.cycle >= self.state.max_cycles:
            self.state.status = "capped"
            self.state.phase = "done"
            self.save()
            return LoopAdvanceResult(
                status="capped",
                message=(
                    f"[yellow]loop capped[/yellow] — hit max_cycles ({self.state.max_cycles}) "
                    f"with {failure_kind} still failing"
                ),
                verdict=verdict,
            )

        self.state.cycle += 1
        self.state.phase = "build"
        self.save()
        if failure_kind == "llm":
            next_prompt = self._fix_prompt_llm(verdict)
            label = verdict.judge
        elif failure_kind == "ci":
            next_prompt = self._fix_prompt_ci(verdict)
            label = verdict.judge
        else:
            next_prompt = self._fix_prompt_tests(verdict)
            label = "tests"
        return LoopAdvanceResult(
            status="continue",
            next_prompt=next_prompt,
            message=(
                f"[yellow]loop cycle {self.state.cycle - 1}/{self.state.max_cycles}[/yellow] "
                f"· {label} FAIL — routing back for fix"
            ),
            verdict=verdict,
        )

    def advance_after_build(
        self,
        project_root: Path,
        *,
        judge_ctx: Optional[LoopJudgeContext] = None,
    ) -> LoopAdvanceResult:
        """Run tests and LLM judges after a build/fix turn; route back or finish."""
        if not self.is_active:
            return LoopAdvanceResult(status=self.state.status)

        test_verdict: Optional[Verdict] = None
        commit_msg: Optional[str] = None
        shell_profiles = self._shell_profiles()

        if shell_profiles:
            test_verdict = self._run_shell_oracle(project_root)
            self._record_verdict(test_verdict)
            bundle = assemble_bundle(
                project_root=project_root,
                loop_id=self.state.loop_id,
                cycle=self.state.cycle,
                goal=self.state.goal,
                success_criteria=self.state.success_criteria,
                test_command=self.state.test_command,
                test_exit_code=test_verdict.exit_code,
                test_output=test_verdict.raw,
                prior_findings=self.state.last_verdicts,
            )
            bundle.save_audit(self.xli_dir)
            if not test_verdict.passed:
                self.emit_cycle_hook(project_root, outcome="tests_fail", console=(
                    judge_ctx.console if judge_ctx else None
                ))
                return self._route_back(test_verdict, failure_kind="tests")

            commit_msg = self._maybe_commit_after_tests(project_root)

        llm_profiles = self._llm_profiles()
        if llm_profiles:
            needs_agent = any(p.kind == "same_vendor" for p in llm_profiles)
            if needs_agent and (judge_ctx is None or judge_ctx.agent is None):
                self.state.status = "failed"
                self.state.phase = "done"
                self.save()
                return LoopAdvanceResult(
                    status="failed",
                    message="[red]loop failed[/red] — same-vendor judges require an agent context",
                )

            pricing = None
            if judge_ctx is not None and judge_ctx.agent is not None:
                pricing = getattr(judge_ctx.agent.cfg, "pricing", None)

            for profile in llm_profiles:
                if self._judge_budget_exceeded():
                    self.state.status = "capped"
                    self.state.phase = "done"
                    self.save()
                    return LoopAdvanceResult(
                        status="capped",
                        message=(
                            f"[yellow]loop capped[/yellow] — judge budget "
                            f"${self.state.budget_usd:.2f} reached "
                            f"(spent ${self.state.cost.get('judges_usd', 0.0):.4f})"
                        ),
                    )

                self.state.phase = "verify"
                self.save()
                if profile.mode == "peer":
                    bundle = assemble_peer_bundle(
                        project_root=project_root,
                        loop_id=self.state.loop_id,
                        cycle=self.state.cycle,
                        since=profile.since,
                        prior_findings=self.state.last_verdicts,
                    )
                else:
                    bundle = assemble_bundle(
                        project_root=project_root,
                        loop_id=self.state.loop_id,
                        cycle=self.state.cycle,
                        goal=self.state.goal,
                        success_criteria=self.state.success_criteria,
                        test_command=self.state.test_command,
                        test_exit_code=test_verdict.exit_code if test_verdict else 0,
                        test_output=test_verdict.raw if test_verdict else "(no shell oracle ran)",
                        base=self.state.base_commit,
                        prior_findings=self.state.last_verdicts,
                    )
                bundle.save_audit(self.xli_dir)

                agent = judge_ctx.agent if judge_ctx is not None else None
                project = agent.project if agent is not None else None
                try:
                    verdict = run_llm_judge(
                        profile,
                        bundle=bundle,
                        xli_dir=self.xli_dir,
                        agent=agent,
                        project=project,
                        pricing=pricing,
                        project_root=project_root,
                        read_budget=self.state.read_budget,
                    )
                except NotImplementedError as e:
                    self.state.status = "failed"
                    self.state.phase = "done"
                    self.save()
                    return LoopAdvanceResult(
                        status="failed",
                        message=f"[red]loop failed[/red] — {e}",
                    )
                self._record_verdict(verdict)
                if self._judge_budget_exceeded():
                    self.state.status = "capped"
                    self.state.phase = "done"
                    self.save()
                    return LoopAdvanceResult(
                        status="capped",
                        message=(
                            f"[yellow]loop capped[/yellow] — judge budget "
                            f"${self.state.budget_usd:.2f} exceeded "
                            f"(spent ${self.state.cost.get('judges_usd', 0.0):.4f})"
                        ),
                        verdict=verdict,
                    )
                if judge_ctx is not None and judge_ctx.console is not None:
                    mark = "PASS" if verdict.passed else "FAIL"
                    tier = profile.tier or profile.kind
                    judge_ctx.console.print(
                        f"[dim][loop] judge {profile.name} ({tier}): {mark} — "
                        f"{verdict.summary[:80]}[/dim]"
                    )
                if not verdict.passed:
                    self.emit_cycle_hook(
                        project_root,
                        outcome="llm_fail",
                        console=judge_ctx.console if judge_ctx else None,
                    )
                    return self._route_back(verdict, failure_kind="llm")

        collusion = self._check_collusion(project_root)
        if collusion:
            self.state.status = "failed"
            self.state.phase = "done"
            self.save()
            self.emit_cycle_hook(project_root, outcome="collusion", console=(
                judge_ctx.console if judge_ctx else None
            ))
            return LoopAdvanceResult(
                status="failed",
                message=(
                    "[red]loop failed[/red] — collusion alarm: test files weakened "
                    f"({collusion}). See .xlii/loop-collusion-alarm.md"
                ),
            )

        ci_profiles = self._ci_profiles()
        pre_ci_commit: Optional[str] = None
        for profile in ci_profiles:
            self.state.phase = "ci"
            self.save()
            do_push = self._should_push_before_ci()
            if do_push:
                pre_ci_commit = self._maybe_commit_before_ci(project_root) or pre_ci_commit
            verdict = run_ci_judge(
                profile,
                cwd=project_root,
                console=judge_ctx.console if judge_ctx else None,
                do_push=do_push,
            )
            self._record_verdict(verdict)
            if judge_ctx is not None and judge_ctx.console is not None:
                mark = "PASS" if verdict.passed else "FAIL"
                judge_ctx.console.print(
                    f"[dim][loop] judge {profile.name} (machine): {mark} — "
                    f"{verdict.summary[:80]}[/dim]"
                )
            if not verdict.passed:
                outcome = "ci_fail"
                if verdict.signature.startswith("unavailable|"):
                    self.state.status = "failed"
                    self.state.phase = "done"
                    self.save()
                    self.emit_cycle_hook(project_root, outcome=outcome, console=(
                        judge_ctx.console if judge_ctx else None
                    ))
                    return LoopAdvanceResult(
                        status="failed",
                        message=f"[red]loop failed[/red] — {verdict.summary}",
                        verdict=verdict,
                    )
                self.emit_cycle_hook(project_root, outcome=outcome, console=(
                    judge_ctx.console if judge_ctx else None
                ))
                return self._route_back(verdict, failure_kind="ci")

        final_commit = self._maybe_commit_on_done(project_root) or pre_ci_commit
        self.state.phase = "done"
        self.state.status = "done"
        self.save()
        self.emit_cycle_hook(project_root, outcome="done", console=(
            judge_ctx.console if judge_ctx else None
        ))
        judges_done = self._judges_done_label()
        msg = (
            f"[green]loop complete[/green] — cycle {self.state.cycle}/{self.state.max_cycles} "
            f"· all judges PASS ({judges_done})"
        )
        if self.state.swarm_size > 1 and self._ci_profiles():
            msg += "\n[dim]CI judge skipped in swarm — run CI manually on the landed PR[/dim]"
        if commit_msg:
            msg += f"\n[dim]committed (each): {commit_msg}[/dim]"
        if final_commit:
            msg += f"\n[dim]committed (final): {final_commit}[/dim]"
        return LoopAdvanceResult(
            status="done",
            message=msg,
            verdict=test_verdict,
        )

    def judge_panel(
        self,
        tree_root: Path,
        *,
        judge_ctx: Optional[LoopJudgeContext] = None,
    ) -> Optional[Verdict]:
        """Run the full verdict panel — shell oracle, LLM/cross-vendor judges,
        collusion check — against ``tree_root`` as a READ-ONLY gate. Returns the
        first FAILING verdict, or None when everything passes.

        Unlike :meth:`advance_after_build`, this never routes back, commits, or
        marks the loop done — it is a pure gate. The writer swarm uses it to
        verify the integration worktree BEFORE landing, so the *same* judges
        that gate a serial loop also gate a parallel one (verify-then-land,
        atomically). Costs and verdicts are still recorded so the judge budget
        stays honest across the gate.
        """
        test_verdict: Optional[Verdict] = None
        if self._shell_profiles():
            test_verdict = self._run_shell_oracle(tree_root)
            self._record_verdict(test_verdict)
            if not test_verdict.passed:
                return test_verdict

        llm_profiles = self._llm_profiles()
        if llm_profiles:
            needs_agent = any(p.kind == "same_vendor" for p in llm_profiles)
            if needs_agent and (judge_ctx is None or judge_ctx.agent is None):
                return Verdict(
                    passed=False,
                    summary="same-vendor judges require an agent context",
                    signature="gate:setup",
                    judge="(setup)",
                )
            agent = judge_ctx.agent if judge_ctx is not None else None
            project = agent.project if agent is not None else None
            pricing = getattr(agent.cfg, "pricing", None) if agent is not None else None

            for profile in llm_profiles:
                if self._judge_budget_exceeded():
                    return Verdict(
                        passed=False,
                        summary=f"judge budget ${self.state.budget_usd:.2f} reached",
                        signature="gate:budget",
                        judge=profile.name,
                    )
                if profile.mode == "peer":
                    bundle = assemble_peer_bundle(
                        project_root=tree_root,
                        loop_id=self.state.loop_id,
                        cycle=self.state.cycle,
                        since=profile.since,
                        prior_findings=self.state.last_verdicts,
                    )
                else:
                    bundle = assemble_bundle(
                        project_root=tree_root,
                        loop_id=self.state.loop_id,
                        cycle=self.state.cycle,
                        goal=self.state.goal,
                        success_criteria=self.state.success_criteria,
                        test_command=self.state.test_command,
                        test_exit_code=test_verdict.exit_code if test_verdict else 0,
                        test_output=test_verdict.raw if test_verdict else "(no shell oracle ran)",
                        prior_findings=self.state.last_verdicts,
                    )
                bundle.save_audit(self.xli_dir)
                try:
                    verdict = run_llm_judge(
                        profile,
                        bundle=bundle,
                        xli_dir=self.xli_dir,
                        agent=agent,
                        project=project,
                        pricing=pricing,
                        project_root=tree_root,
                        read_budget=self.state.read_budget,
                    )
                except NotImplementedError as e:
                    return Verdict(
                        passed=False,
                        summary=str(e),
                        signature="gate:unimpl",
                        judge=profile.name,
                    )
                self._record_verdict(verdict)
                if judge_ctx is not None and judge_ctx.console is not None:
                    mark = "PASS" if verdict.passed else "FAIL"
                    tier = profile.tier or profile.kind
                    judge_ctx.console.print(
                        f"[dim][swarm-gate] judge {profile.name} ({tier}): {mark} — "
                        f"{verdict.summary[:80]}[/dim]"
                    )
                if not verdict.passed:
                    return verdict

        collusion = self._check_collusion(tree_root)
        if collusion:
            return Verdict(
                passed=False,
                summary=f"test files weakened ({collusion})",
                signature="collusion",
                judge="collusion",
            )

        return None

    def mark_swarm_done(self) -> None:
        """Finish the loop after the swarm landed a tree that :meth:`judge_panel`
        already verified green. No re-judging — the pre-land gate was the
        authoritative verification, and re-running it on the (identical) landed
        working tree would only double the judge cost."""
        self.state.phase = "done"
        self.state.status = "done"
        self.save()

    def status_lines(self) -> list[str]:
        s = self.state
        lines = [
            f"[loop] {s.status} · cycle {s.cycle}/{s.max_cycles} · phase {s.phase}",
            f"       goal: {s.goal[:72]}{'…' if len(s.goal) > 72 else ''}",
            f"       judges: {', '.join(s.judges)}",
            f"       test: {s.test_command}",
        ]
        if s.last_verdicts:
            # Cycle-labeled so a stale prior-cycle ✗ can't read as the current
            # state while a fresh cycle is still running (2026-07-10 postmortem:
            # an unlabeled "tests ✗" from cycle 1 looked live during cycle 2).
            parts = []
            for lv in s.last_verdicts[-3:]:
                mark = "✓" if lv.get("passed") else "✗"
                parts.append(f"c{lv.get('cycle', '?')} {lv.get('judge', '?')} {mark}")
            lines.append(f"       verdicts: {', '.join(parts)}")
        builder = s.cost.get("builder_usd", 0.0)
        judges = s.cost.get("judges_usd", 0.0)
        if builder or judges:
            lines.append(f"       cost: builder ${builder:.4f} · judges ${judges:.4f}")
        if s.budget_usd is not None:
            lines.append(f"       judge budget: ${judges:.4f} / ${s.budget_usd:.2f}")
        if s.lock_tests:
            lines.append("       test lock: on (cross-vendor)")
        if s.commit_mode != "never":
            lines.append(f"       commit: {s.commit_mode}")
        if s.push_mode != "never":
            lines.append(f"       push: {s.push_mode}")
        if s.read_budget:
            lines.append(f"       read budget: {s.read_budget} files/judge")
        if s.swarm_size > 1:
            lines.append(f"       swarm: {s.swarm_size} writers · merge={s.merge_mode}")
        return lines


def _resolve_merge_judge(
    name: str,
    config_judges: dict[str, Any] | None,
) -> Optional[Any]:
    if not name:
        return None
    try:
        profiles = resolve_judges([name], config_judges)
        return profiles[0] if profiles else None
    except ValueError:
        return None


def resolve_push_mode(
    push_mode: Optional[str],
    judges: list[str],
    config_judges: dict[str, Any] | None,
    *,
    loop_defaults: dict[str, Any] | None = None,
) -> str:
    """Resolve push mode for a loop start.

    Auto-push is **never** implied. Explicit ``--push``, ``loop_defaults.push_mode``,
    or config only — CI in the stack does not silently enable push.
    """
    if push_mode:
        return push_mode
    defaults = loop_defaults or {}
    cfg_push = defaults.get("push_mode")
    if cfg_push:
        return str(cfg_push)
    return "never"


def resolve_commit_mode(
    commit_mode: Optional[str],
    push_mode: str,
    *,
    loop_defaults: dict[str, Any] | None = None,
) -> str:
    """Resolve commit mode. When push is enabled, default commit to ``each``."""
    if commit_mode:
        return commit_mode
    defaults = loop_defaults or {}
    cfg_commit = defaults.get("commit_mode") or defaults.get("commit")
    if cfg_commit:
        return str(cfg_commit)
    if push_mode != "never":
        return "each"
    return "never"


def ci_wall_time_hint(profiles: list[JudgeProfile], max_cycles: int) -> Optional[str]:
    ci = [p for p in profiles if p.kind == "ci"]
    if not ci:
        return None
    worst = max(p.timeout_s + min(p.grace_after_push_s, MAX_GRACE_S) for p in ci)
    total = worst * max(1, max_cycles)
    return f"CI judge may poll up to ~{total // 60}m wall time ({max_cycles} cycles × {worst}s)"


def loop_start_push_warnings(
    *,
    console: Any,
    profiles: list[JudgeProfile],
    push_mode: str,
    project_root: Path,
    max_cycles: int,
) -> None:
    """Emit push/CI warnings shared by REPL and headless loop starts."""
    if any(p.kind == "ci" for p in profiles) and push_mode == "never":
        console.print(
            "[yellow]heads-up:[/yellow] CI judge with push=never — polling remote as-is. "
            "Pass --push each --commit each to ship local fixes before CI."
        )
    if push_mode != "never":
        branch, _ = current_branch(project_root)
        console.print(
            f"[yellow][loop] auto-push {push_mode}[/yellow] — will "
            f"git push origin/{branch or '?'} before CI when an open PR exists"
        )
    wall = ci_wall_time_hint(profiles, max_cycles)
    if wall:
        console.print(f"[dim]{wall}[/dim]")


def run_loop_cli(
    *,
    controller: LoopController,
    project_root: Path,
    run_turn: Any,
    console: Any,
    agent: Any = None,
    config_judges: dict[str, Any] | None = None,
) -> str:
    """Drive loop to completion headlessly. Returns LOOP_PASS|LOOP_FAIL|LOOP_CAP."""
    judge_ctx = LoopJudgeContext(agent=agent, console=console) if agent is not None else None
    prompt = controller.initial_build_prompt()
    s = controller.state
    swarm_note = f" · swarm {s.swarm_size}" if s.swarm_size > 1 else ""
    console.print(
        f"[cyan][loop][/cyan] started · judges: {', '.join(controller.state.judges)} "
        f"· max {controller.state.max_cycles} cycles{swarm_note}"
    )

    while controller.is_active:
        console.print(f"[dim][loop] cycle {controller.state.cycle}/{controller.state.max_cycles} · build[/dim]")
        if agent is not None:
            controller.sync_test_lock(agent.session)

        use_swarm = (
            s.swarm_size > 1
            and s.phase == "build"
            and s.cycle == 1
            and agent is not None
        )
        if use_swarm:
            from xlii.plugin import load_subscriptions
            from xlii.swarm import run_swarm_build

            merge_profile = None
            if s.merge_mode == "llm":
                merge_profile = _resolve_merge_judge(s.merge_judge, config_judges)

            def _serial_redispatch(idx: int, integration_wt: Path) -> None:
                from xlii.swarm import commit_tree, run_writer_worker

                task = f"{s.goal}\n\n[Serial re-dispatch after merge conflict — integrate on top of current tree.]"
                run_writer_worker(
                    pool=agent.pool,
                    project=agent.project,
                    cfg=agent.cfg,
                    worktree=integration_wt,
                    task=task,
                    lock_tests=s.lock_tests,
                    yolo=getattr(agent.session, "yolo", False),
                    subscribed_plugins=load_subscriptions(agent.project.xli_dir),
                )
                # The writer edits the integration worktree in place but does no git.
                # Commit it, mirroring _fix()/commit_tree and dispatch_writers._one()/
                # _worker_commit — otherwise the re-dispatched fix stays uncommitted, the
                # follow-on integrate_workers re-merges onto a dirty tree, and
                # land_integration lands a tip that excludes this work (silent data loss).
                commit_tree(integration_wt, f"swarm: {s.loop_id} serial re-dispatch (writer {idx})")

            last_verdict: dict[str, Any] = {"v": None}
            fix_seen: set[str] = set()

            def _gate(tree_root: Path) -> tuple[bool, str]:
                verdict = controller.judge_panel(tree_root, judge_ctx=judge_ctx)
                last_verdict["v"] = verdict
                if verdict is None:
                    return True, "all judges passed"
                return False, f"{verdict.judge}: {verdict.summary}"

            def _fix(integration_wt: Path, attempt: int) -> bool:
                """Patch the integration tree to satisfy the last failing verdict,
                commit it, and report whether a retry is worthwhile. Returns False
                to give up: an infrastructure failure (budget/setup) that writing
                code can't fix, or the same finding twice (no progress)."""
                from xlii.swarm import commit_tree, run_writer_worker

                verdict = last_verdict["v"]
                if verdict is None:
                    return False
                sig = verdict.signature or ""
                if sig.startswith("gate:"):
                    return False  # budget / setup / unimplemented — code won't help
                if sig in fix_seen:
                    return False  # Goodhart guard: same failure twice = stuck
                fix_seen.add(sig)
                task = (
                    controller._fix_prompt_tests(verdict)
                    if verdict.judge == "tests"
                    else controller._fix_prompt_llm(verdict)
                )
                _text, cost = run_writer_worker(
                    pool=agent.pool,
                    project=agent.project,
                    cfg=agent.cfg,
                    worktree=integration_wt,
                    task=task,
                    lock_tests=s.lock_tests,
                    yolo=getattr(agent.session, "yolo", False),
                    subscribed_plugins=load_subscriptions(agent.project.xli_dir),
                )
                controller.record_builder_cost(cost)
                commit_tree(integration_wt, f"swarm: {s.loop_id} fix {attempt + 1}")
                return True

            result, swarm_state = run_swarm_build(
                repo_root=project_root,
                project=agent.project,
                cfg=agent.cfg,
                pool=agent.pool,
                loop_id=s.loop_id,
                goal=s.goal,
                swarm_size=min(s.swarm_size, agent.cfg.max_parallel_workers),
                merge_mode=s.merge_mode,
                merge_judge_profile=merge_profile,
                test_command=s.test_command,
                commit_mode=s.commit_mode,
                lock_tests=s.lock_tests,
                yolo=getattr(agent.session, "yolo", False),
                xli_dir=controller.xli_dir,
                pricing=getattr(agent.cfg, "pricing", None),
                read_budget=s.read_budget,
                subscribed_plugins=load_subscriptions(agent.project.xli_dir),
                console=console,
                serial_redispatch=_serial_redispatch,
                verify_tree=_gate,
                fix_tree=_fix,
                max_fix_attempts=max(0, s.max_cycles - 1),
            )
            s.swarm = swarm_state.to_dict()
            controller.save()
            if result.builder_cost:
                controller.record_builder_cost(result.builder_cost)
            if not result.ok:
                s.status = "failed"
                s.phase = "done"
                controller.save()
                console.print(f"[red][swarm failed][/red] {result.message}")
                return "LOOP_FAIL"
            # The pre-land gate (verify_tree → judge_panel) already ran the full
            # panel on the integration tree and it landed green. The loop is
            # done — do NOT re-judge the (identical) landed working tree.
            controller.mark_swarm_done()
            judges_done = controller._judges_done_label()
            console.print(
                f"[green][loop][/green] swarm landed a verified tree · "
                f"all judges PASS ({judges_done})"
            )
            if controller._ci_profiles():
                console.print(
                    "[dim]CI judge skipped in swarm — run CI manually on the landed PR[/dim]"
                )
            break

        turn_out = run_turn(prompt)
        if isinstance(turn_out, tuple) and len(turn_out) >= 3:
            stats = turn_out[2]
            cost = getattr(stats, "total_cost", None)
            if cost is not None:
                controller.record_builder_cost(cost)

        result = controller.advance_after_build(project_root, judge_ctx=judge_ctx)
        if result.message:
            console.print(result.message)
        if result.status == "continue" and result.next_prompt:
            prompt = result.next_prompt
            continue
        break

    status = controller.state.status
    if status == "done":
        return "LOOP_PASS"
    if status == "capped":
        return "LOOP_CAP"
    return "LOOP_FAIL"
