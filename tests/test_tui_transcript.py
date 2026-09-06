"""Tests for the Textual transcript layer (structured answers, copyable fences)."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual")

from textual.widgets import Static

from xlii.tui.transcript import (
    AnswerTranscript,
    CopyableMarkdownFence,
    TuiAnswer,
    TranscriptLog,
)
from xlii.tui_textual import XliiApp


def _state(tmp_path):
    from types import SimpleNamespace
    return SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj"),
    )


def _run(coro_fn):
    asyncio.run(coro_fn())


def test_answer_mounts_copyable_fence(tmp_path):
    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            app.write_block(TuiAnswer(
                markdown="Try this:\n\n```python\nprint('hi')\n```",
                model="grok-build-0.1",
            ))
            await pilot.pause()
            fences = app.query(CopyableMarkdownFence)
            assert len(fences) == 1
            assert fences[0].code.strip() == "print('hi')"
            text = app.query_one("#log", TranscriptLog).plain_text()
            assert "print('hi')" in text
            assert "grok-build-0.1" in text
    _run(body)


def test_mode_framed_answer_focus_accepts_border_color(tmp_path):
    """Regression: AnswerTranscript.on_focus used to set styles.border to
    ``$accent``, which Color.parse rejects outside TCSS and crashed the TUI on
    mouse/keyboard focus of a mode-framed answer (SelectableAnswer)."""

    async def body():
        app = XliiApp(
            project_name="proj",
            agent=None,
            run_turn=lambda q: ("", set(), None),
            state=_state(tmp_path),
        )
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            app.write_block(
                TuiAnswer(
                    markdown="mode-framed answer",
                    mode="PLAN",
                    mode_color="green",
                )
            )
            await pilot.pause()
            answers = [w for w in app.query_one("#log", TranscriptLog).children
                       if isinstance(w, AnswerTranscript)]
            assert answers, "expected a mode-framed answer widget"
            ans = answers[0]
            # Must not raise StyleValueError / ColorParseError.
            ans.on_focus()
            await pilot.pause()
            ans.on_blur()
            await pilot.pause()
            # And real focus via the pilot must also stay clean.
            ans.focus()
            await pilot.pause()

    _run(body)


def test_copy_fence_copies_code(tmp_path, monkeypatch):
    copied = {}

    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        app.copy_to_clipboard = lambda text: copied.setdefault("text", text)
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            app.write_block(TuiAnswer(markdown="```\nsecret\n```"))
            await pilot.pause()
            fence = app.query(CopyableMarkdownFence)[0]
            fence.focus()
            await pilot.press("c")
            await pilot.pause()
            assert copied["text"] == "secret"
    _run(body)


def test_shell_blocks_still_render_as_rich_static(tmp_path):
    async def body():
        from xlii.tui import blocks
        from xlii.tui.events import MetaMessage

        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            app.write_block(blocks.meta_block(MetaMessage("hello meta", "info")))
            await pilot.pause()
            log = app.query_one("#log", TranscriptLog)
            assert "hello meta" in log.plain_text()
            assert any(isinstance(c, Static) for c in log.children)
    _run(body)
