"""``/off`` — leave every overlay/mode at once, back to the base surface.

xlii layers *overlays* and *modes* on top of the three base surfaces
(**code · chat · scratch**): the mode controllers (plan · rail · debug ·
discovery · ops), the talk-primary howto overlay, and a foreground
harness (cursor · claude · grok-build · codex). Each has its own ``… off``,
which is a lot to remember when you're two layers deep. ``/off`` clears them
all in one move and drops you back to the base surface.

It deliberately does NOT touch:
- the **base surface** (code/chat/scratch) — that's not a mode you leave, it's
  where you land; switch it with /code · /chat · /scratch;
- the **trust ladder** (yolo/freeball) — that's a separate axis; /safe lowers it.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def _clear_active_mode(ctx: dict[str, Any]) -> str:
    """Clear the mode-controller slot (plan/rail/debug/discovery/ops). Returns a
    label when one was active, else ''."""
    agent = ctx.get("agent")
    active = getattr(agent, "active_mode", None) if agent is not None else None
    if active is None:
        return ""
    label = ""
    try:
        tag = active.status_tag()
        if tag:
            label = str(tag[1] or tag[0]).lower()
    except Exception:
        label = active.__class__.__name__.replace("Controller", "").lower()
    try:
        agent.set_mode(None)
    except Exception:
        return ""
    return label or "mode"


def _clear_howto(ctx: dict[str, Any]) -> str:
    from xlii.repl_cmds.howto import _attachment_owner, _detach, _set_mode

    owner = _attachment_owner(ctx)
    on = getattr(ctx.get("state") or owner, "howto_mode", False)
    if not on:
        return ""
    _detach(owner)
    _set_mode(ctx, False)
    return "howto"


def _clear_harness(ctx: dict[str, Any]) -> str:
    state = ctx.get("state")
    reg = getattr(state, "cursor_sessions", None) if state is not None else None
    if reg is None or getattr(reg, "foreground", None) is None:
        return ""
    try:
        from xlii.harness.detect import harness_label
        left = reg.leave_mode()
        return harness_label(left) if left else ""
    except Exception:
        try:
            return reg.leave_mode() or ""
        except Exception:
            return ""


def _off_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    # Order is cosmetic; each clearer is independent and no-ops when its axis
    # is already off, so /off is idempotent.
    cleared = [
        _clear_active_mode(ctx),
        _clear_howto(ctx),
        _clear_harness(ctx),
    ]
    cleared = [c for c in cleared if c]

    if not cleared:
        console.print(
            "[dim]nothing to leave — already on the base surface "
            "(code/chat/scratch). [/dim][cyan]/safe[/cyan][dim] lowers yolo.[/dim]"
        )
        return True

    console.print(
        f"[green]✓[/green] left [cyan]{', '.join(cleared)}[/cyan] — "
        "[dim]back to the base surface.[/dim]"
    )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="off",
            handler=_off_handler,
            description="Leave every overlay/mode at once (plan·rail·debug·howto·harness) — back to code/chat/scratch",
            usage="/off",
            category="mode",
        )
    )
