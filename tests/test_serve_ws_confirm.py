"""serve-ws confirm-swap is lock-guarded (round-2 adversarial sweep).

``run_ws_turn`` swaps the process-global ``xlii.tools._confirm`` for the turn,
but the server runs one turn per connection thread. The pre-fix plain
save/restore let two overlapping turns clobber each other — one turn's ``finally``
could restore the real blocking ``input()`` while another was mid-turn (wedging
on server stdin), or leave ``_confirm`` stuck on auto-deny forever.

These tests are deliberately socket-free and single-threaded so they are
deterministic (no real-port / subprocess flakiness): they assert the swap is
held under :data:`_CONFIRM_SWAP_LOCK` for the turn and always restored after.
"""

from __future__ import annotations

import xlii.cmds.serve_ws as sw
import xlii.tools as tools


def test_ws_auto_deny_returns_empty_string():
    assert sw._ws_auto_deny("run rm -rf /?") == ""


def test_confirm_swap_activates_and_restores():
    original = tools._confirm
    try:
        assert sw._CONFIRM_SWAP_LOCK.locked() is False
        with sw._ws_confirm_swap():
            assert tools._confirm is sw._ws_auto_deny
            assert sw._CONFIRM_SWAP_LOCK.locked() is True
        assert tools._confirm is original
        assert sw._CONFIRM_SWAP_LOCK.locked() is False
    finally:
        tools._confirm = original


def test_confirm_swap_is_mutually_exclusive():
    """While one turn holds the swap, another cannot enter it — the exact race
    the fix closes. A plain ``Lock`` is non-reentrant, so a same-thread
    non-blocking acquire returns False iff the lock is currently held."""
    original = tools._confirm
    try:
        with sw._ws_confirm_swap():
            assert sw._CONFIRM_SWAP_LOCK.acquire(blocking=False) is False
        # released once the turn ends
        got = sw._CONFIRM_SWAP_LOCK.acquire(blocking=False)
        assert got is True
        sw._CONFIRM_SWAP_LOCK.release()
    finally:
        tools._confirm = original


def test_confirm_swap_restores_on_exception():
    original = tools._confirm
    try:
        try:
            with sw._ws_confirm_swap():
                assert tools._confirm is sw._ws_auto_deny
                raise RuntimeError("turn blew up")
        except RuntimeError:
            # The raise above is deliberate -- what's asserted below is that the swap still restored.
            pass
        assert tools._confirm is original
        assert sw._CONFIRM_SWAP_LOCK.locked() is False
    finally:
        tools._confirm = original
