"""The Fold — Vector A: `/personas` folds into `/persona`.

Bare `/persona` lists every persona (house convention: bare-lists, argument-acts),
current marked; `/personas` is a hidden alias that lands on the same handler. The
list prints through the session console (the `out` seam), not the buried global.
"""

from __future__ import annotations

import xlii.persona
from xlii.persona import Persona
from xlii.repl_cmds.chat import _persona_handler
from tests.helpers import FakeConsole


def _isolate(monkeypatch, tmp_path, *names):
    pdir = tmp_path / "personas"
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", pdir)
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", tmp_path / "chat")
    pdir.mkdir(parents=True, exist_ok=True)
    for n in names:
        (pdir / f"{n}.md").write_text(f"You are {n}.")


def _ctx(current):
    con = FakeConsole()
    return {"persona": Persona(current), "console": con}, con


def test_bare_persona_lists_with_current_marked(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path, "alice", "bob")
    ctx, con = _ctx("alice")
    assert _persona_handler("/persona", ctx) is True
    text = con.text
    assert "alice" in text and "bob" in text        # every persona listed
    assert "●" in text                              # current is marked
    assert "to switch" in text                      # bare-lists + usage hint


def test_personas_alias_lists_the_same(tmp_path, monkeypatch):
    """`/personas` routes to `_persona_handler` (a hidden alias) → bare listing."""
    _isolate(monkeypatch, tmp_path, "alice", "bob")
    ctx, con = _ctx("bob")
    assert _persona_handler("/personas", ctx) is True
    text = con.text
    assert "alice" in text and "bob" in text
    assert "to switch" in text


def test_bare_persona_prints_to_session_console_not_global(tmp_path, monkeypatch):
    """The `out` seam: the listing must land on ctx['console'] (the buried global
    would be invisible in the TUI)."""
    _isolate(monkeypatch, tmp_path, "alice")
    ctx, con = _ctx("alice")
    _persona_handler("/persona", ctx)
    assert "alice" in con.text                       # went to the session console
