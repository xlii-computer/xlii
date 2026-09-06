"""RP6 — marks as a cross-persona idea library.

Drives the real /mark, /marks, /recall handlers against a real REPLState with a
live code Profile (so marks route through the active profile's turn store, not a
chat persona) plus on-disk personas for cross-identity addressing. No network.

Run with `python -m pytest` (imports tests.helpers).
"""

import xlii.persona
from xlii.persona import Persona
from xlii.profile import code_profile
from xlii.repl import REPLState
from xlii.repl_cmds.chat import _mark_handler, _marks_handler, _recall_handler
from xlii.transcript import get_marked_span, list_marks, write_turn
from tests.helpers import FakeConsole, make_agent


def _code_state(tmp_path):
    """A launched-in-code REPLState whose active store is the project's turns."""
    root = tmp_path / "repo"
    (root / ".xlii").mkdir(parents=True, exist_ok=True)
    agent = make_agent(root)
    state = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                      cfg=agent.cfg, pool=agent.pool)
    state.profile = code_profile(agent.project, seed_limit=20)
    state.command_scope = "code"
    return state


def _store(state):
    return state.profile.memory.turns_dir


def _persona_on_disk(monkeypatch, tmp_path, name, *, turns=()):
    """Create a persona prompt file + (optionally) marked turns under a tmp HOME."""
    pdir = tmp_path / "personas"
    cdir = tmp_path / "chat"
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", pdir)
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", cdir)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{name}.md").write_text(f"You are {name}.")
    p = Persona(name)
    for user, assistant, marks in turns:
        path = write_turn(p.turns_dir, user, assistant)
        for mk in marks:
            from xlii.transcript import mark_last_turn
            mark_last_turn(p.turns_dir, mk)
        del path
    return p


# --------------------------------------------------------------------------- #
#  un-gated: marks work in code (the active profile's turn store)
# --------------------------------------------------------------------------- #

def test_mark_works_in_code_mode(tmp_path):
    state = _code_state(tmp_path)
    write_turn(_store(state), "how does auth work?", "it uses device trust")
    assert _mark_handler("/mark auth", state.as_context_dict()) is True
    assert [n for n, _ in list_marks(_store(state))] == ["auth"]
    assert "marked last turn" in state.console.text


def test_mark_with_no_turns(tmp_path):
    state = _code_state(tmp_path)
    _mark_handler("/mark x", state.as_context_dict())
    assert "no turns to mark yet" in state.console.text


def test_mark_window_stored(tmp_path):
    state = _code_state(tmp_path)
    for i in range(4):
        write_turn(_store(state), f"u{i}", f"a{i}")
    _mark_handler("/mark idea --window 2", state.as_context_dict())
    span = get_marked_span(_store(state), "idea")
    assert [t.user for t in span] == ["u1", "u2", "u3"]   # 2 preceding + marked


# --------------------------------------------------------------------------- #
#  /recall — active store, window override, cross-persona
# --------------------------------------------------------------------------- #

def test_recall_attaches_marked_turn(tmp_path):
    state = _code_state(tmp_path)
    write_turn(_store(state), "the idea", "the answer")
    from xlii.transcript import mark_last_turn
    mark_last_turn(_store(state), "spark")
    _recall_handler("/recall spark", state.as_context_dict())
    docs = dict(state.attached_docs)
    assert "point:spark" in docs
    assert "the idea" in docs["point:spark"]
    assert "the answer" in docs["point:spark"]


def test_recall_unknown_mark_is_graceful(tmp_path):
    state = _code_state(tmp_path)
    write_turn(_store(state), "u", "a")
    _recall_handler("/recall nope", state.as_context_dict())
    assert "no mark named" in state.console.text
    assert state.attached_docs == []


def test_recall_window_override_pulls_span(tmp_path):
    state = _code_state(tmp_path)
    for i in range(4):
        write_turn(_store(state), f"user-{i}", f"assistant-{i}")
    from xlii.transcript import mark_last_turn
    mark_last_turn(_store(state), "synthesis")            # stored window 0
    _recall_handler("/recall synthesis --window 2", state.as_context_dict())
    body = dict(state.attached_docs)["point:synthesis"]
    assert "window of 3 turns" in body
    for i in (1, 2, 3):
        assert f"user-{i}" in body
    assert "user-0" not in body                           # outside the window


def test_recall_cross_persona(tmp_path, monkeypatch):
    state = _code_state(tmp_path)
    _persona_on_disk(monkeypatch, tmp_path, "bob",
                     turns=[("auth question", "auth answer", ["auth-insight"])])
    _recall_handler("/recall bob:auth-insight", state.as_context_dict())
    docs = dict(state.attached_docs)
    # no persona baggage rides into the paste — the recalled point is the bare mark
    assert "point:auth-insight" in docs
    assert "auth answer" in docs["point:auth-insight"]


def test_recall_unknown_prefix_is_treated_as_local_mark(tmp_path, monkeypatch):
    # `<x>:<y>` only addresses a persona when <x> is a REAL persona. An unknown
    # prefix is a local mark name (colon and all) — so colon-marks stay reachable.
    state = _code_state(tmp_path)
    _persona_on_disk(monkeypatch, tmp_path, "bob")
    _recall_handler("/recall ghost:idea", state.as_context_dict())
    assert "no mark named" in state.console.text and "ghost:idea" in state.console.text
    assert state.attached_docs == []


def test_recall_local_mark_with_colon(tmp_path):
    # A mark whose name contains a colon (not a persona address) is recallable.
    state = _code_state(tmp_path)
    write_turn(_store(state), "what ratio?", "three to one")
    from xlii.transcript import mark_last_turn
    mark_last_turn(_store(state), "ratio 3:1")
    _recall_handler("/recall ratio 3:1", state.as_context_dict())
    assert "point:ratio 3:1" in dict(state.attached_docs)


def test_recall_real_persona_missing_mark(tmp_path, monkeypatch):
    # A real persona with no such mark gets the "no mark ... for <persona>" hint.
    state = _code_state(tmp_path)
    _persona_on_disk(monkeypatch, tmp_path, "bob",
                     turns=[("q", "a", ["other"])])
    _recall_handler("/recall bob:missing", state.as_context_dict())
    assert "no mark named" in state.console.text and "bob" in state.console.text
    assert state.attached_docs == []


def test_mark_rejects_window_suffix_name(tmp_path):
    # A name ending in "(window: N)" would corrupt on reload — refuse it.
    state = _code_state(tmp_path)
    write_turn(_store(state), "u", "a")
    _mark_handler("/mark sneaky (window: 3)", state.as_context_dict())
    assert "can't end with" in state.console.text
    assert list_marks(_store(state)) == []


def test_recall_window_trims_to_inline_cap(tmp_path):
    from xlii.doc import INLINE_SOFT_CAP_BYTES
    state = _code_state(tmp_path)
    big = "X" * (INLINE_SOFT_CAP_BYTES // 2)
    write_turn(_store(state), "oldest", big)
    write_turn(_store(state), "middle", big)
    write_turn(_store(state), "marked", big)
    from xlii.transcript import mark_last_turn
    mark_last_turn(_store(state), "fat", window=2)
    _recall_handler("/recall fat", state.as_context_dict())
    body = dict(state.attached_docs)["point:fat"]
    assert "trimmed to fit" in state.console.text
    assert "marked" in body                               # the marked turn is kept
    assert "oldest" not in body                           # the oldest is trimmed first


def test_recall_single_oversized_turn_warns_but_attaches(tmp_path):
    from xlii.doc import INLINE_SOFT_CAP_BYTES
    state = _code_state(tmp_path)
    write_turn(_store(state), "u", "Y" * (INLINE_SOFT_CAP_BYTES + 100))
    from xlii.transcript import mark_last_turn
    mark_last_turn(_store(state), "huge")
    _recall_handler("/recall huge", state.as_context_dict())
    assert "exceeds the inline cap" in state.console.text  # warned
    assert "point:huge" in dict(state.attached_docs)       # but still attached in full


# --------------------------------------------------------------------------- #
#  /marks --all — the cross-persona library view
# --------------------------------------------------------------------------- #

def test_marks_all_aggregates_personas(tmp_path, monkeypatch):
    state = _code_state(tmp_path)
    # Two personas, each with a mark, written under a shared tmp HOME.
    pdir = tmp_path / "personas"
    cdir = tmp_path / "chat"
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", pdir)
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", cdir)
    pdir.mkdir(parents=True, exist_ok=True)
    from xlii.transcript import mark_last_turn
    for name, mk in (("bob", "auth-insight"), ("susan", "pasta-trick")):
        (pdir / f"{name}.md").write_text(f"You are {name}.")
        p = Persona(name)
        write_turn(p.turns_dir, "q", "a")
        mark_last_turn(p.turns_dir, mk)

    _marks_handler("/marks --all", state.as_context_dict())
    text = state.console.text
    assert "bob:auth-insight" in text                     # addressable form
    assert "susan:pasta-trick" in text


def test_marks_all_empty(tmp_path, monkeypatch):
    state = _code_state(tmp_path)
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", tmp_path / "personas")
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", tmp_path / "chat")
    _marks_handler("/marks --all", state.as_context_dict())
    assert "no marks anywhere yet" in state.console.text


def test_marks_local_list(tmp_path):
    state = _code_state(tmp_path)
    write_turn(_store(state), "u", "a")
    from xlii.transcript import mark_last_turn
    mark_last_turn(_store(state), "local-mark")
    _marks_handler("/marks", state.as_context_dict())
    assert "local-mark" in state.console.text


# --------------------------------------------------------------------------- #
#  TUI /recall picker — the suggestion builder (pure logic, no Textual app)
# --------------------------------------------------------------------------- #

def test_recall_suggestions_addressable_and_filtered(tmp_path, monkeypatch):
    from xlii.tui_textual import _recall_suggestions
    state = _code_state(tmp_path)
    pdir = tmp_path / "personas"
    cdir = tmp_path / "chat"
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", pdir)
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", cdir)
    pdir.mkdir(parents=True, exist_ok=True)
    from xlii.transcript import mark_last_turn
    for name, mk in (("bob", "auth-insight"), ("susan", "pasta-trick")):
        (pdir / f"{name}.md").write_text(f"You are {name}.")
        p = Persona(name)
        write_turn(p.turns_dir, "q", "a")
        mark_last_turn(p.turns_dir, mk)

    cands = [c for c, _ in _recall_suggestions(state, "")]
    assert "bob:auth-insight" in cands and "susan:pasta-trick" in cands

    # partial filters case-insensitively across the whole address
    only_bob = [c for c, _ in _recall_suggestions(state, "BOB")]
    assert only_bob == ["bob:auth-insight"]
