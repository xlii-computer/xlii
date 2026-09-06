"""User hooks: .xlii/hooks/<event>/* scripts run at named lifecycle points.

The Emacs move (roadmap 5.2): instead of feature requests, users wire the
substrate themselves. Contract:

  * Events: pre-turn, post-turn, pre-sync, post-tool, on-plan-approved, on-loop-cycle.
  * A hook is any executable file in `.xlii/hooks/<event>/` (sorted order).
  * It receives one JSON object on stdin:
      {"event": ..., "project_root": ..., "data": {...event-specific...}}
    and the env var XLII_EVENT.
  * stdout is shown to the user (dimmed). Exit code != 0 prints a warning;
    hooks never abort the operation that fired them (observers, not gates).
  * Per-hook wall clock is capped; a hung hook is killed, not waited on.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

HOOK_EVENTS = {
    "pre-turn",
    "post-turn",
    "pre-sync",
    "post-tool",
    "on-plan-approved",
    "on-loop-cycle",
    # cursor-workflows.md B1 — control hooks. Observers by default; only when
    # control is explicitly enabled may an on-turn-stop hook return a followup
    # to drive another turn (see run_control_hooks).
    "on-turn-stop",
}

HOOK_TIMEOUT_S = 10

# Policy-hook (B1) follow-up budget: how many synthetic follow-up turns an
# on-turn-stop hook may drive. A hook can request its own cap, but it is clamped
# to POLICY_HOOK_HARD_CAP — there is no unbounded "keep going" (cloud-style
# unlimited loops are explicitly out of scope).
POLICY_HOOK_DEFAULT_MAX = 3
POLICY_HOOK_HARD_CAP = 5


def _executable(p: Path) -> bool:
    """The one X_OK predicate ``hooks_for`` (fire) and ``inert_hooks``
    (surface) share, so the two can never drift."""
    return os.access(p, os.X_OK)


def hooks_for(xli_dir: Path, event: str) -> list[Path]:
    d = xli_dir / "hooks" / event
    if not d.is_dir():
        return []
    return sorted(
        p for p in d.iterdir() if p.is_file() and _executable(p)
    )


def inert_hooks(xli_dir: Path) -> list[Path]:
    """Files under ``.xlii/hooks/`` that will never run: not executable.

    The inverse of the ``hooks_for`` X_OK predicate — a hook that lost its
    +x bit is silently skipped at fire time, so doctor surfaces these."""
    root = xli_dir / "hooks"
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*") if p.is_file() and not _executable(p)
    )


def _invoke_hook(
    script: Path, payload: str, event: str, xli_dir: Path, say
) -> "Optional[subprocess.CompletedProcess[str]]":
    """Run one hook script with the standard stdin/env/timeout contract.

    Returns the completed process, or None if it timed out / failed to start
    (a warning is emitted via `say`). Shared by run_hooks and run_control_hooks
    so both have identical sandbox, timeout, and env behavior."""
    try:
        return subprocess.run(
            [str(script)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=HOOK_TIMEOUT_S,
            cwd=xli_dir.parent,
            env={**os.environ, "XLII_EVENT": event},
        )
    except subprocess.TimeoutExpired:
        say(f"[yellow]hook {event}/{script.name} timed out after {HOOK_TIMEOUT_S}s — killed[/yellow]")
    except OSError as e:
        say(f"[yellow]hook {event}/{script.name} failed to start: {e}[/yellow]")
    return None


def run_hooks(
    xli_dir: Path,
    event: str,
    data: dict[str, Any],
    *,
    console: Optional[Any] = None,
) -> None:
    """Fire all hooks for `event`. Never raises; never blocks past timeout."""
    assert event in HOOK_EVENTS, f"unknown hook event: {event}"
    scripts = hooks_for(xli_dir, event)
    if not scripts:
        return

    payload = json.dumps({
        "event": event,
        "project_root": str(xli_dir.parent),
        "data": data,
    })

    def _say(msg: str) -> None:
        if console is not None:
            console.print(msg)

    for script in scripts:
        proc = _invoke_hook(script, payload, event, xli_dir, _say)
        if proc is None:
            continue
        out = (proc.stdout or "").strip()
        if out:
            for line in out.splitlines()[:10]:
                _say(f"[dim]hook {script.name}: {line}[/dim]")
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()
            tail = f" — {err[-1]}" if err else ""
            _say(f"[yellow]hook {event}/{script.name} exited {proc.returncode}{tail}[/yellow]")


def control_enabled_for_project(xli_dir: Path) -> bool:
    """Whether control hooks are enabled in project.json (`hooks.control: true`).

    The persistent project-level default; a session `/hook-control on|off` (the
    `hook_control` SessionState flag) overrides it. Default False — control hooks
    are opt-in, and a project that never sets this can only ever run observers."""
    cfg = xli_dir / "project.json"
    try:
        data = json.loads(cfg.read_text())
    except (OSError, ValueError):
        return False
    hooks = data.get("hooks")
    return bool(isinstance(hooks, dict) and hooks.get("control"))


def run_control_hooks(
    xli_dir: Path,
    data: dict[str, Any],
    *,
    console: Optional[Any] = None,
    control_enabled: bool,
) -> Optional[dict[str, Any]]:
    """Fire `on-turn-stop` hooks and, when control is enabled, return a control
    directive.

    A control hook may print a single JSON object on stdout:
        {"followup": "run the tests again and fix any failures", "max_loops": 3}
    The first hook that emits a non-empty `followup` wins; the REPL then runs that
    text as a synthetic user turn (capped — see _drive_policy_hooks).

    When ``control_enabled`` is False the hooks still run, but their stdout is
    treated as ordinary observer output (printed dimmed) and None is returned —
    so the same hook is harmless until the user opts in. Non-JSON stdout is always
    shown as observer output regardless of the flag."""
    scripts = hooks_for(xli_dir, "on-turn-stop")
    if not scripts:
        return None

    payload = json.dumps({
        "event": "on-turn-stop",
        "project_root": str(xli_dir.parent),
        "data": data,
    })

    def _say(msg: str) -> None:
        if console is not None:
            console.print(msg)

    directive: Optional[dict[str, Any]] = None
    for script in scripts:
        proc = _invoke_hook(script, payload, "on-turn-stop", xli_dir, _say)
        if proc is None:
            continue
        out = (proc.stdout or "").strip()
        parsed: Optional[dict[str, Any]] = None
        if out:
            try:
                obj = json.loads(out)
            except ValueError:
                obj = None
            if isinstance(obj, dict) and str(obj.get("followup", "")).strip():
                parsed = obj
        # Show as observer output unless it is an acted-on control directive.
        if out and (parsed is None or not control_enabled):
            for line in out.splitlines()[:10]:
                _say(f"[dim]hook {script.name}: {line}[/dim]")
        if proc.returncode != 0:
            err = (proc.stderr or "").strip().splitlines()
            tail = f" — {err[-1]}" if err else ""
            _say(f"[yellow]hook on-turn-stop/{script.name} exited {proc.returncode}{tail}[/yellow]")
        if control_enabled and parsed is not None and directive is None:
            directive = parsed  # first valid directive wins

    return directive if control_enabled else None
