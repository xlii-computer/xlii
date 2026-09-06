"""Project removal subcommands."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from xlii.client import Clients, MissingCredentials
from xlii.config import GlobalConfig, ProjectConfig
from xlii.addressing import resolve
from xlii.project_resolver import resolve_registered_project
from xlii.registry import Registry
from xlii.ui import confirm, console

from xlii.cmds.project._collections import (
    _count_collection_docs,
    _list_cloud_collections,
    _orphan_journal_collections,
)
from xlii.cmds.project.lifecycle import _print_project_resolution


# A whole-Collection delete above this many documents is called out explicitly in
# the confirmation, so a fat Collection can't vanish without the user seeing its
# size (the >N-doc delete guard, mirroring sync.DELETE_GUARD_THRESHOLD).
_FAT_COLLECTION_DOCS = 10


def _resolve_rm_target(name: str) -> Optional[ProjectConfig]:
    """Resolve `xlii project rm <name|.|path>` to a loaded ProjectConfig.

    returns None when nothing usable resolves.

    Destructive-op safety (#132): for a *bare* name (not ``.``/path-like), a
    registered project wins over a same-named cwd subdirectory — silently
    targeting ``./<name>`` when a registered ``<name>`` exists could delete the
    wrong project, and an ambiguous name never guesses. Path/cwd disambiguation
    still lives once in the ``project://`` provider (``xlii.addressing``)."""
    tok = (name or ".").strip()
    if tok not in (".", "") and not ("/" in tok or tok.startswith((".", "~"))):
        res = resolve_registered_project(tok)
        if res.ok and res.project is not None:
            return res.project
        if res.matches:  # ambiguous — refuse to guess which project to delete
            _print_project_resolution(res)
            return None
        # no registry match → fall through to the provider (a bare dir name, etc.)
    r = resolve(f"project://{tok}")
    if r.handle is not None:  # a loaded ProjectConfig
        return r.handle
    if r.detail is not None:  # registry-name branch → reuse the existing resolution printer
        _print_project_resolution(r.detail)
    else:  # cwd/path branch that wasn't an initialized project
        console.print(f"[red]not an xlii project: {r.path}[/red]")
    return None


def _registry_remove_by_path(registry: Registry, root: Path) -> bool:
    """Drop the registry entry matching this exact path. Safe for local-only
    projects whose collection_id is the empty sentinel (registry.remove("")
    would otherwise nuke every local-only entry at once)."""
    target = str(Path(root).resolve())
    before = len(registry.entries)
    registry.entries = [e for e in registry.entries if e.path != target]
    return len(registry.entries) != before


def _fmt_docs(n: Optional[int]) -> str:
    if n is None:
        return ""
    if n == 0:
        return " [dim](empty)[/dim]"
    return f" [yellow]({n} docs)[/yellow]"


def _run_project_rm(
    clients,
    project: ProjectConfig,
    *,
    keep_local: bool,
    local_only: bool,
    dry_run: bool,
    assume_yes: bool,
    console,
) -> int:
    """Tear a project down: its xAI Collection(s) + registry entry + local
    `.xlii/` tree — and NOTHING else (your source files are never touched).

    Reuses gc's primitives (`collections.delete`, `registry.remove`, the cloud
    listing) and adds the Shadow-aware orphan journal sweep (OQ3). `clients` may
    be None for a `--local-only` teardown (no cloud ops)."""
    registry = Registry.load()
    name, root, xli_dir = project.name, project.project_root, project.xli_dir
    touch_cloud = not local_only and clients is not None

    # 1) This project's Collections (main + journal), with doc counts for the guard.
    coll_targets: list[tuple[str, str, Optional[int]]] = []  # (cid, label, ndocs)
    if touch_cloud:
        if project.collection_id:
            coll_targets.append(
                (project.collection_id, "collection", _count_collection_docs(clients, project.collection_id))
            )
        if project.journal_collection_id:
            coll_targets.append(
                (project.journal_collection_id, "journal collection",
                 _count_collection_docs(clients, project.journal_collection_id))
            )

    # 2) Orphan journal sweep (OQ3) — only when we're touching the cloud.
    orphans: list[tuple[str, str]] = []
    if touch_cloud:
        try:
            with console.status("[cyan]listing collections…[/cyan]"):
                cloud = _list_cloud_collections(clients)
            exclude = {c for c in (project.collection_id, project.journal_collection_id) if c}
            orphans = _orphan_journal_collections(
                cloud, registry, exclude=exclude, project_name=name,
            )
        except Exception as e:
            console.print(f"[yellow]orphan journal sweep skipped ({e})[/yellow]")

    remove_local = not keep_local and xli_dir.exists()
    will_unbind = bool(project.bound_persona)

    # --- present the plan (mirrors `gc --dry-run`) ---
    console.print(f"[bold]project rm:[/bold] {name}  [dim]{root}[/dim]")
    console.print(
        "[dim]source files are never touched — only .xlii/, the cloud Collection(s), "
        "and the registry entry.[/dim]"
    )
    for cid, label, ndocs in coll_targets:
        console.print(f"  [red]- {label}[/red] [dim]{cid}[/dim]{_fmt_docs(ndocs)}")
    for cid, cname in orphans:
        console.print(f"  [red]- orphan journal[/red] {cname}  [dim]{cid}[/dim]")
    if local_only and (project.collection_id or project.journal_collection_id):
        console.print("  [dim]· cloud Collection(s) left in place (--local-only)[/dim]")
    if remove_local:
        console.print(f"  [red]- local[/red] {xli_dir}")
    elif keep_local:
        console.print(f"  [dim]· keep local {xli_dir} (--keep-local)[/dim]")
    if will_unbind:
        console.print(
            f"  [dim]· unbind persona [magenta]{project.bound_persona}[/magenta] "
            "(the persona itself is global — never deleted)[/dim]"
        )

    if dry_run:
        console.print("[yellow]dry-run:[/yellow] nothing was deleted.")
        return 0

    if not assume_yes:
        for _cid, label, ndocs in coll_targets:
            if ndocs and ndocs > _FAT_COLLECTION_DOCS:
                console.print(f"[yellow]⚠ {label} holds {ndocs} documents[/yellow]")
        if not confirm(f"remove project {name}? [y/N] "):
            console.print("[yellow]aborted — nothing deleted.[/yellow]")
            return 1

    # --- apply ---
    from xlii.storage_backend import CollectionsBackend

    collection_delete_failed = False
    for cid, label, _ in coll_targets:
        try:
            CollectionsBackend.delete_collection(clients, cid)
            console.print(f"  [green]✓[/green] deleted {label} [dim]{cid}[/dim]")
        except Exception as e:  # noqa: BLE001 — surface, keep going (gc's pattern)
            collection_delete_failed = True
            console.print(f"  [red]✗[/red] {label} {cid}: {e}")
    if collection_delete_failed:
        console.print(
            "  [yellow]cloud Collection deletion failed; kept local .xlii/ and registry entry[/yellow]"
        )
        console.print(
            "  [dim]retry after fixing the cloud error, or use --local-only if you intentionally want local teardown.[/dim]"
        )
        return 1
    for cid, cname in orphans:
        try:
            CollectionsBackend.delete_collection(clients, cid)
            registry.remove(cid)
            console.print(f"  [green]✓[/green] swept orphan journal {cname}")
        except Exception as e:  # noqa: BLE001
            console.print(f"  [red]✗[/red] orphan {cname}: {e}")

    # Registry: by collection id (gc's primitive) AND by path (covers local-only).
    if project.collection_id:
        registry.remove(project.collection_id)
    if project.journal_collection_id:
        registry.remove(project.journal_collection_id)
    _registry_remove_by_path(registry, root)
    registry.save()

    # Persona: unbind only — personas are global/shared, never deleted. Persisting
    # the unbind is only meaningful when we keep the .xlii tree (else it's gone).
    if will_unbind and not remove_local:
        project.bound_persona = None
        try:
            project.save()
        except OSError as e:
            console.print(f"  [yellow]⚠[/yellow] could not persist persona unbind: {e}")

    if remove_local:
        import shutil
        shutil.rmtree(xli_dir, ignore_errors=True)
        console.print(f"  [green]✓[/green] removed {xli_dir}")

    console.print(
        f"[green]✓[/green] project [bold]{name}[/bold] removed "
        "[dim](your source files were not touched)[/dim]"
    )
    return 0


def cmd_project_rm(args: argparse.Namespace) -> int:
    keep_local = getattr(args, "keep_local", False)
    local_only = getattr(args, "local_only", False)
    if keep_local and local_only:
        console.print("[red]--keep-local and --local-only are mutually exclusive[/red]")
        return 1
    project = _resolve_rm_target(getattr(args, "name", None) or ".")
    if project is None:
        return 1
    clients = None
    if not local_only:
        cfg = GlobalConfig.load()
        try:
            clients = Clients.from_config(cfg)
        except MissingCredentials as e:
            console.print(f"[red]{e}[/red]")
            console.print(
                "[dim](use [/dim][cyan]--local-only[/cyan][dim] to remove the local "
                ".xlii/ + registry entry without touching the cloud)[/dim]"
            )
            return 1
    return _run_project_rm(
        clients, project,
        keep_local=keep_local, local_only=local_only,
        dry_run=getattr(args, "dry_run", False), assume_yes=getattr(args, "yes", False),
        console=console,
    )
