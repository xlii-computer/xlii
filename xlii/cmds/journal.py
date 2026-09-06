"""Journal (Project Shadow) CLI subcommands.

JRN-1 ships `xlii journal key` — provision an xAI API key used **exclusively** by
the journal, so its LLM spend (rolling summaries + `/askjo`) is isolated on the
xAI dashboard for clean cost auditing. The pool keeps this key out of the
orchestrator + worker rotation (see pool.primary/acquire); only the journal bills
to it. JRN-2 will add `install` / `uninstall` / `serve` here for bash-wide capture.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from xlii.config import GLOBAL_CONFIG_DIR, JOURNAL_KEY_LABEL, GlobalConfig
from xlii.ui import console


def cmd_journal_key(args: argparse.Namespace) -> int:
    """Provision a dedicated journal key (label == JOURNAL_KEY_LABEL).

    Idempotent: a journal key already in the pool isolates spend already, so we
    skip unless --force. The secret is sealed into the vault; config.json
    keeps only a ``vault_ref``."""
    cfg = GlobalConfig.load()

    from xlii.bootstrap import (
        DEFAULT_EXPIRE_DAYS,
        BootstrapError,
        MultipleTeamsError,
        discover_team_id,
        has_key_labeled,
        provision_labeled_key,
        require_management_key,
    )

    try:
        require_management_key(cfg)
    except BootstrapError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    if has_key_labeled(cfg, JOURNAL_KEY_LABEL) and not args.force:
        console.print(
            f"[green]✓[/green] a dedicated journal key already exists "
            f"(label [cyan]{JOURNAL_KEY_LABEL}[/cyan]) — the journal's spend is already "
            "isolated for auditing. Re-run with [cyan]--force[/cyan] to provision another."
        )
        return 0

    try:
        team_id = discover_team_id(cfg)
    except MultipleTeamsError:
        console.print(
            "[yellow]multiple teams found[/yellow] — set [cyan]team_id[/cyan] in "
            "config.json first (see `xlii bootstrap`)."
        )
        return 1
    except BootstrapError as e:
        console.print(f"[red]team discovery failed: {e}[/red]")
        return 1

    expire_days = getattr(args, "expire_days", None) or DEFAULT_EXPIRE_DAYS
    # bump=False: the journal key's label is fixed (JOURNAL_KEY_LABEL); the
    # idempotency/force policy above owns collisions.
    result = provision_labeled_key(
        cfg, team_id, label=JOURNAL_KEY_LABEL, expire_days=expire_days, bump=False
    )
    if not result.ok:
        if result.error_kind == "api":
            console.print(f"[red]could not create journal key: {result.error}[/red]")
        else:
            console.print(f"[red]create response missing the key string. raw: {result.raw}[/red]")
        return 1

    console.print(
        f"[green]✓[/green] provisioned dedicated journal key "
        f"(label [cyan]{JOURNAL_KEY_LABEL}[/cyan] → xAI: {result.name_on_server}, "
        f"expires in {expire_days}d)"
    )
    console.print(
        "[dim]the journal's rolling-summary + /askjo calls now bill to this key "
        "only — audit it separately on the xAI console. The secret is in the "
        "vault. Restart [/dim][cyan]xlii code[/cyan][dim] to pick it up.[/dim]"
    )
    return 0


# --------------------------------------------------------------------------- #
#  JRN-2 — opt-in bash-wide capture.
#
#  Extends JRN-1 to commands run OUTSIDE xlii, behind an explicit, reversible
#  installer. Three pieces (see xlii/journal_daemon.py + proposals §B4):
#    - install/uninstall: add/remove ONE marked `source <hook>` block in ~/.bashrc.
#    - the hook (~/.config/xlii/journal.sh): a fork-free DEBUG trap appending
#      `ts \t cwd \t cmd` to an append-only feed; no-op unless XLII_JOURNAL is set.
#    - `xlii journal serve`: a mostly-sleeping daemon that tails the feed and
#      batch-summarizes into each opted-in project's journal — detached, PID-
#      supervised, idempotent. Standalone (no XMPP dependency); the fabric daemon
#      can host the same ingest seam (journal_daemon.tick) for cross-machine sync.
# --------------------------------------------------------------------------- #

# Marked block so install is idempotent and uninstall removes exactly its lines.
_RC_BEGIN = "# >>> xlii journal (JRN-2) >>>"
_RC_END = "# <<< xlii journal (JRN-2) <<<"


def _bashrc_path() -> Path:
    return Path(os.environ.get("XLII_BASHRC") or (Path.home() / ".bashrc"))


def _hook_path() -> Path:
    return Path(os.environ.get("XLII_JOURNAL_HOOK") or (GLOBAL_CONFIG_DIR / "journal.sh"))


def _rc_block(hook: Path) -> str:
    return f'{_RC_BEGIN}\nsource "{hook}"\n{_RC_END}\n'


def cmd_journal_install(args: argparse.Namespace) -> int:
    from xlii import journal_daemon

    hook = _hook_path()
    rc = _bashrc_path()
    try:
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text(journal_daemon.hook_script())
    except OSError as e:
        console.print(f"[red]could not write the hook ({hook}): {e}[/red]")
        return 1

    # Ensure the feed + runtime dir exist so the very first command has somewhere
    # to land even before the daemon starts.
    try:
        journal_daemon.runtime_dir().mkdir(parents=True, exist_ok=True)
        journal_daemon.feed_path().touch(exist_ok=True)
    except OSError as e:
        # Non-fatal: install can continue; daemon/startup paths may create these later.
        console.print(
            f"[yellow]warning:[/yellow] could not pre-create journal runtime/feed files: {e}"
        )

    existing = rc.read_text() if rc.exists() else ""
    if _RC_BEGIN in existing:
        console.print(
            f"[green]✓[/green] bash-wide journaling already installed "
            f"[dim]({rc})[/dim]. Hook refreshed."
        )
    else:
        try:
            sep = "" if existing.endswith("\n") or not existing else "\n"
            rc.write_text(existing + sep + _rc_block(hook))
        except OSError as e:
            console.print(f"[red]could not update {rc}: {e}[/red]")
            return 1
        console.print(
            f"[green]✓[/green] bash-wide journaling installed — added a source line to "
            f"[cyan]{rc}[/cyan] [dim](one marked block; [/dim][cyan]xlii journal "
            f"uninstall[/cyan][dim] removes it).[/dim]"
        )

    console.print(
        "[dim]Next: open a new shell (or [/dim][cyan]source "
        f"{rc}[/cyan][dim]), then [/dim][cyan]export XLII_JOURNAL=1[/cyan][dim] to arm "
        "capture. Only projects you've opted in with [/dim][cyan]/journal --code-auto"
        "[/cyan][dim] are journaled; everything else is dropped. The "
        "[/dim][cyan]xlii journal serve[/cyan][dim] daemon summarizes asynchronously "
        "(auto-spawned, or start it yourself).[/dim]"
    )
    return 0


def cmd_journal_uninstall(args: argparse.Namespace) -> int:
    from xlii import journal_daemon

    rc = _bashrc_path()
    text = rc.read_text() if rc.exists() else ""
    if _RC_BEGIN not in text:
        console.print(
            "[dim]nothing to uninstall — no bash-wide journaling block in "
            f"{rc}.[/dim]"
        )
    else:
        lines = text.splitlines(keepends=True)
        out, skipping = [], False
        for ln in lines:
            stripped = ln.strip()
            if stripped == _RC_BEGIN:
                skipping = True
                continue
            if stripped == _RC_END:
                skipping = False
                continue
            if not skipping:
                out.append(ln)
        try:
            rc.write_text("".join(out))
        except OSError as e:
            console.print(f"[red]could not update {rc}: {e}[/red]")
            return 1
        console.print(
            f"[green]✓[/green] removed the bash-wide journaling block from "
            f"[cyan]{rc}[/cyan] [dim](open a new shell to drop the trap).[/dim]"
        )

    # Best-effort: stop a running daemon and remove the hook file.
    if journal_daemon.stop_daemon():
        console.print("[dim]stopped the running journal daemon.[/dim]")
    try:
        _hook_path().unlink()
    except OSError as e:
        console.print(f"[dim]could not remove journal hook file: {e}[/dim]")
    return 0


def cmd_journal_serve(args: argparse.Namespace) -> int:
    from xlii import journal_daemon

    if getattr(args, "stop", False):
        if journal_daemon.stop_daemon():
            console.print("[green]✓[/green] signaled the journal daemon to stop.")
        else:
            console.print("[dim]no journal daemon running.[/dim]")
        return 0
    if journal_daemon.daemon_running():
        console.print(
            f"[dim]journal daemon already running (pid {journal_daemon.read_pid()}).[/dim]"
        )
        return 0
    return journal_daemon.serve(run_once=getattr(args, "once", False))


def register(sub) -> None:
    p_journal = sub.add_parser(
        "journal",
        help="Project Shadow journal: provision its dedicated cost-audit key.",
    )
    j_sub = p_journal.add_subparsers(dest="journal_action", required=True)
    p_key = j_sub.add_parser(
        "key",
        help="Provision an API key used exclusively by the journal "
             "(isolates its LLM spend for cost auditing).",
    )
    p_key.add_argument("--force", action="store_true",
                       help="Provision another journal key even if one already exists")
    p_key.add_argument("--expire-days", type=int, default=None, dest="expire_days",
                       help="Key expiry in days (default: 180)")
    p_key.set_defaults(func=cmd_journal_key)

    # JRN-2: opt-in bash-wide capture (reversible installer + tailing daemon).
    p_install = j_sub.add_parser(
        "install",
        help="Opt in to bash-wide capture: add one marked source block to ~/.bashrc.",
    )
    p_install.set_defaults(func=cmd_journal_install)
    p_uninstall = j_sub.add_parser(
        "uninstall",
        help="Remove the bash-wide capture block from ~/.bashrc and stop the daemon.",
    )
    p_uninstall.set_defaults(func=cmd_journal_uninstall)
    p_serve = j_sub.add_parser(
        "serve",
        help="Run the journal daemon that summarizes bash-wide capture (detached, idempotent).",
    )
    p_serve.add_argument("--once", action="store_true",
                         help="Drain the feed once and exit (no daemon loop).")
    p_serve.add_argument("--stop", action="store_true",
                         help="Signal a running journal daemon to stop.")
    p_serve.set_defaults(func=cmd_journal_serve)
