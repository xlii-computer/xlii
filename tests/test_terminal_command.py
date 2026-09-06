"""A1 — `/terminal` (alias `/inline`), the named inverse of `/tui`.

From the inline REPL it is a friendly no-op (you're already at the inline
terminal); from inside the full-screen TUI it is intercepted by the front-end
and drops you back to the inline prompt (symmetric to how `/tui` enters it).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from xlii.commands import find_repl_command
from xlii.repl_cmds import register_all
from xlii.repl_cmds.code import _terminal_handler, _tui_handler

register_all()


class _FakeConsole:
    def __init__(self) -> None:
        self.prints: list = []

    def print(self, *a, **k) -> None:
        self.prints.append(" ".join(str(x) for x in a))


def test_terminal_is_code_only_with_inline_alias():
    # Registered in the code REPL, addressable as /terminal and /inline, and
    # deliberately absent from chat (it's the inverse of the code-only /tui).
    assert find_repl_command("/terminal", "code") is not None
    assert find_repl_command("/inline", "code") is not None
    assert find_repl_command("/terminal", "chat") is None
    assert find_repl_command("/inline", "chat") is None


def test_inline_terminal_handler_is_a_friendly_noop():
    console = _FakeConsole()
    assert _terminal_handler("/terminal", {"console": console}) is True
    assert any("already in the inline terminal" in line for line in console.prints)
    assert any("/tui" in line for line in console.prints)


# --- TUI side: /terminal·/inline are front-end-owned and drop back to inline ---

pytest.importorskip("textual")

from xlii.tui_textual import XliiApp, _PromptInput, _TUI_SLASH  # noqa: E402


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


def _run(coro_fn):
    asyncio.run(coro_fn())


def test_terminal_and_inline_are_front_end_owned_tokens():
    assert "/terminal" in _TUI_SLASH
    assert "/inline" in _TUI_SLASH


@pytest.mark.parametrize("tok", ["/terminal", "/inline"])
def test_tui_terminal_drops_back_by_stopping_the_app(tmp_path, tok):
    # In the TUI, /terminal·/inline tear down the app (control unwinds to
    # _tui_handler, which resumes the inline loop). Mirrors test_exit_stops_the_app.
    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            app.query_one("#input", _PromptInput).text = tok
            await pilot.press("enter")
            await pilot.pause()
        assert app.is_running is False
    _run(body)


def test_tui_terminal_leaves_quit_requested_false(tmp_path):
    # The disposition contract (A2 leans on this): /terminal does NOT request a
    # quit, so the inline loop resumes rather than ending the process.
    async def body():
        st = _state(tmp_path)
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            app.query_one("#input", _PromptInput).text = "/terminal"
            await pilot.press("enter")
            await pilot.pause()
        assert getattr(st, "quit_requested", False) is False
    _run(body)


def test_tui_handler_prints_back_at_inline_on_plain_return(tmp_path, monkeypatch):
    # When the TUI exits without a quit request, _tui_handler announces the
    # drop-back to the inline prompt (the post-/terminal message).
    console = _FakeConsole()
    agent = SimpleNamespace(console=console, run_turn=lambda q: None)
    state = SimpleNamespace(
        console=console,
        project=SimpleNamespace(name="proj", project_root=tmp_path),
    )
    ctx = {"console": console, "state": state, "agent": agent}
    monkeypatch.setattr("xlii.tui_textual.launch", lambda **kw: None)
    assert _tui_handler("/tui", ctx) is True
    assert any("back at the inline prompt" in line for line in console.prints)
