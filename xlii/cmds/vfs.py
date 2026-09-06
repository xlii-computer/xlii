"""``xlii ls / cat / cp / mv / rm / mkdir / stat`` — a small file manager over any address.

Client #1 of :mod:`xlii.addressing`: prove the uniform addressing + VFS surface end to end
from real commands (no panes required). These operate on the **address space** (``file``,
``project``, ``conv`` …) — they do **not** shadow your shell: ``xlii ls`` is the ``xlii``
subcommand ``ls``, while ``/bin/ls``, ``cp``, ``apt``, ``which`` and every other binary are
untouched. Browse/read works on ``file``/``project``/``conv``; writes (``cp``/``mv``/``rm``/
``mkdir``) work on ``file``/``project`` (``conv`` is read-only). Destructive ops refuse
without an explicit ``--force``/``--yes``. Bare paths work via the bare-token rule.
"""

from __future__ import annotations

import sys
from pathlib import Path

from xlii.addressing import (
    Address,
    providers,
    supports_vfs,
    supports_write,
    vfs_delete,
    vfs_exists,
    vfs_list,
    vfs_mkdir,
    vfs_read,
    vfs_stat,
    vfs_write,
)
from xlii.ui import console


def _canonical_local_vfs_path(addr: Address) -> Path | None:
    """Return the provider's concrete local path when this VFS address has one."""
    provider = providers().get(addr.scheme)
    try:
        if addr.scheme == "file" and hasattr(provider, "_path"):
            return Path(provider._path(addr)).resolve()
        if addr.scheme == "project" and hasattr(provider, "_target"):
            _root, target = provider._target(addr)
            return Path(target).resolve()
    except (OSError, ValueError, FileNotFoundError):
        return None
    return None


def _same_vfs_target(src: Address, dst: Address) -> bool:
    src_path = _canonical_local_vfs_path(src)
    dst_path = _canonical_local_vfs_path(dst)
    if src_path is not None and dst_path is not None:
        return src_path == dst_path
    return src.scheme == dst.scheme and src.target == dst.target


def cmd_ls(args) -> int:
    addr = Address.parse(args.address)
    if not addr.scheme:
        console.print(f"[red]{args.address}[/red]: no scheme — use scheme://target or a path")
        return 1
    if not supports_vfs(addr.scheme):
        console.print(f"[yellow]{addr.scheme}://[/yellow] is not browseable yet")
        return 1
    try:
        nodes = vfs_list(addr)
    except (NotImplementedError, OSError) as e:
        console.print(f"[red]{args.address}[/red]: {e}")
        return 1
    if not nodes:
        console.print("[dim](empty)[/dim]")
        return 0
    for n in nodes:
        suffix = "/" if n.kind == "container" else ""
        size = "" if n.size is None else f"  [dim]{n.size}[/dim]"
        extra = n.extra or {}
        badge = extra.get("badge") or ""
        tag = f"  [dim]{badge}[/dim]" if badge else ""
        console.print(f"{n.name}{suffix}{size}{tag}")
    return 0


def cmd_cat(args) -> int:
    addr = Address.parse(args.address)
    if not supports_vfs(addr.scheme):
        console.print(f"[red]{args.address}[/red]: not readable ({addr.scheme or 'no'} scheme)")
        return 1
    try:
        data = vfs_read(addr)
    except (NotImplementedError, OSError) as e:
        console.print(f"[red]{args.address}[/red]: {e}")
        return 1
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()
    return 0


def cmd_cp(args) -> int:
    src = Address.parse(args.src)
    dst = Address.parse(args.dst)
    if not supports_vfs(src.scheme):
        console.print(f"[red]{args.src}[/red]: not readable ({src.scheme or 'no'} scheme)")
        return 1
    if not supports_write(dst.scheme):
        console.print(f"[red]{args.dst}[/red]: not writable ({dst.scheme or 'no'} scheme)")
        return 1
    if vfs_exists(dst) and not args.force:
        console.print(f"[yellow]{args.dst}[/yellow] exists — pass --force to overwrite")
        return 1
    try:
        data = vfs_read(src)
        vfs_write(dst, data)
    except (NotImplementedError, OSError) as e:
        console.print(f"[red]error[/red]: {e}")
        return 1
    console.print(f"[green]copied[/green] {args.src} → {args.dst} [dim]({len(data)} bytes)[/dim]")
    return 0


def cmd_mv(args) -> int:
    src = Address.parse(args.src)
    dst = Address.parse(args.dst)
    if not supports_write(src.scheme):
        console.print(f"[red]{args.src}[/red]: can't move from a read-only scheme — use cp")
        return 1
    if not supports_write(dst.scheme):
        console.print(f"[red]{args.dst}[/red]: not writable ({dst.scheme or 'no'} scheme)")
        return 1
    if not vfs_exists(src):
        console.print(f"[red]{args.src}[/red]: no such address")
        return 1
    if vfs_stat(src).kind == "container":
        console.print(f"[red]{args.src}[/red]: moving directories isn't supported yet (use cp, then rm -r)")
        return 1
    if vfs_exists(dst) and not args.force:
        console.print(f"[yellow]{args.dst}[/yellow] exists — pass --force to overwrite")
        return 1
    if _same_vfs_target(src, dst):
        console.print(f"[green]moved[/green] {args.src} → {args.dst} [dim](same address)[/dim]")
        return 0
    try:
        data = vfs_read(src)
        vfs_write(dst, data)
        vfs_delete(src)
    except (NotImplementedError, OSError) as e:
        console.print(f"[red]error[/red]: {e}")
        return 1
    console.print(f"[green]moved[/green] {args.src} → {args.dst}")
    return 0


def cmd_rm(args) -> int:
    addr = Address.parse(args.address)
    if not supports_write(addr.scheme):
        console.print(f"[red]{args.address}[/red]: not removable ({addr.scheme or 'no'} scheme)")
        return 1
    if not vfs_exists(addr):
        console.print(f"[red]{args.address}[/red]: no such address")
        return 1
    if not args.yes:
        console.print(f"[yellow]{args.address}[/yellow]: pass --yes to confirm deletion (-r for non-empty dirs)")
        return 1
    try:
        vfs_delete(addr, recursive=args.recursive)
    except OSError as e:
        console.print(f"[red]{args.address}[/red]: {e} (use -r for non-empty dirs)")
        return 1
    console.print(f"[green]removed[/green] {args.address}")
    return 0


def cmd_mkdir(args) -> int:
    addr = Address.parse(args.address)
    if not supports_write(addr.scheme):
        console.print(f"[red]{args.address}[/red]: not writable ({addr.scheme or 'no'} scheme)")
        return 1
    try:
        vfs_mkdir(addr)
    except (NotImplementedError, OSError) as e:
        console.print(f"[red]{args.address}[/red]: {e}")
        return 1
    console.print(f"[green]created[/green] {args.address}/")
    return 0


def cmd_stat(args) -> int:
    addr = Address.parse(args.address)
    if not supports_vfs(addr.scheme):
        console.print(f"[red]{args.address}[/red]: no info ({addr.scheme or 'no'} scheme)")
        return 1
    if not vfs_exists(addr):
        console.print(f"[red]{args.address}[/red]: no such address")
        return 1
    n = vfs_stat(addr)
    size = "-" if n.size is None else str(n.size)
    console.print(f"{n.address}\n  kind: {n.kind}\n  size: {size}")
    return 0


def register(sub) -> None:
    p_ls = sub.add_parser(
        "ls",
        help="List the contents of an address (browse the VFS). e.g. `xlii ls .` or `xlii ls conv://.`",
    )
    p_ls.add_argument("address", help="An address (scheme://target) or a filesystem path")
    p_ls.set_defaults(func=cmd_ls)

    p_cat = sub.add_parser(
        "cat",
        help="Print the bytes of an address (read a VFS leaf). e.g. `xlii cat ./README.md`",
    )
    p_cat.add_argument("address", help="An address (scheme://target) or a filesystem path")
    p_cat.set_defaults(func=cmd_cat)

    p_cp = sub.add_parser(
        "cp",
        help="Copy any readable address to any writable one (cross-root). "
        "e.g. `xlii cp conv://./turn.md ./backup.md`",
    )
    p_cp.add_argument("src", help="Source address (scheme://target or a path)")
    p_cp.add_argument("dst", help="Destination address (scheme://target or a path)")
    p_cp.add_argument("--force", "-f", action="store_true", help="Overwrite the destination if it exists")
    p_cp.set_defaults(func=cmd_cp)

    p_mv = sub.add_parser("mv", help="Move/rename a file address (read+write+delete). Files only for now.")
    p_mv.add_argument("src", help="Source address (must be on a writable scheme)")
    p_mv.add_argument("dst", help="Destination address (writable scheme)")
    p_mv.add_argument("--force", "-f", action="store_true", help="Overwrite the destination if it exists")
    p_mv.set_defaults(func=cmd_mv)

    p_rm = sub.add_parser("rm", help="Remove an address (a leaf, or an empty dir; -r for non-empty).")
    p_rm.add_argument("address", help="An address on a writable scheme")
    p_rm.add_argument("--yes", "-y", action="store_true", help="Confirm the deletion")
    p_rm.add_argument("--recursive", "-r", action="store_true", help="Recursively delete a non-empty directory")
    p_rm.set_defaults(func=cmd_rm)

    p_mkdir = sub.add_parser("mkdir", help="Create a directory at an address (parents as needed).")
    p_mkdir.add_argument("address", help="An address on a writable scheme")
    p_mkdir.set_defaults(func=cmd_mkdir)

    p_stat = sub.add_parser("stat", help="Show an address's kind/size. e.g. `xlii stat conv://./turn.md`")
    p_stat.add_argument("address", help="An address (scheme://target) or a filesystem path")
    p_stat.set_defaults(func=cmd_stat)
