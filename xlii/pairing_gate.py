"""Bound-TOFU pairing window for ``xlii pair`` (qr-pair P1).

Sibling of ``serve_gate.py``: mint / verify / consume / lockout as
dependency-free, time-injected functions so the fleet can unit-test without
the ``[daemon]`` extra or a live XMPP server. Persistence is a flock'd JSON
bundle in the runtime dir (occupancy's rule: reboot clears it).

The pairing *code* is never stored — only its SHA-256. Possession is proven
when the phone sends the code over the message channel, not by scanning the
QR (the QR carries the desk's JID + fingerprint only).
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from xlii.atomicio import write_text_atomic
from xlii.serve_gate import (
    DEFAULT_LOCKOUT_DURATION_S,
    DEFAULT_LOCKOUT_THRESHOLD,
    DEFAULT_LOCKOUT_WINDOW_S,
    group_code,
    is_valid_format,
    mint_code,
    normalize_code,
)

RAILS = frozenset({"daemon", "notify", "face"})
DEFAULT_TTL_S = 600
PAIRING_FILE = "xlii-pairing.json"

# Audit / decision reasons — stable strings the daemon logs as status.
REASON_GRANT = "pair-grant"
REASON_INVITE = "pair-invite"
REASON_NO_WINDOW = "pair-no-window"
REASON_EXPIRED = "pair-expired"
REASON_REPLAY = "pair-replay"
REASON_WRONG_CODE = "pair-wrong-code"
REASON_LOCKOUT = "pair-lockout"
REASON_NOT_ALLOWLISTED = "pair-not-allowlisted"
REASON_INVITE_MISMATCH = "pair-invite-mismatch"


# --------------------------------------------------------------------------- #
#  Pure records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PairingWindow:
    """One live (or just-consumed) pairing window. The code itself is not here."""

    code_hash: str
    rail: str
    opened_at: float
    expires_at: float
    single_use: bool = True
    invite_jid: str = ""
    consumed_by: str = ""
    consumed_at: float = 0.0

    def live(self, now: float) -> bool:
        if self.consumed_by:
            return False
        return now < self.expires_at


@dataclass(frozen=True)
class PairDecision:
    """Outcome of one pairing attempt. ``granted`` is the only open."""

    granted: bool
    reason: str
    rail: str = "daemon"
    device_id: str = ""
    enroll_jid: str = ""
    window_closed: bool = False


def hash_code(raw: str) -> str:
    """SHA-256 of the canonical 8-char form. Empty / malformed still hashes
    (constant-time compare needs a digest either way)."""
    return hashlib.sha256(normalize_code(raw).encode("ascii")).hexdigest()


def codes_match(window: PairingWindow, presented: str) -> bool:
    """Constant-time compare of ``presented`` against the window's code-hash."""
    presented_hash = hash_code(presented)
    return hmac.compare_digest(window.code_hash, presented_hash)


def mint_window(
    *,
    rail: str = "daemon",
    ttl_s: int = DEFAULT_TTL_S,
    now: float | None = None,
    invite_jid: str = "",
    single_use: bool = True,
    code: str | None = None,
) -> tuple[PairingWindow, str]:
    """Return ``(window, grouped_code)``. ``code`` is injectable for tests."""
    if rail not in RAILS:
        raise ValueError(f"rail must be one of {sorted(RAILS)}; got {rail!r}")
    if ttl_s <= 0:
        raise ValueError(f"ttl_s must be positive; got {ttl_s}")
    if now is None:
        now = time.time()
    grouped = code if code is not None else mint_code()
    if not is_valid_format(grouped):
        raise ValueError(f"invalid pairing code format: {grouped!r}")
    invite = (invite_jid or "").strip()
    window = PairingWindow(
        code_hash=hash_code(grouped),
        rail=rail,
        opened_at=now,
        expires_at=now + ttl_s,
        single_use=single_use,
        invite_jid=invite,
    )
    return window, group_code(grouped)


def verify_code(window: PairingWindow, presented: str, *, now: float) -> str:
    """Return a reason string: ``pair-grant`` on match, else a deny reason.

    Does not mutate. Caller applies consume / lockout.
    """
    if window.consumed_by:
        return REASON_REPLAY
    if now >= window.expires_at:
        return REASON_EXPIRED
    if not is_valid_format(presented) or not codes_match(window, presented):
        return REASON_WRONG_CODE
    return REASON_GRANT


def consume(window: PairingWindow, device: str, *, now: float) -> PairingWindow:
    """Mark the window consumed by ``device``. Idempotent on an already-consumed
    window (returns it unchanged)."""
    if window.consumed_by:
        return window
    return PairingWindow(
        code_hash=window.code_hash,
        rail=window.rail,
        opened_at=window.opened_at,
        expires_at=window.expires_at,
        single_use=window.single_use,
        invite_jid=window.invite_jid,
        consumed_by=str(device),
        consumed_at=now,
    )


# --------------------------------------------------------------------------- #
#  OMEMO identity from the rail's JSON state (no slixmpp)
# --------------------------------------------------------------------------- #


def _ed25519_pub_to_curve25519_pub(ed_pub: bytes) -> bytes:
    """Birational map used by python-omemo's ``format_identity_key``.

    Conversations fingerprints are the Curve25519 form of the identity key.
    """
    if len(ed_pub) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    y = int.from_bytes(ed_pub, "little") & ((1 << 255) - 1)
    p = 2**255 - 19
    denom = (1 - y) % p
    if denom == 0:
        raise ValueError("invalid Ed25519 public key")
    u = ((1 + y) * pow(denom, p - 2, p)) % p
    return u.to_bytes(32, "little")


def own_identity_from_omemo_state(data: dict[str, Any]) -> Optional[tuple[int, str]]:
    """``(device_id, fingerprint_hex)`` from a slixmpp-omemo JSON ledger.

    Keys (python-omemo): ``/own_device_id`` and
    ``/devices/<jid>/<sid>/identity_key`` (urlsafe-b64 of the Ed25519 pub).
    Returns None when the rail has never connected (no identity yet).
    """
    sid = data.get("/own_device_id")
    if not isinstance(sid, int) or sid <= 0:
        return None
    suffix = f"/{sid}/identity_key"
    raw_b64 = None
    for key, value in data.items():
        if isinstance(key, str) and key.startswith("/devices/") and key.endswith(suffix):
            raw_b64 = value
            break
    if not isinstance(raw_b64, str) or not raw_b64:
        return None
    pad = "=" * ((4 - len(raw_b64) % 4) % 4)
    try:
        ik = base64.urlsafe_b64decode(raw_b64 + pad)
    except (ValueError, TypeError):
        return None
    if len(ik) != 32:
        return None
    try:
        fp = _ed25519_pub_to_curve25519_pub(ik).hex()
    except ValueError:
        return None
    return sid, fp


def load_own_identity(state_file: Path) -> Optional[tuple[int, str]]:
    """Read ``(device_id, fingerprint)`` from an on-disk OMEMO state file."""
    try:
        raw = json.loads(Path(state_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return own_identity_from_omemo_state(raw)


def omemo_verify_uri(jid: str, device_id: int, fingerprint: str) -> str:
    """Conversations/Dino/Monal fingerprint-QR URI (S0-confirmed).

    ``xmpp:<jid>?omemo-sid-<deviceId>=<fingerprint-hex>``
    Extra devices append ``;omemo-sid-<id>=<fp>``. The pairing *code* is
    not in this URI — it travels as the first message.
    """
    fp = "".join((fingerprint or "").split()).lower().replace(":", "")
    jid = (jid or "").strip()
    return f"xmpp:{jid}?omemo-sid-{int(device_id)}={fp}"


# --------------------------------------------------------------------------- #
#  Runtime-dir store (occupancy pattern)
# --------------------------------------------------------------------------- #


def pairing_path() -> Path:
    override = (os.environ.get("XLII_PAIRING_PATH") or "").strip()
    if override:
        return Path(override)
    runtime = (os.environ.get("XDG_RUNTIME_DIR") or "").strip()
    if runtime:
        return Path(runtime) / PAIRING_FILE
    return Path(tempfile.gettempdir()) / f"xlii-{os.getuid()}-{PAIRING_FILE}"


@contextmanager
def _pairing_lock(path: Path):
    lock = path.with_name(path.name + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield fd
    finally:
        os.close(fd)


def _window_from_dict(raw: Any) -> Optional[PairingWindow]:
    if not isinstance(raw, dict):
        return None
    rail = str(raw.get("rail") or "")
    if rail not in RAILS:
        return None
    try:
        opened = float(raw.get("opened_at") or 0.0)
        expires = float(raw.get("expires_at") or 0.0)
    except (TypeError, ValueError):
        return None
    code_hash = str(raw.get("code_hash") or "")
    if len(code_hash) != 64:
        return None
    return PairingWindow(
        code_hash=code_hash,
        rail=rail,
        opened_at=opened,
        expires_at=expires,
        single_use=bool(raw.get("single_use", True)),
        invite_jid=str(raw.get("invite_jid") or ""),
        consumed_by=str(raw.get("consumed_by") or ""),
        consumed_at=float(raw.get("consumed_at") or 0.0),
    )


def _window_to_dict(w: PairingWindow) -> dict[str, Any]:
    return {
        "code_hash": w.code_hash,
        "rail": w.rail,
        "opened_at": w.opened_at,
        "expires_at": w.expires_at,
        "single_use": w.single_use,
        "invite_jid": w.invite_jid,
        "consumed_by": w.consumed_by,
        "consumed_at": w.consumed_at,
    }


def _empty_bundle() -> dict[str, Any]:
    return {"windows": {}, "fails": {}, "lockout_until": {}}


def _read_bundle(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return _empty_bundle()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return _empty_bundle()
    if not isinstance(raw, dict):
        return _empty_bundle()
    windows = raw.get("windows")
    if not isinstance(windows, dict):
        windows = {}
    fails = raw.get("fails")
    if not isinstance(fails, dict):
        fails = {}
    lockout = raw.get("lockout_until")
    if not isinstance(lockout, dict):
        lockout = {}
    return {"windows": windows, "fails": fails, "lockout_until": lockout}


def _write_bundle(path: Path, bundle: dict[str, Any]) -> None:
    write_text_atomic(
        path,
        json.dumps(bundle, indent=2, sort_keys=True) + "\n",
        mode=0o600,
    )


class PairingStore:
    """Flock'd JSON store of per-rail windows + per-JID lockout.

    Time is injected via ``now`` on every mutating call. Missing/corrupt files
    read as a closed store (fail closed).
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        *,
        lockout_threshold: int = DEFAULT_LOCKOUT_THRESHOLD,
        lockout_window_s: int = DEFAULT_LOCKOUT_WINDOW_S,
        lockout_duration_s: int = DEFAULT_LOCKOUT_DURATION_S,
    ) -> None:
        self.path = Path(path) if path is not None else pairing_path()
        self.lockout_threshold = lockout_threshold
        self.lockout_window_s = lockout_window_s
        self.lockout_duration_s = lockout_duration_s

    def get(self, rail: str = "daemon") -> Optional[PairingWindow]:
        with _pairing_lock(self.path):
            return _window_from_dict(_read_bundle(self.path)["windows"].get(rail))

    def put(self, window: PairingWindow) -> None:
        with _pairing_lock(self.path):
            bundle = _read_bundle(self.path)
            bundle["windows"][window.rail] = _window_to_dict(window)
            _write_bundle(self.path, bundle)

    def clear(self, rail: str) -> None:
        with _pairing_lock(self.path):
            bundle = _read_bundle(self.path)
            bundle["windows"].pop(rail, None)
            _write_bundle(self.path, bundle)

    def unconsume(self, rail: str) -> None:
        """Undo a grant consume (trust-store write failed after evaluate)."""
        with _pairing_lock(self.path):
            bundle = _read_bundle(self.path)
            window = _window_from_dict(bundle["windows"].get(rail))
            if window is None or not window.consumed_by:
                return
            restored = PairingWindow(
                code_hash=window.code_hash,
                rail=window.rail,
                opened_at=window.opened_at,
                expires_at=window.expires_at,
                single_use=window.single_use,
                invite_jid=window.invite_jid,
                consumed_by="",
                consumed_at=0.0,
            )
            bundle["windows"][rail] = _window_to_dict(restored)
            _write_bundle(self.path, bundle)

    def locked_until(self, jid: str) -> float:
        with _pairing_lock(self.path):
            raw = _read_bundle(self.path)["lockout_until"].get(jid)
            try:
                return float(raw or 0.0)
            except (TypeError, ValueError):
                return 0.0

    def _record_fail_unlocked(self, bundle: dict[str, Any], jid: str, now: float) -> bool:
        """Record a failed attempt. Returns True if this trip closed the lockout."""
        stamps = [float(t) for t in (bundle["fails"].get(jid) or []) if _is_float(t)]
        cutoff = now - self.lockout_window_s
        stamps = [t for t in stamps if t >= cutoff]
        stamps.append(now)
        bundle["fails"][jid] = stamps
        if len(stamps) >= self.lockout_threshold:
            bundle["lockout_until"][jid] = now + self.lockout_duration_s
            bundle["fails"][jid] = []
            return True
        return False

    def evaluate(
        self,
        *,
        rail: str,
        sender_jid: str,
        body: str,
        device_id: str,
        allowlisted: bool,
        now: Optional[float] = None,
    ) -> PairDecision:
        """One pairing attempt against the live window. Mutates the store."""
        clock = time.time() if now is None else now
        sender = (sender_jid or "").strip()
        device = str(device_id or "")
        with _pairing_lock(self.path):
            bundle = _read_bundle(self.path)
            window = _window_from_dict(bundle["windows"].get(rail))
            if window is None:
                return PairDecision(False, REASON_NO_WINDOW, rail=rail)

            if clock >= window.expires_at:
                bundle["windows"].pop(rail, None)
                _write_bundle(self.path, bundle)
                return PairDecision(
                    False, REASON_EXPIRED, rail=rail, window_closed=True,
                )

            try:
                lock_until = float(bundle["lockout_until"].get(sender) or 0.0)
            except (TypeError, ValueError):
                lock_until = 0.0
            if lock_until > clock:
                return PairDecision(False, REASON_LOCKOUT, rail=rail)

            invite = (window.invite_jid or "").strip()
            invited = bool(invite) and sender == invite
            # Invite windows are exclusive: only the invited JID may consume,
            # even if some other JID is already on the allowlist.
            if invite and sender != invite:
                tripped = self._record_fail_unlocked(bundle, sender, clock)
                _write_bundle(self.path, bundle)
                return PairDecision(
                    False,
                    REASON_LOCKOUT if tripped else REASON_INVITE_MISMATCH,
                    rail=rail,
                )
            if not allowlisted and not invited:
                tripped = self._record_fail_unlocked(bundle, sender, clock)
                _write_bundle(self.path, bundle)
                return PairDecision(
                    False,
                    REASON_LOCKOUT if tripped else REASON_NOT_ALLOWLISTED,
                    rail=rail,
                    window_closed=False,
                )

            reason = verify_code(window, body, now=clock)
            if reason == REASON_REPLAY:
                return PairDecision(False, REASON_REPLAY, rail=rail)
            if reason == REASON_WRONG_CODE:
                tripped = self._record_fail_unlocked(bundle, sender, clock)
                closed = False
                if tripped:
                    bundle["windows"].pop(rail, None)
                    closed = True
                    reason = REASON_LOCKOUT
                _write_bundle(self.path, bundle)
                return PairDecision(
                    False, reason, rail=rail, window_closed=closed,
                )

            consumed = consume(window, device or sender, now=clock)
            bundle["windows"][rail] = _window_to_dict(consumed)
            bundle["fails"].pop(sender, None)
            _write_bundle(self.path, bundle)
            enroll = invite if invited and not allowlisted else ""
            grant_reason = REASON_INVITE if enroll else REASON_GRANT
            return PairDecision(
                True,
                grant_reason,
                rail=rail,
                device_id=consumed.consumed_by,
                enroll_jid=enroll,
                window_closed=True,
            )


def _is_float(v: Any) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def qr_ansi(payload: str) -> str:
    """Terminal half-block QR, or empty when the optional encoder is missing."""
    try:
        import qrcode
        import qrcode.constants
    except ImportError:
        return ""
    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(payload)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    # Pad to even height so half-blocks pair cleanly.
    if len(matrix) % 2:
        matrix = list(matrix) + [[False] * len(matrix[0])]
    lines: list[str] = []
    for y in range(0, len(matrix), 2):
        top, bot = matrix[y], matrix[y + 1]
        row = []
        for a, b in zip(top, bot):
            if a and b:
                row.append("█")
            elif a:
                row.append("▀")
            elif b:
                row.append("▄")
            else:
                row.append(" ")
        lines.append("".join(row))
    return "\n".join(lines)
