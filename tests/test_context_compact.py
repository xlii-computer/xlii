"""Phase 9 — /compact context summarization and continue."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.helpers import FakeConsole, make_agent, make_cfg
from xlii.agent_stats import TurnStats
from xlii.context_compact import (
    COMPACT_SUMMARY_HEADER,
    compact_agent_history,
    conversation_messages,
    estimate_history_tokens,
    init_compact_auto_from_env,
    maybe_auto_compact,
    prior_compacted_summary,
    should_auto_compact,
    split_for_compact,
)
from xlii.repl_cmds.compact import h_compact
from xlii.transcript import write_turn


def _summary_resp(text: str, *, cost: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        cost_usd=cost,
        prompt_tokens=100,
        completion_tokens=50,
    )


def _long_history(n_turns: int) -> list[dict]:
    hist = [{"role": "system", "content": "SYS"}]
    for i in range(n_turns):
        hist.append({"role": "user", "content": f"user task {i} " + ("x" * 200)})
        hist.append({"role": "assistant", "content": f"assistant reply {i} " + ("y" * 200)})
    return hist


def _history_with_tools(n_plain: int = 2) -> list[dict]:
    hist = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "read the config"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "tc1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"cfg.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc1", "content": "CONFIG=1"},
        {"role": "assistant", "content": "config says CONFIG=1"},
    ]
    for i in range(n_plain):
        hist.append({"role": "user", "content": f"follow-up {i} " + ("z" * 100)})
        hist.append({"role": "assistant", "content": f"reply {i} " + ("w" * 100)})
    return hist


def test_split_for_compact_keeps_recent_window():
    conv: list[dict] = []
    for i in range(6):
        conv.append({"role": "user", "content": f"u{i}"})
        conv.append({"role": "assistant", "content": f"a{i}"})
    old, kept = split_for_compact(conv, recent_turns=2)
    assert len(old) == 8
    assert len(kept) == 4
    assert kept[0]["content"] == "u4"


def test_split_recent_zero_summarizes_all():
    conv = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "d"},
    ]
    old, kept = split_for_compact(conv, recent_turns=0)
    assert old == conv
    assert kept == []


def test_split_short_conversation_summarizes_nothing():
    conv = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    old, kept = split_for_compact(conv, recent_turns=4)
    assert old == []
    assert kept == conv


def test_compact_rebuilds_history_and_drops_tokens(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.history = _long_history(8)
    before = estimate_history_tokens(agent.history)

    monkeypatch.setattr(
        "xlii.context_compact._summarize_messages",
        lambda msgs, prior_summary="": _summary_resp(
            "## Plan\nfix auth\n\n## Work done\nedited foo.py\n\n"
            "## Files touched\n- src/foo.py\n\n## Next steps\nadd tests"
        ),
    )

    result = compact_agent_history(agent, recent_turns=2)
    assert result.compacted is True
    assert result.summarized_turns == 6
    assert result.kept_turns == 2
    assert len(agent.history) == 1 + 1 + 4  # system + summary + 2 exchanges
    assert COMPACT_SUMMARY_HEADER in agent.history[1]["content"]
    assert "src/foo.py" in agent.history[1]["content"]
    assert agent.history[-2]["content"] == "user task 7 " + ("x" * 200)
    assert estimate_history_tokens(agent.history) < before
    assert agent.session.last_turn_stats.context_tokens == estimate_history_tokens(agent.history)


def test_compact_strips_tool_messages_from_rebuilt_history(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.history = _history_with_tools(n_plain=4)
    captured: dict[str, list] = {}

    def fake_summarize(msgs, prior_summary=""):
        captured["msgs"] = msgs
        return _summary_resp("## Plan\np\n\n## Work done\nw\n\n## Files touched\nf\n\n## Next steps\nn")

    monkeypatch.setattr("xlii.context_compact._summarize_messages", fake_summarize)
    result = compact_agent_history(agent, recent_turns=1)
    assert result.compacted is True
    roles = {m["role"] for m in agent.history}
    assert "tool" not in roles
    assert all("tool_calls" not in m for m in agent.history)
    assert all(m.get("role") in ("system", "user", "assistant") for m in agent.history)
    assert all(not any(tc in str(m) for tc in ("tool_calls", "tool_call_id")) for m in agent.history)


def test_second_compact_carries_prior_summary(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.history = _long_history(6)
    seen_prior: list[str] = []

    def fake_summarize(msgs, prior_summary=""):
        seen_prior.append(prior_summary)
        return _summary_resp(
            "## Plan\nround2\n\n## Work done\nw\n\n## Files touched\nf\n\n## Next steps\nn"
        )

    monkeypatch.setattr("xlii.context_compact._summarize_messages", fake_summarize)
    compact_agent_history(agent, recent_turns=1)
    first_body = prior_compacted_summary(agent.history)
    assert "round2" in first_body or "## Plan" in first_body

    agent.history.extend(_long_history(4)[1:])  # more turns after compact
    compact_agent_history(agent, recent_turns=1)
    assert seen_prior[-1] == first_body


def test_compact_skips_when_nothing_old_enough(tmp_path):
    agent = make_agent(tmp_path)
    agent.history = _long_history(2)
    result = compact_agent_history(agent, recent_turns=4)
    assert result.compacted is False
    assert "shorter" in result.reason


def test_compact_empty_summary_leaves_history_unchanged(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    before = _long_history(6)
    agent.history = list(before)
    monkeypatch.setattr(
        "xlii.context_compact._summarize_messages",
        lambda msgs, prior_summary="": _summary_resp(""),
    )
    result = compact_agent_history(agent, recent_turns=1)
    assert result.compacted is False
    assert agent.history == before


def test_compact_summarizer_error_leaves_history_unchanged(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    before = _long_history(6)
    agent.history = list(before)

    def boom(msgs, prior_summary=""):
        raise RuntimeError("network down")

    monkeypatch.setattr("xlii.context_compact._summarize_messages", boom)
    result = compact_agent_history(agent, recent_turns=1)
    assert result.compacted is False
    assert "summarizer failed" in result.reason
    assert agent.history == before


def test_compact_records_summarizer_cost(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.history = _long_history(6)
    monkeypatch.setattr(
        "xlii.context_compact._summarize_messages",
        lambda msgs, prior_summary="": _summary_resp(
            "## Plan\np\n\n## Work done\nw\n\n## Files touched\nf\n\n## Next steps\nn",
            cost=0.42,
        ),
    )
    compact_agent_history(agent, recent_turns=1)
    assert agent.session.session_cost == pytest.approx(0.42)
    assert agent.session.session_tokens == 150


def test_compact_does_not_touch_disk_turns(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    turns_dir = tmp_path / ".xlii" / "turns"
    write_turn(turns_dir, "first user", "first assistant")
    write_turn(turns_dir, "second user", "second assistant")
    files_before = sorted(turns_dir.glob("*.md"))

    agent.history = _long_history(6)
    monkeypatch.setattr(
        "xlii.context_compact._summarize_messages",
        lambda msgs, prior_summary="": _summary_resp(
            "## Plan\np\n\n## Work done\nw\n\n## Files touched\n-\n\n## Next steps\n-"
        ),
    )
    compact_agent_history(agent, recent_turns=1)

    assert sorted(turns_dir.glob("*.md")) == files_before
    assert "first user" in files_before[0].read_text()


def test_conversation_messages_skips_prior_compaction(tmp_path):
    agent = make_agent(tmp_path)
    agent.history = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": f"{COMPACT_SUMMARY_HEADER}\n\nold summary"},
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": "new answer"},
    ]
    conv = conversation_messages(agent.history)
    assert len(conv) == 2
    assert conv[0]["content"] == "new question"
    assert prior_compacted_summary(agent.history) == "old summary"


def test_should_auto_compact_near_cap():
    assert should_auto_compact(900_000, 1_000_000) is True
    assert should_auto_compact(100_000, 1_000_000) is False
    assert should_auto_compact(100_000, None) is False


def test_maybe_auto_compact_runs_when_enabled(tmp_path, monkeypatch):
    agent = make_agent(tmp_path)
    agent.history = _long_history(8)
    agent.session.compact_auto = True
    agent.session.last_turn_stats = TurnStats(context_tokens=900_000)
    agent.cfg = make_cfg(orchestrator="grok-4")

    called = {"n": 0}

    def fake_compact(agent, **kw):
        called["n"] += 1
        return SimpleNamespace(
            compacted=True,
            before_tokens=900,
            after_tokens=100,
            summarized_turns=5,
            kept_turns=2,
        )

    monkeypatch.setattr("xlii.context_compact.compact_agent_history", fake_compact)
    state = SimpleNamespace(agent=agent, console=FakeConsole())
    assert maybe_auto_compact(state) is True
    assert called["n"] == 1


def test_init_compact_auto_from_env(monkeypatch):
    session = SimpleNamespace(compact_auto=False, compact_auto_env_cleared=False)
    monkeypatch.setenv("XLII_COMPACT_AUTO", "1")
    init_compact_auto_from_env(session)
    assert session.compact_auto is True


def test_compact_command_handler(tmp_path, monkeypatch):
    con = FakeConsole()
    agent = make_agent(tmp_path, console=con)
    agent.history = _long_history(6)
    monkeypatch.setattr(
        "xlii.context_compact._summarize_messages",
        lambda msgs, prior_summary="": _summary_resp(
            "## Plan\np\n\n## Work done\nw\n\n## Files touched\nf\n\n## Next steps\nn"
        ),
    )
    ctx = {"agent": agent, "console": con}
    assert h_compact("/compact --recent 1", ctx) is True
    assert "compacted" in con.text
    assert "turns/" in con.text
    assert agent.session.compact_recent == 1


def test_compact_registered():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    assert find_repl_command("/compact", "code") is not None
    assert find_repl_command("/compact", "chat") is not None
