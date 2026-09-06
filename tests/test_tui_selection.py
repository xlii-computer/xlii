"""In-app transcript selection & copy — the-fold Vector E (Textual pilot).

Exercises the delta layered on Textual 8.2.7's built-in selection engine:
drag-selection over transcript blocks, OSC 52 clipboard writes, and the
whole-answer / whole-block copy affordances. Also guards coexistence — a
transcript selection must not swallow the mouse from chips/menus.

Driven through run_test() (no real TTY); skipped when textual isn't installed.
The OSC 52 assertions check the *escape write* on the driver (\\x1b]52;c;…\\a),
not an OS clipboard, so they hold headless and over SSH.
"""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from rich.console import Group  # noqa: E402
from rich.text import Text  # noqa: E402
from textual.geometry import Offset  # noqa: E402
from textual.selection import SELECT_ALL, Selection  # noqa: E402

from xlii.tui.selection import (  # noqa: E402
    SelectableAnswer,
    SelectableStatic,
    SelectableTranscriptLog,
)
from xlii.tui.transcript import (  # noqa: E402
    CopyableMarkdownFence,
    TranscriptLog,
    TuiAnswer,
)
from xlii.tui_textual import XliiApp, _ChipRow  # noqa: E402


# --------------------------------------------------------------------------- #
#  Harness (mirrors tests/test_tui_tabs_interaction.py)
# --------------------------------------------------------------------------- #

def _fake_agent():
    from xlii.agent import SessionState

    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
        howto_mode=False, history=[], model_override=None, session=SessionState(),
    )


def _state(tmp_path):
    return SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj"),
        agent=_fake_agent(),
    )


def _app(tmp_path, **state_kw):
    st = _state(tmp_path)
    for k, v in state_kw.items():
        setattr(st, k, v)
    return XliiApp(project_name="proj", agent=st.agent,
                   run_turn=lambda q: ("", set(), None), state=st), st


def _run(coro_fn):
    asyncio.run(coro_fn())


async def _fresh_log(app, pilot):
    """The transcript, cleared of the mount-time splash block so the first child
    we write is at index 0."""
    log = app.query_one("#log", TranscriptLog)
    log.clear()
    await pilot.pause()
    return log


def _capture_osc52(app):
    """Wrap the live driver's write so we can see OSC 52 escapes; forwards to the
    real write so rendering keeps working. Returns the decoded-payload list."""
    payloads: list[str] = []
    orig = app._driver.write

    def _write(data):
        if isinstance(data, str) and "\x1b]52;c;" in data:
            b64 = data.split("\x1b]52;c;")[1].split("\a")[0]
            payloads.append(base64.b64decode(b64).decode("utf-8"))
        return orig(data)

    app._driver.write = _write
    return payloads


async def _drag(pilot, widget, start, end):
    """Drive a real drag: down at `start`, move to `end`, up at `end`. Distinct
    offsets keep it a drag (a same-cell down+up is a click that clears)."""
    pilot.app.screen.clear_selection()
    await pilot.mouse_down(widget, offset=start)
    await pilot.hover(widget, offset=end)
    await pilot.mouse_up(widget, offset=end)
    await pilot.pause()


def _span_for(cap, kind):
    return next(s for s in cap._spans if s[2] == kind)


# --------------------------------------------------------------------------- #
#  Compose swap + subclass contract
# --------------------------------------------------------------------------- #

def test_transcript_is_selectable_subclass(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = await _fresh_log(app, pilot)
            # The compose swap landed, and the old identity/type still resolves.
            assert isinstance(log, SelectableTranscriptLog)
            assert isinstance(log, TranscriptLog)

            log.write(Text("a plain block"))
            log.write(TuiAnswer(markdown="answer body", model="grok"))
            await pilot.pause()
            kinds = [type(c).__name__ for c in log.children]
            assert "SelectableStatic" in kinds
            assert "SelectableAnswer" in kinds
            # write() still records plain text for .lines / plain_text (unchanged contract).
            assert log.lines == log._plain_chunks
            assert "a plain block" in log.plain_text()
    _run(body)


# --------------------------------------------------------------------------- #
#  get_selection reads the block's stored plain text (the Group/Panel gap fix)
# --------------------------------------------------------------------------- #

def test_get_selection_slices_stored_plain_text():
    # No app needed: the override reads self._plain, not a rendered visual.
    block = SelectableStatic(Text("ignored visual"), plain="line one\nline two")
    # Whole-block selection (double-click / spanning drag) → full text.
    assert block.get_selection(SELECT_ALL) == ("line one\nline two", "\n")
    # A partial selection slices by (col,row) offsets, like the base class.
    sel = Selection.from_offsets(Offset(0, 0), Offset(4, 0))
    assert block.get_selection(sel) == ("line", "\n")


def test_get_selection_empty_when_no_plain_text():
    block = SelectableStatic(Text("x"), plain="")
    assert block.get_selection(SELECT_ALL) is None


# --------------------------------------------------------------------------- #
#  Drag-select extracts real text (the audit's empty-copy gap, closed)
# --------------------------------------------------------------------------- #

def test_drag_over_group_tool_block_extracts_text(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = await _fresh_log(app, pilot)
            # A Group is exactly what shell/tool/user blocks render as — the case
            # Textual's default get_selection reads as ''.
            log.write(Group(Text("alpha output line"), Text("beta output line")))
            await pilot.pause()
            block = log.children[0]
            assert isinstance(block, SelectableStatic)

            await _drag(pilot, block, (0, 0), (20, 1))
            selected = app.screen.get_selected_text()
            assert selected  # not None, not ''
            assert "alpha output line" in selected
    _run(body)


def test_drag_over_answer_body_extracts_native_text(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = await _fresh_log(app, pilot)
            log.write(TuiAnswer(markdown="the quick brown fox jumps over", model="grok"))
            await pilot.pause()
            answer = log.children[0]
            # Row 0 is the round border, row 1 the "xlii" title; body starts row 2.
            await _drag(pilot, answer, (2, 2), (28, 2))
            selected = app.screen.get_selected_text()
            assert selected and "quick" in selected
    _run(body)


# --------------------------------------------------------------------------- #
#  Copy reaches the clipboard via OSC 52
# --------------------------------------------------------------------------- #

def test_copy_selection_writes_osc52_escape(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            payloads = _capture_osc52(app)
            log = await _fresh_log(app, pilot)
            log.write(Group(Text("copy me one"), Text("copy me two")))
            await pilot.pause()

            await _drag(pilot, log.children[0], (0, 0), (11, 1))
            # The built-in ctrl+c binding action — copies the selection over OSC 52.
            app.screen.action_copy_text()
            await pilot.pause()
            assert payloads, "expected an OSC 52 clipboard write"
            assert "copy me one" in payloads[-1]
    _run(body)


# --------------------------------------------------------------------------- #
#  Whole-block (tool-output) copy affordance: action / click / `c`
# --------------------------------------------------------------------------- #

def test_tool_output_copy_affordance(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            payloads = _capture_osc52(app)
            log = await _fresh_log(app, pilot)
            log.write(Group(Text("tool line one"), Text("tool line two")))
            await pilot.pause()
            block = log.children[0]

            # (a) the action copies the whole block's plain text.
            block.action_copy_block()
            await pilot.pause()
            assert payloads and "tool line one" in payloads[-1]
            assert "tool line two" in payloads[-1]

            # (b) a plain click on the block fires the same affordance.
            payloads.clear()
            await pilot.click(block, offset=(1, 0))
            await pilot.pause()
            assert payloads and "tool line one" in payloads[-1]

            # (c) keyboard: focus + `c`.
            payloads.clear()
            block.focus()
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            assert payloads and "tool line one" in payloads[-1]
    _run(body)


def test_empty_block_copy_is_noop(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            payloads = _capture_osc52(app)
            log = await _fresh_log(app, pilot)
            log.write(Text("   "))  # whitespace-only block
            await pilot.pause()
            log.children[0].action_copy_block()
            await pilot.pause()
            assert not payloads  # nothing worth copying → no clipboard write
    _run(body)


# --------------------------------------------------------------------------- #
#  Whole-answer copy affordance + fence layering
# --------------------------------------------------------------------------- #

def test_whole_answer_copy_affordance(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            payloads = _capture_osc52(app)
            log = await _fresh_log(app, pilot)
            log.write(TuiAnswer(markdown="# Heading\n\nsome prose to copy", model="grok"))
            await pilot.pause()
            answer = log.children[0]
            assert isinstance(answer, SelectableAnswer)

            # The action copies the raw markdown source (the useful paste target).
            answer.action_copy_answer()
            await pilot.pause()
            assert payloads
            assert "# Heading" in payloads[-1] and "some prose to copy" in payloads[-1]
    _run(body)


def test_fence_click_copies_code_not_whole_answer(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            payloads = _capture_osc52(app)
            log = await _fresh_log(app, pilot)
            log.write(TuiAnswer(
                markdown="intro prose\n\n```python\nprint('fenced')\n```\n",
                model="grok",
            ))
            await pilot.pause()

            fence = app.query(CopyableMarkdownFence).first()
            # Click mid-fence: the fence stops the click (copies code) so it never
            # bubbles to the answer's whole-answer copy.
            off_y = max(1, fence.region.height // 2)
            await pilot.click(fence, offset=(2, off_y))
            await pilot.pause()

            assert payloads, "clicking a fence should copy something"
            assert "print('fenced')" in payloads[-1]
            assert "intro prose" not in payloads[-1]  # NOT the whole answer
    _run(body)


# --------------------------------------------------------------------------- #
#  Coexistence: a selection must not steal the mouse from chips / menus
# --------------------------------------------------------------------------- #

def test_chip_click_still_activates_after_selection(tmp_path):
    async def body():
        app, _ = _app(tmp_path, attached_docs=[("conventions", "x")])  # a doc → docs doorway chip
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause()
            app._refresh_status()
            await pilot.pause()
            log = await _fresh_log(app, pilot)
            log.write(Group(Text("selected earlier")))
            await pilot.pause()

            # Make a live selection in the transcript first…
            await _drag(pilot, log.children[0], (0, 0), (10, 0))
            assert app.screen.get_selected_text()

            # …then a chip click must still land on its handler.
            calls = []
            app._activate_tab = lambda kind, payload: calls.append((kind, payload))
            chips = app.query_one("#input-chips", _ChipRow)
            start, end, kind, _payload = _span_for(chips, "door")
            await pilot.click(chips, offset=((start + end) // 2, 0))
            await pilot.pause()
            assert calls and calls[0][0] == "door"
    _run(body)


def test_menu_click_still_opens_after_selection(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause()
            from xlii.tui.menu_bar import _MenuBar

            log = await _fresh_log(app, pilot)
            log.write(Group(Text("some selected output")))
            await pilot.pause()
            await _drag(pilot, log.children[0], (0, 0), (12, 0))

            # The menu bar calls its own _on_open (captured at compose), so patch
            # THAT — proving the click reaches the bar despite the live selection.
            opened = []
            bar = app.query_one(_MenuBar)
            bar._on_open = lambda title, x: opened.append(title)
            start, end, _title = bar._spans[0]  # the first title ("Console")
            # on_click subtracts the 1-col padding, so add it back to hit the span.
            await pilot.click(bar, offset=((start + end) // 2 + 1, 0))
            await pilot.pause()
            assert opened, "a menu-bar click must still open its menu"
    _run(body)
