"""Shared harness types and JSON stdout parsing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from xlii.harness.brief import HarnessBrief


@dataclass
class HarnessResult:
    text: str
    model: str
    harness: str
    tier: str
    error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    raw: str = ""


class HarnessAdapter(Protocol):
    name: str
    tier: str
    default_model: str

    def available(self) -> tuple[bool, str]:
        """Return ``(available, hint_or_path)`` for this harness CLI."""

    def run_ask(
        self,
        brief: HarnessBrief,
        *,
        model: str | None = None,
        timeout_s: int = 600,
        mode: str = "ask",
        permission: str = "allow",
    ) -> HarnessResult:
        """Run a one-shot ask against the harness CLI.

        ``mode`` (ask/plan/agent) and ``permission`` (allow/reject) are accepted
        uniformly by every adapter so the dispatcher in ``runner.run_ask`` can
        forward them without per-harness special-casing; adapters that do not
        support a parameter simply ignore it.
        """


def parse_json_stdout(
    stdout: str, stderr: str, returncode: int
) -> tuple[str, dict[str, Any], str | None]:
    """Parse harness CLI ``--output-format json`` stdout.

    Returns ``(result_text, usage, error)``. Scans from the last JSON line so
    leading banner/log lines on stdout do not break parsing.
    """
    out = (stdout or "").strip()
    if not out:
        msg = (stderr or "").strip() or f"empty output (exit {returncode})"
        return "", {}, msg[:200]

    obj: dict[str, Any] | None = None
    for line in reversed(out.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            obj = parsed
            break

    if obj is None:
        if returncode != 0:
            msg = (stderr or out or "").strip() or f"exit {returncode}"
            return "", {}, msg[:200]
        return out, {}, None

    usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    if obj.get("is_error"):
        detail = str(obj.get("result") or obj.get("subtype") or obj.get("error") or "error")
        return "", usage, detail[:200]
    text = obj.get("result")
    if text is None:
        text = obj.get("content") or obj.get("message") or obj.get("output") or ""
    return str(text), usage, None


def normalize_token_usage(usage: dict[str, Any]) -> tuple[int, int]:
    tin = usage.get("inputTokens") or usage.get("input_tokens") or usage.get("prompt_tokens") or 0
    tout = usage.get("outputTokens") or usage.get("output_tokens") or usage.get("completion_tokens") or 0
    return int(tin or 0), int(tout or 0)
