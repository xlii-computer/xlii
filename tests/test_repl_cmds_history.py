"""``/history`` — open the input-line history panel (Track F)."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.repl_cmds import history as history_cmd
from xlii.tui import panels


class _Console:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))

    @property
    def text(self):
        return "\n".join(self.lines)


class _FakeHost(panels.PanelHost):
    def __init__(self, *, side="right"):
        self.calls = []
        self._side = side

    def show_panel(self, side, view, *, state):
        self.calls.append(("show", side, view))
        return True

    def hide_panel(self):
        return True

    def is_open(self):
        return False

    def current_side(self):
        return self._side


def _run(line, host=None, state=None):
    console = _Console()
    if state is None:
        state = SimpleNamespace(project=SimpleNamespace(project_root="/proj"), shell_cwd="/proj")
    ctx = {"console": console, "state": state}
    prev = panels.set_panel_host(host)
    try:
        handled = history_cmd._cmd_history(line, ctx)
    finally:
        panels.set_panel_host(prev)
    return handled, console, host


def test_history_registered_and_view_known():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    for repl in ("code", "chat"):
        assert find_repl_command("/history", repl) is not None
    assert "history" in panels.panel_views()


def test_no_state_is_handled_gracefully():
    console = _Console()
    assert history_cmd._cmd_history("/history", {"console": console, "state": None}) is True
    assert "No active REPLState" in console.text


def test_no_host_nudges_to_tui():
    handled, console, _ = _run("/history", host=None)
    assert handled is True
    assert "--tui" in console.text or "/tui" in console.text


def test_opens_history_panel_on_preferred_side():
    handled, console, host = _run("/history", host=_FakeHost(side="left"))
    assert handled is True
    assert ("show", "left", "history") in host.calls
    assert "history panel" in console.text
