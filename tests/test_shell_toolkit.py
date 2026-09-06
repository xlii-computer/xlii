"""Phase 7 — shell NL→command, failure nudge, output post-process."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers import FakeConsole
from xlii.repl import REPLState, process_repl_input
from xlii.shell_toolkit import (
    _extract_command,
    gate_shell_command,
    last_shell_capture,
    nl_command_flow,
    nl_to_command,
    offer_failure_nudge,
    post_process_output,
    record_last_shell,
    suggest_fix,
)
from xlii.tui.events import ShellRan
from tests.test_rail import _bare_agent


@pytest.fixture
def state(tmp_path):
    agent = _bare_agent()
    agent.history = [{"role": "system", "content": "s"}]
    xli = tmp_path / ".xlii"
    xli.mkdir()
    proj = SimpleNamespace(
        project_root=tmp_path, xli_dir=xli, local_only=True, name="proj"
    )
    st = REPLState(
        console=FakeConsole(),
        agent=agent,
        project=proj,
        cfg=SimpleNamespace(orchestrator_temp=lambda: 0.7),
        pool=[],
    )
    st.shell_cwd = tmp_path.resolve()
    return st


def _fake_shell_ev(**kw) -> ShellRan:
    defaults = dict(
        command="gti status",
        cwd=Path("/tmp"),
        stdout="",
        stderr="gti: command not found",
        returncode=127,
        source="user_shell",
    )
    defaults.update(kw)
    return ShellRan(**defaults)


def test_extract_command_strips_fences_and_prefix():
    assert _extract_command("```bash\ngit status\n```") == "git status"
    assert _extract_command("$ ls -la") == "ls -la"


def test_record_and_read_last_shell(state):
    ev = _fake_shell_ev(command="echo hi", returncode=0, stderr="")
    record_last_shell(state, ev)
    assert last_shell_capture(state) is ev
    assert state.agent.session.last_shell is ev


def test_nl_to_command_uses_system_profile(monkeypatch, state, tmp_path):
    from xlii.system_profile import SystemProfile

    captured: dict[str, str] = {}
    monkeypatch.setattr(
        "xlii.system_profile.load_system_profile",
        lambda: SystemProfile(package_manager="apt", os_id="parrot"),
    )

    def fake_query(system, user):
        captured["system"] = system
        return "apt install ripgrep fd-find"

    monkeypatch.setattr("xlii.shell_toolkit._secondary_query", fake_query)
    cmd = nl_to_command(
        "install ripgrep and fd",
        cwd=tmp_path,
        last_listing="Last listing ($ ls):\n  Cursor-3.15.AppImage",
    )
    assert cmd == "apt install ripgrep fd-find"
    assert "[SYSTEM]" in captured["system"]
    assert "pkg=apt" in captured["system"]
    assert "[DESK]" in captured["system"]
    assert "Drop zone:" in captured["system"]
    assert "Last listing" in captured["system"]
    assert "Cursor-3.15" in captured["system"]


def test_secondary_query_strips_empty_sentinel(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "xlii.secondary_ai.query_with_profile",
        lambda *a, **k: SimpleNamespace(text="(empty response from secondary model)"),
    )
    from xlii.shell_toolkit import _secondary_query

    assert _secondary_query("sys", "user") == ""


def test_empty_model_reply_not_proposed(monkeypatch, state):
    monkeypatch.setattr(
        "xlii.shell_toolkit.nl_to_command",
        lambda nl, cwd, **k: "",
    )
    nl_command_flow(state, "do something")
    assert "model returned an empty command" in state.console.text


def test_gate_shell_command_denies_modifies_system(state, monkeypatch):
    monkeypatch.setattr("xlii.tools._confirm", lambda prompt="": "")
    assert gate_shell_command(state, "sudo reboot") is False


def test_gate_offers_copy_for_terminal(state, monkeypatch):
    """[c] copies the command to the clipboard and does NOT run it here."""
    copied: list[str] = []
    state._clipboard = lambda text: copied.append(text)
    monkeypatch.setattr("xlii.tools._confirm", lambda prompt="": "c")
    cmd = "sudo apt-get update && sudo apt-get install -y mc"
    assert gate_shell_command(state, cmd) is False  # not run in the captured shell
    assert copied == [cmd]
    assert "copied" in state.console.text.lower()


def test_copy_for_terminal_prefers_hook_then_osc52(state, monkeypatch):
    from xlii.shell_toolkit import copy_for_terminal

    seen: list[str] = []
    state._clipboard = lambda t: seen.append(t)
    assert copy_for_terminal(state, "echo hi") is True
    assert seen == ["echo hi"]

    # No TUI clipboard → falls back to the OSC 52 escape path.
    state._clipboard = None
    emitted: list[str] = []
    monkeypatch.setattr(
        "xlii.shell_toolkit._osc52_copy", lambda t: emitted.append(t) or True
    )
    assert copy_for_terminal(state, "echo bye") is True
    assert emitted == ["echo bye"]


def test_nl_flow_prompt_includes_command(monkeypatch, state):
    """The confirm prompt must carry the command — the TUI modal floats over and
    hides the 'proposed' line printed above it."""
    seen: dict[str, str] = {}

    def fake_confirm(prompt=""):
        seen["prompt"] = prompt
        return "n"

    monkeypatch.setattr("xlii.tools._confirm", fake_confirm)
    monkeypatch.setattr(
        "xlii.shell_toolkit.nl_to_command", lambda nl, cwd, **k: "sudo apt-get install -y mc"
    )
    nl_command_flow(state, "update midnight commander")
    assert "sudo apt-get install -y mc" in seen["prompt"]
    assert "[c]" in seen["prompt"]  # copy offered at the propose step


def test_gate_prompt_includes_command(monkeypatch, state):
    seen: dict[str, str] = {}
    monkeypatch.setattr(
        "xlii.tools._confirm",
        lambda prompt="": seen.__setitem__("prompt", prompt) or "n",
    )
    gate_shell_command(state, "sudo reboot")
    assert "sudo reboot" in seen["prompt"]


def test_needs_password_tty_detects_sudo():
    from xlii.interactive import needs_password_tty

    assert needs_password_tty("sudo apt install mc") is True
    assert needs_password_tty("sudo apt update && sudo apt install -y mc") is True
    assert needs_password_tty("doas pkg_add mc") is True
    assert needs_password_tty("apt install mc") is False
    assert needs_password_tty("echo sudo here") is False  # sudo as an arg, not a command
    assert needs_password_tty("ls -la") is False
    assert needs_password_tty("pkexec apt install slack") is True


def test_run_proposed_routes_sudo_to_interactive_hook(monkeypatch, state):
    """A sudo command must go to the inherited-TTY hook, NOT the captured runner
    (whose DEVNULL stdin makes sudo fail 'a terminal is required')."""
    from xlii.shell_toolkit import run_proposed_command

    monkeypatch.setattr("xlii.shell_toolkit.gate_shell_command", lambda st, cmd: True)
    captured: list[int] = []
    monkeypatch.setattr(
        "xlii.shell_run.run_shell_captured",
        lambda *a, **k: captured.append(1) or _fake_shell_ev(),
    )
    monkeypatch.setattr("xlii.shell_run.styled_enabled", lambda: True)
    handed: list[str] = []
    state._run_interactive = lambda cmd, cwd: handed.append(cmd)

    run_proposed_command(state, "sudo apt install -y mc")
    assert handed == ["sudo apt install -y mc"]  # routed to the real terminal
    assert captured == []  # NOT the captured runner


def test_run_proposed_face_sudo_opens_window_not_server_tty(monkeypatch, state):
    """Face has no TTY. Sudo must not inherit the hidden server terminal."""
    from xlii.shell_toolkit import run_proposed_command

    monkeypatch.setattr("xlii.shell_toolkit.gate_shell_command", lambda st, cmd: True)
    if hasattr(state, "_run_interactive"):
        delattr(state, "_run_interactive")
    state.console.is_terminal = False
    launched = []
    monkeypatch.setattr("xlii.interactive.has_graphical_session", lambda: True)
    monkeypatch.setattr(
        "xlii.interactive.launch_in_external_terminal",
        lambda cwd, run="", preferred="": launched.append(run) or (True, "opened"),
    )
    captured: list[int] = []
    monkeypatch.setattr(
        "xlii.shell_run.run_shell_captured",
        lambda *a, **k: captured.append(1),
    )
    run_proposed_command(state, "sudo apt install -y slack")
    assert launched == ["sudo apt install -y slack"]
    assert captured == []


def test_nl_flow_copy_option(monkeypatch, state):
    """[c] at the propose step copies and does NOT run."""
    copied: list[str] = []
    state._clipboard = lambda t: copied.append(t)
    monkeypatch.setattr("xlii.tools._confirm", lambda prompt="": "c")
    monkeypatch.setattr(
        "xlii.shell_toolkit.nl_to_command", lambda nl, cwd, **k: "sudo apt-get install -y mc"
    )
    ran: list[str] = []
    monkeypatch.setattr(
        "xlii.shell_toolkit.run_proposed_command", lambda st, cmd, **kw: ran.append(cmd)
    )
    nl_command_flow(state, "update mc")
    assert copied == ["sudo apt-get install -y mc"]
    assert ran == []  # copied, not run
    assert "copied" in state.console.text.lower()


def test_suggest_fix_typo(monkeypatch):
    monkeypatch.setattr(
        "xlii.shell_toolkit._secondary_query",
        lambda system, user: "git status",
    )
    fix = suggest_fix("gti status", stderr="gti: command not found", exit_code=127)
    assert fix == "git status"


def test_post_process_without_rerun(monkeypatch, state):
    ev = ShellRan(
        command="grep -oE '[0-9.]+' log",
        cwd=Path("/tmp"),
        stdout="connect from 10.0.0.1\nconnect from 192.168.1.5\n",
        stderr="",
        returncode=0,
    )
    record_last_shell(state, ev)
    monkeypatch.setattr(
        "xlii.shell_toolkit._secondary_query",
        lambda system, user: "10.0.0.1\n192.168.1.5",
    )
    out = post_process_output("extract the IPs from that", ev)
    assert "10.0.0.1" in out
    assert "grep" not in out or "10.0.0.1" in out


def test_failure_nudge_offers_fix(monkeypatch, state):
    ev = _fake_shell_ev()
    answers = iter(["y"])
    monkeypatch.setattr("xlii.tools._confirm", lambda prompt="": next(answers))
    monkeypatch.setattr(
        "xlii.shell_toolkit.suggest_fix",
        lambda *a, **k: "git status",
    )
    ran: list[str] = []
    monkeypatch.setattr(
        "xlii.shell_toolkit.run_proposed_command",
        lambda st, cmd, **kw: ran.append(cmd),
    )
    offer_failure_nudge(state, ev)
    assert ran == ["git status"]


def test_failure_nudge_stops_at_depth_cap(monkeypatch, state):
    """At the retry cap we bail without even asking for another fix."""
    import xlii.shell_toolkit as stk
    from xlii.shell_toolkit import _MAX_FIX_ATTEMPTS

    asked: list[int] = []
    monkeypatch.setattr(stk, "suggest_fix", lambda *a, **k: asked.append(1) or "sudo x")
    ev = _fake_shell_ev(command="x", returncode=1, stderr="boom")
    offer_failure_nudge(state, ev, depth=_MAX_FIX_ATTEMPTS)
    assert asked == []  # never queried the model for another suggestion
    assert "leaving this one to you" in state.console.text


def test_failure_nudge_stops_on_repeated_suggestion(monkeypatch, state):
    """A suggestion that repeats an earlier attempt breaks the cycle (the sudo case)."""
    import xlii.shell_toolkit as stk
    from xlii.shell_toolkit import _norm_cmd

    monkeypatch.setattr(stk, "suggest_fix", lambda *a, **k: "sudo apt-get install -y mc")
    ran: list[str] = []
    monkeypatch.setattr(stk, "run_proposed_command", lambda st, cmd, **kw: ran.append(cmd))
    ev = _fake_shell_ev(command="apt-get install -y mc", returncode=100, stderr="permission denied")
    tried = frozenset(
        {_norm_cmd("apt-get install -y mc"), _norm_cmd("sudo apt-get install -y mc")}
    )
    offer_failure_nudge(state, ev, tried=tried, depth=1)
    assert ran == []  # repeated suggestion is not re-offered
    assert "repeats an earlier attempt" in state.console.text


def test_failure_nudge_loop_terminates(monkeypatch, state):
    """End-to-end: an unfixable failure cannot recurse forever — runs are bounded."""
    import xlii.shell_toolkit as stk
    from xlii.shell_toolkit import _MAX_FIX_ATTEMPTS, run_proposed_command

    runs: list[str] = []

    def fake_capture(cmd, cwd, *, source, intent):
        runs.append(cmd)
        return _fake_shell_ev(command=cmd, returncode=1, stderr="needs root")

    monkeypatch.setattr("xlii.shell_run.run_shell_captured", fake_capture)
    monkeypatch.setattr("xlii.shell_run.styled_enabled", lambda: True)
    monkeypatch.setattr("xlii.tui.renderer.emit", lambda ev: None)
    monkeypatch.setattr(stk, "gate_shell_command", lambda st, cmd: True)
    # distinct suggestion each time, so only the depth cap (not dedup) stops it.
    # Non-sudo so it stays on the captured runner (sudo routes to the TTY path).
    counter = iter(range(100))
    monkeypatch.setattr(stk, "suggest_fix", lambda *a, **k: f"badcmd-{next(counter)}")
    monkeypatch.setattr("xlii.tools._confirm", lambda prompt="": "y")

    run_proposed_command(state, "apt-get install -y mc")
    # original run + at most _MAX_FIX_ATTEMPTS retries — never unbounded
    assert len(runs) == _MAX_FIX_ATTEMPTS + 1


def test_sh_command_proposes_and_runs(monkeypatch, state):
    from xlii.repl_cmds.sh import _sh_handler

    answers = iter(["y"])
    monkeypatch.setattr("xlii.tools._confirm", lambda prompt="": next(answers))
    monkeypatch.setattr(
        "xlii.shell_toolkit.nl_to_command",
        lambda nl, cwd, **k: "git status",
    )
    monkeypatch.setattr(
        "xlii.shell_toolkit.run_proposed_command",
        lambda st, cmd, **kw: None,
    )
    _sh_handler("/sh show git status", state.as_context_dict())
    assert "proposed" in state.console.text


def test_post_process_prompt_prefix(monkeypatch, state):
    ev = _fake_shell_ev(command="ls", stdout="a\nb\n", returncode=0, stderr="")
    record_last_shell(state, ev)
    monkeypatch.setattr(
        "xlii.shell_toolkit.post_process_flow",
        lambda st, inst: state.console.print(f"ok:{inst}"),
    )
    out = process_repl_input(state, "?> summarize this log")
    assert out == (None, True)
    assert "ok:summarize this log" in state.console.text


def test_prose_suggests_sh(state, monkeypatch):
    monkeypatch.setattr("xlii.repl.subprocess.call", lambda *a, **k: 0)
    process_repl_input(state, "frobnicate the parser module")
    assert "/sh" in state.console.text


def test_sh_parse_mode():
    from xlii.repl_cmds.sh import _sh_parse_mode

    assert _sh_parse_mode("/sh show git status") == ("nl", "show git status")
    assert _sh_parse_mode("/sh --explain") == ("explain", "")
    assert _sh_parse_mode("/sh --transform") == ("transform", "")
    assert _sh_parse_mode("/sh --transform extract errors") == ("transform", "extract errors")
    assert _sh_parse_mode("/explain") == ("explain", "")
    assert _sh_parse_mode("/shum") == ("transform", "")
    assert _sh_parse_mode("/shum one line") == ("transform", "one line")
    assert _sh_parse_mode("/sh") == ("usage", "")


def test_sh_explain_and_transform(monkeypatch, state):
    from xlii.repl_cmds.sh import _sh_handler
    from xlii.shell_toolkit import record_last_shell

    ev = _fake_shell_ev(command="ls", stdout="a\n", returncode=0, stderr="")
    record_last_shell(state, ev)
    monkeypatch.setattr("xlii.repl_cmds.sh.explain_last", lambda e: "explained")
    monkeypatch.setattr(
        "xlii.repl_cmds.sh.summarize_last",
        lambda e, extra="": f"transformed:{extra}",
    )

    ctx = state.as_context_dict()
    _sh_handler("/sh --explain", ctx)
    assert "explained" in state.console.text

    state.console.lines.clear()
    _sh_handler("/explain", ctx)
    assert "explained" in state.console.text

    state.console.lines.clear()
    _sh_handler("/shum brief", ctx)
    assert "transformed:brief" in state.console.text

    state.console.lines.clear()
    _sh_handler("/sh --transform", ctx)
    assert "transformed:" in state.console.text


def test_sh_legacy_names_no_longer_resolve():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    assert find_repl_command("/shplain", "code") is None
    assert find_repl_command("/shumrise", "code") is None
    assert find_repl_command("/shum", "code").name == "sh"
    assert find_repl_command("/explain", "code").name == "sh"
