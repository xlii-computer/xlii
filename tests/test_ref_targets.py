"""Vector D — reference target type (seam #6) + the §5 sanitizer.

A ref is a typed (name, target_type, target) view over 2-tuple storage. The one
live kind is BOOKMARK (empty collection-id); COLLECTION survives only as the
legacy-detection vocabulary so sanitize_refs can recognize persisted
persona-Collection entries (the banned kind, d96c9d35 / menu-families §5) and
drop them at every load boundary. A2's preview provider dispatches on this view.
"""

from xlii.profile import code_profile
from xlii.refs import (
    BOOKMARK,
    COLLECTION,
    RefView,
    make_bookmark_ref,
    ref_target_type,
    ref_view,
    sanitize_refs,
)
from xlii.repl import REPLState
from xlii.repl_cmds.knowledge import _handle_ref_command
from xlii.transcript import mark_last_turn, write_turn
from tests.helpers import FakeConsole, make_agent


# --------------------------------------------------------------------------- #
#  The model — ref_view normalization
# --------------------------------------------------------------------------- #

def test_legacy_2tuple_classifies_as_collection_for_the_sanitizer():
    # A stored non-empty collection id is legacy contamination — the CLASSIFIER
    # still recognizes it precisely so sanitize_refs can drop it.
    assert ref_view(("alice", "col-123")) == RefView("alice", COLLECTION, "col-123")
    assert ref_target_type(("alice", "col-123")) == COLLECTION


def test_empty_collection_id_is_a_bookmark_ref():
    assert ref_view(("spark", "")) == RefView("spark", BOOKMARK, "spark")
    assert ref_target_type(("spark", "")) == BOOKMARK


def test_three_tuple_is_forward_compatible():
    assert ref_view(("x", "bookmark", "bob:y")) == RefView("x", "bookmark", "bob:y")


def test_bookmark_constructor_produces_the_stored_shape():
    assert make_bookmark_ref("bob:spark") == ("bob:spark", "")
    # There is deliberately NO collection-ref constructor anymore (§5).
    import xlii.refs as refs_mod
    assert not hasattr(refs_mod, "make_collection_ref")
    assert not hasattr(refs_mod, "collection_ids")


def test_sanitize_refs_drops_legacy_collection_entries():
    refs = [("alice", "col-1"), ("spark", ""), ["bob", "col-2"], ["mark2", ""]]
    # JSON-loaded lists normalize to tuples; every collection entry dies.
    assert sanitize_refs(refs) == [("spark", ""), ("mark2", "")]
    assert sanitize_refs([]) == []
    assert sanitize_refs(None) == []


# --------------------------------------------------------------------------- #
#  /ref — the merged recall (cut-and-paste a marked window; global bookmarks)
# --------------------------------------------------------------------------- #

def _code_state(tmp_path):
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


def _persona_with_mark(monkeypatch, tmp_path, name, mark):
    import xlii.persona
    from xlii.persona import Persona
    pdir = tmp_path / "personas"
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", pdir)
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", tmp_path / "chat")
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{name}.md").write_text(f"You are {name}.")
    p = Persona(name)
    write_turn(p.turns_dir, "q", "a")
    mark_last_turn(p.turns_dir, mark)
    return p


def test_ref_pastes_a_marked_window(tmp_path):
    state = _code_state(tmp_path)
    write_turn(_store(state), "the idea", "the answer")
    mark_last_turn(_store(state), "spark")

    _handle_ref_command("/ref spark", state, state.console)
    docs = dict(state.attached_docs)
    assert "point:spark" in docs
    assert "the answer" in docs["point:spark"]
    assert state.attached_refs == []          # a doc paste, never a RAG collection


def test_ref_persona_name_is_not_a_memory_attach(tmp_path, monkeypatch):
    # The old `/ref <persona>` attached a whole persona Collection to search_project.
    # That path is GONE: a bare word is a (global) bookmark lookup, never memory.
    _persona_with_mark(monkeypatch, tmp_path, "alice", "unrelated")
    state = _code_state(tmp_path)

    _handle_ref_command("/ref alice", state, state.console)
    assert state.attached_refs == []          # NO persona memory attached
    assert "no mark named" in state.console.text


def test_ref_global_lookup_needs_no_persona(tmp_path, monkeypatch):
    _persona_with_mark(monkeypatch, tmp_path, "bob", "auth-insight")
    state = _code_state(tmp_path)

    # bare name, no persona typed — resolves across personas
    _handle_ref_command("/ref auth-insight", state, state.console)
    docs = dict(state.attached_docs)
    assert "point:auth-insight" in docs       # bare label, no persona baggage
    assert "point:bob:auth-insight" not in docs


def test_ref_persona_qualifier_strips_persona_from_the_paste(tmp_path, monkeypatch):
    _persona_with_mark(monkeypatch, tmp_path, "bob", "auth-insight")
    state = _code_state(tmp_path)

    _handle_ref_command("/ref bob:auth-insight", state, state.console)
    docs = dict(state.attached_docs)
    assert "point:auth-insight" in docs       # qualifier resolves it, but never rides in
    assert "point:bob:auth-insight" not in docs


def test_ref_name_collision_asks_to_qualify(tmp_path, monkeypatch):
    import xlii.persona
    from xlii.persona import Persona
    pdir = tmp_path / "personas"
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", pdir)
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", tmp_path / "chat")
    pdir.mkdir(parents=True, exist_ok=True)
    for name in ("bob", "sol"):
        (pdir / f"{name}.md").write_text(f"You are {name}.")
        p = Persona(name)
        write_turn(p.turns_dir, "q", "a")
        mark_last_turn(p.turns_dir, "plan")
    state = _code_state(tmp_path)

    _handle_ref_command("/ref plan", state, state.console)
    text = state.console.text
    assert "bob:plan" in text and "sol:plan" in text   # candidates to qualify
    assert state.attached_docs == []                   # nothing pasted until qualified


def test_unref_drops_a_recalled_point(tmp_path):
    state = _code_state(tmp_path)
    write_turn(_store(state), "idea", "answer")
    mark_last_turn(_store(state), "spark")
    _handle_ref_command("/ref spark", state, state.console)
    assert "point:spark" in dict(state.attached_docs)

    _handle_ref_command("/unref spark", state, state.console)
    assert "point:spark" not in dict(state.attached_docs)


def test_ref_no_args_lists_recalled_points(tmp_path):
    state = _code_state(tmp_path)
    write_turn(_store(state), "idea", "answer")
    mark_last_turn(_store(state), "spark")
    _handle_ref_command("/ref spark", state, state.console)

    state.console.lines.clear()
    _handle_ref_command("/ref", state, state.console)
    assert "spark" in state.console.text
