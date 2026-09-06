"""Argparse registration for project subcommands."""

from __future__ import annotations

from xlii.cmds.project.gc import cmd_gc
from xlii.cmds.project.init import cmd_init, cmd_new
from xlii.cmds.project.lifecycle import cmd_find, cmd_projects, cmd_status, cmd_sync
from xlii.cmds.project.rm import cmd_project_rm
from xlii.cmds.project.scratch import cmd_scratch


def register(sub) -> None:
        p_init = sub.add_parser("init", help="Initialize an xlii project. Positional NAME labels the collection.")
        p_init.add_argument("name", nargs="?", help="Project name (default: cwd basename)")
        p_init.add_argument("--path", help="Project directory (default: cwd)")
        p_init.add_argument("--collection-id", help="Reuse an existing collection instead of creating one")
        p_init.add_argument("--id", metavar="PERSONA",
                            help="Bind this project to a persona: code sessions inherit its memory + loadout "
                                 "(rebinds in place if the project already exists).")
        p_init.add_argument("--no-sync", dest="sync", action="store_false", help="Skip the initial sync")
        p_init.add_argument("--yes", "-y", action="store_true",
                            help="Skip the sensitive/large-directory upload confirmation prompt.")
        p_init.add_argument("--force", action="store_true", help="Reinitialize even if project exists")
        p_init.add_argument("--local", action="store_true",
                            help="Local-only mode: no Collection, no upload, no sync. search_project disabled.")
        p_init.add_argument("--snapshot", action="store_true",
                            help="Cache a paths+sizes index at .xlii/index.txt for fast structural search.")
        p_init.add_argument(
            "--kind",
            choices=("code", "collection"),
            default=None,
            help="Folder kind: code (lab / repo) or collection (real-folder pile). "
                 "Missing kind on an existing tree stays code; named scratch is a collection.",
        )
        p_init.set_defaults(func=cmd_init, sync=True)

        p_new = sub.add_parser("new", help="Create a new project directory and initialize it.")
        p_new.add_argument("name", help="Project name (also the new directory name)")
        p_new.add_argument("--path", help="Parent directory (default: cwd)")
        p_new.add_argument("--local", action="store_true",
                            help="Local-only mode: no Collection, no upload, no sync.")
        p_new.add_argument(
            "--kind",
            choices=("code", "collection"),
            default=None,
            help="Folder kind: code (lab / repo) or collection (real-folder pile).",
        )
        p_new.set_defaults(func=cmd_new)

        p_projects = sub.add_parser("projects", help="List all registered xlii projects (filter by substring).")
        p_projects.add_argument(
            "filter",
            nargs="?",
            help="Optional substring filter (matches name/path); use `find NAME` for one project",
        )
        p_projects.add_argument("query", nargs="?", help="Project query for `xlii projects find NAME`")
        p_projects.add_argument("--open", action="store_true", help="With `find`, launch the matched project")
        p_projects.add_argument("--yolo", action="store_true", help="With `--open`, auto-approve bash commands")
        p_projects.add_argument("--no-sync", action="store_true", help="With `--open`, skip sync for this session")
        p_projects.set_defaults(func=cmd_projects)

        p_find = sub.add_parser("find", help="Find one registered project by exact name or unique substring.")
        p_find.add_argument("query", help="Project name or substring")
        p_find.add_argument("--open", action="store_true", help="Launch the matched project")
        p_find.add_argument("--yolo", action="store_true", help="With `--open`, auto-approve bash commands")
        p_find.add_argument("--no-sync", action="store_true", help="With `--open`, skip sync for this session")
        p_find.set_defaults(func=cmd_find)

        p_sync = sub.add_parser("sync", help="Push local changes to the project's collection.")
        p_sync.add_argument("path", nargs="?", default=".")
        p_sync.add_argument("--dry-run", action="store_true")
        p_sync.set_defaults(func=cmd_sync)

        p_status = sub.add_parser("status", help="Show config + project state.")
        p_status.add_argument("path", nargs="?", default=".")
        p_status.set_defaults(func=cmd_status)

        p_gc = sub.add_parser(
            "gc",
            help="Find and delete orphan xAI collections.",
            description=(
                "Find xAI Collections with no live project behind them — tracked-dead "
                "(still in the registry but the project was deleted on disk) or "
                "untracked-cloud (an xli*/xlii* collection with no registry entry) — and "
                "delete them so you stop paying for orphaned storage. Destructive: preview "
                "with --dry-run, delete all with --yes, or answer the interactive "
                "all / dead-path-only prompt."
            ),
        )
        p_gc.add_argument("--dry-run", action="store_true", help="Show what would be deleted, take no action")
        p_gc.add_argument("--yes", action="store_true", help="Delete all orphans without prompting")
        p_gc.set_defaults(func=cmd_gc)

        p_project = sub.add_parser(
            "project",
            help="Per-project lifecycle: remove a project (Collection + registry + local .xlii/).",
        )
        p_project_sub = p_project.add_subparsers(dest="project_action", required=True)
        p_project_rm = p_project_sub.add_parser(
            "rm",
            help="Remove a project: delete its Collection(s) + registry entry + local .xlii/ "
                 "(NEVER your source files). Sweeps orphan journal Collections too.",
        )
        p_project_rm.add_argument(
            "name", nargs="?", default=".",
            help="Project name (registry), path, or . for the project in the cwd (default)",
        )
        p_project_rm.add_argument("--yes", "-y", action="store_true",
                                  help="Skip the confirmation prompt")
        p_project_rm.add_argument("--dry-run", action="store_true",
                                  help="List exactly what would be deleted; take no action")
        p_project_rm.add_argument("--keep-local", action="store_true",
                                  help="Delete the Collection(s) + registry entry only; keep the local .xlii/ tree")
        p_project_rm.add_argument("--local-only", action="store_true",
                                  help="Remove local .xlii/ + registry entry only; leave the cloud Collection(s)")
        p_project_rm.set_defaults(func=cmd_project_rm)

        p_scratch = sub.add_parser(
            "scratch",
            help="Scratch mode: an ephemeral, unbound, never-sync session "
                 "(bare = from home; `here` = local .xlii in the cwd; NAME = ~/.xlii/scratch/NAME).",
        )
        p_scratch.add_argument(
            "name", nargs="?",
            help="`here` for a local never-sync .xlii in the cwd, or a NAME for "
                 "~/.xlii/scratch/NAME; omit for an ephemeral session from home.")
        p_scratch.add_argument("--no-chat", action="store_true",
                               help="Create the scratch (named/here) without entering the session")
        p_scratch.add_argument("--tui", action="store_true",
                               help="Launch the experimental full-screen Textual UI (needs `pip install 'xlii[tui]'`)")
        p_scratch.add_argument(
            "--tauri", action="store_true",
            help="Launch the desktop face (xlii-desktop) over this scratch — "
                 "7am home window (three-faces Q5); bare form uses ~/.xlii/scratch/home",
        )
        p_scratch.add_argument(
            "--resume", dest="face_resume", action="store_true",
            help="With --tauri: if a face is already running, continue that session",
        )
        p_scratch.add_argument(
            "--replace", dest="face_replace", action="store_true",
            help="With --tauri: if a face is already running, stop it and start new",
        )
        p_scratch.add_argument("--yolo", action="store_true", help="Auto-approve bash in the session")
        p_scratch.add_argument("--force", action="store_true", help="Re-init even if a scratch with this name exists")
        p_scratch.set_defaults(func=cmd_scratch)
