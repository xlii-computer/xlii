"""``/btw`` — steer the running agent turn (bg-default P2).

While an agent turn runs in the background (bg-default P1 keeps the input
free), ``/btw <note>`` queues a steering note that the turn folds into its
history at the next **tool boundary** — the same safe yield point the ■ stop
uses. Not a second conversation: one interjection lane for the active job.
Queued with no turn running, the note rides the NEXT turn's first boundary.

Bare ``/btw`` shows the active turn job (if any) and what's pending.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command

_USAGE = "/btw <note>  (bare: show active turn + pending steering)"


def _btw_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    agent = getattr(state, "agent", None) if state is not None else ctx.get("agent")
    session = getattr(agent, "session", None)
    if session is None:
        console.print("[red]/btw needs a live session[/red]")
        return True

    inbox = getattr(session, "btw_inbox", None)
    if inbox is None:
        console.print("[red]this session has no steering inbox[/red]")
        return True

    parts = line.split(maxsplit=1)
    note = parts[1].strip() if len(parts) > 1 else ""

    # The active turn job, when the surface tracks one (TUI bg-default P1).
    active_name = ""
    try:
        from xlii.jobs import get_registry
        reg = get_registry(state)
        if reg is not None:
            for job in reg.active_jobs():
                if job.kind == "turn":
                    active_name = job.name
                    break
    except Exception:
        # active_name stays empty, which only means the note isn't tagged with a job.
        pass

    if not note:
        if active_name:
            console.print(f"[cyan]turn running:[/cyan] {active_name}")
        else:
            console.print("[dim]no agent turn running[/dim]")
        if inbox:
            console.print(f"[cyan]pending steering ({len(inbox)}):[/cyan]")
            for n in inbox:
                console.print(f"  · {n}")
        else:
            console.print(f"[dim]no pending steering — usage: {_USAGE}[/dim]")
        return True

    inbox.append(note)
    where = ("the running turn's next tool boundary" if active_name
             else "the next turn's first tool boundary")
    console.print(f"[green]✓[/green] queued for {where}: [cyan]{note}[/cyan]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="btw",
            handler=_btw_handler,
            description="Steer the running agent turn — a note folded in at the next tool boundary",
            usage=_USAGE,
            category="session",
        )
    )
