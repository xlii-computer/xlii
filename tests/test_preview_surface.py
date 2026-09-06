"""Vector A2 — the preview/edit/discuss/author surface, end-to-end.

Driven through Textual's run_test() pilot (no real TTY); skipped when textual
isn't installed (the optional [tui] extra). These exercise the real
``PreviewSurface`` modal: read a provider render, flip to edit and save via the
``on_save`` callback, discuss via a stubbed AI turn, author from an agent-draft
seed, and the host seam (``AppSurfaceHost`` / ``open_surface``).
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.app import App, ComposeResult  # noqa: E402
from textual.widgets import Input, Static, TextArea  # noqa: E402

from xlii.tui import preview  # noqa: E402


class _Host(App):
    """A minimal app to host the modal under test."""

    def compose(self) -> ComposeResult:
        yield Static("host")


def _run(coro_fn):
    asyncio.run(coro_fn())


def _plain(widget) -> str:
    from io import StringIO

    from rich.console import Console

    buf = StringIO()
    Console(file=buf, width=100, no_color=True).print(widget.content)
    return buf.getvalue()


def _view_text(screen) -> str:
    return _plain(screen.query_one("#surface-view-body", Static))


def _status_text(screen) -> str:
    return _plain(screen.query_one("#surface-status", Static))


def _discuss_text(screen) -> str:
    return _plain(screen.query_one("#discuss-log-body", Static))


# -- view -----------------------------------------------------------------

def test_surface_view_shows_provider_render():
    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            screen = preview.surface("doc", ("notes", "# Heading\n\nthe body line"))
            await app.push_screen(screen)
            await pilot.pause()
            assert "the body line" in _view_text(screen)
            assert screen.query_one("#surface-view").display is True
            assert screen.query_one("#surface-edit", TextArea).display is False
    _run(body)


# -- edit + save ----------------------------------------------------------

def test_surface_edit_and_save():
    async def body():
        saved = {}
        app = _Host()
        async with app.run_test() as pilot:
            screen = preview.open_editor("original text", lambda t: saved.setdefault("text", t), title="x.txt")
            await app.push_screen(screen)
            await pilot.pause()
            ta = screen.query_one("#surface-edit", TextArea)
            assert ta.display is True  # open_editor starts in edit mode
            assert ta.text == "original text"
            ta.text = "edited contents"
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            assert saved.get("text") == "edited contents"
            assert "saved" in _status_text(screen)
    _run(body)


def test_surface_view_to_edit_via_key():
    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            screen = preview.surface("doc", ("n", "body"), on_save=lambda t: None, editable=True)
            await app.push_screen(screen)
            await pilot.pause()
            assert screen.query_one("#surface-edit", TextArea).display is False
            await pilot.press("ctrl+e")
            await pilot.pause()
            assert screen.query_one("#surface-edit", TextArea).display is True
    _run(body)


# -- discuss --------------------------------------------------------------

def test_surface_discuss_runs_turn_and_shows_reply():
    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            screen = preview.open_editor(
                "file body", lambda t: None, on_discuss=lambda q: f"answer to: {q}", title="f.txt"
            )
            await app.push_screen(screen)
            await pilot.pause()
            # /editthis opens AI-focused on the file: discuss is the start mode when
            # a discusser is wired (ctrl+e flips to edit, ctrl+d returns).
            assert screen.query_one("#surface-discuss").display is True
            assert screen.query_one("#surface-edit", TextArea).display is False
            await pilot.press("ctrl+e")
            await pilot.pause()
            assert screen.query_one("#surface-edit", TextArea).display is True
            await pilot.press("ctrl+d")
            await pilot.pause()
            assert screen.query_one("#surface-discuss").display is True
            assert screen.query_one("#surface-edit", TextArea).display is False
            inp = screen.query_one("#discuss-input", Input)
            inp.value = "what is this?"
            inp.focus()
            await pilot.pause()
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            out = _discuss_text(screen)
            assert "what is this?" in out
            assert "answer to: what is this?" in out
    _run(body)


def test_surface_discuss_unavailable_without_callback():
    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            screen = preview.surface("doc", ("n", "b"))  # no on_discuss
            await app.push_screen(screen)
            await pilot.pause()
            await pilot.press("ctrl+d")
            await pilot.pause()
            assert screen.query_one("#surface-view").display is True
            assert "discuss unavailable" in _status_text(screen)
    _run(body)


# -- author (agent-draft seed) -------------------------------------------

def test_surface_author_draft_seeds_editor():
    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            screen = preview.open_editor(
                "",
                lambda t: None,
                draft_prompt="a haiku about shells",
                on_draft=lambda desc: f"DRAFTED<{desc}>",
                title="poem.md",
            )
            await app.push_screen(screen)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            ta = screen.query_one("#surface-edit", TextArea)
            assert ta.text == "DRAFTED<a haiku about shells>"  # seed only, never auto-saved
    _run(body)


# -- read rendering (markdown vs plain) ----------------------------------

def test_markdown_read_renders_markdown_after_edit():
    from rich.markdown import Markdown

    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            screen = preview.open_editor(
                "# Title\n\n- one\n- two", lambda t: None, title="notes.md", read_as="markdown"
            )
            await app.push_screen(screen)
            await pilot.pause()
            await pilot.press("ctrl+r")  # edit -> read
            await pilot.pause()
            body_w = screen.query_one("#surface-view-body", Static)
            assert isinstance(body_w.content, Markdown)  # rendered, not raw text
    _run(body)


def test_text_read_stays_plain():
    from rich.text import Text

    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            screen = preview.open_editor("plain body", lambda t: None, title="x.txt")  # read_as default
            await app.push_screen(screen)
            await pilot.pause()
            await pilot.press("ctrl+r")
            await pilot.pause()
            assert isinstance(screen.query_one("#surface-view-body", Static).content, Text)
    _run(body)


def test_save_status_uses_ok_tone_class():
    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            screen = preview.open_editor("seed", lambda t: None, title="x")
            await app.push_screen(screen)
            await pilot.pause()
            await pilot.press("ctrl+s")
            await pilot.pause()
            status = screen.query_one("#surface-status", Static)
            assert status.has_class("-ok")  # the green tone (CSS class, not inline style)
            assert "saved" in _status_text(screen)
    _run(body)


# -- esc navigation -------------------------------------------------------

def test_surface_esc_from_edit_returns_to_view_then_dismisses():
    async def body():
        app = _Host()
        result = {}
        async with app.run_test() as pilot:
            screen = preview.open_editor("seed", lambda t: None, title="x")
            await app.push_screen(screen, lambda r: result.setdefault("r", r))
            await pilot.pause()
            assert screen.query_one("#surface-edit", TextArea).display is True
            await pilot.press("escape")  # edit -> view
            await pilot.pause()
            assert screen.query_one("#surface-view").display is True
            await pilot.press("escape")  # view -> dismiss
            await pilot.pause()
            assert "r" in result  # screen dismissed (callback fired)
    _run(body)


# -- host seam ------------------------------------------------------------

def test_app_surface_host_pushes_screen():
    async def body():
        app = _Host()
        async with app.run_test() as pilot:
            prev = preview.set_surface_host(preview.AppSurfaceHost(app))
            try:
                screen = preview.open_editor("hi", lambda t: None, title="h")
                pushed = preview.open_surface(screen)
                await pilot.pause()
                assert pushed is True
                assert isinstance(app.screen, preview.PreviewSurface)
            finally:
                preview.set_surface_host(prev)
    _run(body)


def test_open_surface_without_host_returns_false():
    prev = preview.set_surface_host(None)
    try:
        assert preview.open_surface(object()) is False
    finally:
        preview.set_surface_host(prev)
