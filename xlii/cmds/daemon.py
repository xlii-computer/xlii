"""The XMPP/OMEMO command daemon subcommand (ROADMAP 6.2 / proposals/xmpp-fabric.md).

EXPERIMENTAL. The daemon receives OMEMO-encrypted messages and runs verbs / agent
turns, so it is an inbound remote-execution channel: run it only on a trusted
tailnet, never a public interface. With the secure default `blind_trust = false`,
pair a sender's device with `xlii pair` (or pin a fingerprint with
`xlii daemon trust <jid> <fingerprint>`).
The reliability work (reconnect loop, atomic OMEMO state) shipped (F3); the live
OMEMO crypto path is verified against your own server.
"""

from __future__ import annotations

import argparse


def _print_extra_hint(err) -> None:
    """Friendly install hint when the optional [daemon] extra is absent.
    Mirrors `xlii mcp`; brackets are escaped so rich renders `[daemon]` literally."""
    err.print("[red]✗[/red] The XMPP daemon requires the optional [bold]daemon[/bold] extra.")
    err.print()
    err.print("Install with:")
    err.print("  [cyan]pip install \"xlii\\[daemon]\"[/cyan]")
    err.print("  (editable source tree: [cyan]pip install -e \".\\[daemon]\"[/cyan])")
    err.print()
    err.print("[dim]This pulls in slixmpp + OMEMO so the daemon can receive\n"
              "encrypted messages from your phone over the tailnet.[/dim]")


def cmd_daemon(args: argparse.Namespace) -> int:
    from pathlib import Path

    from rich.console import Console

    err = Console(stderr=True, highlight=False)

    # Lazy import: the daemon module imports slixmpp/OMEMO at top level, so it
    # only loads when the [daemon] extra is installed. Mirrors `xlii mcp`.
    try:
        from xlii import daemon as _daemon
    except ImportError:
        _print_extra_hint(err)
        return 1

    err.print(
        "[yellow]⚠ EXPERIMENTAL:[/yellow] the XMPP daemon is an inbound "
        "remote-execution channel — run it only on a trusted tailnet, never a "
        "public interface. Pair devices with [cyan]xlii pair[/cyan] "
        "(never required to flip [bold]blind_trust[/bold]). "
        "[cyan]xlii daemon trust <jid> <fp>[/cyan] remains the manual fallback. "
        "The live OMEMO path is yours to verify (proposals/xmpp-fabric.md)."
    )
    err.print(
        "[dim]Keyed launch (the secure default): export [cyan]$XLII_DAEMON_KEY[/cyan] "
        "with the admin secret (set it via [cyan]/admin set-key[/cyan] in a REPL). "
        "Opt out with [bold]\\[policy] keyed = false[/bold] — loudly insecure.[/dim]"
    )

    config_path = (
        Path(args.config).expanduser() if getattr(args, "config", None)
        else _daemon.DEFAULT_CONFIG_PATH
    )
    return _daemon.run(config_path)


def cmd_daemon_trust(args: argparse.Namespace) -> int:
    """`xlii daemon trust <jid> <fingerprint>` — pin a device's OMEMO key."""
    from pathlib import Path

    from rich.console import Console

    err = Console(stderr=True, highlight=False)

    # Validate operator input BEFORE importing the heavy extra: a typo'd JID or
    # fingerprint should fail instantly with a clear message, extra installed or not.
    from xlii.daemon_gate import normalize_fingerprint, valid_bare_jid

    if not valid_bare_jid(args.jid):
        err.print(f"[red]✗[/red] not a bare JID: [bold]{args.jid}[/bold] "
                  "[dim](expected local@domain, no /resource)[/dim]")
        return 1
    try:
        fingerprint = normalize_fingerprint(args.fingerprint)
    except ValueError as e:
        err.print(f"[red]✗[/red] {e}")
        return 1

    try:
        from xlii import daemon as _daemon
    except ImportError:
        _print_extra_hint(err)
        return 1

    config_path = (
        Path(args.config).expanduser() if getattr(args, "config", None)
        else _daemon.DEFAULT_CONFIG_PATH
    )
    return _daemon.set_manual_trust(
        config_path, args.jid.strip(), fingerprint, distrust=args.distrust
    )


def _cmd_daemon_pair(args: argparse.Namespace) -> int:
    from xlii.cmds.pair import cmd_pair

    args.rail = "daemon"
    return cmd_pair(args)


def register(sub) -> None:
    p = sub.add_parser(
        "daemon",
        help="Run the XMPP/OMEMO command daemon (experimental; requires the [daemon] extra).",
    )
    p.add_argument(
        "--config", metavar="PATH",
        help="Path to daemon.toml (default: ~/.config/xlii/daemon.toml).",
    )
    p.set_defaults(func=cmd_daemon)  # bare `xlii daemon` runs the daemon

    # `xlii daemon trust <jid> <fingerprint>` — device-trust pinning.
    actions = p.add_subparsers(dest="daemon_action")
    t = actions.add_parser(
        "trust",
        help="Pin a sender device's OMEMO fingerprint as trusted (for blind_trust=false).",
    )
    t.add_argument("jid", help="Bare JID of the sender to pin (e.g. you@your.tailnet).")
    t.add_argument("fingerprint",
                   help="The device's OMEMO fingerprint (64 hex chars; spaces/colons ok).")
    t.add_argument(
        "--config", metavar="PATH",
        help="Path to daemon.toml (default: ~/.config/xlii/daemon.toml).",
    )
    t.add_argument("--distrust", action="store_true",
                   help="Distrust (un-pin) the device instead of trusting it.")
    t.set_defaults(func=cmd_daemon_trust)

    # `xlii daemon pair` — same ceremony as `xlii pair --rail daemon`.
    pr = actions.add_parser(
        "pair",
        help="Mint a one-time QR pairing window (pins a device; never flips blind_trust).",
    )
    pr.add_argument(
        "--invite", metavar="JID",
        help="Also append this JID to allowed_jids on grant.",
    )
    pr.add_argument("--ttl", type=int, default=600, metavar="SECS")
    pr.add_argument("--no-wait", action="store_true")
    pr.add_argument("--config", metavar="PATH")
    pr.set_defaults(func=_cmd_daemon_pair)

    # `xlii daemon totp` — provision the elevation secret (hidden /xsu gate).
    tp = actions.add_parser(
        "totp",
        help="Generate a TOTP secret for the daemon's elevation gate "
             "(add to your authenticator, export XLII_DAEMON_TOTP_SECRET).",
    )
    tp.set_defaults(func=cmd_daemon_totp)

    pp = actions.add_parser(
        "panic-phrases",
        help="Set the three panic-mail subject phrases (vault; not git).",
    )
    pp.add_argument(
        "--set",
        nargs=3,
        metavar=("P1", "P2", "P3"),
        help="Three owner-invented subject phrases.",
    )
    pp.set_defaults(func=cmd_daemon_panic_phrases)

    pc = actions.add_parser(
        "panic-check",
        help="Fetch unseen panic mail now (same as daemon/Face wake).",
    )
    pc.set_defaults(func=cmd_daemon_panic_check)


def cmd_daemon_totp(_args: argparse.Namespace) -> int:
    """Mint a fresh TOTP secret + provisioning URI for the elevation gate.

    Prints the secret and an ``otpauth://`` URI to import into an authenticator
    app. The secret is env-only — never written to a file by xlii; the operator
    exports ``XLII_DAEMON_TOTP_SECRET`` (e.g. in the daemon's 0600 env file)."""
    from xlii import totp

    secret = totp.generate_secret()
    uri = totp.provisioning_uri(secret, label="daemon", issuer="xlii")
    print("TOTP secret (base32):")
    print(f"  {secret}")
    print("\nAuthenticator import URI (scan as QR, or paste):")
    print(f"  {uri}")
    print("\nThen make it live for the daemon (env-only — never a config file):")
    print("  export XLII_DAEMON_TOTP_SECRET=" + secret)
    print("  # e.g. add to ~/.config/xlii/daemon.env (0600) and restart the daemon")
    return 0


def cmd_daemon_panic_phrases(args: argparse.Namespace) -> int:
    from xlii.panic_mail import PHRASE_IDEAS, save_phrases

    if args.set:
        save_phrases(list(args.set))
        print("saved three panic phrases in the vault")
        return 0
    print("Invent three subject lines. Absurd is fine. Ideas (do not copy):")
    for i, idea in enumerate(PHRASE_IDEAS, 1):
        print(f"  {i:2}. {idea}")
    print()
    try:
        p1 = input("phrase 1: ").strip()
        p2 = input("phrase 2: ").strip()
        p3 = input("phrase 3: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("cancelled")
        return 1
    save_phrases([p1, p2, p3])
    print("saved. Allow From: in daemon.toml [panic] from = [\"you@mail\"]")
    print("Daemon/Face check unseen mail on wake. Silent drop if a gate misses.")
    return 0


def cmd_daemon_panic_check(_args: argparse.Namespace) -> int:
    from xlii.panic_mail import check_on_wake

    r = check_on_wake()
    print(f"panic-check: {r.status}" + (f" ({r.reason})" if r.reason else ""))
    return 0 if r.status != "error" else 1
