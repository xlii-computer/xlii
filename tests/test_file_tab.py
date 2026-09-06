"""Vector P/A — the /file-tab and /file-view slash commands.

Pure over the published panel host seam (a fake host records the routed calls),
plus the no-host nudge for the inline REPL. No Textual needed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.repl_cmds import file_tab
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
    def __init__(self, *, open=False, side="right", view=None):
        self.calls = []
        self._open = open
        self._side = side
        self._view = view
        self._target = None

    def show_panel(self, side, view, *, state):
        self.calls.append(("show", side, view))
        self._open = True
        self._side = side
        self._view = view
        return True

    def hide_panel(self):
        self.calls.append(("hide",))
        self._open = False
        self._view = None
        self._target = None
        return True

    def is_open(self):
        return self._open

    def current_side(self):
        return self._side

    def set_panel_side(self, side: str, *, persist: bool = True) -> str:
        applied = "left" if str(side).lower() == "left" else "right"
        self.calls.append(("set_side", applied, persist))
        self._side = applied
        return applied

    def current_view(self):
        return self._view

    def current_target(self):
        return self._target

    def show_file(self, path):
        self.calls.append(("file", path))
        self._open = True
        self._target = path
        return True

    def show_tree(self, *, side=None):
        self.calls.append(("tree", side))
        self._open = True
        self._view = "explorer"
        self._target = None
        return True

    def show_gallery(self, *, side=None):
        self.calls.append(("gallery", side))
        self._open = True
        self._view = "locker"
        self._target = None
        return True

    def open_doorway(self, scheme):
        self.calls.append(("door", scheme))
        self._open = True
        self._view = scheme
        return True


def _run(line, host=None, state=None):
    console = _Console()
    if state is None:
        state = SimpleNamespace(
            attach_file=lambda p: None,
            attached_files=[],
            project=SimpleNamespace(project_root="/proj"),
            shell_cwd="/proj",
        )
    ctx = {"console": console, "state": state}
    prev = panels.set_panel_host(host)
    try:
        handled = file_tab._cmd_file_tab(line, ctx)
    finally:
        panels.set_panel_host(prev)
    return handled, console, host


def _run_panel(line, host=None, state=None):
    console = _Console()
    if state is None:
        state = SimpleNamespace(
            attach_file=lambda p: None,
            attached_files=[],
            project=SimpleNamespace(project_root="/proj"),
            shell_cwd="/proj",
        )
    ctx = {"console": console, "state": state}
    prev = panels.set_panel_host(host)
    try:
        handled = file_tab._cmd_panel(line, ctx)
    finally:
        panels.set_panel_host(prev)
    return handled, console, host


def _run_view(line, host=None, state=None):
    console = _Console()
    if state is None:
        state = SimpleNamespace(
            project=SimpleNamespace(project_root="/proj"),
            shell_cwd="/proj",
        )
    ctx = {"console": console, "state": state}
    prev = panels.set_panel_host(host)
    try:
        handled = file_tab._cmd_file_view(line, ctx)
    finally:
        panels.set_panel_host(prev)
    return handled, console, host


def test_no_state_is_handled_gracefully():
    console = _Console()
    assert file_tab._cmd_file_tab("/file-tab", {"console": console, "state": None}) is True
    assert "No active REPLState" in console.text


def test_no_host_nudges_to_tui():
    handled, console, _ = _run("/file-tab on", host=None)
    assert handled is True
    assert "--tui" in console.text or "/tui" in console.text


def test_on_opens_files_dock():
    # `/file-tab` opens the one kernel Dock (vfs), not the legacy tree.
    handled, console, host = _run("/file-tab on", host=_FakeHost())
    assert host.calls == [("show", "right", "vfs")]
    assert "files" in console.text


def test_image_flag_opens_gallery():
    _, console, host = _run("/file-tab --image", host=_FakeHost())
    assert host.calls == [("gallery", "right")]
    assert "gallery" in console.text


def test_named_locker_view_opens_gallery():
    _, _console, host = _run("/file-tab locker", host=_FakeHost())
    assert host.calls == [("gallery", "right")]


def test_explorer_alias_opens_dock():
    # `/file-tab explorer` is an alias for the one Dock now (no separate legacy tree).
    _, _console, host = _run("/file-tab explorer", host=_FakeHost())
    assert host.calls == [("show", "right", "vfs")]


def test_set_left_docks_the_dock_left():
    host = _FakeHost(open=True, side="right", view="locker")
    _, _console, host = _run("/file-tab --set left", host=host)
    assert host.calls == [("set_side", "left", True), ("show", "left", "vfs")]


def test_set_needs_a_side():
    _, console, host = _run("/file-tab --set", host=_FakeHost())
    assert "left" in console.text and "right" in console.text
    assert host.calls == []


def test_off_closes_when_open():
    host = _FakeHost(open=True, view="explorer")
    _, console, host = _run("/file-tab off", host=host)
    assert host.calls == [("hide",)]
    assert "closed" in console.text


def test_off_when_already_closed_is_noop():
    host = _FakeHost(open=False)
    _, console, host = _run("/file-tab off", host=host)
    assert host.calls == []
    assert "already closed" in console.text


def test_bare_file_tab_opens_the_dock():
    # bare /file-tab opens the one kernel Dock (vfs), same as `/file-tab vfs`.
    host = _FakeHost(open=False)
    _run("/file-tab", host=host)
    assert host.calls == [("show", "right", "vfs")]
    host.calls.clear()
    host._open = True
    _run("/file-tab", host=host)
    assert host.calls == [("show", "right", "vfs")]


def test_unknown_view_warns_and_does_not_dock():
    _, console, host = _run("/file-tab bogusview", host=_FakeHost())
    assert "unknown view" in console.text
    assert host.calls == []


def test_file_view_opens_path(tmp_path):
    f = tmp_path / "api.md"
    f.write_text("# API\n")
    state = SimpleNamespace(
        project=SimpleNamespace(project_root=tmp_path),
        shell_cwd=tmp_path,
    )
    host = _FakeHost()
    _, console, host = _run_view(f"/file-view {f.name}", host=host, state=state)
    assert host.calls[0][0] == "file"
    assert "viewing" in console.text


def test_file_view_no_host_nudges():
    _, console, _ = _run_view("/file-view x.md", host=None)
    assert "--tui" in console.text or "/tui" in console.text


def test_register_adds_command_with_aliases():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    assert find_repl_command("/file-tab", repl="code").name == "file-tab"
    assert find_repl_command("/filetab", repl="code").name == "file-tab"
    assert find_repl_command("/panel", repl="code").name == "panel"   # /panel is its own command now
    assert find_repl_command("/panel", repl="chat").name == "panel"
    assert find_repl_command("/home", repl="code").name == "home"
    assert find_repl_command("/home", repl="chat").name == "home"
    assert find_repl_command("/home/user/notes.md", repl="code") is None
    assert find_repl_command("/file-view", repl="code").name == "file-view"
    assert find_repl_command("/fileview", repl="code").name == "file-view"


# --------------------------------------------------------------------------- #
#  /panel — the content-panel opener (doorways + the file explorer)
# --------------------------------------------------------------------------- #

def test_panel_no_state_is_handled_gracefully():
    console = _Console()
    assert file_tab._cmd_panel("/panel", {"console": console, "state": None}) is True
    assert "No active REPLState" in console.text


def test_panel_no_host_nudges_to_tui():
    handled, console, _ = _run_panel("/panel skills", host=None)
    assert handled is True
    assert "--tui" in console.text or "/tui" in console.text


@pytest.mark.parametrize("target,scheme", [
    ("skills", "skills"), ("docs", "docs"),
    ("bookmarks", "mark"), ("marks", "mark"), ("mark", "mark"),
    ("images", "locker"), ("image", "locker"), ("locker", "locker"),
    ("wiki", "wiki"), ("tasks", "tasks"), ("jobs", "jobs"),
])
def test_panel_target_opens_its_doorway(target, scheme):
    _, console, host = _run_panel(f"/panel {target}", host=_FakeHost())
    assert host.calls == [("door", scheme)]           # scheme:// in Pane 2, not the vfs explorer
    assert target in console.text


def test_panel_files_opens_the_explorer_dock():
    _, _console, host = _run_panel("/panel files", host=_FakeHost())
    assert host.calls == [("show", "right", "vfs")]


def test_panel_bare_opens_the_explorer():
    # bare /panel keeps the old /panel-alias / bare /file-tab behaviour: the file explorer.
    _, _console, host = _run_panel("/panel", host=_FakeHost())
    assert host.calls == [("show", "right", "vfs")]


def test_panel_set_left_docks_files_left():
    _, _console, host = _run_panel("/panel files --set left", host=_FakeHost())
    assert host.calls == [("set_side", "left", True), ("show", "left", "vfs")]


def test_panel_off_closes():
    host = _FakeHost(open=True, view="skills")
    _, console, host = _run_panel("/panel off", host=host)
    assert host.calls == [("hide",)]
    assert "closed" in console.text


def test_panel_help_lists_targets_without_docking():
    _, console, host = _run_panel("/panel ?", host=_FakeHost())
    assert host.calls == []
    assert "skills" in console.text and "docs" in console.text and "files" in console.text


def test_panel_unknown_target_warns_and_does_not_dock():
    _, console, host = _run_panel("/panel bogus", host=_FakeHost())
    assert "unknown panel" in console.text
    assert host.calls == []
