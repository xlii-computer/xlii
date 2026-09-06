"""/admin — the capability / elevation gate (Vector E, merge seam #1).

`/admin unlock` verifies the vault admin secret and *elevates* the session;
every capability-gated command (`REPLCommand.capability`) checks that elevation
centrally in `commands.dispatch_repl_command` via `session_is_elevated`. The same
secret keys the XMPP daemon launch (`daemon_gate.evaluate_daemon_launch`), so one
passphrase governs the whole privilege axis.

Verbs:
  /admin            · /admin status   — show elevation + whether a key is set
  /admin unlock [s] — verify the secret → elevate this session
  /admin lock       — drop elevation
  /admin set-key [s]— set (bootstrap) or rotate the admin secret
  /admin clear-key  — remove the admin secret (requires elevation)

Elevation is per-session and ephemeral: it lives on `state.elevated` and is never
persisted, so a new session always starts locked.
"""

from __future__ import annotations

from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command


def _read_secret(rest: str, console, prompt: str) -> Optional[str]:
    """Resolve the secret: the inline argument if present, else a hidden prompt.

    The prompt rides :func:`xlii.console_prompt.request_line` (secret=True): a
    masked modal in the full-screen TUI — where a blocking ``getpass`` would
    deadlock the worker under Textual (the guided-add hang class) — and getpass
    in the inline REPL. None means we couldn't read one (the caller aborts) —
    we never echo the secret or fall back to a visible prompt."""
    rest = (rest or "").strip()
    if rest:
        return rest
    from xlii.console_prompt import can_prompt, request_line

    if not can_prompt(console):
        console.print("[yellow]pass the secret inline: [cyan]/admin <verb> <secret>[/cyan] "
                      "[dim](no interactive surface for a hidden prompt)[/dim][/yellow]")
        return None
    secret = request_line(console, prompt.rstrip(": "), secret=True)
    return (secret or "").strip() or None


def _set_elevated(state, value: bool) -> None:
    """Flip session elevation. `state.elevated` is a dynamic, ephemeral attribute
    (deliberately not a persisted REPLState field — Vector E doesn't own
    repl_state.py); all readers use getattr(..., False), so unset == not elevated."""
    if state is not None:
        state.elevated = bool(value)


def h_admin(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")

    from xlii.vault import (
        admin_secret_is_set,
        clear_admin_secret,
        set_admin_secret,
        verify_admin_secret,
    )

    parts = line.split(maxsplit=2)
    sub = parts[1].lower() if len(parts) > 1 else "status"
    rest = parts[2] if len(parts) > 2 else ""
    elevated = bool(getattr(state, "elevated", False))

    # --- status -------------------------------------------------------------
    if sub in ("status", "show"):
        try:
            has_key = admin_secret_is_set()
        except Exception:
            has_key = False
        console.print("[bold]admin / capability gate[/bold]")
        console.print(f"  elevated:   {'[green]yes[/green]' if elevated else 'no'}")
        console.print(
            "  admin key:  "
            + ("set" if has_key else "[yellow]not set[/yellow] [dim](run /admin set-key)[/dim]")
        )
        if has_key and not elevated:
            console.print("[dim]  /admin unlock to run capability-gated commands[/dim]")
        return True

    # --- lock ---------------------------------------------------------------
    if sub == "lock":
        _set_elevated(state, False)
        console.print("[dim]session locked (elevation dropped)[/dim]")
        return True

    # --- unlock -------------------------------------------------------------
    if sub == "unlock":
        if state is None:
            console.print("[red]/admin unlock needs an interactive session[/red]")
            return True
        if not admin_secret_is_set():
            console.print("[yellow]no admin key set — run [cyan]/admin set-key[/cyan] first[/yellow]")
            return True
        secret = _read_secret(rest, console, "admin secret: ")
        if secret is None:
            return True
        if verify_admin_secret(secret):
            _set_elevated(state, True)
            console.print("[green]✓ session elevated[/green] "
                          "[dim](capability-gated commands unlocked · /admin lock to drop)[/dim]")
        else:
            _set_elevated(state, False)
            console.print("[red]✗ incorrect admin secret[/red]")
        return True

    # --- set-key (bootstrap or rotate) --------------------------------------
    if sub in ("set-key", "setkey"):
        already = False
        try:
            already = admin_secret_is_set()
        except Exception:
            already = False
        # Bootstrapping the first key is open; *rotating* an existing one is
        # privileged — altering the security config is itself admin-gated.
        if already and not elevated:
            console.print("[yellow]an admin key already exists — "
                          "[cyan]/admin unlock[/cyan] first to rotate it[/yellow]")
            return True
        secret = _read_secret(rest, console, "new admin secret: ")
        if secret is None:
            return True
        try:
            set_admin_secret(secret)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            return True
        except Exception as e:
            console.print(f"[red]could not set admin key: {type(e).__name__}: {e}[/red]")
            return True
        # The session that set the key proved the secret, so it is elevated.
        _set_elevated(state, True)
        console.print(f"[green]✓ admin key {'rotated' if already else 'set'}[/green] "
                      "[dim](session elevated)[/dim]")
        return True

    # --- clear-key ----------------------------------------------------------
    if sub in ("clear-key", "clearkey"):
        if not elevated:
            console.print("[yellow][cyan]/admin unlock[/cyan] first to clear the admin key[/yellow]")
            return True
        if clear_admin_secret():
            _set_elevated(state, False)
            console.print("[dim]admin key cleared (session locked)[/dim]")
        else:
            console.print("[dim](no admin key was set)[/dim]")
        return True

    console.print(f"[yellow]unknown /admin verb: {sub}[/yellow] "
                  "[dim](status · unlock · lock · set-key · clear-key)[/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="admin",
            handler=h_admin,
            usage="/admin [status|unlock|lock|set-key|clear-key] [secret]",
            description="Capability gate: elevate/lock the session and manage the admin key",
            category="admin",
            repls=["code", "chat"],
        )
    )
