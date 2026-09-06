"""serve-public V1 — pure GateStore + code mint/normalize (offline, no [web]).

Pinned against the merge contract in proposals/serve-public-fleet.md.
Run:  pytest tests/test_serve_gate.py
"""

from __future__ import annotations

import re

import pytest

from xlii.serve_gate import (
    CODE_ALPHABET,
    CODE_LEN,
    DEFAULT_LOCKOUT_DURATION_S,
    DEFAULT_LOCKOUT_THRESHOLD,
    DEFAULT_LOCKOUT_WINDOW_S,
    GateStore,
    Session,
    group_code,
    is_valid_format,
    mint_code,
    normalize_code,
)


# --------------------------------------------------------------------------- #
#  mint / normalize / format
# --------------------------------------------------------------------------- #

_GROUPED_RE = re.compile(
    rf"^[{re.escape(CODE_ALPHABET)}]{{4}}-[{re.escape(CODE_ALPHABET)}]{{4}}$"
)


def test_mint_code_charset_grouping_and_entropy_shape():
    code = mint_code()
    assert _GROUPED_RE.fullmatch(code), code
    # ~40 bits: 8 symbols from a 32-char alphabet
    assert len(normalize_code(code)) == CODE_LEN
    # Distinct mints almost surely differ (collision would be a bug, not flaky luck)
    assert len({mint_code() for _ in range(20)}) == 20


def test_normalize_upper_separators_and_confusables():
    # Separators / spaces / case
    assert normalize_code("x7k2-m9q4") == "X7K2M9Q4"
    assert normalize_code(" X7K2 M9Q4 ") == "X7K2M9Q4"
    assert normalize_code("x7k2_m9q4") == "X7K2M9Q4"
    # Confusables: I→1, O→0, L→1
    assert normalize_code("OI0L-I1L0") == "01011110"
    assert normalize_code("oi0l i1l0") == "01011110"


def test_is_valid_format_accepts_grouped_and_canonical():
    assert is_valid_format("X7K2-M9Q4")
    assert is_valid_format("X7K2M9Q4")
    assert is_valid_format("x7k2 m9q4")


@pytest.mark.parametrize("bad", [
    "",
    "SHORT",
    "TOOLONG12",          # 9 chars
    "XXXX-XXXXY",         # too long after normalize
    "!!!!!!!!",           # wrong charset
    "UUUU-UUUU",          # U excluded from Crockford set we use
])
def test_is_valid_format_rejects_malformed(bad):
    assert not is_valid_format(bad)


# --------------------------------------------------------------------------- #
#  GateStore — pending / consume / single-use / expiry
# --------------------------------------------------------------------------- #

def test_group_code_groups_normalized_input():
    assert group_code("x7k2 m9q4") == "X7K2-M9Q4"
    assert group_code("X7K2M9Q4") == "X7K2-M9Q4"
    assert group_code("X7K2-M9Q4") == "X7K2-M9Q4"
    # Wrong length passes through normalized but ungrouped.
    assert group_code("abc") == "ABC"


def test_peek_is_read_only_liveness():
    gate = GateStore()
    gate.add_pending("X7K2-M9Q4", ttl_s=300, mode="full", now=1_000.0)

    # Live (messy input normalizes) / expired / unknown.
    assert gate.peek("x7k2 m9q4", now=1_100.0) is True
    assert gate.peek("X7K2-M9Q4", now=1_300.0) is False
    assert gate.peek("ZZZZ-ZZZZ", now=1_100.0) is False
    assert gate.peek("garbage", now=1_100.0) is False

    # Read-only, all three ways (GET never consumes):
    # an expired-looking peek must NOT prune the pending entry…
    assert "X7K2M9Q4" in gate._pending
    # …misses must NOT feed lockout accounting…
    for _ in range(20):
        gate.peek("ZZZZ-ZZZZ", now=1_100.0)
    assert gate._fail_windows == {}
    # …and the code still pairs after any number of peeks.
    session = gate.consume("X7K2-M9Q4", now=1_100.0, remote="203.0.113.9")
    assert session is not None


def test_consume_single_use_and_returns_session():
    g = GateStore()
    code = mint_code()
    g.add_pending(code, ttl_s=300, mode="full", now=1000.0)
    s1 = g.consume(code, now=1001.0, remote="1.2.3.4")
    assert isinstance(s1, Session)
    assert s1.mode == "full"
    assert s1.remote == "1.2.3.4"
    assert s1.paired_at == 1001.0
    assert s1.last_activity == 1001.0
    assert s1.id
    # Second consume of the same code fails (single-use)
    assert g.consume(code, now=1002.0, remote="1.2.3.4") is None
    assert g.sessions() == [s1]


def test_consume_accepts_confusable_and_messy_input():
    g = GateStore()
    # Code with digits that confusables map onto
    g.add_pending("A1B0C1D0", ttl_s=60, mode="preview", now=0.0)
    s = g.consume("aIbO-ClDo", now=1.0, remote="r")
    assert s is not None
    assert s.mode == "preview"


def test_consume_rejects_expired_and_drops_pending():
    g = GateStore()
    code = mint_code()
    g.add_pending(code, ttl_s=60, mode="full", now=1000.0)
    assert g.consume(code, now=1060.0, remote="r") is None  # exactly at expiry
    # Expired entry is gone; a later mint of a different code still works
    assert g.consume(code, now=1061.0, remote="r") is None


def test_consume_unknown_code_is_none():
    g = GateStore()
    assert g.consume("AAAA-BBBB", now=1.0, remote="r") is None


def test_add_pending_rejects_bad_mode_and_ttl():
    g = GateStore()
    with pytest.raises(ValueError):
        g.add_pending(mint_code(), ttl_s=60, mode="write", now=0.0)
    with pytest.raises(ValueError):
        g.add_pending(mint_code(), ttl_s=0, mode="full", now=0.0)


# --------------------------------------------------------------------------- #
#  Lockout window + reset
# --------------------------------------------------------------------------- #

def test_lockout_after_n_fails_in_window_then_resets_on_success():
    g = GateStore(
        lockout_threshold=3,
        lockout_window_s=100,
        lockout_duration_s=50,
    )
    remote = "10.0.0.1"
    # Three fails within the window → lock
    for t in (10.0, 11.0, 12.0):
        g.register_attempt(remote, ok=False, now=t)
    assert g.is_locked(remote, now=12.0)
    assert g.is_locked(remote, now=61.0)  # still inside duration (12+50)
    assert not g.is_locked(remote, now=62.0)  # expired

    # After lockout clears, a success clears the fail window
    g.register_attempt(remote, ok=False, now=70.0)
    g.register_attempt(remote, ok=False, now=71.0)
    g.register_attempt(remote, ok=True, now=72.0)
    # Two more fails alone must not lock (threshold is 3; success reset the window)
    g.register_attempt(remote, ok=False, now=73.0)
    g.register_attempt(remote, ok=False, now=74.0)
    assert not g.is_locked(remote, now=74.0)


def test_locked_remote_cannot_consume_but_does_not_burn_code():
    g = GateStore(lockout_threshold=2, lockout_window_s=60, lockout_duration_s=100)
    code = mint_code()
    g.add_pending(code, ttl_s=300, mode="full", now=0.0)
    g.register_attempt("bad", ok=False, now=1.0)
    g.register_attempt("bad", ok=False, now=2.0)
    assert g.is_locked("bad", now=3.0)
    assert g.consume(code, now=3.0, remote="bad") is None
    # Different remote can still pair with the unburned code
    s = g.consume(code, now=4.0, remote="good")
    assert s is not None
    assert s.remote == "good"


def test_default_lockout_constants_match_daemon_posture():
    # Documented parity with daemon.toml defaults — keep the gate conservative.
    assert DEFAULT_LOCKOUT_THRESHOLD == 5
    assert DEFAULT_LOCKOUT_WINDOW_S == 300
    assert DEFAULT_LOCKOUT_DURATION_S == 300


# --------------------------------------------------------------------------- #
#  Session register / touch / revoke / sweep
# --------------------------------------------------------------------------- #

def test_sessions_touch_revoke():
    g = GateStore()
    code = mint_code()
    g.add_pending(code, ttl_s=300, mode="full", now=100.0)
    s = g.consume(code, now=101.0, remote="r")
    assert s is not None
    g.touch(s.id, now=150.0)
    live = g.sessions()
    assert len(live) == 1
    assert live[0].last_activity == 150.0
    assert g.revoke(s.id) is True
    assert g.sessions() == []
    assert g.revoke(s.id) is False  # already gone
    g.touch("missing", now=200.0)  # no-op


def test_sweep_by_idle_and_by_ttl():
    g = GateStore()
    # Two sessions via two codes
    c1, c2 = mint_code(), mint_code()
    g.add_pending(c1, ttl_s=300, mode="full", now=0.0)
    g.add_pending(c2, ttl_s=300, mode="preview", now=0.0)
    s1 = g.consume(c1, now=10.0, remote="a")
    s2 = g.consume(c2, now=10.0, remote="b")
    assert s1 and s2
    g.touch(s1.id, now=20.0)   # keep s1 warm
    # idle_timeout=30 → s2 idle since 10, dies at now=40; s1 touched at 20, survives
    dead = g.sweep(now=40.0, idle_timeout_s=30, session_ttl_s=10_000)
    assert dead == [s2.id]
    assert [s.id for s in g.sessions()] == [s1.id]

    # Hard TTL from paired_at: s1 paired at 10, ttl=50 → dies at now=60
    dead2 = g.sweep(now=60.0, idle_timeout_s=10_000, session_ttl_s=50)
    assert dead2 == [s1.id]
    assert g.sessions() == []


def test_sweep_prunes_stale_lockout_bookkeeping():
    """A remote that failed and walked away must not leak a window/lockout entry
    forever — sweep prunes both once they age out (public-endpoint hygiene)."""
    g = GateStore()  # threshold 5, window 300, duration 300
    for i in range(3):
        g.register_attempt("drive-by", ok=False, now=float(i))
    for i in range(5):
        g.register_attempt("locked-out", ok=False, now=float(i))
    assert g.is_locked("locked-out", now=10.0)
    assert "drive-by" in g._fail_windows

    # Inside the window/lockout, sweep keeps the bookkeeping.
    g.sweep(now=100.0, idle_timeout_s=10, session_ttl_s=10)
    assert "drive-by" in g._fail_windows
    assert "locked-out" in g._lockout_until

    # Past window + lockout duration, sweep drops both dicts empty.
    g.sweep(now=700.0, idle_timeout_s=10, session_ttl_s=10)
    assert g._fail_windows == {}
    assert g._lockout_until == {}
    assert not g.is_locked("locked-out", now=700.0)
