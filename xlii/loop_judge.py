"""Judge adapters for the autonomous loop."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from xlii.loop_bundle import VerdictBundle, fetch_read_excerpts, render_bundle_brief
from xlii.loop_verdict import Verdict, parse_read_requests, parse_verdict, shell_verdict

SHELL_TIMEOUT_S = 600

# External coding-agent judge (Cursor Composer). The Cursor CLI installs both an
# `agent` and a `cursor-agent` entrypoint; we prefer the latter because `agent`
# collides with the Grok CLI on PATH. Override the binary via XLII_CURSOR_AGENT_BIN.
CURSOR_AGENT_TIMEOUT_S = 600
DEFAULT_CURSOR_MODEL = "composer-2.5"
# Composer 2.5 token pricing (USD per token) for loop cost accounting. Override
# per-call via the `pricing` arg ({"input": ..., "output": ...} USD/token).
COMPOSER_PRICING = {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000}


@dataclass(frozen=True)
class JudgeProfile:
    name: str
    kind: str
    command: str = "pytest -q"
    description: str = ""
    mode: str = "verify"  # verify | peer (LLM judges)
    since: str = "HEAD~1"
    model_role: str = "worker"
    provider: str = ""
    model: str = ""
    api_key_env: str = ""
    tier: str = ""
    harness: str = ""  # harness adapter name when kind is harness/external
    # CI judge (kind=ci) — polls required PR checks via gh CLI
    poll_interval_s: int = 30
    timeout_s: int = 1800
    grace_after_push_s: int = 120
    pr_number: int = 0

    def as_transport(self) -> dict[str, Any]:
        """Profile dict for secondary_ai.query_verdict."""
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key_env": self.api_key_env,
        }


DEFAULT_JUDGES: dict[str, JudgeProfile] = {
    "tests": JudgeProfile(
        name="tests",
        kind="shell",
        command="pytest -q",
        tier="machine",
        description="machine oracle — exit code 0 = PASS",
    ),
    "xai-verify": JudgeProfile(
        name="xai-verify",
        kind="same_vendor",
        mode="verify",
        tier="same_model",
        description="xAI cold verifier (task + diff) — correlated, cheap",
    ),
    "xai-peer": JudgeProfile(
        name="xai-peer",
        kind="same_vendor",
        mode="peer",
        since="HEAD~1",
        tier="same_model",
        description="xAI blind peer (diff only) — catches intent mismatch",
    ),
    "cursor": JudgeProfile(
        name="cursor",
        kind="harness",
        harness="cursor",
        mode="verify",
        model=DEFAULT_CURSOR_MODEL,
        tier="cross_agent",
        timeout_s=CURSOR_AGENT_TIMEOUT_S,
        description="Cursor Composer 2.5 — cross-agent cold verifier (read-only)",
    ),
    "github-ci": JudgeProfile(
        name="github-ci",
        kind="ci",
        tier="machine",
        description="required PR checks via gh CLI (poll after push)",
    ),
}


def _default_judge_tier(kind: str, harness: str) -> str:
    if kind == "cross_vendor":
        return "cross_org"
    if kind == "ci":
        return "machine"
    if kind in ("harness", "external"):
        from xlii.harness.detect import HARNESS_SPECS

        if harness and harness in HARNESS_SPECS:
            return HARNESS_SPECS[harness].tier
        return "cross_agent" if kind == "external" else "cross_org"
    return ""


def resolve_judges(names: list[str], config_judges: dict[str, Any] | None = None) -> list[JudgeProfile]:
    """Resolve judge profile names to JudgeProfile objects."""
    cfg = config_judges or {}
    out: list[JudgeProfile] = []
    for name in names:
        if name in cfg:
            raw = cfg[name]
            out.append(
                JudgeProfile(
                    name=name,
                    kind=str(raw.get("kind", "shell")),
                    command=str(raw.get("command", "pytest -q")),
                    description=str(raw.get("description", "")),
                    mode=str(raw.get("mode", "verify")),
                    since=str(raw.get("since", "HEAD~1")),
                    model_role=str(raw.get("model_role", "worker")),
                    provider=str(raw.get("provider", "")),
                    model=str(raw.get("model", "")),
                    api_key_env=str(raw.get("api_key_env", "")),
                    harness=str(raw.get("harness", "")),
                    tier=str(raw.get("tier", "")) or _default_judge_tier(
                        str(raw.get("kind", "")),
                        str(raw.get("harness", "")),
                    ),
                    poll_interval_s=int(raw.get("poll_interval_s", 30)),
                    timeout_s=int(
                        raw.get(
                            "timeout_s",
                            CURSOR_AGENT_TIMEOUT_S
                            if str(raw.get("kind", "")) in ("harness", "external")
                            else 1800,
                        )
                    ),
                    grace_after_push_s=int(raw.get("grace_after_push_s", 120)),
                    pr_number=int(raw.get("pr_number", 0)),
                )
            )
        elif name in DEFAULT_JUDGES:
            out.append(DEFAULT_JUDGES[name])
        else:
            raise ValueError(f"unknown judge profile: {name}")
    return out


def run_shell_judge(
    profile: JudgeProfile,
    *,
    cwd: Path,
    tail_lines: int = 80,
) -> Verdict:
    """Run a shell oracle (tier 0)."""
    from xlii.shellgate import NETWORK, MODIFIES_SYSTEM, classify_command

    classified = classify_command(profile.command, cwd)
    if classified in (NETWORK, MODIFIES_SYSTEM):
        return shell_verdict(
            judge=profile.name,
            exit_code=126,
            output=f"refused: test command classified as {classified}",
            tail_lines=tail_lines,
        )
    try:
        proc = subprocess.run(
            profile.command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=SHELL_TIMEOUT_S,
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        return shell_verdict(
            judge=profile.name,
            exit_code=proc.returncode,
            output=output,
            tail_lines=tail_lines,
        )
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + (e.stderr or "") if e.stdout or e.stderr else ""
        return shell_verdict(
            judge=profile.name,
            exit_code=124,
            output=out or "test command timed out",
            tail_lines=tail_lines,
        )


def spawn_worker_review(
    *,
    agent: Any,
    project: Any,
    brief: str,
    system_prompt: str,
    model_role: str = "worker",
) -> tuple[str, Any]:
    """Spawn a cold-context WorkerAgent review. Returns (text, CallStats)."""
    from xlii.agent import WorkerAgent
    from xlii.plugin import load_subscriptions

    cfg = agent.cfg
    role_key = (model_role or "worker").strip().lower()
    try:
        judge_model = cfg.get_model_for_role(role_key)
    except (ValueError, AttributeError):
        judge_model = cfg.get_model_for_role("worker")

    worker_clients = agent.pool.acquire()
    worker = WorkerAgent(
        clients=worker_clients,
        project=project,
        cfg=cfg,
        subscribed_plugins=load_subscriptions(project.xli_dir),
        model=judge_model,
    )
    try:
        # Judges walk a real multi-file diff (read the bundle, open files,
        # write a verdict) — the default worker allowance starves on any
        # non-trivial vector. Give reviews headroom; ordinary workers keep
        # the configured cap.
        text, call = worker.run(
            task=brief,
            system_prompt_override=system_prompt,
            max_iterations=max(getattr(cfg, "max_worker_iterations", 10) or 10, 24),
        )
        agent.pool.report_success(worker_clients)
        return text, call
    except Exception as e:
        from xlii.pool import is_auth_failure

        if is_auth_failure(e):
            try:
                agent.pool.report_auth_failure(worker_clients)
            except Exception:
                # Reporting the auth failure to the pool is bookkeeping; the original error is re-raised below
                # regardless.
                pass
        raise


def save_review_report(
    xli_dir: Path,
    *,
    label: str,
    text: str,
    sink: str,
) -> None:
    """Save a reviewer report for /consult --from-* chaining."""
    try:
        ts = datetime.now().isoformat(timespec="seconds")
        (xli_dir / sink).write_text(f"# {label} — {ts}\n\n{text}\n")
    except OSError:
        # The report is a convenience for /consult chaining; the review itself already ran.
        pass


def _system_prompt_for_mode(mode: str) -> str:
    from xlii.repl_cmds.review import PEER_SYSTEM_PROMPT, VERIFIER_SYSTEM_PROMPT

    return PEER_SYSTEM_PROMPT if mode == "peer" else VERIFIER_SYSTEM_PROMPT


def _save_loop_verdict_reports(
    xli_dir: Path,
    *,
    profile: JudgeProfile,
    bundle: VerdictBundle,
    text: str,
) -> None:
    label = "verifier" if profile.mode == "verify" else "peer review"
    sink = f"loop-verdict-{bundle.cycle}-{profile.name}.md"
    save_review_report(xli_dir, label=label, text=text, sink=sink)
    if profile.mode == "verify":
        save_review_report(xli_dir, label=label, text=text, sink="verify-last.md")
    elif profile.mode == "peer":
        save_review_report(xli_dir, label=label, text=text, sink="peer-last.md")


def _apply_read_followup(
    text: str,
    *,
    brief: str,
    project_root: Path | None,
    read_budget: int,
    requery: Any,
) -> Any | None:
    """If the judge asked for more context, fetch excerpts and re-query once (L3)."""
    if read_budget <= 0 or project_root is None:
        return None
    requests = parse_read_requests(text)
    if not requests:
        return None
    excerpts = fetch_read_excerpts(project_root, requests, max_files=read_budget)
    if not excerpts.strip():
        return None
    followup_brief = (
        f"{brief}\n\n"
        f"---\n"
        f"Additional excerpts (READ_REQUEST follow-up, one-shot):\n\n"
        f"{excerpts}\n\n"
        f"Now respond with PASS: or FAIL as before."
    )
    return requery(followup_brief)


def run_same_vendor_judge(
    profile: JudgeProfile,
    *,
    bundle: VerdictBundle,
    agent: Any,
    project: Any,
    xli_dir: Path,
    project_root: Path | None = None,
    read_budget: int = 0,
) -> Verdict:
    """Run a same-vendor WorkerAgent judge on a cold VerdictBundle."""
    brief = render_bundle_brief(bundle)
    system_prompt = _system_prompt_for_mode(profile.mode)
    root = project_root or getattr(project, "project_root", None)

    def _call(b: str) -> tuple[str, Any]:
        return spawn_worker_review(
            agent=agent,
            project=project,
            brief=b,
            system_prompt=system_prompt,
            model_role=profile.model_role,
        )

    try:
        text, call = _call(brief)
        follow = _apply_read_followup(
            text,
            brief=brief,
            project_root=root,
            read_budget=read_budget,
            requery=_call,
        )
        if follow is not None:
            text, call = follow
    except Exception as e:
        return Verdict(
            passed=False,
            summary=f"reviewer crashed: {type(e).__name__}",
            signature=f"crash|{type(e).__name__}",
            raw=str(e),
            judge=profile.name,
            model="",
        )

    verdict = parse_verdict(text, judge=profile.name, model=call.model)
    verdict.raw = text
    verdict.cost_usd = call.cost_usd
    verdict.tokens_in = call.prompt_tokens
    verdict.tokens_out = call.completion_tokens
    _save_loop_verdict_reports(xli_dir, profile=profile, bundle=bundle, text=text)
    return verdict


def run_cross_vendor_judge(
    profile: JudgeProfile,
    *,
    bundle: VerdictBundle,
    xli_dir: Path,
    pricing: dict | None = None,
    project_root: Path | None = None,
    read_budget: int = 0,
) -> Verdict:
    """Run a cross-vendor cold judge via secondary_ai."""
    from xlii.secondary_ai import query_verdict

    brief = render_bundle_brief(bundle)

    def _call(b: str):
        return query_verdict(
            b,
            profile=profile.as_transport(),
            mode=profile.mode,
            pricing=pricing,
        )

    try:
        resp = _call(brief)
        follow = _apply_read_followup(
            resp.text,
            brief=brief,
            project_root=project_root,
            read_budget=read_budget,
            requery=_call,
        )
        if follow is not None:
            resp = follow
        text = resp.text
    except Exception as e:
        return Verdict(
            passed=False,
            summary=f"judge unavailable: {type(e).__name__}",
            signature=f"unavailable|{type(e).__name__}",
            raw=str(e),
            judge=profile.name,
            model=profile.model,
        )

    verdict = parse_verdict(text, judge=profile.name, model=resp.model)
    verdict.raw = text
    verdict.cost_usd = resp.cost_usd
    verdict.tokens_in = resp.prompt_tokens
    verdict.tokens_out = resp.completion_tokens
    _save_loop_verdict_reports(xli_dir, profile=profile, bundle=bundle, text=text)
    return verdict


MERGE_JUDGE_SYSTEM = (
    "You are a cross-vendor merge judge for xlii writer-swarm integration.\n"
    "Given base, ours, theirs, and the resolved file plus both task intents, "
    "decide whether either side's change was silently dropped or semantically negated.\n"
    "Respond with PASS: <reason> or FAIL: <reason>."
)


def run_merge_judge(
    profile: JudgeProfile,
    *,
    xli_dir: Path,
    base: str,
    ours: str,
    theirs: str,
    resolved: str,
    intent_ours: str,
    intent_theirs: str,
    relpath: str,
    pricing: dict | None = None,
    read_budget: int = 0,
) -> Verdict:
    """Cross-vendor judge for LLM merge resolutions (W3)."""
    from xlii.secondary_ai import query_verdict

    brief = (
        f"Merge review for: {relpath}\n\n"
        f"Intent (already integrated):\n{intent_ours}\n\n"
        f"Intent (incoming writer):\n{intent_theirs}\n\n"
        f"--- BASE ---\n{base}\n\n"
        f"--- OURS ---\n{ours}\n\n"
        f"--- THEIRS ---\n{theirs}\n\n"
        f"--- RESOLVED ---\n{resolved}\n\n"
        "Was either side's change silently dropped or semantically negated?"
    )

    try:
        resp = query_verdict(
            brief,
            profile=profile.as_transport(),
            mode="verify",
            pricing=pricing,
            system_prompt=MERGE_JUDGE_SYSTEM,
        )
        text = resp.text
    except Exception as e:
        return Verdict(
            passed=False,
            summary=f"merge-judge unavailable: {type(e).__name__}",
            signature=f"merge-unavailable|{type(e).__name__}",
            raw=str(e),
            judge=profile.name,
            model=profile.model,
        )

    verdict = parse_verdict(text, judge=profile.name, model=resp.model)
    verdict.raw = text
    verdict.cost_usd = resp.cost_usd
    verdict.tokens_in = resp.prompt_tokens
    verdict.tokens_out = resp.completion_tokens
    save_review_report(
        xli_dir,
        label=f"merge-judge {relpath}",
        text=text,
        sink=f"loop-merge-judge-{relpath.replace('/', '-')}.md",
    )
    return verdict


def resolve_cursor_cli() -> str | None:
    """Locate the Cursor agent CLI (re-export for tests and legacy callers)."""
    from xlii.harness.cursor import resolve_cursor_cli as _resolve

    return _resolve()


def _parse_cursor_output(
    stdout: str, stderr: str, returncode: int
) -> tuple[str, dict[str, Any], str | None]:
    from xlii.harness.cursor import parse_cursor_output

    return parse_cursor_output(stdout, stderr, returncode)


def _composer_cost(tokens_in: int, tokens_out: int, pricing: dict | None) -> float | None:
    p = pricing or COMPOSER_PRICING
    try:
        return round(tokens_in * p["input"] + tokens_out * p["output"], 6)
    except (KeyError, TypeError):
        return None


def _harness_name_for_profile(profile: JudgeProfile) -> str:
    if profile.harness:
        return profile.harness
    if profile.kind == "external":
        return "cursor"
    raise ValueError(f"harness judge {profile.name!r} missing harness name")


def run_harness_judge(
    profile: JudgeProfile,
    *,
    bundle: VerdictBundle,
    xli_dir: Path,
    project_root: Path | None = None,
    pricing: dict | None = None,
    timeout_s: int = CURSOR_AGENT_TIMEOUT_S,
) -> Verdict:
    """Run a registered external harness in read-only ask mode on a cold bundle."""
    from xlii.harness import run_ask
    from xlii.harness.brief import HarnessBrief

    # Until the harness is resolved we cannot know its default model, so leave it
    # blank for config-error verdicts rather than mislabeling non-cursor harnesses
    # (Claude/Codex/Grok) as the Cursor default. Reset to the harness default once
    # the harness name is known below.
    model = profile.model or ""

    def _unavailable(summary: str, sig: str, raw: str = "") -> Verdict:
        return Verdict(
            passed=False,
            summary=summary,
            signature=sig,
            raw=raw,
            judge=profile.name,
            model=model,
        )

    try:
        harness = _harness_name_for_profile(profile)
    except ValueError as e:
        return _unavailable(str(e), "unavailable|config", raw=str(e))

    from xlii.harness.detect import harness_meta, load_local_harnesses

    load_local_harnesses(xli_dir)
    model = profile.model or str(harness_meta(harness)["default_model"])

    brief_text = render_bundle_brief(bundle)
    prompt = f"{_system_prompt_for_mode(profile.mode)}\n\n{brief_text}"
    result = run_ask(
        harness,
        HarnessBrief(
            kind="loop_judge",
            tier=profile.tier,
            question=prompt,
            project_root=project_root,
        ),
        model=profile.model or None,
        timeout_s=timeout_s,
    )
    if result.error:
        err_lower = result.error.lower()
        if "not found" in err_lower:
            sig = "unavailable|no-cli"
        elif "timed out" in err_lower:
            sig = "unavailable|timeout"
        elif "failed to launch" in err_lower:
            sig = f"unavailable|{result.error.split(':')[-1].strip().split()[0]}"
        else:
            sig = "unavailable|harness"
        return _unavailable(result.error, sig, raw=result.raw)

    text = result.text
    verdict = parse_verdict(text, judge=profile.name, model=result.model or model)
    verdict.raw = text
    verdict.tokens_in = result.tokens_in
    verdict.tokens_out = result.tokens_out
    if harness == "cursor":
        verdict.cost_usd = result.cost_usd if result.cost_usd is not None else _composer_cost(
            verdict.tokens_in, verdict.tokens_out, pricing
        )
    _save_loop_verdict_reports(xli_dir, profile=profile, bundle=bundle, text=text)
    return verdict


def run_external_judge(
    profile: JudgeProfile,
    *,
    bundle: VerdictBundle,
    xli_dir: Path,
    project_root: Path | None = None,
    pricing: dict | None = None,
    timeout_s: int = CURSOR_AGENT_TIMEOUT_S,
) -> Verdict:
    """Run an external coding-agent judge on a cold bundle (L3).

    Legacy alias for :func:`run_harness_judge` with harness ``cursor``.
    """
    if profile.kind == "external" and not profile.harness:
        profile = JudgeProfile(
            name=profile.name,
            kind=profile.kind,
            command=profile.command,
            description=profile.description,
            mode=profile.mode,
            since=profile.since,
            model_role=profile.model_role,
            provider=profile.provider,
            model=profile.model,
            api_key_env=profile.api_key_env,
            tier=profile.tier,
            harness="cursor",
            poll_interval_s=profile.poll_interval_s,
            timeout_s=profile.timeout_s,
            grace_after_push_s=profile.grace_after_push_s,
            pr_number=profile.pr_number,
        )
    return run_harness_judge(
        profile,
        bundle=bundle,
        xli_dir=xli_dir,
        project_root=project_root,
        pricing=pricing,
        timeout_s=timeout_s,
    )


def run_llm_judge(
    profile: JudgeProfile,
    *,
    bundle: VerdictBundle,
    xli_dir: Path,
    agent: Any | None = None,
    project: Any | None = None,
    pricing: dict | None = None,
    project_root: Path | None = None,
    read_budget: int = 0,
) -> Verdict:
    """Dispatch same_vendor, cross_vendor, harness, or external judge."""
    if profile.kind == "same_vendor":
        if agent is None or project is None:
            raise ValueError("same_vendor judge requires agent context")
        return run_same_vendor_judge(
            profile,
            bundle=bundle,
            agent=agent,
            project=project,
            xli_dir=xli_dir,
            project_root=project_root,
            read_budget=read_budget,
        )
    if profile.kind == "cross_vendor":
        return run_cross_vendor_judge(
            profile,
            bundle=bundle,
            xli_dir=xli_dir,
            pricing=pricing,
            project_root=project_root,
            read_budget=read_budget,
        )
    if profile.kind in ("external", "harness"):
        return run_harness_judge(
            profile,
            bundle=bundle,
            xli_dir=xli_dir,
            project_root=project_root,
            pricing=pricing,
            timeout_s=profile.timeout_s,
        )
    raise ValueError(f"not an LLM judge profile: {profile.name!r} ({profile.kind})")
