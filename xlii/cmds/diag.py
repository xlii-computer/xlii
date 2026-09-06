"""Diagnostics subcommands: help and doctor.

Moved out of the former monolithic cli.py (see proposals/done/cli-refactor.md).
The doctor engine lives in the kernel at ``xlii/doctor.py`` (godzilla-mothra
Stage 1, B3) — this module is the CLI façade: HELP_TEXT, the console
presenter, argparse wiring, and back-compat re-exports.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from xlii.config import GLOBAL_CONFIG_FILE, ProjectConfig  # noqa: F401  (test seam: monkeypatched here)
from xlii.doctor import (
    DoctorFinding,
    DoctorReport,
    apply_doctor_fix,
    collect_doctor_findings,
    is_runnable_fix,
)
from xlii.ui import console

__all__ = [
    "DoctorFinding",
    "DoctorReport",
    "apply_doctor_fix",
    "cmd_doctor",
    "cmd_help",
    "is_runnable_fix",
    "register",
    "run_doctor",
]


HELP_TEXT = """xlii — personal AI substrate (Grok + xAI Collections).

PROJECT (code work — files, edits, builds)
  init [NAME]              Initialize an xlii project in the current directory.
                           --local: no Collection upload (Midnight Commander mode).
                           --snapshot: cache .xlii/index.txt for fast structural search.
                           --id PERSONA: bind a code persona's memory to this project.
  new NAME                 Create a new project directory and initialize it.
  scratch [NAME]           Ephemeral local-only project under ~/.xlii/scratch/, then chat.
  projects [FILTER]        List all registered xlii projects.
  status [PATH]            Show config + project state.
  sync [PATH]              Push local changes to the project's collection.
  code [TARGET]            Start the project-scoped code REPL (--tui for full-screen;
                           --rail for the coding rail; --preview/--init for ephemeral).

CHAT (conversation with persistent memory)
  chat [NAME]              Start a chat as persona NAME (default: most-recently-used).
  chat --new NAME          Create a new persona; opens $EDITOR on its prompt.
  chat --list              List personas.
  chat --edit NAME         Edit a persona's prompt in $EDITOR.
  chat --delete NAME       Delete a persona (prompt + state dir).

HEADLESS (no REPL — for scripts, hooks, and the daemon)
  ask PROMPT               Run a single agent turn and print just the reply.
  loop [GOAL]              Autonomous build→test→fix loop (walk away to green).
                           --judge, --max, --test, --commit, --swarm, --from-plan, …

KNOWLEDGE (attached in any REPL via /doc; plugins via /lib + /get)
  doc --new NAME           Create a new reference doc; opens $EDITOR.
  doc --list               List all docs.
  doc --edit NAME          Edit a doc in $EDITOR.
  doc --delete NAME        Delete a doc.
  plugin --new ID          Create a new plugin from template; opens $EDITOR.
  plugin --list            List all installed plugins.
  plugin --show ID         Print a plugin's full markdown.
  plugin --edit ID         Edit a plugin in $EDITOR.
  plugin --delete ID       Delete a plugin.
  plugin --install-stock   Install the bundled stock plugins.
  auth set ID ENV_VAR      Store a plugin credential in the encrypted vault (prompted).
  auth list                List plugins + env var names in the vault (never values).
  auth clear ID [ENV_VAR]  Remove a credential or a plugin's whole entry.

SETUP & CREDENTIALS
  setup                    First-time setup: config + provision primary + worker keys.
  config                   Write a config template to ~/.config/xlii/config.json.
  bootstrap                Lower-level: provision worker keys via management API.
  shell-init [SHELL]       Print a shell wrapper so `cd` in `xlii code` follows you out.
  models list              List models the team has access to.
  models recommended       Show heuristic best-of-class picks.
  models set [--orchestrator MODEL] [--worker MODEL]
  keys list                List local chat keys with server-side expiration.
  keys rotate [--label X]  Rotate the secret of one or all keys.
  keys expire --days N     Update expireTime on existing key(s).
  keys revoke [--prefix X] Delete keys by label prefix (server + local).
  keys prune               Delete orphaned provisioned keys not in this pool.

PORTABILITY & MULTI-MACHINE
  export DEST              Serialize personas, docs, plugins + registry (no secrets).
  import SRC               Restore an `xlii export` tree.
  mcp deep-contexts        Expose DeepContexts over MCP (Grok Build bridge; [mcp] extra).
  daemon                   Run the XMPP/OMEMO command daemon (experimental; [daemon] extra).
  notify MESSAGE           Send an OMEMO-encrypted notification to your phone ([daemon] extra).

MAINTENANCE
  gc [--dry-run]           Find and delete orphan xAI collections.
  sweep                    Throne housekeep: list collections / ghosts / keys.
                           --empty --test --ghosts --keys [--yes] to clean.
  doctor [--online]        Check install + project health and print fixes.
  help                     Show this message.

For per-command flags:  xlii <cmd> --help
For version:            xlii --version
"""


def cmd_help(args: argparse.Namespace) -> int:
    print(HELP_TEXT)
    return 0


_GLYPH = {"ok": "  [green]✓[/green]", "warn": "  [yellow]⚠[/yellow]", "bad": "  [red]✗[/red]"}


def _print_event(kind: str, payload: object) -> None:
    """Console presenter for collect_doctor_findings render events — the face
    half of the old run_doctor's inline prints (same glyphs, same order)."""
    if kind == "header":
        console.print(f"[bold]{payload}[/bold]")
    elif kind == "note":
        console.print(payload)  # preformatted by the collector
    else:
        f = payload  # a DoctorFinding
        hint = f.fix or f.fix_cmd  # print the prose when we have it, else the command
        suffix = f"\n      [dim]fix: {hint}[/dim]" if hint else ""
        console.print(f"{_GLYPH[f.severity]} {f.message}{suffix}")


def _print_summary(report: DoctorReport) -> None:
    if report.problems:
        console.print(f"\n[red]{report.problems} problem(s) found[/red]"
                      + (f" [yellow]· {report.warn_count} warning(s)[/yellow]" if report.warn_count else ""))
    elif report.warn_count:
        console.print(f"\n[green]no problems found[/green] [yellow]· {report.warn_count} warning(s)[/yellow]")
    else:
        console.print("\n[green]all checks pass[/green]")


def run_doctor(args: argparse.Namespace) -> DoctorReport:
    """Check install + current project; print fixes; return structured findings.

    Offline-safe: network checks only run with ``--online``.
    """
    report = collect_doctor_findings(
        Path(".").resolve(),
        online=getattr(args, "online", False),
        migrate_legacy=getattr(args, "migrate_legacy", False),
        dry_run=getattr(args, "dry_run", False),
        config_file=GLOBAL_CONFIG_FILE,
        emit=_print_event,
    )
    _print_summary(report)
    return report


def cmd_doctor(args: argparse.Namespace) -> int:
    """One command that checks the install + current project and prints fixes.

    Replaces 'read the source' as the failure-mode answer. Offline-safe:
    network checks only run with --online.
    """
    return run_doctor(args).exit_code


def register(sub) -> None:
        p_help = sub.add_parser("help", help="Show grouped command listing.")
        p_help.set_defaults(func=cmd_help)

        p_doctor = sub.add_parser("doctor", help="Check install + project health and print fixes.")
        p_doctor.add_argument("--online", action="store_true", help="Also test Collection reachability")
        p_doctor.add_argument(
            "--migrate-legacy",
            action="store_true",
            help="Migrate legacy paths (.xli/ → .xlii/, global loadouts, .xliignore)",
        )
        p_doctor.add_argument(
            "--dry-run",
            action="store_true",
            help="With --migrate-legacy, print actions without applying",
        )
        p_doctor.set_defaults(func=cmd_doctor)
