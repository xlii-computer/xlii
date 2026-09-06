"""/wiki — author the project's semantic memory (the ``wiki://`` store).

The write half of ``wiki://`` (browse/attach live in the WikiPane, read lives in ``xlii cat``):
``list · show · new · edit · rm``, plus the two model-powered steps of write→refute→promote —
``distill`` (the AI writes a page from source addresses, born unverified) and ``verify`` (a
skeptical-editor pass promotes it). A thin shell over :mod:`xlii.wiki` (the store) and
:mod:`xlii.wiki_author` (the AI steps); the scope is the ambient session's ``.xlii/wiki/``.
"""

from __future__ import annotations

from typing import Any

from xlii.commands import REPLCommand, register_repl_command


def _xli_dir(ctx: dict[str, Any]):
    from xlii.active_session import active_xli_dir, xli_dir_of

    return xli_dir_of(ctx.get("state")) or active_xli_dir()


def _trust(page) -> str:
    return "[green]✓[/green]" if page.verified else "[yellow]?[/yellow]"


def _split_intent(tokens: "list[str]") -> "tuple[list[str], str]":
    """Split ``addr addr -- free intent`` into (addresses, intent)."""
    if "--" in tokens:
        i = tokens.index("--")
        return tokens[:i], " ".join(tokens[i + 1:]).strip()
    return tokens, ""


def _list(console, xli_dir) -> None:
    from xlii import wiki as W

    pages = W.list_pages(xli_dir)
    if not pages:
        console.print("[dim](no wiki pages yet)[/dim]")
        console.print("[dim]create one: [/dim][cyan]/wiki new <name>[/cyan]"
                      "[dim] · or let the AI write it: [/dim][cyan]/wiki distill <name> <addr>…[/cyan]")
        return
    console.print("[bold]wiki[/bold] [dim](✓ verified · ? unverified)[/dim]")
    for p in pages:
        srcs = f"  [dim]{len(p.sources)} src[/dim]" if p.sources else ""
        console.print(f"  {_trust(p)} [cyan]{p.name}[/cyan]  [dim]{p.title}[/dim]{srcs}")


def _show(console, xli_dir, name: str) -> None:
    from xlii import wiki as W

    if not W.page_exists(xli_dir, name):
        console.print(f"[red]no such wiki page: {name!r}[/red]")
        return
    page = W.read_page(xli_dir, name)
    console.print(f"{_trust(page)} [cyan]wiki://{name}[/cyan]")
    if page.sources:
        console.print("[dim]sources: [/dim]" + ", ".join(page.sources))
    console.print()
    console.print(page.body)


def _new(console, xli_dir, name: str) -> None:
    from xlii import wiki as W

    if not W.is_valid_name(name):
        console.print(f"[red]invalid page name: {name!r}[/red] "
                      "[dim](letters, digits, _ . - only)[/dim]")
        return
    if W.page_exists(xli_dir, name):
        console.print(f"[dim](already exists: {name} — edit with [/dim][cyan]/wiki edit {name}[/cyan][dim])[/dim]")
        return
    W.write_page(xli_dir, name, W.DEFAULT_WIKI_TEMPLATE)
    console.print(f"[green]✓[/green] created [cyan]wiki://{name}[/cyan] [yellow]?[/yellow]"
                  " [dim](unverified — edit it, then /wiki verify)[/dim]")
    _edit(console, xli_dir, name, announce=False)


def _edit(console, xli_dir, name: str, *, announce: bool = True) -> None:
    from xlii import wiki as W
    from xlii.editor import open_for_edit

    if not W.page_exists(xli_dir, name):
        console.print(f"[red]no such wiki page: {name!r}[/red] "
                      f"[dim](create with [/dim][cyan]/wiki new {name}[/cyan][dim])[/dim]")
        return
    before = W.read_page(xli_dir, name)
    open_for_edit(W.page_path(xli_dir, name))
    after = W.read_page(xli_dir, name)
    # A body edit invalidates a prior verification — re-render through the store so the flag resets.
    if before.verified and after.body != before.body:
        W.write_page(xli_dir, name, after.body, sources=after.sources, verified=False)
        console.print(f"[dim]edited — [/dim][yellow]?[/yellow][dim] verification reset; re-run "
                      f"[/dim][cyan]/wiki verify {name}[/cyan]")
    elif announce:
        console.print(f"[dim]edited [/dim][cyan]wiki://{name}[/cyan]")


def _rm(console, xli_dir, name: str) -> None:
    from xlii import wiki as W

    if W.delete_page(xli_dir, name):
        console.print(f"[green]removed[/green] wiki://{name}")
    else:
        console.print(f"[red]no such wiki page: {name!r}[/red]")


def _distill(console, ctx, xli_dir, tokens: "list[str]") -> None:
    from xlii import wiki_author

    if not tokens:
        console.print("[dim]usage: [/dim][cyan]/wiki distill <name> <addr>… [-- intent][/cyan]")
        return
    name, rest = tokens[0], tokens[1:]
    addresses, intent = _split_intent(rest)
    if not addresses:
        console.print("[yellow]give at least one source address[/yellow] "
                      "[dim]— e.g. /wiki distill arch conv://./t1.md file://notes.md[/dim]")
        return
    complete = wiki_author.session_completer(ctx.get("state"))
    if complete is None:
        console.print("[red]no model available[/red] [dim](can't reach a chat client this session)[/dim]")
        return
    try:
        with console.status(f"[cyan]distilling wiki://{name} from {len(addresses)} source(s)…[/cyan]"):
            recorded = wiki_author.distill(xli_dir, name, addresses, complete, intent=intent)
    except wiki_author.WikiAuthorError as e:
        console.print(f"[red]distill failed:[/red] {e}")
        return
    except Exception as e:  # noqa: BLE001 — never kill the REPL over an author call
        console.print(f"[red]distill failed: {type(e).__name__}: {e}[/red]")
        return
    console.print(f"[green]✓[/green] wrote [cyan]wiki://{name}[/cyan] [yellow]?[/yellow] "
                  f"[dim]from {len(recorded)} source(s) — review, then [/dim][cyan]/wiki verify {name}[/cyan]")


def _verify(console, ctx, xli_dir, tokens: "list[str]") -> None:
    from xlii import wiki as W, wiki_author

    promote_only = "--promote" in tokens
    names = [t for t in tokens if not t.startswith("--")]
    if not names:
        console.print("[dim]usage: [/dim][cyan]/wiki verify <name> [--promote][/cyan]"
                      "[dim] (--promote trusts it without the AI check)[/dim]")
        return
    name = names[0]
    if not W.page_exists(xli_dir, name):
        console.print(f"[red]no such wiki page: {name!r}[/red]")
        return
    if promote_only:
        W.mark_verified(xli_dir, name, True)
        console.print(f"[green]✓[/green] [cyan]wiki://{name}[/cyan] marked verified "
                      "[dim](manual promote)[/dim]")
        return
    complete = wiki_author.session_completer(ctx.get("state"))
    if complete is None:
        console.print("[red]no model available[/red] "
                      f"[dim](use [/dim][cyan]/wiki verify {name} --promote[/cyan][dim] to trust manually)[/dim]")
        return
    try:
        with console.status(f"[cyan]skeptical-editor pass on wiki://{name}…[/cyan]"):
            verdict = wiki_author.verify(xli_dir, name, complete)
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]verify failed: {type(e).__name__}: {e}[/red]")
        return
    if verdict.promoted:
        console.print(f"[green]✓ verified[/green] [cyan]wiki://{name}[/cyan] "
                      "[dim]— every claim checks out against its sources[/dim]")
    else:
        console.print(f"[yellow]? not verified[/yellow] [cyan]wiki://{name}[/cyan]")
        if verdict.reasons:
            console.print(verdict.reasons)


def _handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx.get("console")
    xli_dir = _xli_dir(ctx)
    if xli_dir is None:
        console.print("[dim]the wiki lives in a project's .xlii — open one first[/dim]")
        return True

    tokens = line.split()[1:]  # drop "/wiki"
    sub = tokens[0] if tokens else "list"
    rest = tokens[1:]
    arg = rest[0] if rest else ""

    if sub in ("list", "ls"):
        _list(console, xli_dir)
    elif sub == "show" and arg:
        _show(console, xli_dir, arg)
    elif sub == "new" and arg:
        _new(console, xli_dir, arg)
    elif sub == "edit" and arg:
        _edit(console, xli_dir, arg)
    elif sub in ("rm", "delete") and arg:
        _rm(console, xli_dir, arg)
    elif sub == "distill":
        _distill(console, ctx, xli_dir, rest)
    elif sub == "verify":
        _verify(console, ctx, xli_dir, rest)
    else:
        console.print("[dim]usage:[/dim] [cyan]/wiki[/cyan] [dim](list) ·[/dim] "
                      "[cyan]show|new|edit|rm <name>[/cyan][dim] ·[/dim] "
                      "[cyan]distill <name> <addr>…[/cyan][dim] ·[/dim] "
                      "[cyan]verify <name> [--promote][/cyan]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="wiki",
            handler=_handler,
            description="Author the project's semantic-memory wiki: list/show/new/edit/rm, plus AI distill + verify",
            usage="/wiki [list] | show|new|edit|rm <name> | distill <name> <addr>… [-- intent] | verify <name> [--promote]",
            category="knowledge",
            repls=["code"],
        )
    )
