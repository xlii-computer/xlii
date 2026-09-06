"""Tests for the REPL input chrome (tui-layer T3).

The toolbar + mode-colored prompt are gated by styled_enabled() AND a TTY, with
XLII_NO_TOOLBAR as a sub-opt-out. Since CI has no TTY, these monkeypatch the
_isatty seam and the env explicitly. The render functions read REPLState
defensively, so fake SimpleNamespace states are enough.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from prompt_toolkit.formatted_text import FormattedText

from xlii.debug_mode import DebugController
from xlii.mode_controller import PlanController
from xlii.rail import RailController, RailStage
from xlii.tui import input_chrome


def _agent_with_mode(mode):
    if isinstance(mode, RailController):
        return SimpleNamespace(active_mode=mode, rail=mode)
    if isinstance(mode, DebugController):
        return SimpleNamespace(active_mode=mode, debug=mode)
    if isinstance(mode, PlanController):
        return SimpleNamespace(active_mode=mode)
    return SimpleNamespace(active_mode=mode)


def _state(**kw):
    base = dict(
        agent=SimpleNamespace(active_mode=None, rail=None),
        plan_mode=False,
        persona=None,
        yolo=False,
        attached_refs=[],
        attached_docs=[],
        shell_cwd=None,
        project=SimpleNamespace(name="myapp", project_root=Path("/tmp/myapp")),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _text(ft: FormattedText) -> str:
    return "".join(t for _, t in ft)


# -- mode resolution ------------------------------------------------------

def test_mode_precedence():
    assert input_chrome._mode(_state())[1] == "SHELL"
    assert input_chrome._mode(_state(yolo=True))[1] == "YOLO"
    assert input_chrome._mode(_state(persona=object()))[1] == "CHAT"
    assert input_chrome._mode(_state(agent=_agent_with_mode(PlanController())))[1] == "PLAN"
    rail = RailController()
    rail.current_stage = RailStage.EDGE_CASES
    label, key = input_chrome._mode(_state(agent=_agent_with_mode(rail)))
    assert key == "RAIL"
    assert label.startswith("RAIL 2/")  # /LAST_STAGE


def test_mode_precedence_rail_active():
    rail = RailController()
    st = _state(agent=_agent_with_mode(rail))
    assert input_chrome._mode(st)[1] == "RAIL"


# -- gating ---------------------------------------------------------------

def test_toolbar_none_when_not_styled(monkeypatch):
    monkeypatch.delenv("XLII_SHELL_STYLE", raising=False)
    monkeypatch.setattr(input_chrome, "_isatty", lambda: True)
    assert input_chrome.toolbar(_state()) is None


def test_toolbar_none_without_tty(monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    monkeypatch.setattr(input_chrome, "_isatty", lambda: False)
    assert input_chrome.toolbar(_state()) is None


def test_toolbar_none_when_no_toolbar_env(monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    monkeypatch.setenv("XLII_NO_TOOLBAR", "1")
    monkeypatch.setattr(input_chrome, "_isatty", lambda: True)
    assert input_chrome.toolbar(_state()) is None


# -- toolbar content ------------------------------------------------------

def test_toolbar_shows_mode_cwd_attachments(monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    monkeypatch.delenv("XLII_NO_TOOLBAR", raising=False)
    monkeypatch.setattr(input_chrome, "_isatty", lambda: True)
    ft = input_chrome.toolbar(_state(attached_refs=[("a", "b")], attached_docs=[("c", "d")]))
    assert ft is not None
    text = _text(ft)
    assert "SHELL" in text
    assert "myapp" in text  # cwd falls back to project name
    assert "refs:1" in text and "docs:1" in text


def test_toolbar_mode_carries_color(monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    monkeypatch.setattr(input_chrome, "_isatty", lambda: True)
    ft = input_chrome.toolbar(_state(agent=_agent_with_mode(PlanController())))
    assert any("ansiyellow" in style for style, _ in ft)  # PLAN = yellow
    assert "PLAN" in _text(ft)


def test_toolbar_shows_the_exit_hint_only_when_there_is_a_way_out(monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    monkeypatch.setattr(input_chrome, "_isatty", lambda: True)
    # Plain code surface: nothing to leave → no hint.
    assert "/off" not in _text(input_chrome.toolbar(_state()))
    assert "/code" not in _text(input_chrome.toolbar(_state()))
    # A mode (PLAN) is active → the way out is /off.
    plan = _text(input_chrome.toolbar(_state(agent=_agent_with_mode(PlanController()))))
    assert "/off to exit" in plan
    # A chat/persona surface → the way back is /code.
    chat = _text(input_chrome.toolbar(_state(persona=SimpleNamespace(name="ixaac"))))
    assert "/code for code" in chat


# -- prompt message -------------------------------------------------------

def test_prompt_message_plain_when_disabled(monkeypatch):
    monkeypatch.delenv("XLII_SHELL_STYLE", raising=False)
    out = input_chrome.prompt_message(_state(), "\n~/x › ")
    assert out == "\n~/x › "


def test_prompt_message_colors_arrow_when_enabled(monkeypatch):
    monkeypatch.setenv("XLII_SHELL_STYLE", "styled")
    monkeypatch.setattr(input_chrome, "_isatty", lambda: True)
    out = input_chrome.prompt_message(_state(), "\n~/x › ")
    assert isinstance(out, FormattedText)
    assert _text(out) == "\n~/x › "  # identical text, only styled
    # the arrow fragment carries the SHELL (green) color; the base does not
    assert any("ansigreen" in style and "›" in txt for style, txt in out)
