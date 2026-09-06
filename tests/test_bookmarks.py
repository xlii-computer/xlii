"""Vector D — /marks renamed to /bookmarks (the view), `marks` a hidden alias.

The verb `/mark <name>` is unchanged; only the listing view is renamed. The old
name keeps working (muscle memory + existing docs) but never shows in /help.

Behavioral coverage of the underlying mark/list/recall handlers lives in
test_marks_library.py — this file pins the *rename contract*.
"""

import xlii.persona
from xlii.commands import find_repl_command, get_repl_help
from xlii.persona import Persona
from xlii.profile import code_profile
from xlii.repl import REPLState
from xlii.repl_cmds import register_all
from xlii.repl_cmds.chat import _marks_handler
from xlii.transcript import mark_last_turn, write_turn
from tests.helpers import FakeConsole, make_agent

register_all()


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


# --------------------------------------------------------------------------- #
#  The rename contract
# --------------------------------------------------------------------------- #

def test_bookmarks_is_the_primary_name():
    for repl in ("code", "chat"):
        cmd = find_repl_command("/bookmarks", repl)
        assert cmd is not None and cmd.name == "bookmarks"


def test_marks_is_a_working_alias():
    for repl in ("code", "chat"):
        cmd = find_repl_command("/marks", repl)
        assert cmd is not None
        assert cmd.name == "bookmarks"            # resolves to the renamed command
        assert cmd.handler is _marks_handler


def test_marks_alias_is_hidden_from_help():
    help_code = get_repl_help("code")
    assert "/bookmarks" in help_code              # the view is documented
    assert "/marks" not in help_code              # the alias never surfaces
    # the verb is untouched and still listed
    assert "/mark " in help_code


def test_mark_verb_still_exists_and_is_distinct():
    cmd = find_repl_command("/mark", "code")
    assert cmd is not None and cmd.name == "mark"
    assert cmd is not find_repl_command("/bookmarks", "code")


# --------------------------------------------------------------------------- #
#  The renamed view still works (under both names) and is titled "bookmarks"
# --------------------------------------------------------------------------- #

def test_bookmarks_lists_and_titles_bookmarks(tmp_path):
    state = _code_state(tmp_path)
    write_turn(_store(state), "u", "a")
    mark_last_turn(_store(state), "spark")
    _marks_handler("/bookmarks", state.as_context_dict())
    text = state.console.text
    assert "bookmarks" in text                    # retitled view
    assert "spark" in text


def test_marks_alias_all_still_aggregates(tmp_path, monkeypatch):
    state = _code_state(tmp_path)
    pdir = tmp_path / "personas"
    monkeypatch.setattr(xlii.persona, "PERSONAS_DIR", pdir)
    monkeypatch.setattr(xlii.persona, "CHAT_STATE_DIR", tmp_path / "chat")
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "bob.md").write_text("You are bob.")
    p = Persona("bob")
    write_turn(p.turns_dir, "q", "a")
    mark_last_turn(p.turns_dir, "auth-insight")

    # invoked through the OLD name to prove the alias drives the same handler
    _marks_handler("/marks --all", state.as_context_dict())
    assert "bob:auth-insight" in state.console.text
