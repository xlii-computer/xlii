"""Codex harness as an xlii agent tool — bounded OpenAI subtasks."""

from __future__ import annotations

from pathlib import Path

from xlii.harness.brief import HarnessBrief
from xlii.harness.codex import run_ask as codex_run_ask


def run_codex_task(
    task: str,
    *,
    project_root: Path,
    mode: str = "ask",
    model: str | None = None,
    timeout_s: int = 600,
) -> tuple[str, str | None]:
    """Run one Codex exec task. Returns ``(text, error)``."""
    result = codex_run_ask(
        HarnessBrief(
            kind="delegate",
            tier="cross_org",
            question=task,
            project_root=project_root,
        ),
        model=model,
        timeout_s=timeout_s,
        mode=mode,
    )
    if result.error:
        return "", result.error
    return result.text, None
