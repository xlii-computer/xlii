"""Bash tool handler and its intent/secret helpers."""

from __future__ import annotations

import os
from typing import Any, Optional

from xlii.tool_context import (
    ToolContext,
    ToolResult,
)

from ._common import _cap_output


def _check_intent_and_gate(ctx: ToolContext, cmd: str, intent: str) -> Optional[ToolResult]:
    """Validate the declared intent, enforce the worker read-only ceiling, and
    run the human y/N gate on risky intents.

    Returns a ToolResult to REFUSE the command (invalid intent, worker overreach,
    headless gate, or user denial), or None to let it proceed. Split out of
    t_bash so the whole security decision is unit-testable without a subprocess.
    """
    from xlii.tool_context import VALID_INTENTS

    if intent not in VALID_INTENTS:
        return ToolResult(
            f"bash refused: missing or invalid `intent` (got {intent!r}). "
            f"Required values: {sorted(VALID_INTENTS)}. Declare what this command is meant to do.",
            is_error=True,
        )

    from xlii.tool_context import authorize_shell
    auth = authorize_shell(
        cmd,
        project_root=ctx.project.project_root,
        declared_intent=intent,
        yolo=ctx.yolo,
        auto_approve=ctx.auto_approve,
        is_worker=ctx.is_worker,
        worker_writes=ctx.worker_writes,
    )
    classified = auth.classified
    effective = auth.effective
    if not auth.allow:
        return ToolResult(auth.refusal, is_error=True)

    if auth.needs_confirm:
        if effective != intent and ctx.console is not None:
            ctx.console.print(
                f"  [red]⚠ intent mismatch[/red] [dim]declared={intent} "
                f"classified={classified} — gating on the stronger[/dim]"
            )
        intent = effective
        if ctx.console is None:
            return ToolResult(
                f"bash refused: intent={intent!r} requires confirmation but no console "
                "is attached (running headless). Use --yolo or simpler intent.",
                is_error=True,
            )
        from xlii.tools import _confirm

        # Self-contained prompt: the command + its (effective) intent travel WITH
        # the question. Inline this reads as one block; in the TUI the modal only
        # receives this string, so without the command in it the user sees a box
        # with no idea what they're approving. Plain text only — `_confirm`
        # defaults to input(), which would echo Rich markup literally.
        extra = ""
        try:
            from xlii.interactive import needs_password_tty

            if needs_password_tty(cmd):
                extra = "This will open a terminal for the sudo password.\n"
        except Exception:
            extra = ""
        prompt = (
            f"approve {intent} command?\n"
            f"  {cmd}\n"
            f"{extra}"
            f"[y/N] "
        )
        try:
            answer = _confirm(prompt).strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer != "y":
            return ToolResult(
                f"bash denied by user (intent={intent}). "
                "Try a different approach or ask the user for permission first.",
                is_error=True,
            )
    return None


def _env_with_plugin_secrets(ctx: ToolContext, cmd: str) -> tuple[Optional[dict], tuple[str, ...]]:
    """Subprocess environment with vault-held plugin credentials injected when
    the command references their declared env vars (e.g.
    `curl -H "Authorization: $WEATHER_API_KEY" ...`). Returns an environment and
    the injected secret values to redact, or ``(None, ())`` to inherit the parent
    environment unchanged."""
    if not ctx.subscribed_plugins:
        return None, ()
    try:
        from xlii.plugin import Plugin
        from xlii.vault import env_for_command

        overrides = env_for_command(
            cmd, [Plugin(id=pid) for pid in ctx.subscribed_plugins]
        )
        if overrides:
            return {**os.environ, **overrides}, tuple(v for v in overrides.values() if v)
    except Exception:
        return None, ()  # vault unavailable — command runs without secrets
    return None, ()


def _redact_secret_values(text: str, secrets: tuple[str, ...]) -> str:
    redacted = text
    for secret in sorted(set(secrets), key=len, reverse=True):
        if secret:
            redacted = redacted.replace(secret, "[redacted secret]")
    return redacted


def t_bash(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from xlii.turn_events import ShellRan
    from xlii.shell_run import capture

    cmd = args["command"]
    intent = args.get("intent", "")
    timeout = int(args.get("timeout", 60))

    refusal = _check_intent_and_gate(ctx, cmd, intent)
    if refusal is not None:
        return refusal

    try:
        from xlii.desk_files import (
            files_mount_address,
            is_stub_inventory_command,
            stub_inventory_refusal,
        )

        mount = files_mount_address(ctx.project) or ""
        if mount and is_stub_inventory_command(cmd, ctx.project.project_root):
            return ToolResult(stub_inventory_refusal(mount), is_error=True)
    except Exception:
        # Pointer-desk guard is best-effort; a broken check must not block bash.
        pass

    from xlii.interactive import needs_password_tty, handoff_password_tty

    if needs_password_tty(cmd):
        # Capture closes stdin; sudo talks to /dev/tty or waits unseen.
        # Face has no TTY — open a real terminal and tell the user where.
        have_tty = bool(getattr(ctx.console, "is_terminal", False))
        ok, msg = handoff_password_tty(
            cmd, ctx.project.project_root, cfg=ctx.cfg, have_tty=have_tty,
        )
        if ctx.console is not None:
            try:
                ctx.console.print(
                    f"[yellow]{msg}[/yellow]" if ok else f"[red]{msg}[/red]"
                )
            except Exception:
                # The gate decision already applied; only its notice is lost.
                pass
        ev = ShellRan(
            command=cmd,
            cwd=ctx.project.project_root,
            stdout="",
            stderr=msg,
            returncode=0 if ok else 1,
            duration_s=0.0,
            source="agent_bash",
            intent=intent.replace("_", "-") if intent else None,
        )
        body = (
            f"{msg}\ncommand: {cmd}\n"
            "Do not retry with sudo -S or a piped password. "
            "Wait for the user to finish in that terminal."
        )
        return ToolResult(body, is_error=not ok, shell=ev)

    # Evidence sharpening (turn-receipts): only when BOTH the declared intent
    # and the code classifier say read-only can the command not have modified
    # the tree — then it leaves no dirty mark, so a read-only bash turn no
    # longer satisfies edit claims (and end-of-turn sync skips a rescan).
    # Either signal above read-only keeps the conservative "may have written".
    from xlii.shellgate import READ_ONLY as _RO
    from xlii.shellgate import classify_command as _classify
    read_only_cmd = (
        intent == _RO and _classify(cmd, ctx.project.project_root) == _RO
    )

    env, injected_secrets = _env_with_plugin_secrets(ctx, cmd)
    try:
        cap = capture(cmd, ctx.project.project_root, timeout=timeout, env=env)
    except OSError as e:
        return ToolResult(f"bash failed to start: {e}", is_error=True)

    # Model-facing content keeps the prior shape, with any vault-injected
    # plugin secrets scrubbed before transcript/display surfaces see it.
    stdout = _redact_secret_values(cap.stdout or "", injected_secrets)
    stderr = _redact_secret_values(cap.stderr or "", injected_secrets)
    out = stdout
    if stderr:
        out += "\n--- stderr ---\n" + stderr
    disp_stderr = stderr
    if cap.timed_out:
        out += f"\n--- TIMED OUT after {timeout}s (process group killed; output above is partial) ---"
        note = f"TIMED OUT after {timeout}s (partial output above)"
        disp_stderr = f"{disp_stderr}\n{note}" if disp_stderr else note
        ctx.dirty_paths.add("__rescan__")  # it may have written before dying
        is_error = True
    else:
        out += f"\n--- exit {cap.returncode} ---"
        # A successful command might have touched files — unless intent AND
        # classification both pinned it read-only (the timeout path above stays
        # unconditionally paranoid: a killed process gets no benefit of doubt).
        if cap.returncode == 0 and not read_only_cmd:
            ctx.dirty_paths.add("__rescan__")
        is_error = cap.returncode != 0

    ev = ShellRan(
        command=cmd,
        cwd=ctx.project.project_root,
        stdout=stdout,
        stderr=disp_stderr,
        returncode=cap.returncode,
        duration_s=cap.duration_s,
        source="agent_bash",
        intent=intent.replace("_", "-") if intent else None,
    )
    return ToolResult(_cap_output(ctx, out), is_error=is_error, shell=ev)
