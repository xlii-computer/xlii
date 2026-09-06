"""/journal — Project Shadow's recorder toggle (JRN-1), code REPL only.

`/journal` toggles the in-process code journal + the self-building wiki (and shows
status). The recorder stays a SILENT background observer: it watches and writes,
but the "ask" voice is no longer a separate identity. Asking about this project's
history is now `/mojo <question>` — mojo answers with its own memory FUSED with
this project's journal + wiki (`ProjectJournal.recall_context`). `/askjo` survives
only as a hidden alias of `/mojo` (muscle memory); the old Project-Shadow-as-a-
separate-voice teacher path is retired.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command

_CODE_FLAGS = {"--code-on", "--code-off", "--code-auto"}
_CHAT_FLAGS = {"--chat-on", "--chat-off", "--chat-explicit-off"}
_WIKI_FLAGS = {"--wiki-on", "--wiki-off"}


def _journal(ctx: dict[str, Any]):
    state = ctx.get("state")
    return getattr(state, "journal", None) if state is not None else None


def _journal_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    journal = _journal(ctx)
    if journal is None:
        console.print("[dim]the journal is available in the code REPL[/dim]")
        return True

    flags = line.split()[1:]
    if not flags:
        console.print("[bold]Project Shadow[/bold] [dim]— the project's silent journalist[/dim]")
        for ln in journal.status_lines():
            console.print(ln)
        console.print("[dim]toggle: /journal --code-on | --code-off | --code-auto · ask: /mojo <q>[/dim]")
        return True

    unknown = [f for f in flags if f not in _CODE_FLAGS and f not in _CHAT_FLAGS
               and f not in _WIKI_FLAGS]
    if unknown:
        console.print(
            f"[yellow]unknown flag(s): {' '.join(unknown)}[/yellow] "
            "[dim](/journal for status)[/dim]"
        )
        return True

    # Chat pipe = the persona compacter (OQ6) — a separate, JRN-2 feature.
    if any(f in _CHAT_FLAGS for f in flags):
        console.print(
            "[dim]chat journaling (the persona memory compacter) ships with JRN-2 — "
            "[/dim][cyan]xlii journal install[/cyan][dim]. /journal here is the "
            "in-process code journal.[/dim]"
        )

    # Code pipe (JRN-1). --code-auto implies on + persists; --code-off clears auto.
    if "--code-auto" in flags:
        journal.set_code(True, persist_auto=True)
        console.print(
            "[green]✓[/green] code journal [green]ON[/green] + "
            "auto-enabled for this project [dim](persists across sessions)[/dim]"
        )
    elif "--code-on" in flags:
        journal.set_code(True)
        console.print("[green]✓[/green] code journal [green]ON[/green] [dim](this session)[/dim]")
    elif "--code-off" in flags:
        journal.set_code(False, persist_auto=False)
        console.print("[dim]code journal off [dim](auto-enable cleared; buffer flushed)[/dim][/dim]")

    # The self-building wiki (rides flush). Independent of the code toggles above, but only
    # *acts* while the code journal is recording — so nudge if it's on but journaling is off.
    if "--wiki-on" in flags:
        journal.set_wiki_auto(True)
        console.print("[green]✓[/green] self-building wiki [green]ON[/green] "
                      "[dim](drafts new pages on flush, born unverified → [/dim][cyan]/wiki list[/cyan][dim])[/dim]")
        if not journal.is_recording():
            console.print("[dim]  (it acts once the code journal is recording — [/dim]"
                          "[cyan]/journal --code-on[/cyan][dim])[/dim]")
    elif "--wiki-off" in flags:
        journal.set_wiki_auto(False)
        console.print("[dim]self-building wiki off[/dim]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="journal",
            handler=_journal_handler,
            description="Project Shadow journal: show status, toggle the code journal, or the self-building wiki",
            usage="/journal [--code-on | --code-off | --code-auto | --wiki-on | --wiki-off]",
            category="knowledge",
            repls=["code"],
        )
    )
