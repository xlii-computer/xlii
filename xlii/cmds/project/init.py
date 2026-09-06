"""Project init and new subcommands."""

from __future__ import annotations

import argparse
from pathlib import Path

from xlii.client import Clients, MissingCredentials
from xlii.config import GlobalConfig, ProjectConfig
from xlii.sync import init_project
from xlii.ui import console

from xlii.cmds.project._guards import _confirm_risky_init, _ensure_bind_persona
from xlii.cmds.project.lifecycle import cmd_sync


def cmd_init(args: argparse.Namespace) -> int:
    cfg = GlobalConfig.load()
    project_root = Path(args.path or ".").resolve()
    if not project_root.is_dir():
        console.print(f"[red]not a directory: {project_root}[/red]")
        return 1
    name = args.name or project_root.name
    existing = ProjectConfig.load(project_root)
    persona_id = getattr(args, "id", None)
    # Validate (+ auto-create) the bind persona BEFORE any init, so an invalid
    # --id fails fast (rc=1) without a half-done project or an unusable binding.
    if persona_id is not None and not _ensure_bind_persona(persona_id):
        return 1
    if existing and not args.force:
        kind_arg = getattr(args, "kind", None)
        if kind_arg and not persona_id:
            from xlii.config import normalize_project_kind, project_kind

            stamped = normalize_project_kind(kind_arg)
            if stamped is None:
                console.print(f"[red]unknown kind {kind_arg!r}[/red]")
                return 1
            existing.kind = stamped
            existing.save()
            console.print(
                f"[green]✓[/green] [bold]{existing.name}[/bold] kind → "
                f"[cyan]{project_kind(existing)}[/cyan]"
            )
            return 0
        if persona_id:
            # Re-point only the chat-persona preference — no destructive reinit.
            existing.bound_persona = persona_id
            existing.save()
            console.print(
                f"[green]✓[/green] [bold]{existing.name}[/bold]'s chat persona → "
                f"[magenta]{persona_id}[/magenta] "
                "[dim](bare /chat opens it; code memory stays project-local)[/dim]"
            )
            return 0
        console.print(
            f"[yellow]project already initialized[/yellow] "
            f"(name={existing.name}, collection={existing.collection_id or 'local-only'})"
        )
        return 0

    # Pre-flight guard: bulk-uploading a huge or sensitive tree (e.g. `xlii init`
    # in ~) is almost never intended. Only fires when we'd actually upload — i.e.
    # non-local AND syncing. --local and --no-sync are themselves the recommended
    # safe escapes, so users who picked them aren't nagged. Only --yes skips it
    # outright; --force is about reinitializing, not "yes, upload everything".
    if not args.local and args.sync and not getattr(args, "yes", False):
        if not _confirm_risky_init(project_root):
            console.print("[yellow]init cancelled.[/yellow]")
            return 1

    # Local mode skips Collection provisioning, so no clients are needed for init.
    clients = None
    if not args.local:
        try:
            clients = Clients.from_config(cfg)
        except MissingCredentials as e:
            console.print(f"[red]{e}[/red]")
            return 1

    kind_arg = getattr(args, "kind", None)
    project = init_project(
        clients,
        project_root,
        name=name,
        existing_collection_id=args.collection_id,
        local_only=args.local,
        kind=kind_arg,
    )

    from xlii.config import project_kind as _project_kind

    kind_note = _project_kind(project)
    if args.local:
        console.print(
            f"[green]✓[/green] initialized [bold]{project.name}[/bold] "
            f"[dim]({kind_note} · local mode — no Collection)[/dim]"
        )
    else:
        console.print(
            f"[green]✓[/green] initialized [bold]{project.name}[/bold] → "
            f"collection {project.collection_id}"
        )

    # Record the project's chat-persona preference: the persona that bare `/chat`
    # opens here. Named `--id` (validated + created above), else KEEP an existing
    # preference (so `xlii init --force` without `--id` doesn't silently reset a
    # custom persona back to default), else the shipped iXaac companion. This is a
    # CHAT default ONLY — code never reads it; code's conversational memory stays
    # project-local, isolated from every chat persona.
    from xlii.persona import DEFAULT_PERSONA_ID, ensure_default_persona
    prior_pref = existing.bound_persona if existing else None
    bind_to = persona_id or prior_pref or DEFAULT_PERSONA_ID
    if bind_to == DEFAULT_PERSONA_ID:
        ensure_default_persona()
    project.bound_persona = bind_to
    project.save()

    from xlii.project_rules import scaffold_rules

    for note in scaffold_rules(project_root, project.xli_dir):
        console.print(f"  [dim]{note}[/dim]")

    console.print(
        f"  [dim]chat persona: [magenta]{bind_to}[/magenta] "
        "(bare /chat opens it; set another with [/dim][cyan]xlii init --id <name>[/cyan][dim])[/dim]"
    )

    if args.snapshot:
        from xlii.sync import write_file_index
        with console.status("[cyan]indexing files… 0[/cyan]") as status:
            def progress(n: int, last: str) -> None:
                # Truncate last path so the status line doesn't wrap weirdly.
                short = last if len(last) <= 60 else "…" + last[-59:]
                status.update(f"[cyan]indexing files… {n}[/cyan]  [dim]{short}[/dim]")
            count = write_file_index(project, cfg, on_progress=progress)
        console.print(
            f"  [dim]index: .xlii/index.txt — {count} files cached[/dim]"
        )

    # Git nudge: the autonomous loop, /verify and /peer all review work through
    # `git diff`. With no repo they see "zero change" and silently can't function.
    # A repo already here is almost certainly a real/cloned project — say nothing;
    # only nudge when there's none.
    from xlii.git_status import is_git_repo
    if not is_git_repo(project_root):
        console.print(
            "\n[yellow]tip:[/yellow] this folder isn't a git repo. Run "
            "[cyan]git init[/cyan] so [cyan]/loop[/cyan], [cyan]/verify[/cyan] and "
            "[cyan]/peer[/cyan] can review changes by diff "
            "[dim](without git they see no changes and stall on \"zero change\").[/dim]"
        )

    pool_size = len(cfg.key_pairs())
    if not args.local and pool_size <= 1 and cfg.management_api_key:
        console.print(
            f"\n[yellow]tip:[/yellow] you have only {pool_size} chat key in the pool. "
            "Run [cyan]xlii bootstrap[/cyan] to auto-provision worker keys for parallel "
            "swarm investigation."
        )
    if args.sync and not args.local:
        return cmd_sync(argparse.Namespace(path=str(project_root), dry_run=False))
    return 0


def cmd_new(args: argparse.Namespace) -> int:
    """Create a new project directory and initialize it."""
    name = args.name
    try:
        from xlii.tasks import TaskParseError, claim_folder_name

        name = claim_folder_name(name)
    except TaskParseError:
        console.print("[red]need a simple name (letters, digits, . _ -)[/red]")
        return 1
    base = Path(args.path or ".").resolve()
    project_root = base / name
    if project_root.exists():
        console.print(f"[red]already exists: {project_root}[/red]")
        return 1
    project_root.mkdir(parents=True)
    console.print(f"[green]✓[/green] created {project_root}")
    return cmd_init(
        argparse.Namespace(
            path=str(project_root),
            name=name,
            collection_id=None,
            sync=not bool(getattr(args, "local", False)),
            force=False,
            local=bool(getattr(args, "local", False)),
            snapshot=False,
            yes=True,  # freshly created empty dir — nothing to warn about
            kind=getattr(args, "kind", None),
        )
    )
