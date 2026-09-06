"""Codex CLI harness — headless ``codex exec``."""

from __future__ import annotations

import os
import shutil
import subprocess

from xlii.harness.base import HarnessResult, normalize_token_usage, parse_json_stdout
from xlii.harness.brief import HarnessBrief

name = "codex"
tier = "cross_org"
default_model = "gpt-5.3-codex"
DEFAULT_TIMEOUT_S = 600


def resolve_codex_cli() -> str | None:
    override = os.environ.get("XLII_CODEX_BIN")
    if override:
        return override if os.path.exists(override) else None
    return shutil.which("codex")


def available() -> tuple[bool, str]:
    cli = resolve_codex_cli()
    if cli:
        return True, cli
    return False, "ChatGPT plan sign-in or OPENAI_API_KEY"


def _sandbox_for_mode(mode: str, *, permission: str = "allow") -> str:
    if mode in ("ask", "plan") or permission == "reject":
        return "read-only"
    return "workspace-write"


def _mode_prefix(mode: str) -> str:
    if mode == "plan":
        return (
            "You are in PLAN mode. Propose a detailed plan only — do not edit files "
            "or run destructive commands.\n\n"
        )
    if mode == "ask":
        return "You are in ASK mode. Answer from the codebase; do not edit files.\n\n"
    return ""


def run_ask(
    brief: HarnessBrief,
    *,
    model: str | None = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    mode: str = "ask",
    permission: str = "allow",
) -> HarnessResult:
    model = model or default_model
    cli = resolve_codex_cli()
    if cli is None:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error="codex CLI not found (install: npm i -g @openai/codex)",
        )

    prompt = _mode_prefix(mode) + brief.render_prompt()
    cmd = [
        cli,
        "exec",
        "-",
        "--sandbox",
        _sandbox_for_mode(mode, permission=permission),
        "--ask-for-approval",
        "never",
        "--ephemeral",
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
            error=f"codex harness timed out after {timeout_s}s",
        )
    except OSError as e:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error=f"codex harness failed to launch: {type(e).__name__}",
            raw=str(e),
        )

    raw = (proc.stdout or "") + (proc.stderr or "")
    # Non-JSON exec: stdout is the final agent message.
    text, usage, err = parse_json_stdout(proc.stdout, proc.stderr, proc.returncode)
    if err is None and not text.strip() and proc.stdout:
        text = proc.stdout.strip()
    if err is not None:
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error=f"codex harness unavailable: {err}",
            raw=raw,
        )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or text.strip() or f"exit {proc.returncode}"
        return HarnessResult(
            text="",
            model=model,
            harness=name,
            tier=brief.tier or tier,
            error=f"codex harness failed: {detail[:200]}",
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
