"""RendererTap — the public renderer-tap seam in xlii/agent_render.py (GVM B5).

A body mirrors an agent's typed event stream (xlii.turn_events) to its own
sink; the inner renderer is untouched. The tap also survives the Agent's
``_renderer_cache`` freshness guard via its delegating ``console`` property.
"""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from xlii.agent_render import RendererTap
from xlii.turn_events import AssistantAnswer, UserTurn


def _renderer(buf):
    from xlii.tui.renderer import Renderer

    return Renderer(Console(file=buf, width=100, no_color=True), plain=True)


def test_tap_mirrors_every_emit_before_inner_renders():
    buf = StringIO()
    seen: list = []
    tap = RendererTap(_renderer(buf), seen.append)

    tap.emit(UserTurn("hi"))
    tap.emit(AssistantAnswer("hello", streamed=False))

    assert seen == [UserTurn("hi"), AssistantAnswer("hello", streamed=False)]
    assert "hello" in buf.getvalue()  # the inner renderer still rendered


def test_tap_console_delegates_for_the_agent_cache_guard():
    buf = StringIO()
    inner = _renderer(buf)
    tap = RendererTap(inner, lambda e: None)
    assert tap.console is inner.console


def test_tap_meta_and_error_delegate_without_sinking():
    """meta/error go straight to the inner renderer (pre-tap behavior kept)."""
    buf = StringIO()
    seen: list = []
    tap = RendererTap(_renderer(buf), seen.append)

    tap.meta("note")
    tap.error("boom")

    assert seen == []  # only emit() reaches the sink
    assert "note" in buf.getvalue() and "boom" in buf.getvalue()


def test_tap_survives_agent_renderer_cache_guard():
    """Installed as _renderer_cache, the tap is returned (not rebuilt away):
    ``r.console is not self.console`` must be False for the tap."""
    buf = StringIO()
    con = Console(file=buf, width=100, no_color=True)
    inner = _renderer(buf)
    inner.console = con  # bind to the "agent's" console
    tap = RendererTap(inner, lambda e: None)

    # the Agent._renderer() guard, verbatim:
    r = tap
    assert not (r is None or r.console is not con)
