"""Dependency-free policy/config for the XMPP daemon (fabric F4 — the gate).

The daemon's *security gate* — config parsing, the per-JID rate limiter, and the
default-secure device-trust policy — lives here, free of any slixmpp/OMEMO
import, so it is unit-testable without the `[daemon]` extra. xlii/daemon.py wires
these into the live slixmpp client.
"""

from __future__ import annotations

import os
import re
import time
import tomllib
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "xlii" / "daemon.toml"
DEFAULT_VERBS_DIR = Path.home() / ".config" / "xlii" / "verbs"
DEFAULT_AUDIT_LOG = Path.home() / ".local" / "share" / "xlii" / "daemon-audit.log"
DEFAULT_OMEMO_STATE = Path.home() / ".config" / "xlii" / "daemon-omemo-state.json"

# Phase-1 send-only notification rail (`xlii notify`). The sender is a *separate*
# XMPP identity from the daemon and keeps its own OMEMO state.
DEFAULT_NOTIFY_CONFIG = Path.home() / ".config" / "xlii" / "notify.toml"
DEFAULT_NOTIFY_STATE = Path.home() / ".config" / "xlii" / "notify-omemo-state.json"
TRUSTED_OMEMO_LEVELS = frozenset({"TRUSTED", "BLINDLY_TRUSTED"})


# --------------------------------------------------------------------------- #
#  Operator input validation (for `xlii daemon trust`)
# --------------------------------------------------------------------------- #
#
# Dependency-free so the CLI can reject a typo'd JID/fingerprint *before* it
# touches the heavy [daemon] extra — and so the rules are unit-testable here.

_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
_BARE_JID_RE = re.compile(r"[^@/\s]+@[^@/\s]+")


def normalize_fingerprint(raw: str) -> str:
    """Canonicalize a pasted OMEMO fingerprint to 64 lowercase hex chars.

    OMEMO identity-key fingerprints are a 32-byte Curve25519 key — python-omemo's
    `format_identity_key` renders it as eight groups of eight lowercase hex. Apps
    (Conversations, Gajim) show them with spaces or colons, so strip those and
    lowercase, then require exactly 64 hex chars. Raises ValueError otherwise.
    """
    s = re.sub(r"[\s:]", "", raw or "").lower()
    if not _FINGERPRINT_RE.fullmatch(s):
        raise ValueError(
            "OMEMO fingerprint must be 64 hex chars (a 32-byte Curve25519 "
            f"identity key); got {len(s)} char(s) after removing spaces/colons"
        )
    return s


def valid_bare_jid(jid: str) -> bool:
    """True for a bare JID (`local@domain`, no `/resource`, no whitespace).
    Permissive on the charset XMPP allows; strict enough to catch fat-fingers."""
    return bool(_BARE_JID_RE.fullmatch((jid or "").strip()))


def omemo_device_trusted(device_info: object) -> bool:
    """True only for sender devices that OMEMO already considers trusted.

    python-omemo can decrypt from UNDECIDED devices; the daemon is a remote
    execution channel, so decrypt success alone must not authorize dispatch.
    """
    return getattr(device_info, "trust_level_name", None) in TRUSTED_OMEMO_LEVELS


@dataclass
class DaemonConfig:
    jid: str
    password_env: str
    state_file: Path
    verbs_dir: Path
    audit_log: Path
    allowed_jids: list[str]
    max_per_minute: int
    lockout_threshold: int
    lockout_duration_s: int
    fallback_enabled: bool
    fallback_workspace: str   # alias or path; "" = most-recent-project from registry
    # The persona the agent fallback runs AS (fabric F2 / the mojo). Empty
    # still speaks as the factory journal (mojo) — the traveler exists on
    # every node install. Set an explicit name to rename that one seat.
    # ``force_lab`` (sitting ``$``) is the only faceless project-scoped path.
    # The persona is the continuity, so the per-sender session is dropped.
    fallback_persona: str = ""
    # This body's name in the fabric (throne, node1, …). Surfaced so you know
    # which body you're texting: set as XMPP presence status on connect, and
    # answered by /whoami. Also the provenance key the throne's `xlii fabric
    # pull` uses. "" = unnamed (presence carries no status). One persona, many
    # named surfaces — the name is the body, not the identity.
    node_name: str = ""
    # Device trust. Default False = OMEMO blind-trust-before-verification is OFF
    # (a new/unknown device is NOT trusted). The daemon is an inbound
    # remote-execution channel, so blind-trusting any device that messages it is
    # the wrong default. Prefer `xlii pair` to pin a device. Set
    # `[trust] blind_trust = true` only as an escape hatch for debugging.
    blind_trust: bool = False

    # Admin tier (`[trust] admin_jids`) — the roster's first role column: the
    # JIDs allowed to run the daemon's control verbs (webcode / kill /
    # remote-control). Empty
    # (the default) = every allowlisted JID is an admin — the single-owner
    # backward-compatible posture. Non-admins still get verbs and agent turns.
    admin_jids: list[str] = field(default_factory=list)

    # Fabric elevation (hidden `/xsu`): the env var holding the base32 TOTP
    # secret that gates destructive verbs. Env-only, never a file (same posture
    # as the daemon password / management key). Unset → the elevation gate is
    # disabled and destructive verbs keep their admin-tier gate. Provision with
    # `xlii daemon totp`.
    totp_secret_env: str = "XLII_DAEMON_TOTP_SECRET"

    # Message grammar (`[policy] grammar`). "slash" (default) projects the
    # REPL's own sigil onto the mouth: bare text = a chat/agent turn,
    # `/webcode` `/kill` `/<verb>` = commands — texting "kill the lights idea"
    # is a prompt, not a shutdown. "bare" is the legacy first-word-verb grammar.
    grammar: str = "slash"

    # Launch policy (Vector E). Secure defaults: the daemon is *keyed* — a key
    # (the admin secret) must be presented at $XLII_DAEMON_KEY and verified before
    # it starts, gated at the function chokepoint (see evaluate_daemon_launch) so
    # CLI/cron can't bypass it — and is neither auto-started nor always-on.
    # Loosening any of these is opt-in and warned about loudly at launch.
    keyed: bool = True
    autostart: bool = False
    always_on: bool = False

    # X2 reply ergonomics: if a dispatch (verb or agent one-shot) runs longer
    # than this many seconds, send ONE encrypted "still working…" message so
    # the phone doesn't read dead air as a dead daemon. 0 = off (default).
    # Deliberately a single ping, not a stream — XMPP messages are discrete.
    progress_after_s: int = 0

    @classmethod
    def load(cls, path: Path) -> "DaemonConfig":
        with open(path, "rb") as f:
            data = tomllib.load(f)
        d = data.get("daemon", {})
        wl = data.get("whitelist", {})
        rl = data.get("rate_limit", {})
        af = data.get("agent_fallback", {})
        tr = data.get("trust", {})
        po = data.get("policy", {})
        grammar = str(po.get("grammar", "slash")).strip().lower()
        if grammar not in ("slash", "bare"):
            raise ValueError(
                f"[policy] grammar must be 'slash' or 'bare'; got {grammar!r}"
            )
        return cls(
            jid=d["jid"],
            password_env=d.get("password_env", "XMPP_DAEMON_PASSWORD"),
            state_file=Path(d.get("state_file", str(DEFAULT_OMEMO_STATE))).expanduser(),
            verbs_dir=Path(d.get("verbs_dir", str(DEFAULT_VERBS_DIR))).expanduser(),
            audit_log=Path(d.get("audit_log", str(DEFAULT_AUDIT_LOG))).expanduser(),
            allowed_jids=list(wl.get("allowed_jids", [])),
            max_per_minute=int(rl.get("max_per_minute", 10)),
            lockout_threshold=int(rl.get("lockout_threshold", 5)),
            lockout_duration_s=int(rl.get("lockout_duration_s", 300)),
            fallback_enabled=bool(af.get("enabled", True)),
            fallback_workspace=str(af.get("default_workspace", "")),
            fallback_persona=str(af.get("persona", "")).strip(),
            node_name=str(d.get("node_name", "")).strip(),
            totp_secret_env=str(tr.get("totp_secret_env", "XLII_DAEMON_TOTP_SECRET")).strip()
            or "XLII_DAEMON_TOTP_SECRET",
            blind_trust=bool(tr.get("blind_trust", False)),
            admin_jids=list(tr.get("admin_jids", [])),
            grammar=grammar,
            keyed=bool(po.get("keyed", True)),
            autostart=bool(po.get("autostart", False)),
            always_on=bool(po.get("always_on", False)),
            progress_after_s=int(d.get("progress_after_s", 0)),
        )


@dataclass
class NotifyConfig:
    """Config for the send-only notification rail (`xlii notify`)."""

    jid: str               # the sender's own JID
    password_env: str
    recipient: str         # the JID to send notifications to (your phone)
    state_file: Path
    # Trusting the *recipient's* devices to encrypt to them. Secure-default off,
    # same posture as the daemon (see DaemonConfig.blind_trust).
    blind_trust: bool = False

    @classmethod
    def load(cls, path: Path) -> "NotifyConfig":
        with open(path, "rb") as f:
            data = tomllib.load(f)
        n = data.get("notify", {})
        tr = data.get("trust", {})
        # Accept blind_trust from a dedicated [trust] section OR inline in
        # [notify] — a single-section notify.toml is the intuitive shape, and
        # silently ignoring an inline blind_trust (defaulting to distrust) is a
        # footgun that makes the whole rail look broken.
        blind_trust = tr.get("blind_trust", n.get("blind_trust", False))
        return cls(
            jid=n["jid"],
            password_env=n.get("password_env", "XMPP_NOTIFY_PASSWORD"),
            recipient=n["recipient"],
            state_file=Path(n.get("state_file", str(DEFAULT_NOTIFY_STATE))).expanduser(),
            blind_trust=bool(blind_trust),
        )


class RateLimiter:
    """Per-JID sliding-window rate limit + repeated-deny lockout.

    Keeps a 60-second deque of accept timestamps per JID. If the JID submits
    more than `max_per_minute` accepts within the window, subsequent messages
    are denied. After `lockout_threshold` consecutive denials, the JID is
    locked out for `lockout_duration_s` seconds (no messages accepted at all).
    """

    def __init__(self, max_per_minute: int, lockout_threshold: int, lockout_duration_s: int):
        self.max_per_minute = max_per_minute
        self.lockout_threshold = lockout_threshold
        self.lockout_duration_s = lockout_duration_s
        self.windows: dict[str, deque[float]] = defaultdict(deque)
        self.consecutive_denied: dict[str, int] = defaultdict(int)
        self.lockout_until: dict[str, float] = {}

    def check(self, jid: str) -> tuple[bool, Optional[str]]:
        now = time.time()
        if jid in self.lockout_until:
            if now < self.lockout_until[jid]:
                left = int(self.lockout_until[jid] - now)
                return (False, f"locked out, {left}s remaining")
            del self.lockout_until[jid]
            self.consecutive_denied[jid] = 0

        window = self.windows[jid]
        cutoff = now - 60
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= self.max_per_minute:
            self.consecutive_denied[jid] += 1
            if self.consecutive_denied[jid] >= self.lockout_threshold:
                self.lockout_until[jid] = now + self.lockout_duration_s
                self.consecutive_denied[jid] = 0
                return (False, f"rate limit hit too often; locked out {self.lockout_duration_s}s")
            return (False, f"rate limit ({self.max_per_minute}/min) exceeded")

        window.append(now)
        self.consecutive_denied[jid] = 0
        return (True, None)


# --------------------------------------------------------------------------- #
#  Keyed launch gate — the daemon's function chokepoint (Vector E)
# --------------------------------------------------------------------------- #
#
# `xlii daemon` is an inbound remote-execution channel, so *starting* it is the
# privileged act. We gate it at the function chokepoint (daemon.run), not the
# REPL command, because a CLI/cron invocation bypasses any command-only gate.
# The decision is pure — the key check is injected — so it's unit-testable
# without the vault or the [daemon] extra; daemon.run wires in vault.verify_*.

KEY_ENV = "XLII_DAEMON_KEY"


@dataclass
class DaemonLaunchDecision:
    """Whether the daemon may launch under the current policy — computed, not
    executed. `warnings` are emitted regardless of `allowed` (loosened device/run
    policy is loud even on an otherwise-clean keyed launch)."""

    allowed: bool
    reason: str                          # one-line audit / explain string
    warnings: list[str] = field(default_factory=list)


def evaluate_daemon_launch(
    *,
    keyed: bool,
    key_is_set: bool,
    provided_key: Optional[str],
    verify_key: Callable[[str], bool],
    blind_trust: bool = False,
    autostart: bool = False,
    always_on: bool = False,
) -> DaemonLaunchDecision:
    """Decide whether the daemon may start.

    Secure default is ``keyed=True``: a key (the admin secret) must be presented
    via ``$XLII_DAEMON_KEY`` and verified (``verify_key``) before launch.
    ``keyed=False`` opts into a keyless launch — allowed, but loud. Loosened
    device/run policy (``blind_trust``/``autostart``/``always_on``) is surfaced
    as warnings independently of the keyed outcome. The check fails closed: an
    unset key, a missing ``$XLII_DAEMON_KEY``, or a mismatch all refuse.
    """
    warnings: list[str] = []
    if blind_trust:
        warnings.append(
            "INSECURE: [trust] blind_trust = true — the daemon trusts ANY new "
            "OMEMO device that messages it. Prefer `xlii pair`; keep this only "
            "as an escape hatch for debugging trust state."
        )
    if autostart:
        warnings.append("[policy] autostart = true — the daemon is set to start unattended.")
    if always_on:
        warnings.append("[policy] always_on = true — the daemon is set to keep itself running.")

    if not keyed:
        warnings.append(
            "INSECURE: [policy] keyed = false — the daemon will start WITHOUT a "
            "key, so anyone who can run `xlii daemon` opens the remote-execution "
            "channel. Set keyed = true (the default) to require the admin key."
        )
        return DaemonLaunchDecision(True, "keyless launch (policy keyed=false)", warnings)

    if not key_is_set:
        return DaemonLaunchDecision(
            False,
            "daemon is keyed but no admin key is set — run `xlii` then "
            "/admin set-key (or set [policy] keyed = false to opt out, loudly)",
            warnings,
        )
    if not provided_key:
        return DaemonLaunchDecision(
            False,
            f"daemon is keyed — export ${KEY_ENV} (the admin key) to launch it",
            warnings,
        )
    if not verify_key(provided_key):
        return DaemonLaunchDecision(
            False,
            f"${KEY_ENV} does not match the admin key — refusing to launch",
            warnings,
        )
    return DaemonLaunchDecision(True, "keyed launch verified", warnings)


# --------------------------------------------------------------------------- #
#  Dispatch routing — pure decision, no execution
# --------------------------------------------------------------------------- #

# Optional workspace prefix: "[alias] message…" overrides the agent-fallback
# target. DOTALL so a multi-line body after the prefix is captured whole.
WORKSPACE_PREFIX_RE = re.compile(r"^\[([\w][\w.-]*)\]\s*(.*)$", flags=re.DOTALL)


# --------------------------------------------------------------------------- #
#  webcode — phone-side pairing verb (serve-public P1)
# --------------------------------------------------------------------------- #
#
# Built-in like `kill`: rides whitelist + device pinning + the global rate
# limiter unchanged. Mint/preview also get a *stricter* per-JID mint window
# (open-Q4): a code mint is heavier than a status query — each successful mint
# is a shell grant waiting on the public entry page.
#
# Preview mode is the risk-reducer: serve spawns `xlii code --preview --tui`
# (read-only), not a full shell. Full mode → `xlii code --tui`.

WEBCODE_VERB = "webcode"
REMOTE_CONTROL_VERB = "remote-control"
REMOTE_CONTROL_ALIASES = frozenset({"remote-control", "remote-lab"})
# Daemon bare `/remote-control` opens sitting (the privileged act, like
# webcode mint). Face REPL bare `/remote-control` stays status.

# Serve command lines keyed by spool mode (V2 honors these when pairing).
WEBCODE_SERVE_CMD = {
    "full": ("xlii", "code", "--tui"),
    "preview": ("xlii", "code", "--preview", "--tui"),
}
# open-Q4 decision: at most 3 successful mints per rolling 5 minutes per JID
# (global RateLimiter still applies to every inbound message first).
WEBCODE_MINT_MAX = 3
WEBCODE_MINT_WINDOW_S = 300
# Pinned OMEMO reply shape (fleet contract) — tests lock this string.
# "code: X7K2-M9Q4  ·  https://xlii-code.com/?code=X7K2-M9Q4  ·  expires in 5m"
WEBCODE_REPLY_SEP = "  ·  "
# Same bound serve_spool.append_revoke enforces — reject before audit/reply/file.
WEBCODE_KILL_TARGET_MAX = 128


@dataclass
class DispatchDecision:
    """Where one inbound daemon message should go — computed, not executed.

    Pure routing: no slixmpp, no subprocess, no daemon state mutated. The live
    CommandDaemon turns this into an action (set the shutdown flag / run the verb
    / run the agent / send the canned reply). Extracted here so the routing
    policy is unit-testable without the [daemon] extra.
    """

    kind: str                          # "kill" | "verb" | "agent" | "unknown" | "webcode" | "elevate" | "node" | "remote_lab"
    audit: str                         # status string for the audit log
    reply: Optional[str] = None        # canned reply for "kill"/"unknown"
    verb_path: Optional[Path] = None   # "verb": the script to exec
    verb_args: list[str] = field(default_factory=list)
    prompt: Optional[str] = None       # "agent": the prompt to forward
    workspace: str = ""                # "agent": target; "" ⇒ most-recent project
    session: str = ""                  # "agent": `xlii ask --session` id ("" ⇒ one-shot)
    # webcode: action is mint|preview|ls|last|kill; mode is full|preview for mint arms;
    # kill_target is a session id or "all" (kill only).
    webcode_action: Optional[str] = None
    webcode_mode: str = "full"
    webcode_kill_target: Optional[str] = None
    # "elevate": the TOTP code from the hidden /xsu command (fabric elevation).
    elevate_code: Optional[str] = None
    # remote_lab: open|lock|unlock|drop|status. Daemon bare verb = open.
    remote_lab_action: Optional[str] = None


def format_webcode_reply(code: str, base_url: str, ttl_s: int) -> str:
    """Pinned OMEMO reply for a successful mint (fleet contract).

    The URL is the magic link — it carries ``?code=`` so the phone's tap lands
    on a prefilled pairing form (GET peeks, never consumes).
    """
    if ttl_s >= 60 and ttl_s % 60 == 0:
        exp = f"{ttl_s // 60}m"
    else:
        exp = f"{ttl_s}s"
    link = f"{base_url.rstrip('/')}/?code={code}"
    return (
        f"code: {code}{WEBCODE_REPLY_SEP}{link}"
        f"{WEBCODE_REPLY_SEP}expires in {exp}"
    )


def webcode_serve_cmd(mode: str) -> tuple[str, ...]:
    """Command line serve will spawn for a paired session of ``mode``."""
    try:
        return WEBCODE_SERVE_CMD[mode]
    except KeyError as e:
        raise ValueError(f"unknown webcode mode: {mode!r}") from e


class WebcodeMintLimiter:
    """Stricter per-JID mint window (open-Q4) — successful mints only.

    Denies a 4th mint inside ``WEBCODE_MINT_WINDOW_S`` without touching the
    global RateLimiter. ``check`` is pure observation; ``record`` is called
    only after a mint actually lands in the spool.
    """

    def __init__(
        self,
        *,
        max_per_window: int = WEBCODE_MINT_MAX,
        window_s: int = WEBCODE_MINT_WINDOW_S,
    ) -> None:
        self.max_per_window = max_per_window
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, jid: str, *, now: float) -> tuple[bool, Optional[str]]:
        window = self._hits[jid]
        cutoff = now - self.window_s
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.max_per_window:
            return (
                False,
                f"webcode mint rate limit ({self.max_per_window}/"
                f"{self.window_s}s) exceeded",
            )
        return (True, None)

    def record(self, jid: str, *, now: float) -> None:
        self._hits[jid].append(now)


def _classify_webcode(body: str) -> DispatchDecision:
    """Parse ``webcode`` / ``webcode preview`` / ``ls`` / ``kill …``."""
    parts = body.split()
    # parts[0] is "webcode" (already lowercased by caller via first_word check;
    # re-lower the rest for sub-verb matching).
    rest = [p.lower() for p in parts[1:]]

    if not rest:
        return DispatchDecision(
            kind="webcode",
            audit="webcode: mint",
            webcode_action="mint",
            webcode_mode="full",
        )
    head = rest[0]
    if head == "preview":
        return DispatchDecision(
            kind="webcode",
            audit="webcode: preview",
            webcode_action="preview",
            webcode_mode="preview",
        )
    if head in ("email", "mail"):
        mode = "preview" if rest[1:] and rest[1] == "preview" else "full"
        return DispatchDecision(
            kind="webcode",
            audit=f"webcode: email ({mode})",
            webcode_action="email",
            webcode_mode=mode,
        )
    if head == "ls":
        return DispatchDecision(
            kind="webcode",
            audit="webcode: ls",
            webcode_action="ls",
        )
    if head == "last":
        return DispatchDecision(
            kind="webcode",
            audit="webcode: last",
            webcode_action="last",
        )
    if head == "kill":
        target = rest[1] if len(rest) > 1 else None
        if not target or len(target) > WEBCODE_KILL_TARGET_MAX:
            return DispatchDecision(
                kind="webcode",
                audit="webcode: kill (missing/oversized id)",
                webcode_action="kill",
                reply="[daemon] usage: webcode kill <id>|all",
            )
        return DispatchDecision(
            kind="webcode",
            audit=f"webcode: kill {target}",
            webcode_action="kill",
            webcode_kill_target=target,
        )
    # Unknown sub-verb — still webcode-kind so we don't fall through to agent
    # with a half-parsed pairing intent.
    return DispatchDecision(
        kind="webcode",
        audit=f"webcode: unknown sub-verb {head}",
        webcode_action=None,
        reply=(
            "[daemon] usage: webcode | webcode preview | webcode ls | "
            "webcode last | webcode email | webcode kill <id>|all"
        ),
    )


_REMOTE_LAB_ACTIONS = frozenset({
    "open", "start", "lock", "unlock", "drop", "close", "status", "show",
})


def _classify_remote_lab(body: str) -> DispatchDecision:
    """Parse ``remote-control`` / ``open`` / ``lock`` / ``drop`` / ``status``.

    No sub-verb on the daemon = open (``/xsu`` then ``/remote-control``).
    """
    parts = body.split()
    rest = [p.lower() for p in parts[1:]]
    if not rest:
        action = "open"
    else:
        head = rest[0]
        if head not in _REMOTE_LAB_ACTIONS:
            return DispatchDecision(
                kind="remote_lab",
                audit=f"remote-control: unknown sub-verb {head}",
                remote_lab_action=None,
                reply=(
                    "[daemon] usage: /remote-control "
                    "[open|lock|unlock|drop|status]"
                ),
            )
        action = {
            "start": "open",
            "close": "drop",
            "show": "status",
        }.get(head, head)
    return DispatchDecision(
        kind="remote_lab",
        audit=f"remote-control: {action}",
        remote_lab_action=action,
    )


# Session mirror + revoke spool filenames (serve writes the mirror; daemon/admin
# append revokes). Shared state-dir with serve-grants.json (body contract #1).
SESSIONS_MIRROR_NAME = "serve-sessions.json"
REVOKES_SPOOL_NAME = "serve-revokes.json"


def read_sessions_mirror(state_dir: Path) -> list[dict]:
    """Load serve's session mirror; corrupt/missing → empty list."""
    import json
    path = Path(state_dir) / SESSIONS_MIRROR_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    sessions = data.get("sessions")
    if not isinstance(sessions, list):
        return []
    out: list[dict] = []
    for s in sessions:
        if not isinstance(s, dict) or not s.get("id"):
            continue
        # Coerce the numerics here so one hand-mangled entry can't turn the
        # whole `webcode ls` reply into a daemon error (float() would raise).
        s = dict(s)
        paired = s.get("paired_at", 0.0)
        s["paired_at"] = float(paired) if isinstance(paired, (int, float)) else 0.0
        last = s.get("last_activity", s["paired_at"])
        s["last_activity"] = float(last) if isinstance(last, (int, float)) else s["paired_at"]
        out.append(s)
    return out


def format_sessions_ls(sessions: list[dict], *, now: float) -> str:
    """Human-readable ``webcode ls`` reply."""
    if not sessions:
        return "[daemon] no active webcode sessions"
    lines = ["[daemon] webcode sessions:"]
    for s in sessions:
        sid = str(s.get("id", "?"))
        mode = str(s.get("mode", "?"))
        paired = float(s.get("paired_at", now))
        last = float(s.get("last_activity", paired))
        idle_s = max(0, int(now - last))
        lines.append(f"  {sid}  mode={mode}  idle={idle_s}s  paired_at={int(paired)}")
    return "\n".join(lines)


def daemon_state_dir(cfg: object = None) -> Path:
    """Shared node state dir (body contract #1 — same accessor serve drains).

    NOT derived from ``cfg.audit_log``: a relocated audit log in daemon.toml would
    silently move the grant spool where serve never looks (#283's sibling),
    and the canonical accessor honors ``XLII_STATE_DIR``.

    ``cfg`` is accepted for call-site clarity but intentionally ignored — the
    spool dir is always :func:`xlii.serve_spool.default_state_dir`.
    """
    from xlii.serve_spool import default_state_dir

    return default_state_dir()


def append_revoke(state_dir: Path, target: str) -> None:
    """Queue a revoke for serve to honor (session id or ``all``).

    Thin wrapper over :func:`xlii.serve_spool.append_revoke` so the daemon, the
    admin CLI, and serve's drain share ONE queue implementation (flock'd,
    deduped, length-capped) instead of two drifting copies."""
    from xlii.serve_spool import append_revoke as _append_revoke

    _append_revoke(Path(state_dir), target)


def mint_webcode_to_spool(
    state_dir: Path,
    *,
    mode: str,
    ttl_s: int,
    now: float,
    base_url: str,
) -> str:
    """Mint a pairing code into the grant spool; return the pinned OMEMO reply.

    Preview mode → serve spawns ``xlii code --preview --tui`` (see
    :func:`webcode_serve_cmd`). Raises ValueError if ``base_url`` is empty.
    """
    from xlii.serve_gate import mint_code
    from xlii.serve_spool import append_pending

    if not (base_url or "").strip():
        raise ValueError(
            "serve.public.base_url is not configured — set it in "
            "config.json under [serve.public] before minting a webcode"
        )
    webcode_serve_cmd(mode)  # validate mode → serve command mapping
    code = mint_code()
    append_pending(Path(state_dir), code, ttl_s=ttl_s, mode=mode, now=now)
    return format_webcode_reply(code, base_url.strip(), ttl_s)


# --------------------------------------------------------------------------- #
#  Elevation gate — TOTP-gated destructive commands + 3-strike lockout
# --------------------------------------------------------------------------- #
#
# The hidden `/xsu` command (never advertised — README prose only) elevates a
# sender so a destructive verb (`kill`, `webcode`) or phone `/remote-control`
# can run. A compromised chat device alone can't destroy anything: it lacks
# the fresh code from a *separate* authenticator. Three bad attempts in a
# minute LOCK the whole daemon until an SSH/console restart — and because the
# lock is in-memory, the restart is the recovery (nothing to un-set).
#
# /xsu is an idle window: 5 minutes with no inbound from that sender, then
# the elevation lapses. Activity refreshes it. kill/webcode still consume
# the elevation (one code → one destructive act). Phone /remote-control
# open consumes too, unless Face already opened sitting (no /xsu needed).

# A successful /xsu elevates until 5 minutes of inactivity.
ELEVATION_WINDOW_S = 300
# Failures inside this rolling window count toward the lockout.
ELEVATION_FAIL_WINDOW_S = 60
# The 2nd failure is the "got sidetracked" grace; the 3rd locks the daemon.
ELEVATION_LOCKOUT_AT = 3


class ElevationGate:
    """Per-sender TOTP elevation for destructive verbs, with a 3-strike lockout.

    Stateful and time-injected (``now`` always passed) so the state machine is
    deterministically unit-testable without a clock or a live daemon. Disabled
    (``configured`` False) when no secret is set — the daemon then falls back to
    its existing admin-tier gate, so nothing regresses until an owner opts in.
    """

    def __init__(
        self,
        secret: str,
        *,
        window_s: int = ELEVATION_WINDOW_S,
        fail_window_s: int = ELEVATION_FAIL_WINDOW_S,
        lockout_at: int = ELEVATION_LOCKOUT_AT,
    ) -> None:
        self._secret = secret or ""
        self._window_s = window_s
        self._fail_window_s = fail_window_s
        self._lockout_at = lockout_at
        self._elevated_until: dict[str, float] = {}
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._locked = False

    @property
    def configured(self) -> bool:
        """True when a TOTP secret is set — the gate is armed."""
        return bool(self._secret)

    def is_locked(self) -> bool:
        return self._locked

    def is_elevated(self, sender: str, *, now: float) -> bool:
        return now < self._elevated_until.get(sender, 0.0)

    def attempt(self, sender: str, code: str, *, now: float) -> str:
        """Try to elevate ``sender`` with ``code``. Returns a result token:

        ``"elevated"`` — code valid; sender is elevated until idle timeout.
        ``"invalid"``  — first bad code in the rolling minute.
        ``"grace"``    — second bad code ("got sidetracked", one more try).
        ``"locked"``   — third bad code: the daemon is locked until restart.
        """
        if self._locked:
            return "locked"
        from xlii import totp

        if self._secret and totp.verify(self._secret, code, now):
            self._elevated_until[sender] = now + self._window_s
            self._fails.pop(sender, None)
            return "elevated"

        window = self._fails[sender]
        cutoff = now - self._fail_window_s
        while window and window[0] < cutoff:
            window.popleft()
        window.append(now)
        if len(window) >= self._lockout_at:
            self._locked = True
            return "locked"
        if len(window) == 2:
            return "grace"
        return "invalid"

    def touch(self, sender: str, *, now: float) -> bool:
        """Refresh the idle clock if ``sender`` is currently elevated."""
        if not self.is_elevated(sender, now=now):
            return False
        self._elevated_until[sender] = now + self._window_s
        return True

    def consume_elevation(self, sender: str) -> None:
        """Spend the elevation after a destructive verb runs (one code → one act).

        Belt-and-suspenders on top of the idle window: kill/webcode/
        phone-minted remote-control don't ride the rest of the 5 minutes."""
        self._elevated_until.pop(sender, None)


def ask_command_line(
    prompt: str, *, persona: str = "", workspace: str = "", session: str = "",
    attachments: "Optional[list[str]]" = None, outbox: str = "",
) -> list[str]:
    """The ``xlii ask`` argv the daemon spawns for an agent turn (pure, testable).

    Persona wins (fabric F2 / the mojo): when set, the turn runs AS that persona
    over its own memory, and the persona is the continuity — so the per-sender
    ``--session`` and the project ``--workspace`` are dropped. Otherwise the
    legacy project-scoped turn: ``--workspace`` + ``--session``.

    ``attachments`` (media-in) become ``--attach <path>`` so a file fetched from
    the message (a photo, a PDF) reaches the turn's multimodal layer — the agent
    SEES it. ``outbox`` (media-out) becomes ``--outbox <dir>`` — the delivery
    channel the send_file tool queues into; the daemon uploads what lands there
    and it arrives in the sender's chat. Ordered before the prompt so argparse
    binds them, prompt last.
    """
    cmd = ["xlii", "ask"]
    if (persona or "").strip():
        cmd.extend(["--persona", persona.strip()])
        # Do not append --yolo. Headless paid-action confirms EOF-deny, which
        # is the correct default for a remote chat turn. The caller can still
        # elevate a specific verb; the authenticated JID is not a blanket
        # gates-off grant.
    else:
        if workspace:
            cmd.extend(["--workspace", workspace])
        if session:
            cmd.extend(["--session", session])
    for path in attachments or []:
        cmd.extend(["--attach", path])
    if outbox:
        cmd.extend(["--outbox", outbox])
    cmd.append(prompt)
    return cmd


def daemon_agent_persona(daemon_cfg: Any, *, force_lab: bool = False,
                         global_cfg: Any = None) -> str:
    """The persona a node mouth speaks as.

    Empty ``[agent_fallback] persona`` is still Mojo — the journal exists on
    every node install. ``force_lab`` (sitting ``$``) is the only faceless
    path. Never a second throne: the name is the limb, the persona is the soul.
    """
    if force_lab:
        return ""
    from xlii.persona import factory_persona_id, journal_knob_id

    raw = journal_knob_id(getattr(daemon_cfg, "fallback_persona", "") or "")
    if raw:
        return raw
    try:
        return factory_persona_id(global_cfg)
    except Exception:
        return "mojo"


def derive_session_id(sender_jid: str, workspace: str = "") -> str:
    """The per-sender conversation id the agent fallback runs under (X1):
    ``xmpp:<bare-jid>[:<workspace>]``. One phone ↔ one continuous conversation
    per workspace; distinct whitelisted JIDs get isolated sessions for free.
    Pure string derivation — `xlii ask` owns the state keyed by this id."""
    bare = (sender_jid or "").split("/", 1)[0].strip()
    if not bare:
        return ""
    return f"xmpp:{bare}:{workspace}" if workspace else f"xmpp:{bare}"


def list_verbs(verbs_dir: Path) -> str:
    """Human-readable list of executable verb scripts in `verbs_dir`."""
    if not verbs_dir.is_dir():
        return "(none — daemon's verbs_dir does not exist)"
    names = sorted(p.stem for p in verbs_dir.glob("*.sh") if os.access(p, os.X_OK))
    return ", ".join(names) if names else "(none — verbs_dir is empty)"


def is_admin_jid(cfg: DaemonConfig, jid: str) -> bool:
    """Admin check for the control verbs (webcode / kill / remote-control).

    Empty ``admin_jids`` = every allowlisted JID is an admin (single-owner
    backward compat); otherwise bare-JID membership, case-insensitive,
    resource stripped. The whitelist gate upstream already decided *entry* —
    this only decides the role.
    """
    admins = [a.strip().lower() for a in cfg.admin_jids if a.strip()]
    if not admins:
        return True
    bare = (jid or "").split("/", 1)[0].strip().lower()
    return bare in admins


def _deny_control(verb: str) -> DispatchDecision:
    return DispatchDecision(
        kind="unknown", audit=f"denied {verb}: non-admin sender",
        reply="[daemon] not permitted.",
    )


def classify_dispatch(
    body: str,
    *,
    verbs_dir: Path,
    fallback_enabled: bool,
    fallback_workspace: str = "",
    sender: str = "",
    grammar: str = "bare",
    is_admin: bool = True,
) -> DispatchDecision:
    """Route one inbound message body to kill / webcode / verb / agent / unknown.

    Two grammars (``[policy] grammar``; the daemon defaults to "slash", this
    function defaults to "bare" so existing callers/tests are unchanged):

    **slash** — the REPL's own sigil, projected onto the mouth: a body starting
    with ``/`` is a command (``/kill`` · ``/webcode …`` · ``/<verb> …``; an
    unmatched ``/word`` is an unknown-command reply, never an agent prompt);
    anything else is a chat/agent turn — texting "kill the lights idea" is a
    prompt, not a shutdown. The slash is recognized on the RAW body only, so
    "[alias] /kill" stays a prompt (commands can't be smuggled behind a prefix
    — the same invariant bare grammar pins for kill).

    **bare** (legacy) — the control flow, in order:
      1. `kill` as the (unprefixed) first word → shut the daemon down.
      2. An optional `[alias] …` prefix overrides the agent-fallback workspace
         and is stripped before the rest of the routing runs. Note kill is
         checked *before* stripping, so "[alias] kill" is a prompt, not a
         shutdown — the kill switch can't be smuggled behind a prefix.
      3. `webcode` (+ preview / ls / kill) → the serve-public pairing verb
         (built-in; does not require a verbs_dir script).
      4. `remote-control` (+ open / lock / unlock / drop / status) → sitting.
         Bare verb = open.
      5. A first word matching an executable `<verbs_dir>/<word>.sh` → run it.
      6. Otherwise the agent fallback — unless it's disabled, in which case
         reply with the unknown-verb hint listing the available verbs.

    In both grammars the control verbs (kill / webcode / remote-control) are admin-gated:
    ``is_admin=False`` turns them into a dry "not permitted" reply with an
    audit marker (verbs and agent turns are unaffected).

    When `sender` is given, an agent decision also carries the per-sender
    conversation id (see derive_session_id) so the fallback is a continuing
    conversation instead of a stateless one-shot (X1).
    """
    body = body.strip()

    if grammar == "slash":
        return _classify_slash(
            body, verbs_dir=verbs_dir, fallback_enabled=fallback_enabled,
            fallback_workspace=fallback_workspace, sender=sender, is_admin=is_admin,
        )

    first_word = body.split(maxsplit=1)[0].lower() if body else ""

    if first_word == "kill":
        if not is_admin:
            return _deny_control("kill")
        return DispatchDecision(
            kind="kill", audit="shutdown requested", reply="[daemon] shutting down."
        )

    workspace_override = ""
    m = WORKSPACE_PREFIX_RE.match(body)
    if m:
        workspace_override = m.group(1)
        body = m.group(2).strip()
        first_word = body.split(maxsplit=1)[0].lower() if body else ""

    if first_word == WEBCODE_VERB:
        if not is_admin:
            return _deny_control("webcode")
        return _classify_webcode(body)

    if first_word in REMOTE_CONTROL_ALIASES:
        if not is_admin:
            return _deny_control("remote-control")
        return _classify_remote_lab(body)

    if first_word:
        verb_path = verbs_dir / f"{first_word}.sh"
        if verb_path.exists() and os.access(verb_path, os.X_OK):
            return DispatchDecision(
                kind="verb", audit=f"verb: {first_word}",
                verb_path=verb_path, verb_args=body.split()[1:],
            )

    if not fallback_enabled:
        return DispatchDecision(
            kind="unknown", audit=f"unknown verb: {first_word}",
            reply=f"[daemon] unknown verb '{first_word}'. Verbs: {list_verbs(verbs_dir)}",
        )

    workspace = workspace_override or fallback_workspace
    return DispatchDecision(
        kind="agent", audit=f"agent fallback (ws={workspace or 'auto'})",
        prompt=body, workspace=workspace,
        session=derive_session_id(sender, workspace) if sender else "",
    )


def _classify_slash(
    body: str,
    *,
    verbs_dir: Path,
    fallback_enabled: bool,
    fallback_workspace: str,
    sender: str,
    is_admin: bool,
) -> DispatchDecision:
    """The slash grammar: ``/`` on the raw body = command, everything else = chat."""
    if body.startswith("/"):
        cmd = body[1:].strip()
        first_word = cmd.split(maxsplit=1)[0].lower() if cmd else ""

        if first_word == "kill":
            if not is_admin:
                return _deny_control("kill")
            return DispatchDecision(
                kind="kill", audit="shutdown requested", reply="[daemon] shutting down."
            )
        if first_word == WEBCODE_VERB:
            if not is_admin:
                return _deny_control("webcode")
            return _classify_webcode(cmd)
        if first_word in REMOTE_CONTROL_ALIASES:
            if not is_admin:
                return _deny_control("remote-control")
            return _classify_remote_lab(cmd)
        # The hidden elevation command (fabric): never listed in any hint/help —
        # only the daemon and the README know it. The TOTP is the second factor
        # for admin-only destructive verbs; non-admin chat senders must not be
        # able to spend bad guesses into the global lockout.
        if first_word in ("xsu", "xsudo"):
            if not is_admin:
                return _deny_control("elevation")
            rest = cmd.split(maxsplit=1)
            return DispatchDecision(
                kind="elevate", audit="elevation attempt",
                elevate_code=(rest[1].strip() if len(rest) > 1 else ""),
            )
        # "which body am I talking to?" — the daemon answers with its node name
        # (fabric). Pure routing can't see the live name, so it only recognizes
        # the verb; the daemon fills self.cfg.node_name in _dispatch.
        if first_word in ("whoami", "node"):
            return DispatchDecision(kind="node", audit="node identity")
        if first_word:
            verb_path = verbs_dir / f"{first_word}.sh"
            if verb_path.exists() and os.access(verb_path, os.X_OK):
                return DispatchDecision(
                    kind="verb", audit=f"verb: {first_word}",
                    verb_path=verb_path, verb_args=cmd.split()[1:],
                )
        # A slashed miss is a command typo — never feed it to the agent.
        return DispatchDecision(
            kind="unknown", audit=f"unknown command: /{first_word}",
            reply=(f"[daemon] unknown command '/{first_word}'. "
                   f"Commands: /webcode · /remote-control · /kill · "
                   f"verbs: {list_verbs(verbs_dir)}"),
        )

    # Bare text = chat. The optional [alias] prefix still scopes the workspace.
    workspace_override = ""
    m = WORKSPACE_PREFIX_RE.match(body)
    if m:
        workspace_override = m.group(1)
        body = m.group(2).strip()

    if not fallback_enabled:
        return DispatchDecision(
            kind="unknown", audit="chat with fallback disabled",
            reply=(f"[daemon] chat fallback is disabled. "
                   f"Commands: /webcode · /remote-control · /kill · "
                   f"verbs: {list_verbs(verbs_dir)}"),
        )

    workspace = workspace_override or fallback_workspace
    return DispatchDecision(
        kind="agent", audit=f"agent fallback (ws={workspace or 'auto'})",
        prompt=body, workspace=workspace,
        session=derive_session_id(sender, workspace) if sender else "",
    )


# --------------------------------------------------------------------------- #
#  Reply ergonomics — chunk, don't truncate (X2)
# --------------------------------------------------------------------------- #

def chunk_reply(text: str, max_chars: int) -> list[str]:
    """Split a reply into message bodies of at most `max_chars`, breaking on
    paragraph boundaries first, then lines, then hard-splitting an oversized
    line. Multi-chunk replies get an ` (i/n)` ordering suffix. Replaces the
    old single-message `[:MAX_REPLY_CHARS]` cut — chunk, don't truncate."""
    if len(text) <= max_chars:
        return [text]
    # Reserve room for the ` (i/n)` suffix on every chunk (worst case ~10 chars
    # for three-digit counts) so suffixing never pushes a chunk over the limit.
    budget = max(1, max_chars - 10)

    pieces: list[str] = []
    for para in text.split("\n\n"):
        if len(para) <= budget:
            pieces.append(para)
            continue
        for line in para.split("\n"):
            if len(line) <= budget:
                pieces.append(line)
            else:
                pieces.extend(line[i:i + budget] for i in range(0, len(line), budget))

    chunks: list[str] = []
    cur = ""
    for piece in pieces:
        if not cur:
            cur = piece
        elif len(cur) + 2 + len(piece) <= budget:
            cur = f"{cur}\n\n{piece}"
        else:
            chunks.append(cur)
            cur = piece
    if cur:
        chunks.append(cur)

    if len(chunks) <= 1:
        return chunks or [text[:max_chars]]
    n = len(chunks)
    return [f"{c} ({i}/{n})" for i, c in enumerate(chunks, 1)]
