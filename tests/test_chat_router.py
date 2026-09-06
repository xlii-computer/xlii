"""Chat router — auto-tier heuristics + the escalation contract (chat-tiers B).

L1 heuristics classify the confident cases (fast/expert/heavy); the ambiguous
tail defaults to expert, which self-escalates to heavy via request_deep_search.
Pure functions, so this is all direct.
"""

from __future__ import annotations

from xlii.chat_router import (
    ESCALATE_TOOL,
    EscalationRequest,
    classify,
    detect_escalation,
    maybe_escalate,
    request_deep_search_schema,
    route,
)
from tests.helpers import make_msg


# --- Layer 1 heuristics --------------------------------------------------- #

def test_classify_heavy_on_fresh_data_signals():
    for msg in (
        "what's the latest on the merger",
        "who won the game last night",
        "search the web for the grok-4 release date",
        "what are people saying on x about this",
        "https://example.com/post — is this legit?",
        "current price of a barrel of oil",
        "tweets about the launch",
    ):
        assert classify(msg) == "heavy", msg


def test_classify_does_not_heavy_on_recall_of_tweets():
    """Bare 'tweets' is not a live-X ask — session recall must stay expert."""
    msg = (
        "I need you to rehash some of these tweets for me. Its a new day. "
        "that was yesterday and debug session as it was. but it was still "
        "solid gold and I want to preserve it in another session. So can "
        "you bring that stuff back up?"
    )
    assert classify(msg) != "heavy"


def test_classify_expert_on_reasoning_signals():
    for msg in (
        "prove that sqrt 2 is irrational",
        "why does this loop segfault",
        "debug this function for me",
        "walk through the time complexity here",
        "```\nfor x in range(10):\n    print(x)\n```",
        "x" * 401,  # a long, substantial question
    ):
        assert classify(msg) == "expert", msg[:40]


def test_classify_fast_on_casual_and_short():
    for msg in (
        "thanks!",
        "hi",
        "rewrite this to be shorter please",
        "tldr",
        "ok cool",
    ):
        assert classify(msg) == "fast", msg


def test_classify_ambiguous_is_none():
    assert classify("tell me about the history of rome") is None
    assert classify("") is None
    assert classify(None) is None


def test_heavy_beats_expert_beats_fast_in_precedence():
    # fresh-data need wins over a reasoning verb…
    assert classify("what's the latest and prove it matters") == "heavy"
    # …and a reasoning verb wins over a casual one.
    assert classify("thanks — now explain why this fails") == "expert"


def test_route_defaults_ambiguous_to_expert():
    assert route("what's the latest news") == "heavy"
    assert route("thanks!") == "fast"
    assert route("tell me about the history of rome") == "expert"  # ambiguous → expert
    assert route(None) == "expert"
    assert route("") == "expert"


# --- Layer 2 escalation contract ------------------------------------------ #

def test_schema_shape():
    s = request_deep_search_schema()
    assert s["function"]["name"] == ESCALATE_TOOL
    assert "reason" in s["function"]["parameters"]["required"]
    props = s["function"]["parameters"]["properties"]
    assert "subqueries" in props
    assert "gig" in props
    assert "gaggle" in props


def test_detect_escalation_parses_reason_and_subqueries():
    msg = make_msg(tool_calls=[
        (ESCALATE_TOOL, {"reason": "needs live scores", "subqueries": ["a", "b"]}),
    ])
    esc = detect_escalation(msg.tool_calls)
    assert isinstance(esc, EscalationRequest)
    assert esc.reason == "needs live scores"
    assert esc.subqueries == ["a", "b"]


def test_detect_escalation_ignores_other_tools_and_absence():
    msg = make_msg(tool_calls=[("read_file", {"path": "x"})])
    assert detect_escalation(msg.tool_calls) is None
    assert detect_escalation(None) is None
    assert detect_escalation([]) is None


def test_detect_escalation_tolerates_bad_args():
    # Malformed JSON args must not crash — reason falls back to "".
    bad = make_msg(tool_calls=[("read_file", {})])
    bad.tool_calls[0].function.name = ESCALATE_TOOL
    bad.tool_calls[0].function.arguments = "{not json"
    esc = detect_escalation(bad.tool_calls)
    assert isinstance(esc, EscalationRequest)
    assert esc.reason == "" and esc.subqueries == []


def test_maybe_escalate_one_hop_only():
    calls: list = []

    def fake_heavy(subqueries, reason):
        calls.append((subqueries, reason))
        return f"HEAVY[{reason}]"

    msg = make_msg(tool_calls=[
        (ESCALATE_TOOL, {"reason": "fresh data", "subqueries": ["q1"]}),
    ])
    # escalates: runs the executor, warm-started with the sub-queries.
    assert maybe_escalate(tool_calls=msg.tool_calls, heavy_executor=fake_heavy) == "HEAVY[fresh data]"
    assert calls == [(["q1"], "fresh data")]

    # already on heavy → never re-escalate (one hop, no chain).
    calls.clear()
    assert maybe_escalate(
        tool_calls=msg.tool_calls, heavy_executor=fake_heavy, already_heavy=True,
    ) is None
    assert calls == []

    # no escalation signal → no executor call.
    plain = make_msg(tool_calls=[("read_file", {"path": "x"})])
    assert maybe_escalate(tool_calls=plain.tool_calls, heavy_executor=fake_heavy) is None
