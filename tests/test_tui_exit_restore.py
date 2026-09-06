"""Leaving the in-session TUI (`/tui`) must hand a LIVE console back to the
inline prompt.

Regression: the TUI's on_mount repoints both ``agent.console`` and
``state.console`` at the transcript (and installs TUI-only shell hooks), but the
``/tui`` handler used to restore only ``agent.console``. The stale
``state.console`` then pointed at the torn-down Textual app, so the next
``state.console.print()`` (e.g. ``/exit``'s "bye") raised ``App is not running``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from xlii.repl_cmds.code import _tui_handler  # noqa: E402


class _FakeConsole:
    def __init__(self, tag: str):
        self.tag = tag
        self.prints: list = []

    def print(self, *a, **k):
        self.prints.append(a)


def test_tui_exit_restores_state_console_and_clears_hooks(tmp_path, monkeypatch):
    inline_console = _FakeConsole("inline")
    agent = SimpleNamespace(console=inline_console, run_turn=lambda q: None)
    state = SimpleNamespace(
        console=inline_console,
        project=SimpleNamespace(name="proj", project_root=tmp_path),
    )
    ctx = {"console": inline_console, "state": state, "agent": agent}

    tui_console = _FakeConsole("tui")

    def fake_launch(*, project_name, agent, run_turn, state):
        # Mirror XliiApp.on_mount's mutations to the shared session.
        agent.console = tui_console
        state.console = tui_console
        state._clipboard = lambda text: None
        state._run_interactive = lambda c, cwd: None

    monkeypatch.setattr("xlii.tui_textual.launch", fake_launch)

    assert _tui_handler("/tui", ctx) is True

    # The inline prompt must get a live console back — not the torn-down TUI one.
    assert state.console is inline_console
    assert agent.console is inline_console
    # TUI-only shell hooks must be gone (they did not exist before the TUI).
    assert not hasattr(state, "_clipboard")
    assert not hasattr(state, "_run_interactive")


def test_tui_exit_restores_preexisting_hooks(tmp_path, monkeypatch):
    """If the hooks already existed before /tui, restore those values (don't drop)."""
    inline_console = _FakeConsole("inline")
    sentinel_clip = lambda text: "preexisting"  # noqa: E731
    agent = SimpleNamespace(console=inline_console, run_turn=lambda q: None)
    state = SimpleNamespace(
        console=inline_console,
        project=SimpleNamespace(name="proj", project_root=tmp_path),
        _clipboard=sentinel_clip,
    )
    ctx = {"console": inline_console, "state": state, "agent": agent}

    def fake_launch(*, project_name, agent, run_turn, state):
        state.console = _FakeConsole("tui")
        state._clipboard = lambda text: None
        state._run_interactive = lambda c, cwd: None

    monkeypatch.setattr("xlii.tui_textual.launch", fake_launch)

    assert _tui_handler("/tui", ctx) is True
    assert state._clipboard is sentinel_clip
    assert not hasattr(state, "_run_interactive")
