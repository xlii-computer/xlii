"""The Fold — Vector A: the consolidated `/attach` + `/detach` verbs.

`/attach doc|ref` surfaces the attachment type system at one verb; `/detach`
unifies `/undoc` + `/unref`. Legacy `/doc`, `/undoc` ride as hidden aliases, and
`bookmark` is the accepted alias for the ref type (its pre-rename name).

⚠ The persona-Collection attach (`/attach ref <persona>` → search_project) is
BANNED — menu-families §5, the d96c9d35 rule. The regression guards below pin
the SEMANTICS: no persona Collection id can ever enter session state through
any `/attach` spelling, even when a Collection-bearing persona exists.

These drive the real handlers against a real REPLState, plus the registry to
prove the hidden-alias routing.
"""

from __future__ import annotations

import json

import xlii.doc
import xlii.persona
from xlii.commands import dispatch_repl_command
from xlii.persona import Persona
from xlii.profile import code_profile
from xlii.refs import BOOKMARK, ref_view
from xlii.repl import REPLState
from xlii.repl_cmds.attach import run_attach_command, run_detach_command
from xlii.repl_cmds.knowledge import _handle_ref_command
from xlii.transcript import mark_last_turn, write_turn
from tests.helpers import FakeConsole, make_agent


# --------------------------------------------------------------------------- #
#  fixtures / helpers
# --------------------------------------------------------------------------- #

def _state(tmp_path):
    root = tmp_path / "repo"
    (root / ".xlii").mkdir(parents=True, exist_ok=True)
    agent = make_agent(root)
    state = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                      cfg=agent.cfg, pool=agent.pool)
    state.profile = code_profile(agent.project, seed_limit=20)
    state.command_scope = "code"
    return state


def _ctx(state):
    # Explicit ctx so the FakeConsole is what the handlers print through.
    return {"state": state, "console": state.console}


def _store(state):
    return state.profile.memory.turns_dir


def _isolate_personas(monkeypatch, tmp_path):
    pdir = tmp_path / "personas"
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", pdir)
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", tmp_path / "chat")
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


def _make_persona(monkeypatch, tmp_path, name, *, collection_id=None, mark=None):
    pdir = _isolate_personas(monkeypatch, tmp_path)
    (pdir / f"{name}.md").write_text(f"You are {name}.")
    p = Persona(name)
    if collection_id is not None:
        proj = p.project_root / ".xlii"
        proj.mkdir(parents=True, exist_ok=True)
        (proj / "project.json").write_text(json.dumps({"collection_id": collection_id}))
    if mark is not None:
        write_turn(p.turns_dir, "q", "a")
        mark_last_turn(p.turns_dir, mark)
    return p


def _mkdoc(monkeypatch, tmp_path, name, body):
    monkeypatch.setattr(xlii.doc, "DOCS_DIR", tmp_path / "docs")
    xlii.doc.Doc(name).write(body)


# --------------------------------------------------------------------------- #
#  /attach doc  — the inline-every-turn shape (delegates to the doc handler)
# --------------------------------------------------------------------------- #

def test_attach_doc_inlines(tmp_path, monkeypatch):
    _mkdoc(monkeypatch, tmp_path, "house-style", "always httpx")
    state = _state(tmp_path)
    run_attach_command("/attach doc house-style", _ctx(state))
    assert ("house-style", "always httpx") in state.attached_docs
    assert "inlined" in state.console.text


def test_doc_alias_routes_through_attach(tmp_path, monkeypatch):
    """`/doc <name>` is a hidden alias of `/attach` — the registry routes it to
    the attach command, which delegates to the rich doc handler (list · refresh)."""
    _mkdoc(monkeypatch, tmp_path, "notes", "body")
    state = _state(tmp_path)
    ctx = _ctx(state)
    ctx["command_scope"] = "code"
    assert dispatch_repl_command("/doc notes", ctx) is True
    assert ("notes", "body") in state.attached_docs
    # bare /doc still lists (grammar preserved through the alias)
    state.console.lines.clear()
    assert dispatch_repl_command("/doc", ctx) is True
    assert "notes" in state.console.text


# --------------------------------------------------------------------------- #
#  ⚠ the contamination regression guard (menu-families §5, d96c9d35)
# --------------------------------------------------------------------------- #

def test_persona_name_can_never_attach_a_collection(tmp_path, monkeypatch):
    """THE guard, pinned on semantics not tokens: given a persona that HAS a
    Collection (the exact old happy path), every `/attach` spelling of its name
    fails loudly and no collection id enters session state. `ref` may later mean
    other things (today: a marked turn) — what can never come back is the
    whole-Collection attach."""
    _make_persona(monkeypatch, tmp_path, "alice", collection_id="col-abc")
    state = _state(tmp_path)
    run_attach_command("/attach ref alice", _ctx(state))        # old happy path
    run_attach_command("/attach bookmark alice", _ctx(state))   # alias spelling
    assert state.attached_refs == []
    assert "no mark named" in state.console.text                # loud, honest miss
    # The collection id appears NOWHERE in session state.
    blob = repr(state.attached_refs) + repr(state.attached_docs)
    assert "col-abc" not in blob


# --------------------------------------------------------------------------- #
#  /attach ref  — the live-pointer shape (a marked turn; alias: bookmark)
# --------------------------------------------------------------------------- #

def test_attach_ref_is_a_live_pointer(tmp_path):
    state = _state(tmp_path)
    write_turn(_store(state), "the idea", "the answer")
    mark_last_turn(_store(state), "spark")

    run_attach_command("/attach ref spark", _ctx(state))
    assert ("spark", "") in state.attached_refs           # empty cid = bookmark ref
    assert ref_view(state.attached_refs[0]).target_type == BOOKMARK
    assert state.attached_docs == []                      # not inlined either


def test_attach_bookmark_alias_still_works(tmp_path):
    state = _state(tmp_path)
    write_turn(_store(state), "the idea", "the answer")
    mark_last_turn(_store(state), "spark")
    run_attach_command("/attach bookmark spark", _ctx(state))
    assert ("spark", "") in state.attached_refs


def test_attach_ref_missing_mark(tmp_path):
    state = _state(tmp_path)
    run_attach_command("/attach ref nope", _ctx(state))
    assert state.attached_refs == []
    assert "no mark named" in state.console.text


# --------------------------------------------------------------------------- #
#  bare /attach — the unified list, grouped by type + cost shape
# --------------------------------------------------------------------------- #

def test_bare_attach_lists_by_type(tmp_path, monkeypatch):
    _mkdoc(monkeypatch, tmp_path, "rules", "x")
    state = _state(tmp_path)
    write_turn(_store(state), "i", "a")
    mark_last_turn(_store(state), "spark")
    run_attach_command("/attach doc rules", _ctx(state))
    run_attach_command("/attach ref spark", _ctx(state))

    state.console.lines.clear()
    run_attach_command("/attach", _ctx(state))
    text = state.console.text
    assert "docs" in text and "rules" in text
    assert "refs" in text and "spark" in text
    assert "neither inlined nor searched" in text          # ref cost shape taught


def test_bare_attach_empty_teaches_the_two_types(tmp_path):
    state = _state(tmp_path)
    run_attach_command("/attach", _ctx(state))
    text = state.console.text
    assert "nothing attached" in text
    assert "/attach doc" in text and "/attach ref" in text
    assert "<persona>" not in text                         # the old shape is untaught


def test_unknown_attach_type_is_rejected(tmp_path):
    state = _state(tmp_path)
    run_attach_command("/attach frobnicate x", _ctx(state))
    assert "unknown attach type" in state.console.text


# --------------------------------------------------------------------------- #
#  /detach — unifies /undoc + /unref, across channels
# --------------------------------------------------------------------------- #

def test_detach_untyped_drops_a_ref(tmp_path):
    state = _state(tmp_path)
    write_turn(_store(state), "i", "a")
    mark_last_turn(_store(state), "spark")
    run_attach_command("/attach ref spark", _ctx(state))
    assert state.attached_refs

    state.console.lines.clear()
    run_detach_command("/detach spark", _ctx(state))
    assert state.attached_refs == []
    assert "detached" in state.console.text


def test_detach_typed_doc_uses_the_skill_guarded_path(tmp_path, monkeypatch):
    _mkdoc(monkeypatch, tmp_path, "notes", "body")
    state = _state(tmp_path)
    run_attach_command("/attach doc notes", _ctx(state))
    run_detach_command("/detach doc notes", _ctx(state))
    assert state.attached_docs == []


def test_detach_drops_a_recalled_point(tmp_path):
    state = _state(tmp_path)
    write_turn(_store(state), "idea", "answer")
    mark_last_turn(_store(state), "spark")
    _handle_ref_command("/recall spark", state, state.console)     # inline it
    assert "point:spark" in dict(state.attached_docs)

    run_detach_command("/detach spark", _ctx(state))
    assert "point:spark" not in dict(state.attached_docs)


def test_detach_miss_is_channel_agnostic(tmp_path):
    state = _state(tmp_path)
    run_detach_command("/detach nope", _ctx(state))
    assert "nothing attached named" in state.console.text


def test_undoc_alias_routes_through_detach(tmp_path, monkeypatch):
    _mkdoc(monkeypatch, tmp_path, "notes", "body")
    state = _state(tmp_path)
    ctx = _ctx(state)
    ctx["command_scope"] = "code"
    dispatch_repl_command("/doc notes", ctx)
    assert state.attached_docs
    assert dispatch_repl_command("/undoc notes", ctx) is True
    assert state.attached_docs == []
