"""Cursor harness — headless ``cursor-agent --print --mode ask``."""

from __future__ import annotations

import os
import shutil
import subprocess

from xlii.harness.base import HarnessResult, normalize_token_usage, parse_json_stdout
from xlii.harness.brief import HarnessBrief

name = "cursor"
tier = "cross_agent"
default_model = "composer-2.5"
DEFAULT_TIMEOUT_S = 600
COMPOSER_PRICING = {"input": 3.0 / 1_000_000, "output": 15.0 / 1_000_000}


def resolve_cursor_cli() -> str | None:
    override = os.environ.get("XLII_CURSOR_AGENT_BIN")
    if override:
        return override if os.path.exists(override) else None
    return shutil.which("cursor-agent")


def available() -> tuple[bool, str]:
    cli = resolve_cursor_cli()
    if cli:
        return True, cli
    return False, "cursor-agent login (install: curl https://cursor.com/install | bash)"


def _composer_cost(tokens_in: int, tokens_out: int) -> float | None:
    try:
        return round(
            tokens_in * COMPOSER_PRICING["input"] + tokens_out * COMPOSER_PRICING["output"],
            6,
        )
    except (KeyError, TypeError):
        return None


def run_ask(
    brief: HarnessBrief,
    *,
    model: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    mode: str = "ask",
    permission: str = "allow",
) -> HarnessResult:
    # Cursor headless (`cursor-agent --print`) has no read-only mode toggle; the
    # mode/permission args are accepted for a uniform adapter signature only.
    del mode, permission
    model = model or default_model
    cli = resolve_cursor_cli()
    if cli is None:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error="cursor-agent CLI not found (install: curl https://cursor.com/install | bash)",
        )

    prompt = brief.render_prompt()
    cmd = [
        cli,
        "--print",
        "--output-format",
        "json",
        "--mode",
        "ask",
        "--trust",
        "--model",
        model,
    ]
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
            error=f"cursor harness timed out after {timeout_s}s",
        )
    except OSError as e:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error=f"cursor harness failed to launch: {type(e).__name__}",
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
            error=f"cursor harness unavailable: {err}",
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
            error=f"cursor harness failed: {detail[:200]}",
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
        cost_usd=_composer_cost(tokens_in, tokens_out),
        raw=raw,
    )


# Back-compat alias for loop_judge tests
parse_cursor_output = parse_json_stdout
