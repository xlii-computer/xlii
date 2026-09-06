"""`/theme` — open the Themes picker panel (Options → Theme…).

Pure over the published panel-host seam (a fake host records the routed call) plus the no-host nudge
for the inline REPL, and the `PanelActions.apply_theme` verb → app hook. The ThemesPanel widget's
pure helpers are exercised only when Textual is importable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.repl_cmds import theme
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
        self.calls.append(("hide",))
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
        handled = theme._cmd_theme(line, ctx)
    finally:
        panels.set_panel_host(prev)
    return handled, console, host


# --------------------------------------------------------------------------- #
#  /theme command
# --------------------------------------------------------------------------- #

def test_no_state_is_handled_gracefully():
    console = _Console()
    assert theme._cmd_theme("/theme", {"console": console, "state": None}) is True
    assert "No active REPLState" in console.text


def test_no_host_nudges_to_tui():
    handled, console, _ = _run("/theme", host=None)
    assert handled is True
    assert "--tui" in console.text or "/tui" in console.text


def test_opens_the_themes_panel_on_preferred_side():
    handled, console, host = _run("/theme", host=_FakeHost(side="left"))
    assert handled is True
    assert host.calls == [("show", "left", "themes")]
    assert "theme" in console.text.lower()


def test_register_adds_command():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    assert find_repl_command("/theme", repl="code").name == "theme"


# --------------------------------------------------------------------------- #
#  PanelActions.apply_theme — the Themes view's select verb
# --------------------------------------------------------------------------- #

def test_apply_theme_routes_to_app_hook():
    class FakeApp:
        def __init__(self):
            self.applied = None

        def _apply_theme(self, name):
            self.applied = name

    app = FakeApp()
    panels.PanelActions(state=None, app=app).apply_theme("nord")
    assert app.applied == "nord"


def test_apply_theme_noop_without_app():
    # Headless (inline REPL / tests): app is None, apply_theme must not raise.
    panels.PanelActions(state=None, app=None).apply_theme("nord")


# --------------------------------------------------------------------------- #
#  ThemesPanel — the clickable list (Textual only)
# --------------------------------------------------------------------------- #

def test_themes_panel_lists_and_marks_current():
    pytest.importorskip("textual")
    from types import SimpleNamespace

    app = SimpleNamespace(
        available_themes={"nord": object(), "gruvbox": object(), "textual-dark": object()},
        theme="nord",
    )
    state = SimpleNamespace(cfg=SimpleNamespace(tui_canvas="dark"))
    actions = panels.PanelActions(state=state, app=app)
    panel = panels.themes_view(None, actions=actions)
    assert panel is not None
    assert panel._theme_names() == ["gruvbox", "nord", "textual-dark"]  # sorted
    assert panel._current() == "nord"
    assert panel._label("nord", "nord").startswith("●")      # active theme marked
    assert not panel._label("gruvbox", "nord").startswith("●")


def test_themes_panel_filters_to_light_canvas():
    pytest.importorskip("textual")
    from types import SimpleNamespace

    app = SimpleNamespace(
        available_themes={
            "nord": object(),
            "textual-dark": object(),
            "textual-light": object(),
            "solarized-light": object(),
        },
        theme="textual-light",
    )
    state = SimpleNamespace(cfg=SimpleNamespace(tui_canvas="light"))
    panel = panels.themes_view(None, actions=panels.PanelActions(state=state, app=app))
    assert panel._theme_names() == ["solarized-light", "textual-light"]
