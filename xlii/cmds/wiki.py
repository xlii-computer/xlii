"""``xlii wiki`` — author the current project's semantic-memory wiki from the shell.

The model-free half of the authoring loop, scoped to the **cwd project** (the CLI has no ambient
session, so it resolves the project explicitly rather than through :mod:`xlii.active_session`):
``list · show · new · rm · verify`` (manual promote). The AI steps — ``distill`` (write a page)
and the skeptical-editor ``verify`` pass — live in the REPL ``/wiki`` command, where the session's
client pool + model are already in hand. Read is also available cross-tier via ``xlii cat
wiki://<name>`` inside a live session.
"""

from __future__ import annotations

from pathlib import Path

from xlii.ui import console


def _xli_dir() -> "Path | None":
    """The cwd project's ``.xlii`` dir, or ``None`` when cwd is not an xlii project root."""
    from xlii.config import ProjectConfig

    proj = ProjectConfig.load(Path.cwd().resolve())
    return proj.xli_dir if proj is not None else None


def _need_project() -> "Path | None":
    """Return the cwd project's ``.xlii`` dir, or print an error and return ``None``.

    This helper is intended for CLI commands that require a project root.
    """
    d = _xli_dir()
    if d is None:
        console.print("[red]not in an xlii project[/red] "
                      "[dim](run this from a project root, or [/dim][cyan]xlii project init[/cyan][dim])[/dim]")
    return d


def _trust(page) -> str:
    return "[green]✓[/green]" if page.verified else "[yellow]?[/yellow]"


def cmd_list(args) -> int:
    d = _need_project()
    if d is None:
        return 1
    from xlii import wiki as W

    pages = W.list_pages(d)
    if not pages:
        console.print("[dim](no wiki pages yet — [/dim][cyan]xlii wiki new <name>[/cyan][dim])[/dim]")
        return 0
    for p in pages:
        srcs = f"  [dim]{len(p.sources)} src[/dim]" if p.sources else ""
        console.print(f"{_trust(p)} [cyan]{p.name}[/cyan]  [dim]{p.title}[/dim]{srcs}")
    return 0


def cmd_show(args) -> int:
    d = _need_project()
    if d is None:
        return 1
    from xlii import wiki as W

    if not W.page_exists(d, args.name):
        console.print(f"[red]no such wiki page: {args.name!r}[/red]")
        return 1
    page = W.read_page(d, args.name)
    if page.sources:
        console.print("[dim]sources: [/dim]" + ", ".join(page.sources))
    console.print(page.body)
    return 0


def cmd_new(args) -> int:
    d = _need_project()
    if d is None:
        return 1
    from xlii import wiki as W

    if not W.is_valid_name(args.name):
        console.print(f"[red]invalid page name: {args.name!r}[/red] [dim](letters/digits/._- only)[/dim]")
        return 1
    if W.page_exists(d, args.name):
        console.print(f"[yellow]{args.name}[/yellow] already exists")
        return 1
    W.write_page(d, args.name, W.DEFAULT_WIKI_TEMPLATE)
    console.print(f"[green]✓[/green] created [cyan]wiki://{args.name}[/cyan] [yellow]?[/yellow] "
                  f"[dim]at {W.page_path(d, args.name)}[/dim]")
    return 0


def cmd_verify(args) -> int:
    d = _need_project()
    if d is None:
        return 1
    from xlii import wiki as W

    if not W.page_exists(d, args.name):
        console.print(f"[red]no such wiki page: {args.name!r}[/red]")
        return 1
    W.mark_verified(d, args.name, True)
    console.print(f"[green]✓[/green] [cyan]wiki://{args.name}[/cyan] marked verified [dim](manual promote)[/dim]")
    console.print("[dim]the AI skeptical-editor pass is [/dim][cyan]/wiki verify[/cyan][dim] in the REPL[/dim]")
    return 0


def cmd_rm(args) -> int:
    d = _need_project()
    if d is None:
        return 1
    from xlii import wiki as W

    if not W.page_exists(d, args.name):
        console.print(f"[red]no such wiki page: {args.name!r}[/red]")
        return 1
    if not args.yes:
        console.print(f"[yellow]wiki://{args.name}[/yellow]: pass --yes to confirm deletion")
        return 1
    W.delete_page(d, args.name)
    console.print(f"[green]removed[/green] wiki://{args.name}")
    return 0


def register(sub) -> None:
    p = sub.add_parser("wiki", help="Author the current project's semantic-memory wiki (list/show/new/rm/verify).")
    w = p.add_subparsers(dest="wiki_cmd", required=True)

    p_list = w.add_parser("list", help="List the project's wiki pages with trust markers.")
    p_list.set_defaults(func=cmd_list)

    p_show = w.add_parser("show", help="Print a wiki page (with its sources).")
    p_show.add_argument("name", help="Page name")
    p_show.set_defaults(func=cmd_show)

    p_new = w.add_parser("new", help="Create a wiki page from the template (born unverified).")
    p_new.add_argument("name", help="Page name (letters/digits/._- only)")
    p_new.set_defaults(func=cmd_new)

    p_verify = w.add_parser("verify", help="Mark a page verified (manual promote; AI pass is /wiki verify in the REPL).")
    p_verify.add_argument("name", help="Page name")
    p_verify.set_defaults(func=cmd_verify)

    p_rm = w.add_parser("rm", help="Delete a wiki page.")
    p_rm.add_argument("name", help="Page name")
    p_rm.add_argument("--yes", "-y", action="store_true", help="Confirm the deletion")
    p_rm.set_defaults(func=cmd_rm)
