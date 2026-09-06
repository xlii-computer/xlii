"""Fabric elevation gate — hidden /xsu, TOTP, 3-strike lockout (offline).

Pins the state machine (elevate → window → consume) and the failure ladder
(invalid → grace → locked) without a live daemon or a real clock.
"""

from __future__ import annotations

from xlii import totp
from xlii.daemon_gate import (
    ElevationGate,
    classify_dispatch,
)

_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def _code(now: float) -> str:
    return totp._hotp(totp._decode_secret(_SECRET), int(now // 30), 6)


def _slash(body, tmp_path, **kw):
    kw.setdefault("fallback_enabled", True)
    return classify_dispatch(body, verbs_dir=tmp_path, grammar="slash", **kw)


# --------------------------------------------------------------------------- #
#  classify — /xsu is recognized, carries the code, stays HIDDEN
# --------------------------------------------------------------------------- #

def test_xsu_classifies_as_elevate_with_the_code(tmp_path):
    d = _slash("/xsu 287082", tmp_path)
    assert d.kind == "elevate" and d.elevate_code == "287082"
    # /xsudo is the same hidden verb.
    assert _slash("/xsudo 123456", tmp_path).elevate_code == "123456"
    # No code → empty (the daemon maps that to a denied attempt).
    assert _slash("/xsu", tmp_path).elevate_code == ""


def test_xsu_is_admin_only_so_chat_users_cannot_lock_daemon(tmp_path):
    d = _slash("/xsu 287082", tmp_path, is_admin=False)
    assert d.kind == "unknown"
    assert d.audit == "denied elevation: non-admin sender"
    assert d.reply == "[daemon] not permitted."


def test_xsu_is_never_named_in_the_unknown_command_hint(tmp_path):
    hint = _slash("/frobnicate", tmp_path).reply
    assert "xsu" not in hint.lower()      # hidden — README prose only
    assert "/webcode" in hint and "/kill" in hint
    assert "/remote-control" in hint


# --------------------------------------------------------------------------- #
#  ElevationGate — elevate window, consume, and the failure ladder
# --------------------------------------------------------------------------- #

def test_unconfigured_gate_is_disabled():
    gate = ElevationGate("")
    assert gate.configured is False
    assert gate.is_elevated("me@x", now=0) is False


def test_valid_code_elevates_for_one_window_then_expires():
    gate = ElevationGate(_SECRET)
    now = 10_000.0
    assert gate.attempt("me@x", _code(now), now=now) == "elevated"
    assert gate.is_elevated("me@x", now=now)
    assert gate.is_elevated("me@x", now=now + 299)
    assert not gate.is_elevated("me@x", now=now + 301)  # 5 min idle lapsed
    assert not gate.is_elevated("other@x", now=now)     # per-sender


def test_touch_refreshes_idle_window():
    gate = ElevationGate(_SECRET)
    now = 10_000.0
    gate.attempt("me@x", _code(now), now=now)
    assert not gate.touch("other@x", now=now + 10)
    assert gate.touch("me@x", now=now + 200)
    assert gate.is_elevated("me@x", now=now + 200 + 299)
    assert not gate.is_elevated("me@x", now=now + 200 + 301)
    assert not gate.touch("me@x", now=now + 200 + 301)


def test_consume_spends_the_elevation():
    gate = ElevationGate(_SECRET)
    now = 20_000.0
    gate.attempt("me@x", _code(now), now=now)
    assert gate.is_elevated("me@x", now=now)
    gate.consume_elevation("me@x")
    assert not gate.is_elevated("me@x", now=now)        # one code, one act


def test_failure_ladder_invalid_grace_then_lockout():
    gate = ElevationGate(_SECRET)
    now = 30_000.0
    assert gate.attempt("me@x", "000000", now=now) == "invalid"
    assert gate.attempt("me@x", "000000", now=now + 1) == "grace"
    assert gate.attempt("me@x", "000000", now=now + 2) == "locked"
    assert gate.is_locked()
    # Locked → every further attempt (even a VALID code) is refused until restart.
    assert gate.attempt("me@x", _code(now + 3), now=now + 3) == "locked"


def test_fail_window_resets_after_a_minute():
    gate = ElevationGate(_SECRET)
    assert gate.attempt("me@x", "000000", now=0) == "invalid"
    assert gate.attempt("me@x", "000000", now=10) == "grace"
    # The first two fall outside the rolling minute before a 3rd arrives…
    assert gate.attempt("me@x", "000000", now=75) == "invalid"   # not locked
    assert not gate.is_locked()


def test_success_clears_the_fail_counter():
    gate = ElevationGate(_SECRET)
    now = 40_000.0
    gate.attempt("me@x", "000000", now=now)              # 1 fail
    assert gate.attempt("me@x", _code(now), now=now) == "elevated"
    gate.consume_elevation("me@x")
    # Counter reset → two more fails don't lock (would need 3 fresh).
    assert gate.attempt("me@x", "000000", now=now + 1) == "invalid"
    assert gate.attempt("me@x", "000000", now=now + 2) == "grace"
    assert not gate.is_locked()
