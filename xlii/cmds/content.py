"""Curated-content subcommands: plugins, docs, and export/import.

Moved out of the former monolithic cli.py (see proposals/done/cli-refactor.md).
"""

from __future__ import annotations

import argparse
from pathlib import Path


from xlii.ui import confirm, console


def _plugin_lint() -> int:
    """Render xlii.plugin.lint_plugins() findings; exit 1 on any failure —
    wired into CI over the stock pack."""
    from xlii.plugin import lint_plugins

    failures = 0
    for f in lint_plugins():
        if f.problems:
            failures += 1
            console.print(f"[red]✗ {f.origin}/{f.plugin_id}[/red]")
            for prob in f.problems:
                console.print(f"    [red]· {prob}[/red]")
        else:
            console.print(f"[green]✓[/green] {f.origin}/{f.plugin_id}")

    if failures:
        console.print(f"[red]{failures} plugin(s) failed lint[/red]")
        return 1
    console.print("[green]all plugins pass lint[/green]")
    return 0


def cmd_plugin(args: argparse.Namespace) -> int:
    """Manage plugins at ~/.config/xlii/plugins/<id>.md.

    Plugins are markdown files describing external APIs. Subscribe a plugin
    to a project with `/lib subscribe <id>` from inside the REPL — the agent
    only sees subscribed plugins via plugin_search.
    """
    from xlii.plugin import (
        Plugin, create_plugin, delete_plugin,
        is_valid_id, list_plugins, open_in_editor as _open_in_editor,
    )

    if getattr(args, "lint", False):
        return _plugin_lint()

    if getattr(args, "install_stock", False):
        from xlii.plugin import install_stock_plugins
        installed, skipped = install_stock_plugins(force=getattr(args, "force", False))
        for pid in installed:
            console.print(f"[green]✓[/green] installed [cyan]{pid}[/cyan]")
        for pid in skipped:
            console.print(f"[dim]· kept existing {pid} (use --force to overwrite)[/dim]")
        if not installed and not skipped:
            console.print("[dim](no stock plugins bundled in this build)[/dim]")
        return 0

    if args.list:
        plugins = list_plugins()
        if not plugins:
            console.print(
                "[dim](no plugins yet — create one with [/dim]"
                "[cyan]xlii plugin --new <id>[/cyan][dim])[/dim]"
            )
            return 0
        console.print("[dim]id (use this with /lib subscribe) · risk · categories · description[/dim]")
        for p in plugins:
            try:
                meta = p.metadata()
            except OSError:
                meta = {}
            cats = ", ".join(meta.get("categories") or []) or "—"
            desc = meta.get("description") or "(no description)"
            risk = meta.get("risk", "low")
            risk_color = {"low": "green", "medium": "yellow", "high": "red"}.get(risk, "white")
            console.print(
                f"  [bold cyan]{p.id}[/bold cyan]  "
                f"[{risk_color}]{risk}[/{risk_color}]  "
                f"[dim]{cats}  ·  {desc}[/dim]"
            )
        return 0

    if args.show:
        p = Plugin(id=args.show)
        if not p.exists():
            console.print(f"[red]no such plugin: {args.show!r}[/red]")
            return 1
        console.print(p.read_raw())
        return 0

    if args.new:
        if not is_valid_id(args.new):
            console.print(f"[red]invalid plugin id: {args.new!r}[/red]")
            return 1
        p = Plugin(id=args.new)
        if p.exists():
            console.print(f"[yellow]plugin {args.new!r} already exists[/yellow] — use --edit instead")
            return 1
        create_plugin(args.new)
        console.print(f"[green]✓[/green] created plugin [bold]{args.new}[/bold] at {p.path}")
        console.print("[dim]opening $EDITOR — fill in the template, save, quit…[/dim]")
        _open_in_editor(p.path)
        console.print(
            "[dim]ready. From any project REPL, [/dim]"
            f"[cyan]/lib subscribe {args.new}[/cyan][dim] to make it available there.[/dim]"
        )
        return 0

    if args.edit:
        p = Plugin(id=args.edit)
        if not p.exists():
            console.print(f"[red]no such plugin: {args.edit!r}[/red]")
            return 1
        _open_in_editor(p.path)
        console.print("[dim]ready. Already-subscribed projects will use the new content on next turn.[/dim]")
        return 0

    if args.delete:
        p = Plugin(id=args.delete)
        if not p.exists():
            console.print(f"[red]no such plugin: {args.delete!r}[/red]")
            return 1
        if not args.yes:
            console.print(
                f"[yellow]about to delete plugin [bold]{args.delete}[/bold][/yellow]\n"
                f"  path: {p.path}"
            )
            if not confirm("delete? [y/N] "):
                console.print("[dim]aborted[/dim]")
                return 1
        delete_plugin(args.delete)
        console.print(
            f"[green]✓[/green] deleted plugin {args.delete!r} "
            "[dim](existing project subscriptions become orphan; cleanup on next sub list)[/dim]"
        )
        return 0

    # No flag → list.
    return cmd_plugin(argparse.Namespace(
        list=True, new=None, edit=None, delete=None, show=None, yes=False,
    ))


def cmd_doc(args: argparse.Namespace) -> int:
    """Manage reference docs at ~/.config/xlii/docs/<name>.md.

    Sub-routes off the action flags. Docs are markdown files inlined into
    the agent's system prompt when attached via /doc <name> in either REPL.
    """
    from xlii.doc import (
        Doc, create_doc, delete_doc, is_valid_name as _is_valid,
        list_docs, open_in_editor as _open_in_editor,
    )

    if args.list:
        docs = list_docs()
        if not docs:
            console.print(
                "[dim](no docs yet — create one with [/dim][cyan]xlii doc --new <name>[/cyan][dim])[/dim]"
            )
            return 0
        console.print("[dim]name (use this with /doc) · size · first line[/dim]")
        for d in docs:
            console.print(
                f"  [bold cyan]{d.name}[/bold cyan]"
                f"  [dim]·  {d.size_bytes():,}b  ·  \"{d.first_line()}\"[/dim]"
            )
        return 0

    if args.new:
        name = args.new
        if not _is_valid(name):
            console.print(f"[red]invalid doc name: {name!r}[/red]")
            return 1
        d = Doc(name)
        if d.exists():
            console.print(
                f"[yellow]doc {name!r} already exists[/yellow] — use --edit instead"
            )
            return 1
        create_doc(name)
        console.print(f"[green]✓[/green] created doc [bold]{name}[/bold] at {d.path}")
        console.print("[dim]opening $EDITOR — save and quit when done…[/dim]")
        _open_in_editor(d.path)
        console.print(
            f"[dim]ready. In any REPL, run [/dim][cyan]/doc {name}[/cyan][dim] to attach it.[/dim]"
        )
        return 0

    if args.edit:
        d = Doc(args.edit)
        if not d.exists():
            console.print(f"[red]no such doc: {args.edit!r}[/red]")
            return 1
        _open_in_editor(d.path)
        console.print(
            f"[dim]ready. In a running session, [/dim][cyan]/doc --refresh {args.edit}[/cyan]"
            "[dim] to pull the edit into the inlined copy (already-running sessions hold the "
            "old text until refreshed).[/dim]"
        )
        return 0

    if args.delete:
        d = Doc(args.delete)
        if not d.exists():
            console.print(f"[red]no such doc: {args.delete!r}[/red]")
            return 1
        if not args.yes:
            console.print(
                f"[yellow]about to delete doc [bold]{args.delete}[/bold][/yellow]\n"
                f"  path: {d.path}"
            )
            if not confirm("delete? [y/N] "):
                console.print("[dim]aborted[/dim]")
                return 1
        delete_doc(args.delete)
        console.print(f"[green]✓[/green] deleted doc {args.delete!r}")
        return 0

    # No flag → default to listing.
    return cmd_doc(argparse.Namespace(list=True, new=None, edit=None, delete=None, yes=False))


def cmd_export(args: argparse.Namespace) -> int:
    """Serialize all user curation to a plain directory tree.

    Personas (prompts + memory turns), docs, plugins, and the project
    registry — everything that makes an xlii install *yours* — as files you
    own. Secrets are deliberately excluded: config.json (API keys) and the
    vault never leave the machine this way.
    """
    from xlii.curation import export_curation

    dest = Path(args.dest).resolve()
    try:
        counts = export_curation(dest)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    for name, n in sorted(counts.items()):
        console.print(f"  [green]✓[/green] {name}: {n} file(s)")
    if not counts:
        console.print("[dim](nothing to export yet — no personas/docs/plugins found)[/dim]")
    console.print(f"[green]exported to[/green] {dest}  [dim](secrets excluded by design)[/dim]")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    """Restore an `xlii export` tree. Existing files are kept unless --force."""
    from xlii.curation import import_curation

    src = Path(args.src).resolve()
    try:
        restored, skipped, registry_ref = import_curation(src, force=bool(args.force))
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    if registry_ref is not None:
        console.print(f"[dim]registry saved as {registry_ref.name} (paths belong to the old machine)[/dim]")
    console.print(
        f"[green]imported:[/green] {restored} file(s)"
        + (f"  [dim]({skipped} kept — use --force to overwrite)[/dim]" if skipped else "")
    )
    return 0


def register(sub) -> None:
        p_plugin = sub.add_parser(
            "plugin",
            help="Manage plugins (markdown API descriptors used via /lib + /get).",
        )
        p_plugin.add_argument("--new", metavar="ID", help="Create a new plugin from template; opens $EDITOR")
        p_plugin.add_argument("--list", action="store_true", help="List all installed plugins")
        p_plugin.add_argument("--show", metavar="ID", help="Print a plugin's full markdown")
        p_plugin.add_argument("--edit", metavar="ID", help="Edit a plugin in $EDITOR")
        p_plugin.add_argument("--delete", metavar="ID", help="Delete a plugin")
        p_plugin.add_argument("--yes", action="store_true", help="Skip confirmation for --delete")
        p_plugin.add_argument("--lint", action="store_true",
                              help="Validate installed + stock plugin frontmatter and manifests")
        p_plugin.add_argument("--install-stock", action="store_true",
                              help="Install the bundled stock plugins (skips ones you've edited)")
        p_plugin.add_argument("--force", action="store_true",
                              help="With --install-stock: overwrite existing stock plugins")
        p_plugin.set_defaults(func=cmd_plugin)

        p_doc = sub.add_parser(
            "doc",
            help="Manage reference docs (markdown files attached via /doc in any REPL).",
        )
        p_doc.add_argument("--new", metavar="NAME", help="Create a new doc; opens $EDITOR")
        p_doc.add_argument("--list", action="store_true", help="List all docs")
        p_doc.add_argument("--edit", metavar="NAME", help="Open an existing doc in $EDITOR")
        p_doc.add_argument("--delete", metavar="NAME", help="Delete a doc")
        p_doc.add_argument("--yes", action="store_true", help="Skip confirmation prompt for --delete")
        p_doc.set_defaults(func=cmd_doc)

        p_export = sub.add_parser(
            "export",
            help="Serialize personas, docs, plugins + registry to a directory you own (secrets excluded).",
            description=(
                "Serialize everything that makes an install yours — personas (prompts + "
                "memory turns), docs, plugins, and the project registry — to a plain "
                "directory tree you own. Secrets are excluded: config.json API keys and "
                "the credential vault never leave the machine. The destination must be "
                "empty or new. Round-trips with `xlii import`; use it to back up your "
                "curation or move it to another machine. Example: xlii export ~/xlii-backup"
            ),
        )
        p_export.add_argument("dest", help="Destination directory (must be empty or new)")
        p_export.set_defaults(func=cmd_export)

        p_import = sub.add_parser(
            "import",
            help="Restore an `xlii export` tree (keeps existing files unless --force).",
            description=(
                "Restore an `xlii export` tree onto this machine: personas, memory turns, "
                "docs, and plugins. Existing files are kept per-file unless --force "
                "overwrites them. Secrets are not part of an export, so re-add API keys "
                "with `xlii config` or `xlii setup` afterwards. Example: xlii import ~/xlii-backup"
            ),
        )
        p_import.add_argument("src", help="Path to an export directory")
        p_import.add_argument("--force", action="store_true", help="Overwrite existing personas/docs/plugins")
        p_import.set_defaults(func=cmd_import)
