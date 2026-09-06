"""Transcript keyboard copy-mode — Vector E Round-2 (Textual pilot).

A mouseless, modal block-range selection: ctrl+r enters copy-mode, j/k move the
cursor block, v marks the range anchor, y yanks the selected blocks to the
clipboard over OSC 52, esc/q exits. Driven through run_test() (no real TTY);
the OSC 52 assertion checks the escape write on the driver, so it holds headless.
"""
from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from rich.text import Text  # noqa: E402

from xlii.tui.transcript import TranscriptLog  # noqa: E402
from xlii.tui_textual import XliiApp  # noqa: E402


def _fake_agent():
    from xlii.agent import SessionState
    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
        howto_mode=False, history=[], model_override=None, session=SessionState(),
    )


def _app(tmp_path):
    st = SimpleNamespace(shell_cwd=tmp_path,
                         project=SimpleNamespace(project_root=tmp_path, name="proj"),
                         agent=_fake_agent())
    return XliiApp(project_name="proj", agent=st.agent,
                   run_turn=lambda q: ("", set(), None), state=st)


def _run(coro_fn):
    asyncio.run(coro_fn())


async def _log_with_blocks(app, pilot, *texts):
    log = app.query_one("#log", TranscriptLog)
    log.clear()
    await pilot.pause()
    for t in texts:
        log.write(Text(t))
    await pilot.pause()
    return log


def _capture_osc52(app):
    payloads: list[str] = []
    orig = app._driver.write

    def _write(data):
        if isinstance(data, str) and "\x1b]52;c;" in data:
            b64 = data.split("\x1b]52;c;")[1].split("\a")[0]
            payloads.append(base64.b64decode(b64).decode("utf-8"))
        return orig(data)

    app._driver.write = _write
    return payloads


def test_ctrl_r_enters_copy_mode(tmp_path):
    async def body():
        app = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            log = await _log_with_blocks(app, pilot, "alpha", "bravo", "charlie")
            await pilot.press("ctrl+r")
            await pilot.pause()
            assert log._copy_mode is True
            assert log._cm_cursor == 2                      # starts at the newest block
            assert log.children[2].has_class("copy-cursor")
    _run(body)


def test_nav_bindings_gated_to_copy_mode(tmp_path):
    async def body():
        app = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            log = await _log_with_blocks(app, pilot, "alpha", "bravo")
            assert log.check_action("cm_move", (1,)) is False   # disabled outside copy-mode
            assert log.check_action("cm_yank", ()) is False
            assert log.check_action("scroll_end", ()) is True   # ordinary actions unaffected
            log.enter_copy_mode()
            assert log.check_action("cm_move", (1,)) is True    # enabled in copy-mode
    _run(body)


def test_jk_move_cursor(tmp_path):
    async def body():
        app = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            log = await _log_with_blocks(app, pilot, "alpha", "bravo", "charlie")
            log.enter_copy_mode()
            await pilot.pause()
            assert log._cm_cursor == 2
            await pilot.press("k")            # up
            assert log._cm_cursor == 1
            await pilot.press("k")
            assert log._cm_cursor == 0
            await pilot.press("k")            # clamps at top
            assert log._cm_cursor == 0
            await pilot.press("j")            # down
            assert log._cm_cursor == 1
            await pilot.press("G")            # jump to bottom
            assert log._cm_cursor == 2
            await pilot.press("g")            # jump to top
            assert log._cm_cursor == 0
    _run(body)


def test_mark_and_yank_range_over_osc52(tmp_path):
    async def body():
        app = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            log = await _log_with_blocks(app, pilot, "alpha", "bravo", "charlie")
            payloads = _capture_osc52(app)
            log.enter_copy_mode()
            await pilot.pause()
            await pilot.press("k")            # cursor 2 -> 1 (bravo)
            await pilot.press("v")            # mark anchor at 1
            await pilot.press("k")            # cursor -> 0 (alpha); range [0,1]
            await pilot.press("y")            # yank alpha + bravo
            await pilot.pause()
            assert payloads == ["alpha\n\nbravo"]
            assert log._copy_mode is False    # yank exits copy-mode
    _run(body)


def test_yank_single_block_without_anchor(tmp_path):
    async def body():
        app = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            log = await _log_with_blocks(app, pilot, "alpha", "bravo", "charlie")
            payloads = _capture_osc52(app)
            log.enter_copy_mode()             # cursor at 2 (charlie)
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            assert payloads == ["charlie"]
    _run(body)


def test_escape_exits_and_clears(tmp_path):
    async def body():
        app = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            log = await _log_with_blocks(app, pilot, "alpha", "bravo")
            log.enter_copy_mode()
            await pilot.pause()
            assert log.children[1].has_class("copy-cursor")
            await pilot.press("escape")
            await pilot.pause()
            assert log._copy_mode is False
            assert not any(w.has_class("copy-cursor") or w.has_class("copy-range")
                           for w in log.children)
    _run(body)


def test_empty_transcript_is_noop(tmp_path):
    async def body():
        app = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            log = app.query_one("#log", TranscriptLog)
            log.clear()
            await pilot.pause()
            log.enter_copy_mode()
            assert log._copy_mode is False    # nothing to copy → never enters
    _run(body)
