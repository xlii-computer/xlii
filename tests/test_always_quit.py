"""A2 — `/exit`·`/quit` (and ctrl+d) always quit, from anywhere (OQ5).

The fix for the nested-TUI "backs you out" bug: a full-quit disposition flag
(`state.quit_requested`) plus a `_QuitSession` fire-alarm exception that the
inline loop raises and the top-level command (cmd_code / cmd_chat) catches —
so quitting the TUI nested in the inline REPL ends the process instead of
silently resuming the inline prompt. `/terminal` stays the only "go back" verb.
"""

from __future__ import annotations

import argparse
import asyncio
from types import SimpleNamespace

import pytest

from xlii.commands import REPLCommand, register_repl_command, unregister_repl_command
from xlii.repl import REPLState, _QuitSession, run_repl_loop
from xlii.repl_cmds import register_all
from xlii.repl_cmds.code import _tui_handler
from tests.helpers import FakeConsole, make_agent

register_all()


def test_quit_requested_defaults_false(tmp_path):
    agent = make_agent(tmp_path)
    state = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                      cfg=agent.cfg, pool=agent.pool)
    assert state.quit_requested is False


class _ScriptedSession:
    """Minimal prompt_toolkit PromptSession stand-in: replays scripted inputs.

    A second call past the script raises — so a loop that fails to exit when it
    should is caught as a test failure rather than spinning forever."""

    def __init__(self, inputs):
        self._inputs = list(inputs)
        self._i = 0

    def prompt(self, message, **kwargs):
        if self._i >= len(self._inputs):
            raise AssertionError("loop did not exit when expected")
        val = self._inputs[self._i]
        self._i += 1
        return val


def _state_with_xlii(tmp_path):
    (tmp_path / ".xlii").mkdir(exist_ok=True)
    agent = make_agent(tmp_path)
    state = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                      cfg=agent.cfg, pool=agent.pool)
    state.command_scope = "code"
    return state


def test_inline_loop_raises_quit_session_when_a_command_sets_the_flag(tmp_path):
    # Simulates the nested TUI: /tui's handler returns after the TUI set
    # state.quit_requested (via its /exit). The loop must see the flag on the
    # next check and raise _QuitSession rather than resume.
    def _setflag(line, ctx):
        ctx["state"].quit_requested = True
        return True

    register_repl_command(REPLCommand(name="_quittest", handler=_setflag, repls=["code"]))
    try:
        state = _state_with_xlii(tmp_path)
        session = _ScriptedSession(["/_quittest"])
        with pytest.raises(_QuitSession):
            run_repl_loop(
                state, session=session,
                get_prompt_prefix=lambda: "p› ",
                run_turn=lambda *a, **k: ("", set(), None),
            )
        assert state.quit_requested is True
        assert any("bye" in line for line in state.console.lines)
    finally:
        unregister_repl_command("_quittest")


def test_inline_exit_returns_and_sets_flag(tmp_path):
    # A plain inline /exit returns cleanly (top-level), recording the disposition.
    state = _state_with_xlii(tmp_path)
    session = _ScriptedSession(["/exit"])
    # Returns (does not raise) — inline is already the top level.
    run_repl_loop(
        state, session=session,
        get_prompt_prefix=lambda: "p› ",
        run_turn=lambda *a, **k: ("", set(), None),
    )
    assert state.quit_requested is True
    assert any("bye" in line for line in state.console.lines)


def test_cmd_chat_catches_quit_session(monkeypatch):
    # The persona-restart recursion lives in _chat_run_session; a _QuitSession
    # raised inside it must unwind to cmd_chat's catch (quit beats restart).
    from xlii.cmds import sessions as S
    from xlii.cmds.sessions import chat as sessions_chat

    def boom(*a, **k):
        raise _QuitSession()

    monkeypatch.setattr(sessions_chat, "_chat_run_session", boom)
    args = argparse.Namespace(list=False, new=None, edit=None, delete=None,
                              name=None, yolo=False, force=False, tui=False, yes=False)
    assert S.cmd_chat(args) == 0


def test_cmd_code_catches_quit_session(tmp_path, monkeypatch):
    import xlii.config as C
    from xlii.cmds import sessions as S
    from xlii.cmds.sessions import code as sessions_code
    from xlii.sync import init_project

    # A one-key config so ClientPool.from_config succeeds (lazy clients, no net).
    # Pin the config path per-test: under pytest conftest already redirects it,
    # but a bare-python replay of this test would write the operator's REAL
    # ~/.config/xlii/config.json (the 2026-07-20 clobber class — see
    # tests/test_journal_key.py).
    cdir = tmp_path / "xlii-config"
    monkeypatch.setattr(C, "GLOBAL_CONFIG_DIR", cdir)
    monkeypatch.setattr(C, "GLOBAL_CONFIG_FILE", cdir / "config.json")
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "config.json").write_text('{"keys": ["xai-fake-key"]}')
    init_project(None, tmp_path, name="quittest-proj", local_only=True)

    def boom(*a, **k):
        raise _QuitSession()

    monkeypatch.setattr(sessions_code, "_cmd_code_run", boom)
    # B4: the nested-session guard lives in xlii.session_boot now — patch it there.
    import xlii.session_boot as session_boot
    monkeypatch.setattr(session_boot, "nested_session_guard", lambda *a, **k: True)
    args = argparse.Namespace(
        target=str(tmp_path), yolo=False, rail=False, discovery=False, ops=False,
        no_sync=True, tui=False, force=False, preview=False, init=False, launch=True,
    )
    assert S.cmd_code(args) == 0


def test_tui_handler_suppresses_back_message_on_quit(tmp_path, monkeypatch):
    # When the TUI exited via /exit (quit_requested True), _tui_handler must not
    # announce a return to the inline prompt — the loop's quit check ends it.
    console = FakeConsole()
    agent = SimpleNamespace(console=console, run_turn=lambda q: None)
    state = SimpleNamespace(
        console=console, quit_requested=False,
        project=SimpleNamespace(name="proj", project_root=tmp_path),
    )
    ctx = {"console": console, "state": state, "agent": agent}

    def fake_launch(*, project_name, agent, run_turn, state):
        state.quit_requested = True  # the TUI's /exit set this before teardown

    monkeypatch.setattr("xlii.tui_textual.launch", fake_launch)
    assert _tui_handler("/tui", ctx) is True
    assert not any("back at the inline prompt" in line for line in console.lines)


# --- TUI front-end disposition (textual pilot) -------------------------------

pytest.importorskip("textual")

from xlii.tui_textual import XliiApp, _PromptInput  # noqa: E402


def _fake_agent_ns():
    from xlii.agent import SessionState
    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
        howto_mode=False, history=[], model_override=None, session=SessionState(),
    )


def _ns_state(tmp_path):
    return SimpleNamespace(
        shell_cwd=tmp_path, quit_requested=False,
        project=SimpleNamespace(project_root=tmp_path, name="proj"),
        agent=_fake_agent_ns(),
    )


def _run(coro_fn):
    asyncio.run(coro_fn())


@pytest.mark.parametrize("tok", ["/exit", "/quit"])
def test_tui_exit_quit_request_a_full_quit(tmp_path, tok):
    async def body():
        st = _ns_state(tmp_path)
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            app.query_one("#input", _PromptInput).text = tok
            await pilot.press("enter")
            await pilot.pause()
        assert st.quit_requested is True
        assert app.is_running is False
    _run(body)


def test_tui_ctrl_d_requests_a_full_quit(tmp_path):
    async def body():
        st = _ns_state(tmp_path)
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_quit()        # the ctrl+d binding target
            await pilot.pause()
        assert st.quit_requested is True
        assert app.is_running is False
    _run(body)


def test_tui_terminal_does_not_request_quit(tmp_path):
    # /terminal is the "go back a layer" verb — it must NOT set quit_requested,
    # so the inline loop resumes instead of ending the process.
    async def body():
        st = _ns_state(tmp_path)
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            app.query_one("#input", _PromptInput).text = "/terminal"
            await pilot.press("enter")
            await pilot.pause()
        assert st.quit_requested is False
        assert app.is_running is False
    _run(body)
