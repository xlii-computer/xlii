"""``xlii pair`` — mint a bound-TOFU pairing window (qr-pair P2).

The ceremony never edits ``daemon.toml`` and never flips ``blind_trust``.
A runtime-dir window expires by clock, is consumed by the first matching
code, and dies with reboot.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _load_daemon_cfg(config: str | None):
    from xlii.daemon_gate import DEFAULT_CONFIG_PATH, DaemonConfig

    path = Path(config).expanduser() if config else DEFAULT_CONFIG_PATH
    if not path.is_file():
        print(f"pair: daemon config not found at {path}", file=sys.stderr)
        print("      copy daemon.toml.example there and run the rail once first.", file=sys.stderr)
        return None, path
    try:
        return DaemonConfig.load(path), path
    except (OSError, ValueError, KeyError) as e:
        print(f"pair: invalid daemon config at {path}: {e}", file=sys.stderr)
        return None, path


def _identity(cfg, *, sid: int, fingerprint: str, jid: str) -> tuple[str, int, str] | None:
    """Return (jid, device_id, fingerprint) or print why we refused."""
    from xlii.pairing_gate import load_own_identity

    bare = (jid or getattr(cfg, "jid", "") or "").strip()
    if sid > 0 and fingerprint:
        return bare, sid, fingerprint
    state = Path(getattr(cfg, "state_file", ""))
    if not state.is_file():
        print(
            "pair: OMEMO state missing — run the rail once first so slixmpp "
            "can mint this body's identity. Refusing to invent a key.",
            file=sys.stderr,
        )
        return None
    got = load_own_identity(state)
    if got is None:
        print(
            "pair: OMEMO state has no own device yet — run the rail once first.",
            file=sys.stderr,
        )
        return None
    device_id, fp = got
    if not bare:
        print("pair: daemon.toml has no jid", file=sys.stderr)
        return None
    return bare, device_id, fp


def cmd_pair(args: argparse.Namespace) -> int:
    from xlii.daemon_gate import valid_bare_jid
    from xlii.pairing_gate import (
        DEFAULT_TTL_S,
        PairingStore,
        mint_window,
        omemo_verify_uri,
        qr_ansi,
    )
    from xlii.serve_gate import group_code

    rail = getattr(args, "rail", None) or "daemon"
    if rail != "daemon":
        print(
            "pair: --rail notify|face is not wired yet (qr-pair P4). "
            "Use `xlii pair` (daemon rail) for now.",
            file=sys.stderr,
        )
        return 2
    ttl = int(getattr(args, "ttl", DEFAULT_TTL_S) or DEFAULT_TTL_S)
    invite = (getattr(args, "invite", None) or "").strip()
    if invite and not valid_bare_jid(invite):
        print(f"pair: --invite is not a bare JID: {invite}", file=sys.stderr)
        return 2

    cfg, _cfg_path = _load_daemon_cfg(getattr(args, "config", None))
    if cfg is None:
        return 3

    ident = _identity(
        cfg,
        sid=int(getattr(args, "sid", 0) or 0),
        fingerprint=(getattr(args, "fingerprint", None) or "").strip(),
        jid=(getattr(args, "jid", None) or "").strip(),
    )
    if ident is None:
        return 3
    jid, device_id, fingerprint = ident

    now = time.time()
    window, grouped = mint_window(
        rail=rail, ttl_s=ttl, now=now, invite_jid=invite,
    )
    store = PairingStore()
    store.put(window)
    uri = omemo_verify_uri(jid, device_id, fingerprint)
    art = qr_ansi(uri)
    expires = time.strftime("%H:%M", time.localtime(window.expires_at))
    if art:
        print(art)
        print()
    print(f"pair code: {group_code(grouped)}   (expires {expires}, single use)")
    print(f"xmpp uri:  {uri}")
    print()
    print(f"1. scan with Conversations → adds {jid},")
    print("      daemon key pre-verified on the phone")
    print("2. send the code as your first message")
    if invite:
        print(f"   invite: {invite} will be appended to allowed_jids on grant")
    if getattr(args, "no_wait", False):
        print("window open (--no-wait). Ctrl-C / reboot / expiry closes it.")
        return 0
    print("waiting… (Ctrl-C cancels the window)")
    try:
        while True:
            time.sleep(0.4)
            clock = time.time()
            live = store.get(rail)
            if live is None:
                print("pair: window gone (expired or cancelled).", file=sys.stderr)
                return 1
            if live.consumed_by:
                print(
                    f"paired: device {live.consumed_by}  → pinned TRUSTED"
                )
                print("window closed.")
                return 0
            if clock >= live.expires_at:
                store.clear(rail)
                print("pair: window expired.", file=sys.stderr)
                return 1
    except KeyboardInterrupt:
        store.clear(rail)
        print("\npair: cancelled — window closed.")
        return 130


def register(sub) -> None:
    p = sub.add_parser(
        "pair",
        help="Mint a one-time QR pairing window (pins an OMEMO device; never flips blind_trust).",
    )
    p.add_argument(
        "--rail", choices=("daemon", "notify", "face"), default="daemon",
        help="Which OMEMO rail owns the window (default: daemon).",
    )
    p.add_argument(
        "--invite", metavar="JID",
        help="Also append this JID to allowed_jids on grant (opt-in enrollment).",
    )
    p.add_argument(
        "--ttl", type=int, default=600, metavar="SECS",
        help="Window lifetime in seconds (default 600).",
    )
    p.add_argument(
        "--no-wait", action="store_true",
        help="Mint and print, then exit — leave the window open.",
    )
    p.add_argument(
        "--config", metavar="PATH",
        help="Path to daemon.toml (default: ~/.config/xlii/daemon.toml).",
    )
    p.add_argument("--jid", help=argparse.SUPPRESS)
    p.add_argument("--sid", type=int, default=0, help=argparse.SUPPRESS)
    p.add_argument("--fingerprint", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_pair)
