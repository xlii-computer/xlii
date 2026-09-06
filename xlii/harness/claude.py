"""Claude Code harness — headless ``claude -p --bare --output-format json``."""

from __future__ import annotations

import os
import shutil
import subprocess

from xlii.harness.base import HarnessResult, normalize_token_usage, parse_json_stdout
from xlii.harness.brief import HarnessBrief

name = "claude"
tier = "cross_org"
default_model = "claude-sonnet-4-6"
DEFAULT_TIMEOUT_S = 600


def resolve_claude_cli() -> str | None:
    override = os.environ.get("XLII_CLAUDE_BIN")
    if override:
        return override if os.path.exists(override) else None
    return shutil.which("claude")


def available() -> tuple[bool, str]:
    cli = resolve_claude_cli()
    if cli:
        return True, cli
    return False, "ANTHROPIC_API_KEY or claude login"


def run_ask(
    brief: HarnessBrief,
    *,
    model: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    mode: str = "ask",
    permission: str = "allow",
) -> HarnessResult:
    # Claude headless read-only is enforced via mode/prompt; permission is
    # accepted for a uniform adapter signature (write-gating lives in the ACP
    # delegate path, not this headless ask).
    del permission
    model = model or default_model
    cli = resolve_claude_cli()
    if cli is None:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error="claude CLI not found (install Claude Code)",
        )

    prefix = ""
    if mode == "plan":
        prefix = "Plan mode: propose a plan only; do not edit files.\n\n"
    elif mode == "ask":
        prefix = "Ask mode: read-only; do not edit files.\n\n"

    prompt = prefix + brief.render_prompt()
    cmd = [
        cli,
        "-p",
        "--output-format",
        "json",
        "--bare",
        "--model",
        model,
    ]
    if mode == "agent":
        cmd.append("--dangerously-skip-permissions")
    cwd = str(brief.project_root) if brief.project_root else None

    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error=f"claude harness timed out after {timeout_s}s",
        )
    except OSError as e:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error=f"claude harness failed to launch: {type(e).__name__}",
            raw=str(e),
        )

    text, usage, err = parse_json_stdout(proc.stdout, proc.stderr, proc.returncode)
    tokens_in, tokens_out = normalize_token_usage(usage)
    raw = (proc.stdout or "") + (proc.stderr or "")
    if err is not None:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error=f"claude harness unavailable: {err}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            raw=raw,
        )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or text.strip() or f"exit {proc.returncode}"
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error=f"claude harness failed: {detail[:200]}",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            raw=raw,
        )
    return HarnessResult(
        text=text,
        model=model,
        harness=name,
        tier=brief.tier or tier,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        raw=raw,
    )
