"""``xlii skin`` — list, lint, and install face skin packs.

Packs are folders of CSS + images (the markdown-plugin move, for pixels).
``list`` shows compiled built-ins plus discovered packs; ``check`` lints a
directory (selector scope, absolute ``/skins/<name>/`` urls, size/extension);
``install`` copies a local directory into ``~/.config/xlii/skins/`` — no
network fetch. Authoring: ``docs/selfwiki/skin-packs.md``.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from xlii.ui import console


def cmd_skin_list(_args: argparse.Namespace) -> int:
    from xlii.skin_packs import catalog_entries, discover_packs

    for row in catalog_entries():
        kind = row.get("kind") or "builtin"
        extra = f"  [dim]{row.get('css')}[/dim]" if row.get("css") else ""
        console.print(
            f"[cyan]{row['id']}[/cyan]  {row['label']}  [dim]{row.get('blurb', '')}[/dim]  "
            f"[dim]{kind}[/dim]{extra}"
        )
    packs = discover_packs()
    if not any(p.origin == "user" for p in packs):
        console.print(
            "[dim]user packs: ~/.config/xlii/skins/<name>/  ·  "
            "xlii skin install <dir>  ·  selfwiki skin-packs[/dim]"
        )
    return 0


def cmd_skin_check(args: argparse.Namespace) -> int:
    from xlii.skin_packs import check_pack, find_pack, parse_pack_skin_id

    target = Path(args.path).expanduser()
    if not target.is_dir():
        name = parse_pack_skin_id(args.path) or args.path.strip().lower()
        pack = find_pack(name)
        if pack is None:
            console.print(f"[red]not a pack directory or known pack:[/red] {args.path}")
            return 1
        target = pack.root
    errors = check_pack(target)
    if errors:
        console.print(f"[red]✗[/red] {target}")
        for err in errors:
            console.print(f"  {err}")
        return 1
    console.print(f"[green]✓[/green] {target.name}  [dim]{target}[/dim]")
    return 0


def cmd_skin_install(args: argparse.Namespace) -> int:
    from xlii.skin_packs import install_pack

    ok, msg = install_pack(Path(args.path))
    if not ok:
        console.print(f"[red]✗[/red] {msg}")
        return 1
    console.print(f"[green]✓[/green] {msg}")
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "skin",
        help="List, lint, or install face skin packs (graphical chrome for the desktop face).",
    )
    s = p.add_subparsers(dest="skin_cmd", required=True)

    p_list = s.add_parser("list", help="List compiled skins and discovered packs.")
    p_list.set_defaults(func=cmd_skin_list)

    p_check = s.add_parser(
        "check",
        help="Lint a pack directory (scope, absolute urls, extension/size).",
    )
    p_check.add_argument("path", help="Pack directory, folder name, or pack:<name>")
    p_check.set_defaults(func=cmd_skin_check)

    p_install = s.add_parser(
        "install",
        help="Copy a local pack directory into ~/.config/xlii/skins/ (no network fetch).",
    )
    p_install.add_argument("path", help="Path to a pack directory")
    p_install.set_defaults(func=cmd_skin_install)
