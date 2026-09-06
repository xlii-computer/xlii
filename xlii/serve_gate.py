"""Pure pairing-code gate for ``xlii serve --public`` (serve-public P0).

Sibling of ``daemon_gate.py``: mint / normalize / expiry / lockout / session
register as dependency-free, time-injected functions so the fleet can unit-test
without the ``[web]`` extra or a live server. Caller owns persistence (spool /
session mirror); this module never touches the filesystem.

Codes are Crockford base32 minus confusables (I/L/O/U), 8 chars (~40 bits),
displayed grouped as ``X7K2-M9Q4``. Confusables map on normalize (I→1, O→0, L→1).
"""

from __future__ import annotations

import secrets
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from typing import Optional

# Crockford base32 without I L O U (confusables / ambiguous with 1 0).
# 32 symbols × 8 chars = 40 bits — pairing handshake, not a password.
CODE_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LEN = 8
_ALPHABET_SET = frozenset(CODE_ALPHABET)

# Map fat-finger / font confusables into the Crockford set before charset check.
_CONFUSABLE_MAP = str.maketrans({
    "I": "1",
    "L": "1",
    "O": "0",
    # lowercase handled by upper() first; keep these for completeness if callers
    # pass mixed case with confusables already uppered.
})

# Lockout mirrors the daemon's posture: N fails in a window → locked for a spell.
# Tunable via GateStore constructor; defaults match daemon.toml rate_limit knobs.
DEFAULT_LOCKOUT_THRESHOLD = 5
DEFAULT_LOCKOUT_WINDOW_S = 300
DEFAULT_LOCKOUT_DURATION_S = 300

_VALID_MODES = frozenset({"full", "preview"})


def mint_code() -> str:
    """Return a fresh grouped pairing code, e.g. ``X7K2-M9Q4``."""
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    return group_code(raw)


def group_code(code: str) -> str:
    """Grouped display form (``X7K2-M9Q4``) of a code; normalizes first.

    Input that doesn't normalize to the canonical length passes through
    normalized but ungrouped — callers gate on :func:`is_valid_format` /
    :meth:`GateStore.peek` before showing it.
    """
    canon = normalize_code(code)
    if len(canon) != CODE_LEN:
        return canon
    return f"{canon[:4]}-{canon[4:]}"


def normalize_code(raw: str) -> str:
    """Canonical 8-char form: upper, strip separators/spaces, map confusables.

    Does not validate length/charset — use :func:`is_valid_format` for that.
    """
    s = (raw or "").strip().upper()
    # Drop grouping / whitespace / common separators humans type.
    for ch in ("-", " ", "_", "."):
        s = s.replace(ch, "")
    return s.translate(_CONFUSABLE_MAP)


def is_valid_format(code: str) -> bool:
    """True if ``code`` normalizes to exactly 8 Crockford-alphabet characters."""
    canon = normalize_code(code)
    return len(canon) == CODE_LEN and all(c in _ALPHABET_SET for c in canon)


@dataclass(frozen=True)
class Session:
    """One live grant after a successful pairing."""

    id: str
    paired_at: float
    last_activity: float
    mode: str          # "full" | "preview"
    remote: str


@dataclass
class _Pending:
    code: str          # canonical 8-char
    minted_at: float
    ttl_s: int
    mode: str


class GateStore:
    """In-memory pending-code register + lockout + live sessions. No I/O.

    Time is always injected via ``now: float`` so tests are deterministic.
    """

    def __init__(
        self,
        *,
        lockout_threshold: int = DEFAULT_LOCKOUT_THRESHOLD,
        lockout_window_s: int = DEFAULT_LOCKOUT_WINDOW_S,
        lockout_duration_s: int = DEFAULT_LOCKOUT_DURATION_S,
    ) -> None:
        self.lockout_threshold = lockout_threshold
        self.lockout_window_s = lockout_window_s
        self.lockout_duration_s = lockout_duration_s
        self._pending: dict[str, _Pending] = {}
        self._sessions: dict[str, Session] = {}
        self._fail_windows: dict[str, deque[float]] = defaultdict(deque)
        self._lockout_until: dict[str, float] = {}

    def add_pending(self, code: str, *, ttl_s: int, mode: str, now: float) -> None:
        """Register a pending pairing code (overwrites same code if re-minted)."""
        if mode not in _VALID_MODES:
            raise ValueError(f"mode must be one of {sorted(_VALID_MODES)}; got {mode!r}")
        if ttl_s <= 0:
            raise ValueError(f"ttl_s must be positive; got {ttl_s}")
        canon = normalize_code(code)
        if not is_valid_format(canon):
            raise ValueError(f"invalid pairing code format: {code!r}")
        self._pending[canon] = _Pending(
            code=canon, minted_at=now, ttl_s=ttl_s, mode=mode,
        )

    def peek(self, raw_code: str, *, now: float) -> bool:
        """Read-only: True iff ``raw_code`` is a live (unexpired) pending code.

        Never consumes, never records an attempt, never deletes — the
        closed-door ``?code=`` probe rides a GET, and GET never consumes a
        pairing code.
        """
        pending = self._pending.get(normalize_code(raw_code))
        if pending is None:
            return False
        return now < pending.minted_at + pending.ttl_s

    def consume(self, raw_code: str, *, now: float, remote: str) -> Optional[Session]:
        """Exchange a pairing code for a :class:`Session`, or ``None`` on failure.

        On success: pending code removed (single-use), attempt recorded as ok.
        On any failure: attempt recorded as fail, return ``None``. A locked
        remote never burns a still-valid pending code (another remote may pair).
        """
        if self.is_locked(remote, now=now):
            self.register_attempt(remote, ok=False, now=now)
            return None

        canon = normalize_code(raw_code)
        pending = self._pending.get(canon)
        if pending is None or not is_valid_format(canon):
            self.register_attempt(remote, ok=False, now=now)
            return None

        if now >= pending.minted_at + pending.ttl_s:
            # Dead grant — drop it so it can't be probed forever.
            del self._pending[canon]
            self.register_attempt(remote, ok=False, now=now)
            return None

        del self._pending[canon]
        sid = secrets.token_urlsafe(16)
        session = Session(
            id=sid,
            paired_at=now,
            last_activity=now,
            mode=pending.mode,
            remote=remote,
        )
        self._sessions[sid] = session
        self.register_attempt(remote, ok=True, now=now)
        return session

    def register_attempt(self, remote: str, *, ok: bool, now: float) -> None:
        """Record a pairing attempt for lockout accounting."""
        if ok:
            self._fail_windows.pop(remote, None)
            self._lockout_until.pop(remote, None)
            return

        window = self._fail_windows[remote]
        cutoff = now - self.lockout_window_s
        while window and window[0] < cutoff:
            window.popleft()
        window.append(now)

        if len(window) >= self.lockout_threshold:
            self._lockout_until[remote] = now + self.lockout_duration_s
            window.clear()

    def is_locked(self, remote: str, *, now: float) -> bool:
        """True if ``remote`` is inside an active lockout window."""
        until = self._lockout_until.get(remote)
        if until is None:
            return False
        if now >= until:
            del self._lockout_until[remote]
            return False
        return True

    def sessions(self) -> list[Session]:
        """Live sessions, stable order by ``paired_at`` then id."""
        return sorted(self._sessions.values(), key=lambda s: (s.paired_at, s.id))

    def touch(self, sid: str, *, now: float) -> None:
        """Update ``last_activity`` for a live session (no-op if unknown)."""
        session = self._sessions.get(sid)
        if session is None:
            return
        self._sessions[sid] = replace(session, last_activity=now)

    def revoke(self, sid: str) -> bool:
        """Kill a live session. True if a session died."""
        return self._sessions.pop(sid, None) is not None

    def sweep(
        self,
        *,
        now: float,
        idle_timeout_s: int,
        session_ttl_s: int,
    ) -> list[str]:
        """Drop idle and hard-TTL sessions; return the dead session ids."""
        dead: list[str] = []
        for sid, session in list(self._sessions.items()):
            idle = now - session.last_activity
            age = now - session.paired_at
            if idle >= idle_timeout_s or age >= session_ttl_s:
                del self._sessions[sid]
                dead.append(sid)
        # Prune lockout bookkeeping too: every distinct failing remote leaves a
        # window/lockout entry that is otherwise only cleaned on that remote's
        # NEXT attempt — on a public endpoint an attacker cycling addresses
        # grows these dicts without bound.
        cutoff = now - self.lockout_window_s
        for remote, window in list(self._fail_windows.items()):
            while window and window[0] < cutoff:
                window.popleft()
            if not window:
                del self._fail_windows[remote]
        for remote, until in list(self._lockout_until.items()):
            if now >= until:
                del self._lockout_until[remote]
        return dead
