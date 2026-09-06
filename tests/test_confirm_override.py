"""tools.confirm_override + tools.auto_deny — the public, lock-guarded
_confirm swap every headless body needs (GVM B5, audit cheap-win #10).

The pre-existing serve-ws swap tests (tests/test_serve_ws_confirm.py) keep
passing against the cmds/serve_ws façade aliases; these pin the kernel home
directly.
"""

from __future__ import annotations

import threading

import xlii.tools as tools


def test_auto_deny_returns_empty_and_prints_nothing(capsys):
    assert tools.auto_deny("run rm -rf /?") == ""
    assert capsys.readouterr().out == ""


def test_confirm_override_activates_and_restores():
    original = tools._confirm
    try:
        assert tools._CONFIRM_SWAP_LOCK.locked() is False
        with tools.confirm_override(tools.auto_deny):
            assert tools._confirm is tools.auto_deny
            assert tools._CONFIRM_SWAP_LOCK.locked() is True
        assert tools._confirm is original
        assert tools._CONFIRM_SWAP_LOCK.locked() is False
    finally:
        tools._confirm = original


def test_confirm_override_restores_on_exception():
    original = tools._confirm
    try:
        try:
            with tools.confirm_override(tools.auto_deny):
                raise RuntimeError("turn blew up")
        except RuntimeError:
            # The raise above is deliberate -- what's asserted below is that the override still restored.
            pass
        assert tools._confirm is original
        assert tools._CONFIRM_SWAP_LOCK.locked() is False
    finally:
        tools._confirm = original


def test_confirm_override_serializes_concurrent_turns():
    """Two overlapping overrides can't interleave their save/restore — while one
    holds the lock the other waits, so each sees a consistent saved value."""
    original = tools._confirm
    seen: list[str] = []
    entered = threading.Event()

    def turn_a():
        with tools.confirm_override(lambda p: "A"):
            seen.append("a-in")
            entered.set()
            threading.Event().wait(0.05)  # hold the swap while b tries to enter
            seen.append(tools._confirm("x"))

    def turn_b():
        entered.wait(timeout=5)
        with tools.confirm_override(lambda p: "B"):
            seen.append("b-in")
            seen.append(tools._confirm("x"))

    try:
        a = threading.Thread(target=turn_a)
        b = threading.Thread(target=turn_b)
        a.start()
        b.start()
        a.join(timeout=10)
        b.join(timeout=10)
        # b could not enter until a released: a's read sees "A", b's sees "B".
        assert seen == ["a-in", "A", "b-in", "B"]
        assert tools._confirm is original
    finally:
        tools._confirm = original
