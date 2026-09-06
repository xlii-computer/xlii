"""The completion popups float on an overlay layer — reveal must not reflow.

The 2026-07-10 slash-latency diagnosis: #completions sat in normal document
flow, so revealing it shrank the transcript and forced a full-screen relayout
(~185ms with a long transcript, ~57ms per narrowing keystroke, scaling with
session length). The fix docks the popups on their own layer above the input
frame. The one assertion that matters here: showing, narrowing, and hiding the
popup leaves the transcript's region byte-identical.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from xlii.tui_textual import XliiApp  # noqa: E402


def _fake_agent(console=None):
    from xlii.agent import SessionState
    return SimpleNamespace(
        console=console, rail=None, debug=None, plan_mode=False,
        active_mode=None,
        howto_mode=False, history=[], model_override=None,
        session=SessionState(),
    )


def _state(tmp_path):
    return SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj"),
        agent=_fake_agent(),
    )


def _run(coro_fn):
    asyncio.run(coro_fn())


def test_slash_popup_reveal_does_not_move_transcript(tmp_path):
    async def body():
        from textual.widgets import OptionList

        from xlii.repl_cmds import register_all
        register_all()

        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = app.query_one("#log")
            box = app.query_one("#input-box")
            before_log = log.region
            before_box = box.region

            # The `/` keystroke worst case: every command in scope.
            app._update_popup("/")
            await pilot.pause()
            ol = app.query_one("#completions", OptionList)
            assert ol.display and app._popup_open

            # THE point of the vector: the base layout did not move.
            assert log.region == before_log, (log.region, before_log)
            assert box.region == before_box

            # The popup floats directly above the input frame, inside the screen.
            assert ol.region.height > 0
            assert ol.region.bottom <= box.region.y, (ol.region, box.region)

            # Narrowing keystroke — still no reflow.
            app._update_popup("/st")
            await pilot.pause()
            assert app._popup_open
            assert log.region == before_log

            # Hide — still no reflow.
            app._popup_hide()
            await pilot.pause()
            assert not app._popup_open
            assert log.region == before_log
    _run(body)


def test_shortcode_popup_reveal_does_not_move_transcript(tmp_path):
    async def body():
        from xlii.tui_textual import _PromptInput

        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = app.query_one("#log")
            before = log.region

            inp = app.query_one("#input", _PromptInput)
            inp.text = ":thu"
            inp.cursor_location = (0, 4)
            inp._refresh_completions()
            await pilot.pause()

            sc = app.query_one("#shortcode-popup")
            assert sc.display and inp._sc_open
            assert log.region == before, (log.region, before)
            box = app.query_one("#input-box")
            assert sc.region.bottom <= box.region.y

            inp._sc_hide()
            await pilot.pause()
            assert log.region == before
    _run(body)
