"""xlii/turn_store.py — the kernel home for session turn-store policy (GVM B1).

Pins the promoted helpers under their public kernel names plus the ask-session
storage layout; the cmd-file façades (cmds/sessions/memory.py, ask.py) are
covered by the pre-existing suites (test_code_memory.py, test_ask_session.py),
which keep passing unmodified against the old import paths.
"""

from __future__ import annotations

from types import SimpleNamespace

from xlii import turn_store
from xlii.turn_store import (
    SESSION_SEED_TURNS,
    ask_session_turns_dir,
    final_reply_from_history,
    persist_turn,
    seed_history,
    session_key,
)


def test_final_reply_bounded_to_current_turn():
    history = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "old q"},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "new q"},  # current turn, no reply after it
    ]
    assert final_reply_from_history(history) == ""


def test_final_reply_picks_last_assistant_with_content():
    history = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},  # streamed/empty — skipped
        {"role": "assistant", "content": "final answer"},
    ]
    assert final_reply_from_history(history) == "final answer"


def test_seed_history_appends_after_system(tmp_path):
    from xlii import transcript as tx

    turns = tmp_path / "turns"
    tx.write_turn(turns, "q1", "a1")
    tx.write_turn(turns, "q2", "a2")
    agent = SimpleNamespace(history=[{"role": "system", "content": "SYS"}])

    assert seed_history(agent, turns) == 2
    assert [e["content"] for e in agent.history] == ["SYS", "q1", "a1", "q2", "a2"]


def test_persist_turn_writes_and_noops_on_blank(tmp_path):
    turns = tmp_path / "turns"
    assert persist_turn(turns, "q", "") is False
    assert persist_turn(turns, "q", "  \n ") is False
    assert not turns.exists()
    assert persist_turn(turns, "q", "a") is True
    assert len(list(turns.glob("*.md"))) == 1


def test_session_key_is_safe_and_collision_resistant():
    k1 = session_key("xmpp:me@example.org:lab")
    k2 = session_key("xmpp:me@example.org_lab")  # slug-collides with k1
    assert k1 != k2  # digest keeps trust boundaries apart
    assert "/" not in k1 and ":" not in k1
    assert k1 == session_key("xmpp:me@example.org:lab")  # deterministic


def test_ask_session_turns_dir_layout(tmp_path):
    project = SimpleNamespace(xli_dir=tmp_path / ".xlii")
    d = ask_session_turns_dir(project, "xmpp:me@host:lab")
    assert d == tmp_path / ".xlii" / "ask-sessions" / session_key("xmpp:me@host:lab") / "turns"


def test_seed_window_constant():
    assert SESSION_SEED_TURNS == 20


def test_cmd_facades_alias_the_kernel_functions():
    """The old import paths resolve to the very same kernel functions."""
    from xlii.cmds.sessions import ask as ask_mod
    from xlii.cmds.sessions import memory as memory_mod

    assert memory_mod._final_reply_from_history is turn_store.final_reply_from_history
    assert memory_mod.seed_code_history is turn_store.seed_history
    assert memory_mod.persist_code_turn is turn_store.persist_turn
    assert memory_mod.CHAT_RECENT_TURNS == turn_store.CHAT_RECENT_TURNS
    assert ask_mod._session_key is turn_store.session_key
    assert ask_mod._session_turns_dir is turn_store.ask_session_turns_dir
    assert ask_mod.SESSION_SEED_TURNS == turn_store.SESSION_SEED_TURNS
