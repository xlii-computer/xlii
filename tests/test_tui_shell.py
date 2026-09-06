"""Tests for the unified shell layer (tui-layer T2).

Covers the shared captured runner (xlii.tui.shell), the rollout gate
(styled_enabled / XLII_SHELL_STYLE), the repl `!`/`!!` routing, the agent's
bash-as-ShellBlock path, and that t_bash's model-facing bytes are unchanged.

Rollout is opt-in first: with no XLII_SHELL_STYLE set everything stays raw, so
the rest of the suite (which never sets it) sees no behavior change — these
tests flip the gate explicitly.
"""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace

import pytest
from rich.console import Console

from tests.helpers import make_tool_ctx
from xlii.tui.events import ShellRan
from xlii.tui.shell import capture, run_shell_captured, styled_enabled


# -- rollout gate ---------------------------------------------------------

def test_styled_enabled_is_opt_in(monkeypatch):
    monkeypatch.delenv("XLII_SHELL_STYLE", raising=False)
    assert styled_enabled() is False
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    assert styled_enabled() is True
    monkeypatch.setenv("XLII_SHELL_STYLE", "STYLED")  # case-insensitive
    assert styled_enabled() is True
    monkeypatch.setenv("XLII_SHELL_STYLE", "raw")
    assert styled_enabled() is False


# -- captured runner mechanics -------------------------------------------

def test_capture_collects_streams_and_exit(tmp_path):
    cap = capture("printf 'a\\nb'; echo oops 1>&2; exit 5", tmp_path)
    assert cap.returncode == 5
    assert "a" in cap.stdout and "b" in cap.stdout
    assert "oops" in cap.stderr
    assert cap.timed_out is False
    assert cap.duration_s >= 0


def test_capture_timeout_kills_and_flags(tmp_path):
    cap = capture("sleep 2", tmp_path, timeout=0.3)
    assert cap.timed_out is True
    assert cap.returncode == -1


def test_capture_raises_on_unstartable():
    with pytest.raises(OSError):
        capture("true", "/no/such/dir/at/all")


def test_run_shell_captured_builds_event(tmp_path):
    ev = run_shell_captured("echo hi", tmp_path, source="agent_bash", intent="read-only")
    assert isinstance(ev, ShellRan)
    assert ev.command == "echo hi"
    assert ev.source == "agent_bash"
    assert ev.intent == "read-only"
    assert "hi" in ev.stdout
    assert ev.returncode == 0


def test_run_shell_captured_surfaces_timeout_in_stderr(tmp_path):
    ev = run_shell_captured("sleep 2", tmp_path, timeout=0.3)
    assert "timed out" in ev.stderr.lower()
    assert ev.returncode == -1


# -- repl ! / !! routing --------------------------------------------------

def test_passthrough_styled_emits_user_bang_event(monkeypatch, tmp_path):
    import xlii.repl as repl

    events: list = []
    monkeypatch.setattr(repl, "renderer", SimpleNamespace(emit=events.append))
    monkeypatch.setattr(repl, "styled_enabled", lambda: True)
    monkeypatch.setattr(repl.subprocess, "call",
                        lambda *a, **k: pytest.fail("raw path used when styled"))

    handled = repl._run_shell_passthrough("!echo hi", tmp_path)

    assert handled is True
    assert len(events) == 1
    assert events[0].command == "echo hi"
    assert events[0].source == "user_bang"
    assert "hi" in events[0].stdout


def test_double_bang_is_always_raw_even_when_styled(monkeypatch, tmp_path):
    import xlii.repl as repl

    monkeypatch.setattr(repl, "styled_enabled", lambda: True)  # styled ON
    monkeypatch.setattr(repl, "renderer",
                        SimpleNamespace(emit=lambda e: pytest.fail("styled used for !!")))
    calls: list = []
    monkeypatch.setattr(repl.subprocess, "call", lambda cmd, **k: calls.append(cmd) or 0)

    handled = repl._run_shell_passthrough("!!less file.log", tmp_path)

    assert handled is True
    assert calls == ["less file.log"]


def test_single_bang_default_is_raw(monkeypatch, tmp_path):
    import xlii.repl as repl

    monkeypatch.setattr(repl, "styled_enabled", lambda: False)
    calls: list = []
    monkeypatch.setattr(repl.subprocess, "call", lambda cmd, **k: calls.append(cmd) or 0)

    handled = repl._run_shell_passthrough("!echo hi", tmp_path)

    assert handled is True
    assert calls == ["echo hi"]


def test_passthrough_ignores_non_bang(tmp_path):
    import xlii.repl as repl

    assert repl._run_shell_passthrough("echo hi", tmp_path) is False


def test_styled_bang_clear_clears_surface_not_captured(monkeypatch, tmp_path):
    # `!clear` under styled capture would only print escape codes into a
    # ShellBlock; it must clear the real console instead and never capture.
    import xlii.repl as repl

    monkeypatch.setattr(repl, "styled_enabled", lambda: True)
    monkeypatch.setattr(repl, "renderer",
                        SimpleNamespace(emit=lambda e: pytest.fail("clear must not render a block")))
    cleared: list = []
    monkeypatch.setattr(repl, "console", SimpleNamespace(clear=lambda: cleared.append(True)))

    handled = repl._run_shell_passthrough("!clear", tmp_path)

    assert handled is True
    assert cleared == [True]


def test_double_bang_clear_stays_raw(monkeypatch, tmp_path):
    # `!!clear` is the explicit raw-TTY escape — it should reach the inherited
    # terminal, not the in-process console clear.
    import xlii.repl as repl

    monkeypatch.setattr(repl, "styled_enabled", lambda: True)
    monkeypatch.setattr(repl, "console",
                        SimpleNamespace(clear=lambda: pytest.fail("!!clear must stay raw")))
    calls: list = []
    monkeypatch.setattr(repl.subprocess, "call", lambda cmd, **k: calls.append(cmd) or 0)

    handled = repl._run_shell_passthrough("!!clear", tmp_path)

    assert handled is True
    assert calls == ["clear"]


def test_is_clear_command_truth_table():
    from xlii.repl import _is_clear_command

    assert _is_clear_command("clear")
    assert _is_clear_command("  cls  ")
    assert not _is_clear_command("clear && ls")
    assert not _is_clear_command("cleared")
    assert not _is_clear_command("")


# -- t_bash: structured event + unchanged model bytes --------------------

def test_t_bash_carries_agent_shell_event(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    r = ctx_bash(ctx, "echo hello && exit 3", intent="read-only")
    # model-facing content is byte-identical to the pre-T2 format
    assert r.content == "hello\n\n--- exit 3 ---"
    assert r.is_error is True
    # and now also carries a display event
    assert isinstance(r.shell, ShellRan)
    assert r.shell.source == "agent_bash"
    assert r.shell.intent == "read-only"
    assert r.shell.returncode == 3
    assert "hello" in r.shell.stdout


def test_t_bash_success_marks_clean_exit(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    r = ctx_bash(ctx, "echo ok", intent="read-only")
    assert r.content == "ok\n\n--- exit 0 ---"
    assert r.is_error is False
    assert r.shell.returncode == 0


def ctx_bash(ctx, command, *, intent):
    from xlii.tools import t_bash
    return t_bash(ctx, {"command": command, "intent": intent})


# -- agent display gating -------------------------------------------------

def test_announce_skips_bash_when_styled(monkeypatch):
    from xlii.agent import Agent

    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    buf = StringIO()
    stub = SimpleNamespace(console=Console(file=buf, no_color=True, width=80))
    Agent._announce_tool(stub, "bash", {"command": "ls"})
    assert buf.getvalue().strip() == ""  # ShellBlock will show `$ ls` instead


def test_announce_shows_bash_when_raw(monkeypatch):
    from xlii.agent import Agent

    monkeypatch.delenv("XLII_SHELL_STYLE", raising=False)
    buf = StringIO()
    stub = SimpleNamespace(console=Console(file=buf, no_color=True, width=80))
    Agent._announce_tool(stub, "bash", {"command": "ls -la"})
    out = buf.getvalue()
    assert "bash" in out and "ls -la" in out


def test_agent_renderer_binds_to_own_console():
    from xlii.agent import Agent

    c1 = Console()
    stub = SimpleNamespace(console=c1)
    r = Agent._renderer(stub)
    assert r.console is c1
    assert Agent._renderer(stub) is r  # cached
    c2 = Console()
    stub.console = c2
    assert Agent._renderer(stub).console is c2  # rebuilt on console swap


# -- remote-face surfaces: session renderer + no-terminal gate (tauri-face) --


def _face_state(tmp_path, emitted):
    """A session whose console is NOT a terminal and whose agent carries a
    replaced renderer — the serve --face shape."""
    console = SimpleNamespace(is_terminal=False,
                              printed=[],
                              clear=lambda: None)
    console.print = lambda *a, **k: console.printed.append(" ".join(map(str, a)))
    agent = SimpleNamespace(_renderer=lambda: SimpleNamespace(emit=emitted.append),
                            console=console)
    return SimpleNamespace(console=console, agent=agent,
                           shell_cwd=tmp_path,
                           project=SimpleNamespace(project_root=tmp_path))


def test_live_shell_emits_through_the_session_renderer(monkeypatch, tmp_path):
    """The face bug: a bare shell line's ShellRan went through the MODULE
    renderer (the server's terminal) — the browser saw nothing. With a state
    in hand the SESSION's renderer owns the event."""
    import xlii.repl as repl

    monkeypatch.setattr(repl, "styled_enabled", lambda: True)
    monkeypatch.setattr(
        repl, "renderer",
        SimpleNamespace(emit=lambda e: pytest.fail("module renderer used with a session")))
    emitted: list = []
    state = _face_state(tmp_path, emitted)

    repl._handle_live_shell(state, "echo over-the-wire")

    assert len(emitted) == 1
    assert emitted[0].command == "echo over-the-wire"
    assert "over-the-wire" in emitted[0].stdout


def test_passthrough_with_state_emits_through_session_renderer(monkeypatch, tmp_path):
    import xlii.repl as repl

    monkeypatch.setattr(repl, "styled_enabled", lambda: True)
    monkeypatch.setattr(
        repl, "renderer",
        SimpleNamespace(emit=lambda e: pytest.fail("module renderer used with a session")))
    emitted: list = []
    state = _face_state(tmp_path, emitted)

    handled = repl._run_shell_passthrough("!echo hi", tmp_path, state)

    assert handled is True
    assert emitted and emitted[0].source == "user_bang"


def test_raw_bang_opens_external_terminal_without_a_tty(monkeypatch, tmp_path):
    """`!!` on the face must not inherit the server TTY — open a window instead."""
    import xlii.repl as repl

    monkeypatch.setattr(repl, "styled_enabled", lambda: True)
    monkeypatch.setattr(repl, "_run_raw_shell",
                        lambda *a, **k: pytest.fail("raw tty handed over"))
    launched = {}

    def fake_launch(cwd, *, run="", preferred=""):
        launched["run"] = run
        launched["cwd"] = str(cwd)
        return True, "opened less in a new terminal"

    monkeypatch.setattr("xlii.interactive.launch_in_external_terminal", fake_launch)
    state = _face_state(tmp_path, [])

    handled = repl._run_shell_passthrough("!!less big.log", tmp_path, state)

    assert handled is True
    assert launched["run"] == "less big.log"
    assert any("opened less" in line for line in state.console.printed)


def test_interactive_live_shell_opens_external_terminal(monkeypatch, tmp_path):
    import xlii.repl as repl

    monkeypatch.setattr(repl, "styled_enabled", lambda: True)
    monkeypatch.setattr(repl.subprocess, "call",
                        lambda *a, **k: pytest.fail("tty handed over"))
    launched = {}

    def fake_launch(cwd, *, run="", preferred=""):
        launched["run"] = run
        return True, "opened htop in a new terminal"

    monkeypatch.setattr("xlii.interactive.launch_in_external_terminal", fake_launch)
    state = _face_state(tmp_path, [])

    repl._handle_live_shell(state, "htop")

    assert launched["run"] == "htop"
    assert any("opened htop" in line for line in state.console.printed)
