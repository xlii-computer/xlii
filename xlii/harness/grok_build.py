"""Grok Build harness — headless ``grok -p`` (same-vendor delegate)."""

from __future__ import annotations

import os
import shutil
import subprocess

from xlii.harness.base import HarnessResult, normalize_token_usage, parse_json_stdout
from xlii.harness.brief import HarnessBrief

name = "grok"
tier = "same_vendor"
default_model = "grok-build-0.1"
DEFAULT_TIMEOUT_S = 600


def resolve_grok_cli() -> str | None:
    override = os.environ.get("XLII_GROK_BIN")
    if override:
        return override if os.path.exists(override) else None
    return shutil.which("grok")


def available() -> tuple[bool, str]:
    cli = resolve_grok_cli()
    if cli:
        return True, cli
    return False, "SuperGrok / X Premium+ OAuth or XAI_API_KEY"


def _mode_prefix(mode: str) -> str:
    if mode == "plan":
        return "Plan mode: propose steps and diffs only; do not apply yet.\n\n"
    if mode == "ask":
        return "Ask mode: read-only investigation; do not edit files.\n\n"
    return ""


def run_ask(
    brief: HarnessBrief,
    *,
    model: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    mode: str = "ask",
    permission: str = "allow",
) -> HarnessResult:
    # permission accepted for a uniform adapter signature; the headless grok ask
    # path is read-only and does not write, so the flag is a no-op here.
    del permission
    model = model or default_model
    cli = resolve_grok_cli()
    if cli is None:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error="grok CLI not found (install: curl -fsSL https://x.ai/cli/install.sh | bash)",
        )

    prompt = _mode_prefix(mode) + brief.render_prompt()
    cmd = [
        cli,
        "-p",
        "--output-format",
        "json",
        "--no-auto-update",
        "-m",
        model,
    ]
    if mode == "agent":
        cmd.append("--always-approve")
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
            error=f"grok harness timed out after {timeout_s}s",
        )
    except OSError as e:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error=f"grok harness failed to launch: {type(e).__name__}",
            raw=str(e),
        )

    text, usage, err = parse_json_stdout(proc.stdout, proc.stderr, proc.returncode)
    raw = (proc.stdout or "") + (proc.stderr or "")
    if err is not None:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error=f"grok harness unavailable: {err}",
            raw=raw,
        )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or text.strip() or f"exit {proc.returncode}"
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error=f"grok harness failed: {detail[:200]}",
            raw=raw,
        )
    tokens_in, tokens_out = normalize_token_usage(usage)
    return HarnessResult(
        text=text,
        model=model,
        harness=name,
        tier=brief.tier or tier,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        raw=raw,
    )
