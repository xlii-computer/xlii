"""The help-corpus search + topic-index engines behind /help's flags.

``/help --search <keyword>`` is keyword search across **live slash commands**
and **help topics** (the public ``help_corpus.search_corpus`` seam — client
#1). ``/help --topics`` renders the attachable-topic index. Both are pure read
surfaces: they print, attach nothing, and change no session state.

These used to be the standalone ``/apropos`` (alias ``/search-help``) and
``/manuals`` doors; those names live on forever as **hidden aliases** of
``/help`` (registered in ``meta.py``, which dispatches back into the handlers
here — the ``/marks`` → ``/bookmarks`` play). The classic Unix nod survives:
``/man`` is an alias of ``/describe``; the index and ``man -k`` search now ride
one door. Real OS man-page *generation* is deferred.
"""

from __future__ import annotations

from typing import Any

from xlii.help_corpus import load_manifest, render_topic_index, search_corpus

_MAX_HITS = 12


def _render_hit(console, hit) -> None:
    if hit.kind == "command":
        console.print(f"  [cyan]/{hit.name}[/cyan]  [dim]{hit.description}[/dim]")
    else:
        desc = f" — {hit.description}" if hit.description else ""
        console.print(
            f"  [magenta]{hit.name}[/magenta] [dim]topic{desc}[/dim]  "
            f"[dim]([/dim][cyan]/howto {hit.name}[/cyan][dim])[/dim]"
        )


def _apropos_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    parts = line.split(maxsplit=1)
    query = parts[1].strip() if len(parts) > 1 else ""
    if not query:
        console.print(
            "[dim]usage: [/dim][cyan]/apropos <keyword>[/cyan][dim] — search commands "
            "+ help topics. [/dim][cyan]/manuals[/cyan][dim] lists topics.[/dim]"
        )
        return True

    try:
        manifest = load_manifest()
    except Exception as e:
        console.print(f"[red]help corpus unavailable: {e}[/red]")
        return True

    hits = search_corpus(manifest, query)
    if not hits:
        console.print(
            f"[dim]no matches for [/dim][cyan]{query}[/cyan][dim] — try [/dim]"
            "[cyan]/manuals[/cyan][dim] for the topic index, or a broader keyword.[/dim]"
        )
        return True

    console.print(f"[bold]apropos[/bold] [cyan]{query}[/cyan] [dim]({len(hits)} match(es))[/dim]")
    for hit in hits[:_MAX_HITS]:
        _render_hit(console, hit)
    if len(hits) > _MAX_HITS:
        console.print(f"  [dim]… {len(hits) - _MAX_HITS} more[/dim]")
    console.print(
        "[dim]→ [/dim][cyan]/describe <name>[/cyan][dim] for detail · "
        "[/dim][cyan]/howto <topic>[/cyan][dim] to load a guide[/dim]"
    )
    return True


def _manuals_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    try:
        manifest = load_manifest()
    except Exception as e:
        console.print(f"[red]help corpus unavailable: {e}[/red]")
        return True
    try:
        from rich.markdown import Markdown

        console.print(Markdown(render_topic_index(manifest)))
    except Exception:
        # No rich (or a render hiccup) — fall back to the raw markdown.
        console.print(render_topic_index(manifest))
    console.print(
        "[dim]search across these with [/dim][cyan]/apropos <keyword>[/cyan][dim]; "
        "go deep on a command with [/dim][cyan]/man <name>[/cyan][dim] (= /describe).[/dim]"
    )
    return True


