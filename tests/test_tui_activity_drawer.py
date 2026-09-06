"""Turn-activity fold — the tool/shell 'homework' of one turn stacks under a
single collapsible summary line, so a finished turn reads as answer + receipt,
not a wall of tool blocks.

Driven through run_test() (no real TTY); skipped when textual isn't installed.
Mirrors the harness in tests/test_tui_selection.py.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from rich.console import Group  # noqa: E402
from rich.text import Text  # noqa: E402

from xlii.tui.transcript import (  # noqa: E402
    ActivityDrawer,
    TranscriptLog,
    TuiAnswer,
    _activity_verb,
)
from xlii.tui_textual import XliiApp  # noqa: E402


# --------------------------------------------------------------------------- #
#  Harness
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


def _app(tmp_path):
    st = _state(tmp_path)
    return XliiApp(project_name="proj", agent=st.agent,
                   run_turn=lambda q: ("", set(), None), state=st), st


def _run(coro_fn):
    asyncio.run(coro_fn())


async def _fresh_log(app, pilot):
    log = app.query_one("#log", TranscriptLog)
    log.clear()
    await pilot.pause()
    return log


def _tool(name, preview):
    """A stand-in tool block whose plain first line matches a real ToolBlock
    header (``name · preview  ✓``), so the drawer tallies its verb."""
    return Group(Text(f"{name} · {preview}  ✓"), Text("  ⎿ some output"))


# --------------------------------------------------------------------------- #
#  Verb classification (pure)
# --------------------------------------------------------------------------- #

def test_verb_classification():
    assert _activity_verb("read_file · pacman.py · 0.1s  ✓ ────") == "read_file"
    assert _activity_verb("shell · ~/p · you · exit 0 · 0.3s ──") == "shell"
    assert _activity_verb("• 3 tool(s) in parallel") is None   # meta glyph
    assert _activity_verb("◆ Thought for 0.2s") is None         # thought glyph
    assert _activity_verb("   ") is None


# --------------------------------------------------------------------------- #
#  Folding lifecycle
# --------------------------------------------------------------------------- #

def test_homework_folds_into_one_block_with_summary(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = await _fresh_log(app, pilot)

            log.begin_activity()
            log.write(_tool("read_file", "a.py"))
            log.write(_tool("read_file", "b.py"))
            log.write(_tool("grep", "/foo/"))
            await pilot.pause()

            # Live: exactly one transcript child (the drawer), and it holds every
            # folded line — so .lines/plain_text still carry the full text.
            drawers = list(log.query(ActivityDrawer))
            assert len(drawers) == 1
            assert len(log.children) == 1
            assert log.lines == log._plain_chunks
            assert "read_file · a.py" in log.plain_text()
            assert "grep · /foo/" in log.plain_text()

            # The turn's answer ends the fold and lands as its own permanent block.
            log.write(TuiAnswer(markdown="here is the answer", model="grok"))
            await pilot.pause()

            drawer = drawers[0]
            assert drawer._final and drawer.has_class("-collapsed")
            # Summary tallies the two verbs: read_file ×2 · grep.
            summary_text = drawer._summary_text().plain
            assert "read_file ×2" in summary_text
            assert "grep" in summary_text
            assert "3 tools" in summary_text   # 3 tool calls: read_file, read_file, grep

            # Two transcript children now: [drawer, answer]; still 1:1 with chunks.
            assert len(log.children) == 2
            assert len(log._plain_chunks) == 2
            assert "here is the answer" in log.plain_text()
    _run(body)


def test_toggle_expands_and_collapses(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = await _fresh_log(app, pilot)

            log.begin_activity()
            log.write(_tool("bash", "pytest -q"))
            await pilot.pause()
            log.end_activity()
            await pilot.pause()

            drawer = log.query_one(ActivityDrawer)
            assert drawer.has_class("-collapsed")     # collapsed after the turn
            drawer.toggle()
            await pilot.pause()
            assert not drawer.has_class("-collapsed")  # click/toggle expands
            drawer.toggle()
            await pilot.pause()
            assert drawer.has_class("-collapsed")
    _run(body)


def test_tool_free_turn_leaves_no_drawer(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = await _fresh_log(app, pilot)

            # A conversational turn: open the fold, write no homework, answer.
            log.begin_activity()
            log.write(TuiAnswer(markdown="no tools needed", model="grok"))
            await pilot.pause()

            assert not list(log.query(ActivityDrawer))  # nothing folded → no drawer
            assert len(log.children) == 1
            assert "no tools needed" in log.plain_text()
    _run(body)


def test_writes_outside_a_turn_are_not_folded(tmp_path):
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = await _fresh_log(app, pilot)

            # No begin_activity(): a bare !shell run or slash message mounts loose.
            log.write(_tool("shell", "ls"))
            log.write(Text("• a meta line"))
            await pilot.pause()

            assert not list(log.query(ActivityDrawer))
            assert len(log.children) == 2
    _run(body)


def test_footer_and_receipt_after_answer_stay_loose(tmp_path):
    """The render slice ends the fold before the answer; the footer + receipt that
    follow it must land as their own visible blocks, not fold back in."""
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = await _fresh_log(app, pilot)

            log.begin_activity()
            log.write(_tool("read_file", "x.py"))
            await pilot.pause()
            # Render slice collapses the fold, then the answer / footer / receipt.
            log.end_activity()
            log.write(TuiAnswer(markdown="done", model="grok"))
            log.write("[dim]grok · 1 iter · 1 tools[/dim]")   # footer
            log.write(Text("⚠ receipt: unsubstantiated"))     # receipt
            await pilot.pause()

            # [drawer, answer, footer, receipt] — 4 loose children, 1 drawer.
            assert len(list(log.query(ActivityDrawer))) == 1
            assert len(log.children) == 4
            assert "⚠ receipt" in log.plain_text()
    _run(body)


def test_each_batch_is_its_own_drawer_with_loose_reasoning_between(tmp_path):
    """Per-step folding, real flow: each batch seals (begin_activity/seal_activity)
    into its OWN drawer, reasoning between stays loose, and the single turn-end
    end_activity collapses them all. A two-step turn makes TWO drawers."""
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = await _fresh_log(app, pilot)

            # step 1
            log.begin_activity()
            log.write(_tool("read_file", "a.py"))
            await pilot.pause()
            log.seal_activity()
            await pilot.pause()

            # the model's reasoning between steps — must NOT fold
            log.write(Text("Now I'll check the wall-collision code"))
            await pilot.pause()

            # step 2
            log.begin_activity()
            log.write(_tool("grep", "/collide/"))
            log.write(_tool("read_file", "b.py"))
            await pilot.pause()
            log.seal_activity()
            await pilot.pause()

            log.end_activity()  # turn end — one collapse pass
            log.write(TuiAnswer(markdown="found it", model="grok"))
            await pilot.pause()

            drawers = list(log.query(ActivityDrawer))
            assert len(drawers) == 2          # one per batch, not one per turn
            # children: [drawer1, loose-reasoning, drawer2, answer] — 1:1 with chunks
            assert len(log.children) == 4
            assert len(log.children) == len(log._plain_chunks)
            assert "Now I'll check the wall-collision code" in log.plain_text()
    _run(body)


def test_batches_stay_expanded_until_one_turn_end_collapse(tmp_path):
    """The flash fix: a sealed batch freezes its summary but stays EXPANDED — no
    fold shrinks content mid-turn. The collapse happens once, at turn end."""
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = await _fresh_log(app, pilot)

            log.begin_activity()
            log.write(_tool("read_file", "a.py"))
            await pilot.pause()
            log.seal_activity()                       # batch 1 sealed
            log.write(Text("checking collisions next"))
            log.begin_activity()
            log.write(_tool("grep", "/x/"))
            await pilot.pause()
            log.seal_activity()                       # batch 2 sealed
            await pilot.pause()

            drawers = list(log.query(ActivityDrawer))
            assert len(drawers) == 2
            # sealed (summary frozen) but STILL EXPANDED — nothing collapsed yet.
            assert all(d._final for d in drawers)
            assert all(not d.has_class("-collapsed") for d in drawers)

            log.end_activity()                        # the ONE turn-end collapse
            await pilot.pause()
            assert all(d.has_class("-collapsed") for d in drawers)
    _run(body)


def test_copy_mode_treats_folded_activity_as_one_unit(tmp_path):
    """copy-mode indexes children ↔ _plain_chunks 1:1; the folded drawer is one
    child whose chunk carries every homework line, so a yank grabs it whole."""
    async def body():
        app, _ = _app(tmp_path)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            log = await _fresh_log(app, pilot)

            log.begin_activity()
            log.write(_tool("read_file", "a.py"))
            log.write(_tool("grep", "/x/"))
            await pilot.pause()
            log.end_activity()
            log.write(TuiAnswer(markdown="answer", model="grok"))
            await pilot.pause()

            # Invariant copy-mode relies on: one plain chunk per direct child.
            assert len(log.children) == len(log._plain_chunks)
            drawer_chunk = log._plain_chunks[0]
            assert "read_file · a.py" in drawer_chunk
            assert "grep · /x/" in drawer_chunk
    _run(body)
