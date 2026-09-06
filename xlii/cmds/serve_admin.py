"""On-box admin for ``xlii serve mint|sessions|revoke`` (serve-public P0 / V3).

CLI parity with the fabric ``webcode`` verb: mint a pairing code into the
grant spool, list live sessions from serve's session mirror, revoke a session
so the tab goes dark. The conductor wires these under the ``serve`` subparser
via :func:`register_serve_admin` — this module does not touch ``cli.py``.

Session mirror (written by serve / V2; read+revoke here)::

    <state_dir>/serve-sessions.json
    {"version": 1, "sessions": [
        {"id": "...", "paired_at": 1.0, "last_activity": 1.0,
         "mode": "full", "remote": "1.2.3.4"}
    ]}

Revoke is queue-only: it appends the session id (or the ``"all"`` sentinel) to
``serve-revokes.json`` via :mod:`xlii.serve_spool`, and the live serve process
drains that queue on its next sweep and kills the sessions. The mirror is
serve's to write — editing it here would race serve's own rewrites, which
regenerate it from the in-memory gate on every touch.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

from xlii.atomicio import write_text_atomic
from xlii.config import GlobalConfig
from xlii.serve_spool import (
    append_revoke,
    default_state_dir,
    revokes_path as revokes_spool_path,  # noqa: F401 — module API kept stable
)

SESSIONS_MIRROR_NAME = "serve-sessions.json"
MIRROR_VERSION = 1
_VALID_MODES = frozenset({"full", "preview"})


def sessions_mirror_path(state_dir: Path) -> Path:
    return Path(state_dir) / SESSIONS_MIRROR_NAME


def _empty_mirror() -> dict[str, Any]:
    return {"version": MIRROR_VERSION, "sessions": []}


def _read_json(path: Path, empty: dict[str, Any]) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return dict(empty)
    except OSError:
        return dict(empty)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return dict(empty)
    return data if isinstance(data, dict) else dict(empty)


def read_sessions_mirror(state_dir: Path) -> list[dict[str, Any]]:
    """Live sessions from serve's mirror; corrupt/missing → []."""
    data = _read_json(sessions_mirror_path(state_dir), _empty_mirror())
    sessions = data.get("sessions")
    if not isinstance(sessions, list):
        return []
    out: list[dict[str, Any]] = []
    for item in sessions:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        if not isinstance(sid, str) or not sid.strip():
            continue
        mode = item.get("mode", "full")
        if mode not in _VALID_MODES:
            mode = "full"
        paired = item.get("paired_at", 0.0)
        last = item.get("last_activity", paired)
        remote = item.get("remote", "")
        out.append({
            "id": sid.strip(),
            "paired_at": float(paired) if isinstance(paired, (int, float)) else 0.0,
            "last_activity": float(last) if isinstance(last, (int, float)) else 0.0,
            "mode": mode,
            "remote": remote if isinstance(remote, str) else "",
        })
    return sorted(out, key=lambda s: (s["paired_at"], s["id"]))


def write_sessions_mirror(state_dir: Path, sessions: list[dict[str, Any]]) -> None:
    """Overwrite the mirror (0600). Used by revoke and by tests as a fake mirror."""
    path = sessions_mirror_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": MIRROR_VERSION, "sessions": list(sessions)}
    write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n", mode=0o600)


def revoke_session(state_dir: Path, sid: str) -> bool:
    """Queue ``sid`` for the serve process to kill at its next sweep.

    Returns False when the mirror doesn't list the sid (typo guard) — the queue
    is only written for a session that appears live. The mirror itself is never
    edited here; serve rewrites it once the revoke actually lands."""
    sessions = read_sessions_mirror(state_dir)
    if all(s["id"] != sid for s in sessions):
        return False
    append_revoke(state_dir, sid)
    return True


def revoke_all(state_dir: Path) -> list[str]:
    """Queue the ``"all"`` sentinel (kill every live session at next sweep).

    Always queues — the mirror may be stale, and 'all' is explicit operator
    intent; serve kills whatever is actually live. Returns the mirrored ids as
    a best-effort display list."""
    mirrored = [s["id"] for s in read_sessions_mirror(state_dir)]
    append_revoke(state_dir, "all")
    return mirrored


def format_mint_line(code: str, base_url: str, ttl_s: int) -> str:
    """Pinned operator-facing mint line (mirrors the webcode OMEMO reply shape).

    The URL is the magic link — it carries ``?code=`` so opening it lands on a
    prefilled pairing form (GET peeks, never consumes).
    """
    mins = max(1, int(ttl_s) // 60)
    link = f"{base_url.rstrip('/')}/?code={code}"
    return f"code: {code}  ·  {link}  ·  expires in {mins}m"


def mint_pairing_code(
    state_dir: Path,
    *,
    cfg: Optional[GlobalConfig] = None,
    mode: str = "full",
    now: Optional[float] = None,
) -> tuple[str, str]:
    """Mint a code, append to the spool, return ``(grouped_code, print_line)``.

    Raises ``ValueError`` when ``serve.public.base_url`` is missing (friendly
    hint from :meth:`GlobalConfig.require_serve_public_base_url`).
    """
    if mode not in _VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(_VALID_MODES)}; got {mode!r}")
    cfg = cfg if cfg is not None else GlobalConfig.load()
    base_url = cfg.require_serve_public_base_url()
    ttl_s = cfg.serve_public_code_ttl_s
    when = time.time() if now is None else float(now)

    from xlii.serve_gate import mint_code
    from xlii.serve_spool import append_pending

    code = mint_code()
    append_pending(state_dir, code, ttl_s=ttl_s, mode=mode, now=when)
    return code, format_mint_line(code, base_url, ttl_s)


def _resolve_state_dir(args: argparse.Namespace) -> Path:
    raw = getattr(args, "state_dir", None)
    if raw:
        return Path(raw).expanduser()
    # Call-time, not module-level: the canonical accessor reads XLII_STATE_DIR
    # at each call, and mint MUST land where serve drains (#283 — a mismatch
    # here is silent: codes just never become pairable).
    return default_state_dir()


def cmd_serve_mint(args: argparse.Namespace) -> int:
    state_dir = _resolve_state_dir(args)
    mode = "preview" if getattr(args, "preview", False) else "full"
    try:
        code, line = mint_pairing_code(state_dir, mode=mode)
    except ValueError as exc:
        print(f"serve mint: {exc}", file=sys.stderr)
        return 1
    print(line)
    # Second line is the bare code for scripts / copy-paste.
    print(code)
    if not getattr(args, "email", False):
        return 0
    from xlii.config import GlobalConfig
    from xlii.webcode_mail import deliver_webcode_email

    cfg = GlobalConfig.load()
    try:
        to = deliver_webcode_email(
            code=code,
            base_url=cfg.require_serve_public_base_url(),
            ttl_s=cfg.serve_public_code_ttl_s,
            cfg=cfg,
        )
    except Exception as e:  # noqa: BLE001 — code is already in the spool
        print(f"serve mint: emailed: failed ({type(e).__name__}: {e})", file=sys.stderr)
        return 1
    print(f"emailed {to}", file=sys.stderr)
    return 0


def cmd_serve_sessions(args: argparse.Namespace) -> int:
    state_dir = _resolve_state_dir(args)
    sessions = read_sessions_mirror(state_dir)
    if not sessions:
        print("serve sessions: (none)")
        return 0
    now = time.time()
    print(f"{'ID':<24} {'MODE':<8} {'IDLE':>6}  REMOTE")
    for s in sessions:
        idle_s = max(0, int(now - s["last_activity"]))
        print(f"{s['id']:<24} {s['mode']:<8} {idle_s:>5}s  {s['remote'] or '—'}")
    return 0


def cmd_serve_revoke(args: argparse.Namespace) -> int:
    state_dir = _resolve_state_dir(args)
    target = (getattr(args, "session_id", None) or "").strip()
    if not target:
        print("serve revoke: pass a session id, or 'all'", file=sys.stderr)
        return 1
    if target.lower() == "all":
        mirrored = revoke_all(state_dir)
        if mirrored:
            print(f"serve revoke: {len(mirrored)} session(s) revoked — applies at next sweep")
            for sid in mirrored:
                print(f"  {sid}")
        else:
            print("serve revoke: 'all' queued — applies at next sweep (mirror lists none)")
        return 0
    if not revoke_session(state_dir, target):
        print(f"serve revoke: no live session {target!r}", file=sys.stderr)
        return 1
    print(f"serve revoke: {target} revoked — applies at next sweep")
    return 0


def register_serve_admin(serve_subparsers) -> None:
    """Add ``mint`` / ``sessions`` / ``revoke`` under an existing ``serve`` parser.

    Conductor wiring (not done here)::

        serve_p = …  # the ``xlii serve`` parser
        actions = serve_p.add_subparsers(dest="serve_action")
        register_serve_admin(actions)
    """
    mint = serve_subparsers.add_parser(
        "mint",
        help="Mint a single-use pairing code into the grant spool (public gate).",
        description=(
            "Mint a pairing code for the public entry page. Writes the code to "
            "serve-grants.json under the state dir; type it into base_url's form. "
            "Requires serve.public.base_url in config.json."
        ),
    )
    mint.add_argument(
        "--preview", action="store_true",
        help="Mint a preview-mode code (read-only TUI session).",
    )
    mint.add_argument(
        "--email", action="store_true",
        help="Email the magic link to the owner inbox (default email account, "
             "or serve.public.owner_email).",
    )
    mint.add_argument(
        "--state-dir", metavar="PATH",
        help="State directory for the grant spool (default: ~/.local/share/xlii; XLII_STATE_DIR overrides).",
    )
    mint.set_defaults(func=cmd_serve_mint)

    sessions = serve_subparsers.add_parser(
        "sessions",
        help="List live public-serve sessions from the session mirror.",
    )
    sessions.add_argument(
        "--state-dir", metavar="PATH",
        help="State directory for the session mirror (default: ~/.local/share/xlii; XLII_STATE_DIR overrides).",
    )
    sessions.set_defaults(func=cmd_serve_sessions)

    revoke = serve_subparsers.add_parser(
        "revoke",
        help="Revoke a live public-serve session (or all).",
        description=(
            "Revoke a paired browser session: queue it for the live serve process, "
            "which kills it at its next sweep. Pass a session id or 'all'."
        ),
    )
    revoke.add_argument(
        "session_id",
        help="Session id from `xlii serve sessions`, or 'all'.",
    )
    revoke.add_argument(
        "--state-dir", metavar="PATH",
        help="State directory for the session mirror (default: ~/.local/share/xlii; XLII_STATE_DIR overrides).",
    )
    revoke.set_defaults(func=cmd_serve_revoke)
