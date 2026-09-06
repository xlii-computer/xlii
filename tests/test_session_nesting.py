"""The nested-session guard (sessions._nested_session_guard) + /tui handler.

XLII_SESSION is exported by an interactive session and inherited by anything it
spawns, so a nested `xlii code`/`chat` can detect it BEFORE taking the screen and
refuse same-project re-entry (which would collide on on-disk state / race syncs).
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

from rich.console import Console

from xlii.cmds.sessions import _mark_session_active, _nested_session_guard


def _cap():
    buf = StringIO()
    return Console(file=buf, width=100, no_color=True, highlight=False), buf


def test_guard_passes_when_not_nested(monkeypatch):
    monkeypatch.delenv("XLII_SESSION", raising=False)
    con, buf = _cap()
    assert _nested_session_guard(con, project_root=Path("/proj")) is True
    assert buf.getvalue().strip() == ""  # silent when there's no outer session


def test_guard_blocks_same_project(monkeypatch):
    monkeypatch.setenv("XLII_SESSION", "999")
    monkeypatch.setenv("XLII_SESSION_PROJECT", "/proj")
    con, buf = _cap()
    assert _nested_session_guard(con, project_root=Path("/proj")) is False
    out = buf.getvalue().lower()
    assert "blocked" in out
    assert "/tui" in out and "--force" in out  # both escape routes offered


def test_guard_force_overrides_same_project(monkeypatch):
    monkeypatch.setenv("XLII_SESSION", "999")
    monkeypatch.setenv("XLII_SESSION_PROJECT", "/proj")
    con, buf = _cap()
    assert _nested_session_guard(con, project_root=Path("/proj"), force=True) is True
    assert "force" in buf.getvalue().lower()


def test_guard_warns_cross_project_but_proceeds(monkeypatch):
    monkeypatch.setenv("XLII_SESSION", "999")
    monkeypatch.setenv("XLII_SESSION_PROJECT", "/other")
    con, buf = _cap()
    assert _nested_session_guard(con, project_root=Path("/proj")) is True
    assert "heads-up" in buf.getvalue().lower()


def test_mark_advertises_pid_and_project(monkeypatch):
    monkeypatch.delenv("XLII_SESSION", raising=False)
    monkeypatch.delenv("XLII_SESSION_PROJECT", raising=False)
    _mark_session_active(Path("/proj"))
    assert os.environ["XLII_SESSION"] == str(os.getpid())
    assert os.environ["XLII_SESSION_PROJECT"].endswith("proj")


def test_mark_then_guard_blocks_same_project(monkeypatch):
    # End-to-end: a session marks itself, a same-project child is then refused.
    monkeypatch.delenv("XLII_SESSION", raising=False)
    monkeypatch.delenv("XLII_SESSION_PROJECT", raising=False)
    _mark_session_active(Path("/proj"))
    con, _ = _cap()
    assert _nested_session_guard(con, project_root=Path("/proj")) is False


def test_tui_command_is_registered():
    from xlii.commands import _REPL_COMMANDS
    from xlii.repl_cmds import register_all

    register_all()
    names = {c.name for c in _REPL_COMMANDS}
    assert "tui" in names


def test_tui_handler_requires_state():
    from xlii.repl_cmds.code import _tui_handler

    con, buf = _cap()
    # textual is installed in the test venv, so we pass that gate; with no
    # "state" in ctx the handler should bail cleanly (not crash).
    assert _tui_handler("/tui", {"console": con, "agent": object()}) is True
    assert "code repl" in buf.getvalue().lower()
