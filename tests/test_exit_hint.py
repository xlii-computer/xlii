"""exit_hint — the input-chrome "how do I get out of here" pointer.

The discoverability fix for "I didn't even know I was in a mode." It reads the
same signals /off clears, so it never promises an exit /off won't perform:
an overlay/mode active → /off; a chat surface → /code; base surface → nothing.
"""

from __future__ import annotations

from types import SimpleNamespace

from xlii.status import exit_hint


def _state(**kw):
    kw.setdefault("agent", None)
    kw.setdefault("persona", None)
    return SimpleNamespace(**kw)


def test_base_code_surface_has_no_hint():
    # Plain code/scratch: nothing to leave.
    assert exit_hint(_state()) == ""


def test_chat_surface_points_at_code():
    assert exit_hint(_state(persona=SimpleNamespace(name="ixaac"))) == "/code for code"


def test_howto_overlay_points_at_off():
    assert exit_hint(_state(howto_mode=True)) == "/off to exit"


def test_active_work_mode_points_at_off():
    agent = SimpleNamespace(active_mode=SimpleNamespace(status_tag=lambda: ("PLAN", "PLAN")))
    assert exit_hint(_state(agent=agent)) == "/off to exit"


def test_foreground_harness_points_at_off():
    reg = SimpleNamespace(foreground=object())
    assert exit_hint(_state(cursor_sessions=reg)) == "/off to exit"


def test_overlay_wins_over_chat_surface():
    # In a chat surface WITH an overlay, leave the overlay first (/off), then the
    # next repaint (overlay cleared) shows /code — each step surfaced in turn.
    st = _state(persona=SimpleNamespace(name="ixaac"), howto_mode=True)
    assert exit_hint(st) == "/off to exit"
