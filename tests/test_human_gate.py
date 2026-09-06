"""Vector G — foreground human gate (human_only, typed phrase, fresh admin)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.commands import (
    REPLCommand,
    dispatch_repl_command,
    register_repl_command,
    unregister_repl_command,
)
from xlii.console_prompt import can_prompt, request_line
from xlii.human_gate import (
    HumanGateError,
    confirm_typed_phrase,
    is_foreground_console,
    require_human,
    verify_fresh_admin,
)
from xlii.shell_toolkit import capturing_console


class FakeConsole:
    def __init__(self):
        self.lines: list[str] = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(a) for a in args))


class _Recorder:
    def __init__(self, answers):
        self.answers = list(answers)
        self.asked: list[tuple[str, bool]] = []
        self.xlii_foreground = True

    def request_input(self, prompt, *, secret=False):
        self.asked.append((prompt, secret))
        return self.answers.pop(0)


def test_is_foreground_console_matrix():
    assert is_foreground_console(None) is False

    cap = capturing_console()
    assert is_foreground_console(cap) is False

    tui = SimpleNamespace(request_input=lambda *a, **k: "x", xlii_foreground=True)
    assert is_foreground_console(tui) is True

    plain = SimpleNamespace()
    assert is_foreground_console(plain) is True

    marked = SimpleNamespace(xlii_foreground=False)
    assert is_foreground_console(marked) is False


def test_require_human_raises_on_capturing():
    cap = capturing_console()
    with pytest.raises(HumanGateError):
        require_human({"console": cap})


def test_require_human_ok_on_foreground():
    require_human({"console": _Recorder([])})


def test_confirm_typed_phrase_exact_match_only():
    phrase = "DESTROY THIS INSTALL"
    assert confirm_typed_phrase(_Recorder([phrase]), phrase) is True
    assert confirm_typed_phrase(_Recorder([f"  {phrase}  "]), phrase) is True
    assert confirm_typed_phrase(_Recorder(["y"]), phrase) is False
    assert confirm_typed_phrase(_Recorder(["Y"]), phrase) is False
    assert confirm_typed_phrase(_Recorder([""]), phrase) is False
    assert confirm_typed_phrase(_Recorder([None]), phrase) is False
    assert confirm_typed_phrase(_Recorder(["extra words"]), phrase) is False


@pytest.fixture
def vault(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    import xlii.vault as vault_mod

    monkeypatch.setattr(vault_mod, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(vault_mod, "KEY_FILE", tmp_path / ".vault-key")
    monkeypatch.setenv(vault_mod.ENV_VAR, Fernet.generate_key().decode())
    return vault_mod


def test_verify_fresh_admin(vault, monkeypatch):
    assert verify_fresh_admin(_Recorder(["secret"])) is False

    vault.set_admin_secret("opensesame")
    assert verify_fresh_admin(capturing_console()) is False
    assert verify_fresh_admin(_Recorder([None])) is False
    assert verify_fresh_admin(_Recorder(["wrong"])) is False
    assert verify_fresh_admin(_Recorder(["opensesame"])) is True

    elevated_ctx = {"console": _Recorder(["opensesame"]), "elevated": True}
    assert verify_fresh_admin(elevated_ctx["console"]) is True


@pytest.fixture
def human_only_command():
    calls: list[int] = []
    cmd = REPLCommand(
        name="__etest_human_only",
        handler=lambda line, ctx: (calls.append(1), True)[1],
        human_only=True,
        repls=["code"],
    )
    register_repl_command(cmd)
    try:
        yield calls
    finally:
        unregister_repl_command("__etest_human_only")


def test_human_only_refused_on_capturing_console(human_only_command):
    console = capturing_console()
    ctx = {"command_scope": "code", "console": console, "elevated": True}
    handled = dispatch_repl_command("/__etest_human_only", ctx)
    assert handled is True
    assert human_only_command == []
    assert "foreground human" in console.export_text().lower()


def test_human_only_runs_on_foreground_console(human_only_command):
    console = FakeConsole()
    ctx = {"command_scope": "code", "console": console}
    handled = dispatch_repl_command("/__etest_human_only", ctx)
    assert handled is True
    assert human_only_command == [1]


def test_capturing_tty_cannot_prompt(monkeypatch):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        "builtins.input",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("stdin used")),
    )
    cap = capturing_console()
    assert can_prompt(cap) is False
    assert request_line(cap, "phrase") is None
