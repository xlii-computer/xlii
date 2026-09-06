"""Cross-session memory for `xlii code` (RP0).

`code` was amnesiac: it neither seeded prior turns at startup nor persisted
new ones (unlike `chat`). These lock the two helpers that fix that —
`seed_code_history` (re-seed last N turns after the system prompt) and
`persist_code_turn` (write a turn to the project-local store) — plus the reply
extraction they share with chat. See proposals/repl-profiles.md (RP0).

Run the suite with `python -m pytest` so `from tests.helpers import …` resolves
(the repo root must be on sys.path; bare `pytest` mis-collects it)."""

from types import SimpleNamespace

from xlii import transcript as tx
from xlii.cmds.sessions import (
    _final_reply_from_history,
    persist_code_turn,
    seed_code_history,
)
from tests.helpers import FakeConsole, make_agent, make_cfg, make_msg, make_project


def _agent_with_system(prompt="SYS"):
    """A minimal stand-in for Agent — seeding only touches `.history`."""
    return SimpleNamespace(history=[{"role": "system", "content": prompt}])


# --------------------------------------------------------------------------- #
#  persist_code_turn
# --------------------------------------------------------------------------- #

def test_persist_code_turn_writes_and_roundtrips(tmp_path):
    turns = tmp_path / ".xlii" / "turns"
    wrote = persist_code_turn(turns, "where did we leave off?", "we finished the parser")

    assert wrote is True
    assert len(list(turns.glob("*.md"))) == 1

    # round-trips back through the transcript layer that seeds it next session
    loaded = tx.load_recent_turns(turns, 10)
    assert len(loaded) == 1
    assert loaded[0].user == "where did we leave off?"
    assert loaded[0].assistant == "we finished the parser"


def test_persist_code_turn_empty_reply_is_noop(tmp_path):
    turns = tmp_path / ".xlii" / "turns"
    assert persist_code_turn(turns, "q", "") is False
    assert not turns.exists()  # nothing written


def test_persist_code_turn_whitespace_reply_is_noop(tmp_path):
    # A whitespace-only reply would strip to an empty assistant body and seed
    # back as a junk message — treat it as no reply.
    turns = tmp_path / ".xlii" / "turns"
    assert persist_code_turn(turns, "q", "   \n  ") is False
    assert not turns.exists()


# --------------------------------------------------------------------------- #
#  seed_code_history
# --------------------------------------------------------------------------- #

def test_seed_code_history_appends_turns_after_system(tmp_path):
    turns = tmp_path / ".xlii" / "turns"
    tx.write_turn(turns, "q1", "a1")
    tx.write_turn(turns, "q2", "a2")

    agent = _agent_with_system()
    n = seed_code_history(agent, turns)

    assert n == 2
    assert agent.history == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]


def test_seed_code_history_respects_limit(tmp_path):
    turns = tmp_path / ".xlii" / "turns"
    for i in range(5):
        tx.write_turn(turns, f"q{i}", f"a{i}")

    agent = _agent_with_system()
    n = seed_code_history(agent, turns, limit=2)

    assert n == 2
    # system stays at [0]; only the last two turns follow, in order
    assert [e["content"] for e in agent.history] == ["SYS", "q3", "a3", "q4", "a4"]


def test_seed_code_history_no_turns_leaves_history_untouched(tmp_path):
    turns = tmp_path / ".xlii" / "turns"  # never created
    agent = _agent_with_system()
    n = seed_code_history(agent, turns)

    assert n == 0
    assert agent.history == [{"role": "system", "content": "SYS"}]


def test_seed_into_real_agent_keeps_code_prompt_at_head(tmp_path):
    """The actual cmd_code wire: a real Agent (no history= kwarg, so
    __post_init__ builds the *code* system prompt) seeds turns AFTER it."""
    from xlii.agent import Agent, SessionState

    project = make_project(tmp_path)
    project.xli_dir.mkdir(parents=True, exist_ok=True)
    pool = SimpleNamespace(primary=lambda: None)
    agent = Agent(pool=pool, project=project, cfg=make_cfg(),
                  console=FakeConsole(), session=SessionState())

    # __post_init__ built history[0] from the real code prompt (not "SYS").
    assert agent.history[0]["role"] == "system"
    assert "xlii" in agent.history[0]["content"].lower()
    head_before = agent.history[0]["content"]

    turns = project.xli_dir / "turns"
    tx.write_turn(turns, "q1", "a1")
    tx.write_turn(turns, "q2", "a2")
    n = seed_code_history(agent, turns)

    assert n == 2
    assert [e["role"] for e in agent.history] == \
        ["system", "user", "assistant", "user", "assistant"]
    assert agent.history[1:] == [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
        {"role": "user", "content": "q2"},
        {"role": "assistant", "content": "a2"},
    ]
    # seeding never clobbers the system prompt at [0]
    assert agent.history[0]["content"] == head_before


# --------------------------------------------------------------------------- #
#  _final_reply_from_history — bounded to the CURRENT turn
# --------------------------------------------------------------------------- #

def test_final_reply_from_history_picks_last_assistant_with_content():
    history = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "first"},
        {"role": "user", "content": "more"},
        {"role": "assistant", "content": ""},  # streamed/empty — skipped
        {"role": "assistant", "content": "final answer"},
    ]
    assert _final_reply_from_history(history) == "final answer"


def test_final_reply_from_history_empty_when_no_assistant():
    assert _final_reply_from_history([{"role": "system", "content": "SYS"}]) == ""
    assert _final_reply_from_history([]) == ""


def test_final_reply_ignores_prior_turn_when_current_has_no_reply():
    # Current turn appended only a user message (e.g. an empty model reply): the
    # scan must STOP at this turn's user and NOT resurface the seeded prior reply.
    history = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "old q"},
        {"role": "assistant", "content": "old reply"},  # seeded prior turn
        {"role": "user", "content": "new q"},            # current turn, no reply
    ]
    assert _final_reply_from_history(history) == ""


def test_final_reply_ignores_prior_turn_on_max_iters_exhaustion():
    # max_tool_iterations: the current turn ends with tool calls / no content
    # (the "(stopped…)" string is never appended to history). Must not persist
    # the prior turn's reply as if it answered the new question.
    history = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "old q"},
        {"role": "assistant", "content": "old reply"},
        {"role": "user", "content": "new q"},
        {"role": "assistant", "tool_calls": [{"id": "1"}]},  # no content
        {"role": "tool", "content": "tool output"},
    ]
    assert _final_reply_from_history(history) == ""


# --------------------------------------------------------------------------- #
#  Integration: streamed turn + persist/seed round-trip
# --------------------------------------------------------------------------- #

def test_streamed_turn_reply_recovered_and_persisted(tmp_path):
    """The whole reason _final_reply_from_history exists: a streamed run_turn
    returns text=="" while the reply lives only in history — and persist must
    write the recovered reply, not the empty text."""
    agent = make_agent(tmp_path)

    def fake(*_a, **_k):
        return (make_msg("we finished the parser"), None, True)  # streamed=True, no tools
    agent._stream_orchestrator_iteration = fake

    text, _dirty, _stats = agent.run_turn("where are we?")
    assert text == ""  # streamed → run_turn returns empty text

    reply = _final_reply_from_history(agent.history)
    assert reply == "we finished the parser"

    turns = tmp_path / ".xlii" / "turns"
    assert persist_code_turn(turns, "where are we?", reply) is True
    assert tx.load_recent_turns(turns, 1)[0].assistant == "we finished the parser"


def test_persist_then_seed_roundtrip(tmp_path):
    """End-to-end: a turn persisted this session re-seeds next session."""
    turns = tmp_path / ".xlii" / "turns"
    persist_code_turn(turns, "set up the build", "done — make test is green")

    agent = _agent_with_system()
    seed_code_history(agent, turns)

    assert agent.history[1:] == [
        {"role": "user", "content": "set up the build"},
        {"role": "assistant", "content": "done — make test is green"},
    ]
