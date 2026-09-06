"""`xlii notify` — send an OMEMO-encrypted notification to your phone (fabric F1).

The send-only half of the multi-machine fabric: no inbound channel, no remote
execution. Requires the optional [daemon] extra. Useful from scripts and hooks
(e.g. ping your phone when a long `xlii code` turn finishes).
"""

from __future__ import annotations

import argparse


def cmd_notify(args: argparse.Namespace) -> int:
    from pathlib import Path

    from rich.console import Console

    from xlii.daemon_gate import DEFAULT_NOTIFY_CONFIG

    err = Console(stderr=True, highlight=False)

    # Lazy import: the slixmpp/OMEMO send path only loads with the [daemon] extra.
    try:
        from xlii import xmpp_send
    except ImportError:
        err.print("[red]✗[/red] notify requires the optional [bold]daemon[/bold] extra (slixmpp + OMEMO).")
        err.print("  [cyan]pip install \"xlii\\[daemon]\"[/cyan]")
        err.print("  (editable source tree: [cyan]pip install -e \".\\[daemon]\"[/cyan])")
        return 1

    config_path = (
        Path(args.config).expanduser() if getattr(args, "config", None)
        else DEFAULT_NOTIFY_CONFIG
    )
    return xmpp_send.run(config_path, args.message)


def register(sub) -> None:
    p = sub.add_parser(
        "notify",
        help="Send an OMEMO-encrypted notification to your phone (requires the [daemon] extra).",
    )
    p.add_argument("message", help="The message to send")
    p.add_argument(
        "--config", metavar="PATH",
        help="Path to notify.toml (default: ~/.config/xlii/notify.toml).",
    )
    p.set_defaults(func=cmd_notify)
