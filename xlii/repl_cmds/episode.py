"""``/session`` — opt-in episode continuity for code runs (code-session-resume P0).

Two memory layers, one mechanism: the project's turn files are **place**
memory (the default seed, no ceremony); an episode id is **opt-in** for
long/crashy/parallel runs. ``/session on`` starts snapshotting the FULL live
history + the session's ``conversation_id`` to ``.xlii/sessions/<id>.json``
(every turn, via the kernel spine); ``/session resume [id]`` loads it back —
same history, same conversation id, so the prompt cache has a chance to stay
warm. Bare ``/session`` shows status; ``list`` the stored episodes; ``off``
stops tracking (the record stays on disk).

Code-REPL only by design: chat/scratch keep zero resume ceremony.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command

_USAGE = "/session [on | off | list | resume [id]]"


def _cmd_session(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if state is None:
        console.print("[red]/session needs a live session[/red]")
        return True

    from xlii import episode as ep

    parts = line.split()
    sub = parts[1].lower() if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else ""
    active = getattr(state, "episode_id", None)

    if sub == "on":
        if active:
            console.print(f"[dim]episode already on:[/dim] [cyan]sess {active}[/cyan]")
            return True
        eid = ep.new_episode(state)
        if eid is None:
            console.print("[red]couldn't start an episode (no project state dir)[/red]")
            return True
        # Sticky, same as --keep-session: later bare launches offer the restart.
        ep.write_keep_session(state.project.xli_dir, True)
        console.print(
            f"[green]✓[/green] episode [cyan]sess {eid}[/cyan] — full history snapshots "
            f"each turn; sticky for this project (the next launch offers to resume; "
            "same conversation id, so the prompt cache can stay warm)"
        )
        return True

    if sub == "off":
        # Deliberate stop: also clears the sticky preference — "off" means
        # "stop keeping my sessions here", not just "pause this episode".
        was_sticky = ep.read_keep_session(state.project.xli_dir)
        if was_sticky:
            ep.write_keep_session(state.project.xli_dir, False)
        if not active:
            if was_sticky:
                console.print("[green]✓[/green] keep-session preference cleared for this project")
            else:
                console.print("[dim]no episode running[/dim]")
            return True
        ep.update_episode(state)              # final snapshot
        ep.mark_clean(state)                  # deliberate stop ≠ crash residue
        state.episode_id = None
        sticky_note = " · keep-session preference cleared" if was_sticky else ""
        console.print(
            f"[green]✓[/green] episode [cyan]sess {active}[/cyan] off — record kept "
            f"([cyan]/session resume {active}[/cyan] to reload it later){sticky_note}"
        )
        return True

    if sub == "list":
        records = ep.list_episodes(state.project.xli_dir)
        if not records:
            console.print("[dim]no stored episodes — /session on to start one[/dim]")
            return True
        for r in records:
            mark = " [green]●[/green]" if r.get("id") == active else ""
            console.print(
                f"  [cyan]{r.get('id')}[/cyan]{mark}  {r.get('turns', 0)} turn(s)  "
                f"[dim]{r.get('model', '')}  updated {r.get('updated_at', '')}[/dim]"
            )
        return True

    if sub == "resume":
        target = arg
        if not target:
            records = ep.list_episodes(state.project.xli_dir)
            if len(records) > 1:
                console.print("[bold]stored episodes[/bold] [dim]— pick one with "
                              "/session resume <id>[/dim]")
                for i, r in enumerate(records, 1):
                    mark = " [green]●[/green]" if r.get("id") == active else ""
                    console.print(
                        f"  {i}. [cyan]{r.get('id')}[/cyan]{mark}  "
                        f"{r.get('turns', 0)} turn(s)  "
                        f"[dim]updated {r.get('updated_at', '')}[/dim]"
                    )
                return True
            target = records[0]["id"] if records else ""
        if not target:
            console.print("[dim]nothing to resume — /session on to start an episode[/dim]")
            return True
        record = ep.resume_episode(state, target)
        if record is None:
            console.print(f"[red]no episode {target!r}[/red] — /session list to see them")
            return True
        console.print(
            f"[green]✓[/green] resumed [cyan]sess {target}[/cyan] — "
            f"{record.get('turns', 0)} turn(s) of history restored, conversation id "
            "reattached (cache prefix has a chance to stay warm); snapshots continue"
        )
        cache_line = ep.format_cache_resume_line(record)
        if cache_line:
            console.print(f"[dim]{cache_line}[/dim]")
        ptr_line = ep.format_pointers_line(record)
        if ptr_line:
            console.print(f"[dim]{ptr_line}[/dim]")
        return True

    if sub:
        console.print(f"[dim]usage: {_USAGE}[/dim]")
        return True

    # bare: status
    sticky = ep.read_keep_session(state.project.xli_dir)
    if active:
        sticky_note = "sticky — the next launch offers to resume; " if sticky else ""
        console.print(
            f"[cyan]sess {active}[/cyan] [dim]— snapshotting each turn; {sticky_note}"
            "/session off to stop, /session list for stored episodes[/dim]"
        )
    elif sticky:
        console.print(
            "[dim]keep-session is on for this project (no live episode) — the next "
            "launch offers to resume the latest; /session off to clear it[/dim]"
        )
    else:
        console.print(
            "[dim]no episode — project turn-seed only (the default). "
            "/session on for opt-in resume continuity.[/dim]"
        )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="session",
            handler=_cmd_session,
            description="Opt-in episode continuity — snapshot the live run; resume it after a restart",
            usage=_USAGE,
            category="session",
            repls=["code"],
        )
    )
