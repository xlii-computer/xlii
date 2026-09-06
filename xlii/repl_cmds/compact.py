"""/compact — summarize-and-continue for long sessions (terminal-native Phase 9)."""

from __future__ import annotations

from typing import Any, Optional

from xlii.commands import REPLCommand, register_repl_command
from xlii.context_compact import (
    compact_agent_history,
    init_compact_auto_from_env,
)


def _parse_compact_args(parts: list[str]) -> tuple[Optional[int], bool, bool, Optional[bool], Optional[str]]:
    """Return (recent_turns, dry_run, run_compact, auto_toggle, error)."""
    recent: Optional[int] = None
    dry_run = False
    run_compact = True
    auto_toggle: Optional[bool] = None

    i = 1
    while i < len(parts):
        tok = parts[i].lower()
        if tok == "auto":
            if i + 1 >= len(parts):
                return None, False, False, None, "usage: /compact auto on|off"
            sub = parts[i + 1].lower()
            if sub in ("on", "enable", "true", "1"):
                auto_toggle = True
            elif sub in ("off", "disable", "false", "0"):
                auto_toggle = False
            else:
                return None, False, False, None, f"unknown /compact auto arg: {parts[i + 1]!r}"
            run_compact = False
            i += 2
            continue
        if tok in ("status", "?"):
            run_compact = False
            auto_toggle = None
            i += 1
            continue
        if tok == "--dry-run":
            dry_run = True
            i += 1
            continue
        if tok == "--recent" and i + 1 < len(parts):
            try:
                recent = max(0, int(parts[i + 1]))
            except ValueError:
                return None, False, False, None, f"invalid --recent value: {parts[i + 1]!r}"
            i += 2
            continue
        return None, False, False, None, f"unknown /compact arg: {parts[i]!r}"

    return recent, dry_run, run_compact, auto_toggle, None


def h_compact(line: str, ctx: dict[str, Any]) -> bool:
    agent = ctx["agent"]
    console = ctx["console"]
    session = agent.session
    parts = line.split()

    recent, dry_run, run_compact, auto_toggle, err = _parse_compact_args(parts)
    if err:
        console.print(f"[red]{err}[/red]")
        return True

    if auto_toggle is not None:
        session.compact_auto = auto_toggle
        session.compact_auto_env_cleared = True
        state = "ON" if auto_toggle else "off"
        console.print(
            f"[green]compact auto {state}[/green] — "
            "[dim]compacts before turns when context exceeds ~85% of the model window[/dim]"
        )
        return True

    # `status`/`?` set run_compact=False but carry a 2nd token, so the old
    # `len(parts) == 1` guard was unreachable — the status view was dead code and
    # `/compact status` fell through to a *real* compaction. Show status whenever a
    # compaction wasn't requested, and only compact when it was.
    if not run_compact:
        auto = "[green]ON[/green]" if session.compact_auto else "off"
        console.print(
            f"[bold]compact[/bold]  recent={session.compact_recent}  auto={auto}\n"
            "[dim]usage: /compact [--recent N] [--dry-run]  ·  /compact auto on|off[/dim]"
        )
        return True

    if recent is not None:
        session.compact_recent = recent

    result = compact_agent_history(
        agent,
        recent_turns=session.compact_recent,
        dry_run=dry_run,
        state=ctx.get("state"),
    )

    if not result.compacted:
        if dry_run and result.summarized_turns:
            console.print(
                f"[dim]dry run:[/dim] would summarize {result.summarized_turns} turn(s), "
                f"keep {result.kept_turns} — "
                f"~{result.before_tokens} → ~{result.after_tokens} est. tokens"
            )
        else:
            console.print(f"[dim]{result.reason or 'nothing to compact'}[/dim]")
        return True

    console.print(
        f"[green]compacted[/green] — {result.summarized_turns} turn(s) summarized, "
        f"{result.kept_turns} kept · "
        f"~{result.before_tokens} → ~{result.after_tokens} est. context tokens"
    )
    console.print(
        "[dim]in-memory history shortened — on-disk `.xlii/turns/` unchanged "
        "(restart or re-seed recovers full turns; `/rewind` is git-only)[/dim]"
    )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="compact",
            handler=h_compact,
            usage="/compact [--recent N] [--dry-run]  |  /compact auto on|off",
            description="Summarize older turns and continue with a shorter context window",
            category="session",
        )
    )


__all__ = ["h_compact", "init_compact_auto_from_env", "register"]
