"""Inline-REPL shell ghost text (the-fold Vector F follow-up: inline parity).

The inline twin of the Textual --tui's render_line ghost — both read the same
xlii.shell_suggest table. Implemented as a prompt_toolkit AutoSuggest; here we
drive its get_suggestion directly (no live terminal needed), covering the
shell-context gate, the command/ask prefix skips, the graceful empty-table path,
and the no-I/O-in-the-loop guarantee.
"""
from __future__ import annotations

from prompt_toolkit.auto_suggest import AutoSuggest
from prompt_toolkit.document import Document

from xlii.tui import input_chrome

_TABLE = ["git status", "git commit -m", "npm run build"]


def test_ghost_suggests_in_shell_context(monkeypatch):
    monkeypatch.setattr("xlii.repl._is_shell_primary", lambda st: True)
    sug = input_chrome._ShellGhostSuggest(object(), _TABLE)
    out = sug.get_suggestion(None, Document("git st"))
    assert out is not None and out.text == "atus"   # fish-style suffix


def test_ghost_silent_outside_shell_context(monkeypatch):
    # bare input that routes to the agent (not shell) → no ghost
    monkeypatch.setattr("xlii.repl._is_shell_primary", lambda st: False)
    sug = input_chrome._ShellGhostSuggest(object(), _TABLE)
    assert sug.get_suggestion(None, Document("git st")) is None


def test_ghost_skips_command_ask_and_bang_prefixes(monkeypatch):
    monkeypatch.setattr("xlii.repl._is_shell_primary", lambda st: True)
    # even if the table somehow held these, a leading /?:! is never bare-shell
    sug = input_chrome._ShellGhostSuggest(object(), ["/deploy prod", "?why", "!ls", ":tada:"])
    for probe in ("/de", "?wh", "!l", ":ta"):
        assert sug.get_suggestion(None, Document(probe)) is None


def test_ghost_empty_table_is_graceful(monkeypatch):
    monkeypatch.setattr("xlii.repl._is_shell_primary", lambda st: True)
    sug = input_chrome._ShellGhostSuggest(object(), [])
    assert sug.get_suggestion(None, Document("git")) is None   # never-compiled → nothing


def test_ghost_no_match_returns_none(monkeypatch):
    monkeypatch.setattr("xlii.repl._is_shell_primary", lambda st: True)
    sug = input_chrome._ShellGhostSuggest(object(), _TABLE)
    assert sug.get_suggestion(None, Document("zzz")) is None


def test_ghost_serve_path_does_no_io(monkeypatch):
    # the input-loop law: serving a suggestion touches neither disk nor network
    monkeypatch.setattr("xlii.repl._is_shell_primary", lambda st: True)
    import builtins
    import socket
    monkeypatch.setattr(builtins, "open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened a file")))
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened a socket")))
    sug = input_chrome._ShellGhostSuggest(object(), _TABLE)
    out = sug.get_suggestion(None, Document("npm r"))
    assert out is not None and out.text == "un build"


def test_make_shell_autosuggest_loads_table_once(monkeypatch):
    calls = []
    monkeypatch.setattr("xlii.shell_suggest.load_table",
                        lambda *a, **k: calls.append(1) or ["ls -la"])
    sug = input_chrome.make_shell_autosuggest(object())
    assert isinstance(sug, AutoSuggest)
    assert calls == [1]   # loaded exactly once at construction, not per keystroke
    monkeypatch.setattr("xlii.repl._is_shell_primary", lambda st: True)
    assert sug.get_suggestion(None, Document("ls")).text == " -la"
    assert calls == [1]   # serving did NOT reload the table
