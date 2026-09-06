"""GVM B2 — the XLII_SESSION nested-session protocol (xlii/session_state.py)
and the end-of-turn sync gate (xlii/sync.py) in their kernel homes.

The rich rendering half stays in cmds/sessions/nesting.py and is covered by
the pre-existing tests/test_session_nesting.py, which passes unmodified
against the façade.
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

from rich.console import Console

from xlii.session_state import (
    XLII_SESSION_ENV,
    XLII_SESSION_PROJECT_ENV,
    detect_nested_session,
    mark_session_active,
)
from xlii.sync import end_of_turn_sync


def _cap():
    buf = StringIO()
    return Console(file=buf, width=100, no_color=True, highlight=False), buf


# --------------------------------------------------------------------------- #
#  detect_nested_session — pure verdict, no rendering
# --------------------------------------------------------------------------- #

def test_detect_proceeds_when_not_nested(monkeypatch):
    monkeypatch.delenv(XLII_SESSION_ENV, raising=False)
    v = detect_nested_session(Path("/proj"))
    assert v.outcome == "proceed" and v.proceed is True
    assert v.outer_pid == "" and v.same_project is False


def test_detect_blocks_same_project(monkeypatch):
    monkeypatch.setenv(XLII_SESSION_ENV, "999")
    monkeypatch.setenv(XLII_SESSION_PROJECT_ENV, "/proj")
    v = detect_nested_session(Path("/proj"))
    assert v.outcome == "blocked" and v.proceed is False
    assert v.outer_pid == "999" and v.same_project is True


def test_detect_force_overrides_same_project(monkeypatch):
    monkeypatch.setenv(XLII_SESSION_ENV, "999")
    monkeypatch.setenv(XLII_SESSION_PROJECT_ENV, "/proj")
    v = detect_nested_session(Path("/proj"), force=True)
    assert v.outcome == "forced" and v.proceed is True
    assert v.same_project is True


def test_detect_warns_cross_project(monkeypatch):
    monkeypatch.setenv(XLII_SESSION_ENV, "999")
    monkeypatch.setenv(XLII_SESSION_PROJECT_ENV, "/other")
    v = detect_nested_session(Path("/proj"))
    assert v.outcome == "warn" and v.proceed is True
    assert v.same_project is False


def test_mark_session_active_advertises_pid_and_project(monkeypatch):
    monkeypatch.delenv(XLII_SESSION_ENV, raising=False)
    monkeypatch.delenv(XLII_SESSION_PROJECT_ENV, raising=False)
    mark_session_active(Path("/proj"))
    assert os.environ[XLII_SESSION_ENV] == str(os.getpid())
    assert os.environ[XLII_SESSION_PROJECT_ENV].endswith("proj")


def test_mark_then_detect_blocks_same_project(monkeypatch):
    monkeypatch.delenv(XLII_SESSION_ENV, raising=False)
    monkeypatch.delenv(XLII_SESSION_PROJECT_ENV, raising=False)
    mark_session_active(Path("/proj"))
    assert detect_nested_session(Path("/proj")).outcome == "blocked"


# --------------------------------------------------------------------------- #
#  end_of_turn_sync — the gate around sync_project
# --------------------------------------------------------------------------- #

def _state(*, local_only=False, no_sync=False):
    con, buf = _cap()
    return SimpleNamespace(
        project=SimpleNamespace(local_only=local_only),
        cfg=SimpleNamespace(),
        pool=SimpleNamespace(primary=lambda: object()),
        console=con,
        no_sync=no_sync,
    ), buf


def test_end_of_turn_sync_noop_when_nothing_dirty(monkeypatch):
    called = []
    monkeypatch.setattr("xlii.sync.sync_project", lambda *a, **k: called.append(a))
    state, buf = _state()
    end_of_turn_sync(state, set())
    assert called == [] and buf.getvalue() == ""


def test_end_of_turn_sync_noop_for_local_only(monkeypatch):
    called = []
    monkeypatch.setattr("xlii.sync.sync_project", lambda *a, **k: called.append(a))
    state, _ = _state(local_only=True)
    end_of_turn_sync(state, {"f.py"})
    assert called == []


def test_end_of_turn_sync_noop_for_no_sync_session(monkeypatch):
    called = []
    monkeypatch.setattr("xlii.sync.sync_project", lambda *a, **k: called.append(a))
    state, _ = _state(no_sync=True)
    end_of_turn_sync(state, {"f.py"})
    assert called == []


def test_end_of_turn_sync_runs_and_reports(monkeypatch):
    stats = SimpleNamespace(uploaded=1, updated=0, deleted=0,
                            summary=lambda: "1 uploaded")
    monkeypatch.setattr("xlii.sync.sync_project", lambda *a, **k: stats)
    state, buf = _state()
    end_of_turn_sync(state, {"f.py"})
    assert "1 uploaded" in buf.getvalue()


def test_end_of_turn_sync_failure_degrades_to_warning(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("network down")
    monkeypatch.setattr("xlii.sync.sync_project", boom)
    state, buf = _state()
    end_of_turn_sync(state, {"f.py"})  # never raises
    assert "end-of-turn sync failed" in buf.getvalue()
