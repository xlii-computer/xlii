"""History panel — browse ``repl_history`` newest-first and prefill input."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.tui import panels


def _panel_for(tmp_path, lines=None):
    pytest.importorskip("textual")
    from prompt_toolkit.history import FileHistory

    xli = tmp_path / ".xlii"
    xli.mkdir()
    if lines is not None:
        hist = FileHistory(str(xli / "repl_history"))
        for line in lines:
            hist.append_string(line)
    state = SimpleNamespace(
        project=SimpleNamespace(xli_dir=xli),
        cfg=SimpleNamespace(),
    )
    prefilled = []
    actions = panels.PanelActions(
        state,
        app=SimpleNamespace(_prefill_input=lambda t: prefilled.append(t)),
    )
    view = panels.get_panel_view("history")
    panel = view(state, actions=actions)
    return panel, prefilled


def test_history_panel_lists_newest_first(tmp_path):
    panel, _ = _panel_for(tmp_path, lines=["/older", "/newer"])
    assert panel._lines[0] == "/newer"
    assert panel._lines[1] == "/older"


def test_history_panel_empty_state(tmp_path):
    panel, _ = _panel_for(tmp_path, lines=None)
    opts = panel._options()
    assert len(opts) == 1
    assert opts[0].disabled


def test_history_panel_prefills_on_select(tmp_path):
    panel, prefilled = _panel_for(tmp_path, lines=["!git status"])
    panel._actions.prefill_input(panel._lines[0])
    assert prefilled == ["!git status"]


class _Opt:
    def __init__(self, oid):
        self.id = oid


class _Event:
    def __init__(self, opt):
        self.option = opt

    def stop(self):
        pass


# The click handler (on_option_list_option_selected) parses the option id →
# index → bounds-checks → prefills. The sweep flagged this path as untested
# (the test above bypassed it), the same click-to-index class round-1 got wrong.


def test_history_select_handler_maps_option_id_to_correct_line(tmp_path):
    panel, prefilled = _panel_for(tmp_path, lines=["/older", "/newer"])
    # newest-first: _lines == ["/newer", "/older"]; id "line:1" → "/older"
    panel.on_option_list_option_selected(_Event(_Opt("line:1")))
    assert prefilled == ["/older"]


def test_history_select_handler_ignores_out_of_range_index(tmp_path):
    panel, prefilled = _panel_for(tmp_path, lines=["/only"])
    panel.on_option_list_option_selected(_Event(_Opt("line:99")))
    assert prefilled == []


def test_history_select_handler_ignores_non_line_id(tmp_path):
    panel, prefilled = _panel_for(tmp_path, lines=["/only"])
    panel.on_option_list_option_selected(_Event(_Opt("empty")))
    assert prefilled == []


def test_history_panel_clear_typed_wipes_file(tmp_path):
    panel, _ = _panel_for(tmp_path, lines=["/older", "/newer"])
    xli = tmp_path / ".xlii"
    assert (xli / "repl_history").exists()
    notes = []
    panel._actions.notify = lambda msg, **kw: notes.append(msg)
    panel._clear_typed()
    assert panel._lines == []
    assert not (xli / "repl_history").exists()
    assert notes and "typed lines" in notes[0]
