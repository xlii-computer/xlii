"""Tier-0 coverage for xlii/agent.py: the run_turn loop + tool batching.

The orchestration loop is the highest-blast-radius code (it's the thing the user
talks to) and was previously exercised only via bare-object shells. Here we drive
complete turns with a *scripted* LLM — `_stream_orchestrator_iteration` is faked
to replay messages — so the loop, batch ordering, dispatch, stats, history, and
the hallucination guard are all hermetic. No network.

Run directly:  ./venv/bin/python -m pytest tests/test_agent.py
"""

import pytest

from tests.helpers import make_agent, make_cfg, make_msg, script_iterations


def _tool_results(history):
    return [h["content"] for h in history if h.get("role") == "tool"]


def test_turn_terminates_and_returns_final_text(tmp_path):
    agent = make_agent(tmp_path)
    script_iterations(agent, ("All done.", None))  # no tool calls → terminate
    text, dirty, stats = agent.run_turn("hi")
    assert text == "All done."
    assert dirty == set()
    assert stats.tool_calls == 0


def test_write_then_read_in_same_batch_observes_the_write(tmp_path):
    """The ordering invariant: a read declared after a write in the same batch
    must observe the write (mutator is a sequential barrier)."""
    agent = make_agent(tmp_path)
    script_iterations(
        agent,
        ("working", [
            ("write_file", {"path": "f.txt", "content": "V2"}),
            ("read_file", {"path": "f.txt"}),
        ]),
        ("done", None),
    )
    text, dirty, stats = agent.run_turn("update f")
    assert text == "done"
    assert "f.txt" in dirty
    assert stats.tool_calls == 2
    # the read's tool result must contain the just-written content
    read_result = _tool_results(agent.history)[1]
    assert "V2" in read_result


def test_parallel_safe_batch_runs_all_and_records_results(tmp_path):
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "b.txt").write_text("bbb")
    agent = make_agent(tmp_path)
    script_iterations(
        agent,
        ("reading", [
            ("read_file", {"path": "a.txt"}),
            ("read_file", {"path": "b.txt"}),
        ]),
        ("done", None),
    )
    agent.run_turn("read both")
    results = "\n".join(_tool_results(agent.history))
    assert "aaa" in results and "bbb" in results


def test_tool_results_append_in_declaration_order(tmp_path):
    (tmp_path / "a.txt").write_text("AAA")
    agent = make_agent(tmp_path)
    script_iterations(
        agent,
        ("x", [
            ("read_file", {"path": "a.txt"}),
            ("write_file", {"path": "z.txt", "content": "ZZZ"}),
        ]),
        ("done", None),
    )
    agent.run_turn("go")
    tool_msgs = [h for h in agent.history if h.get("role") == "tool"]
    assert tool_msgs[0]["tool_call_id"] == "call_0"  # read first
    assert tool_msgs[1]["tool_call_id"] == "call_1"  # write second
    assert "AAA" in tool_msgs[0]["content"]


def test_hallucination_guard_flags_claim_with_zero_tools(tmp_path):
    agent = make_agent(tmp_path)
    script_iterations(agent, ("I created the file for you.", None))
    _text, _dirty, stats = agent.run_turn("make a file")
    assert any("0 tools" in w for w in stats.warnings)


def test_no_guard_when_a_tool_was_actually_called(tmp_path):
    """A past-tense claim is fine when tools were used — the claim is supported."""
    agent = make_agent(tmp_path)
    script_iterations(
        agent,
        ("", [("write_file", {"path": "f.txt", "content": "x"})]),
        ("I created the file.", None),
    )
    _text, _dirty, stats = agent.run_turn("make f")
    assert stats.warnings == []


def test_hallucination_guard_flags_edit_claim_on_read_only_turn(tmp_path):
    """The read-only loophole (turn-receipts P0): a grep/read turn must not
    let "I fixed the file" through just because tool_calls > 0."""
    (tmp_path / "a.txt").write_text("AAA")
    agent = make_agent(tmp_path)
    script_iterations(
        agent,
        ("looking", [("read_file", {"path": "a.txt"})]),
        ("I fixed the file.", None),
    )
    _text, dirty, stats = agent.run_turn("fix a.txt")
    assert dirty == set()
    assert any("no files were modified" in w for w in stats.warnings)


def test_no_guard_for_non_edit_claim_on_read_only_turn(tmp_path):
    """Verify-tier verbs ("tested", "ran") are not gated by the write check —
    a read-only turn saying "I verified the config" is not an edit lie."""
    (tmp_path / "a.txt").write_text("AAA")
    agent = make_agent(tmp_path)
    script_iterations(
        agent,
        ("looking", [("read_file", {"path": "a.txt"})]),
        ("I verified the config matches.", None),
    )
    _text, _dirty, stats = agent.run_turn("check a.txt")
    assert stats.warnings == []


def test_cancel_at_tool_boundary_stops_next_iteration(tmp_path):
    """Stop pressed while a batch runs: the turn ends at the boundary instead of
    streaming another model iteration (bg-default P0)."""
    agent = make_agent(tmp_path)
    script_iterations(
        agent,
        ("working", [("write_file", {"path": "f.txt", "content": "x"})]),
        ("should never stream", None),
    )
    orig = agent._execute_tool_batch

    def batch_then_cancel(*a, **k):
        orig(*a, **k)
        agent.request_cancel()  # user hits ■ while the batch is executing

    agent._execute_tool_batch = batch_then_cancel
    text, dirty, _stats = agent.run_turn("go")
    assert "cancelled" in text
    assert "f.txt" in dirty  # the atomic step that was in flight still landed


def test_cancel_during_stream_skips_batch_but_answers_tool_calls(tmp_path):
    """Stop pressed during the model stream: the declared batch is NOT executed,
    but every declared call gets a tool result so history stays API-valid."""
    agent = make_agent(tmp_path)
    it = iter([
        ("working", [("write_file", {"path": "f.txt", "content": "x"})]),
    ])

    def stream_then_cancel(*a, **k):
        from tests.helpers import make_msg
        content, tool_calls = next(it)
        agent.request_cancel()  # user hits ■ mid-stream
        return (make_msg(content, tool_calls), None, False)

    agent._stream_orchestrator_iteration = stream_then_cancel
    text, dirty, _stats = agent.run_turn("go")
    assert "cancelled" in text
    assert dirty == set()  # the batch never ran
    assert not (tmp_path / "f.txt").exists()
    tool_msgs = [h for h in agent.history if h.get("role") == "tool"]
    assert tool_msgs and "cancelled by user" in tool_msgs[0]["content"]


def test_stale_cancel_request_does_not_kill_next_turn(tmp_path):
    """A stop that landed after a turn ended (or against a shell command) must
    not cancel the next agent turn — run_turn clears the flag on entry."""
    agent = make_agent(tmp_path)
    agent.request_cancel()
    script_iterations(agent, ("All done.", None))
    text, _dirty, _stats = agent.run_turn("hi")
    assert text == "All done."


def test_scoped_cancel_callback_can_stop_turn_at_entry(tmp_path):
    """A surface with a per-turn cancel token can stop a freshly-created agent
    before its first model call without weakening the stale-cancel guard."""
    agent = make_agent(tmp_path)
    script_iterations(agent, ("should never stream", None))
    text, dirty, stats = agent.run_turn("hi", cancelled=lambda: True)
    assert "cancelled" in text
    assert dirty == set()
    assert stats.orch.iterations == 0


def test_dispatch_subagent_records_worker_stats(tmp_path, monkeypatch):
    from xlii.agent import CallStats

    agent = make_agent(tmp_path)
    # Don't spin a real worker LLM — return a canned reply + stats.
    monkeypatch.setattr(agent, "_run_worker",
                        lambda args: ("worker investigated X", CallStats()))
    script_iterations(
        agent,
        ("delegating", [("dispatch_subagent", {"task": "investigate X"})]),
        ("synthesized", None),
    )
    text, _dirty, stats = agent.run_turn("look into X")
    assert text == "synthesized"
    assert stats.workers_dispatched == 1
    assert any("worker investigated X" in r for r in _tool_results(agent.history))


def test_max_tool_iterations_exit(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg(max_tool_iterations=2))
    # Never terminates — always asks for another read.
    forever = [("loop", [("read_file", {"path": "missing.txt"})])] * 5
    script_iterations(agent, *forever)
    text, _dirty, stats = agent.run_turn("go")
    assert "max_tool_iterations" in text
    assert stats.orch.iterations == 2


def test_chat_turns_use_the_tighter_tool_cap(tmp_path):
    # A conversational (persona/phone) turn caps at max_chat_tool_iterations, not
    # the larger coding budget — bounding the phone-persona tool-spiral.
    agent = make_agent(tmp_path, cfg=make_cfg(max_tool_iterations=9, max_chat_tool_iterations=2))
    agent.session.conversational = True                # → _model_role() == "chat"
    forever = [("loop", [("read_file", {"path": "missing.txt"})])] * 5
    script_iterations(agent, *forever)
    text, _dirty, stats = agent.run_turn("hey iXaac")
    assert stats.orch.iterations == 2                  # the chat cap, not 9
    assert "max_chat_tool_iterations" in text          # and the hint names the right knob


def test_chat_turn_falls_back_when_chat_cap_missing(tmp_path):
    # Legacy/partial cfg without max_chat_tool_iterations uses the coding knob.
    cfg = make_cfg(max_tool_iterations=3)
    del cfg.max_chat_tool_iterations
    agent = make_agent(tmp_path, cfg=cfg)
    agent.session.conversational = True
    forever = [("loop", [("read_file", {"path": "missing.txt"})])] * 5
    script_iterations(agent, *forever)
    text, _dirty, stats = agent.run_turn("hey")
    assert stats.orch.iterations == 3
    assert "max_tool_iterations" in text
    assert "max_chat_tool_iterations" not in text


def test_unknown_tool_is_reported_not_raised(tmp_path):
    # A hallucinated tool name is now caught by dispatch-time palette
    # enforcement (plan-write-domain P0): it was never advertised, so it is
    # refused as a recorded tool result — still reported, still not raised.
    agent = make_agent(tmp_path)
    script_iterations(
        agent,
        ("x", [("no_such_tool", {})]),
        ("done", None),
    )
    text, _dirty, _stats = agent.run_turn("go")
    assert text == "done"
    assert any("not in this turn's advertised palette" in r
               for r in _tool_results(agent.history))


def test_bad_json_arguments_recorded_as_error(tmp_path):
    # Force a tool_call whose arguments aren't valid JSON.
    from types import SimpleNamespace

    agent = make_agent(tmp_path)

    def fake(*a, **k):
        tc = SimpleNamespace(id="call_0",
                             function=SimpleNamespace(name="read_file", arguments="{not json"))
        if not getattr(fake, "done", False):
            fake.done = True
            return (SimpleNamespace(content="x", tool_calls=[tc]), None, False)
        return (SimpleNamespace(content="done", tool_calls=None), None, False)

    agent._stream_orchestrator_iteration = fake
    text, _dirty, _stats = agent.run_turn("go")
    assert text == "done"
    assert any("invalid JSON" in r for r in _tool_results(agent.history))


def test_model_override_reflected_in_stats(tmp_path):
    agent = make_agent(tmp_path, model_override="pinned-model")
    script_iterations(agent, ("ok", None))
    _text, _dirty, stats = agent.run_turn("hi")
    assert stats.orch.model == "pinned-model"


def test_conversational_surface_uses_chat_role(tmp_path):
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_tier = None  # /tier off — this test pins the PLAIN chat role
    script_iterations(agent, ("ok", None))
    _text, _dirty, stats = agent.run_turn("hi")
    assert stats.orch.model == "chat-model"


def test_conversational_default_routes_via_auto(tmp_path):
    # Fresh sessions default to /tier auto (chat-tiers Vector D): a casual
    # message routes to the fast tier's economy model, not the plain chat role.
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    script_iterations(agent, ("ok", None))
    _text, _dirty, stats = agent.run_turn("hi")
    assert stats.orch.model == "grok-build-0.1"


def test_orchestrator_model_and_role_reports_pin(tmp_path):
    from xlii.agent import resolve_orchestrator_model

    agent = make_agent(tmp_path, model_override="pinned-model")
    assert agent.orchestrator_model_and_role() == ("pinned-model", "orchestrator")

    agent.session.conversational = True
    assert agent.orchestrator_model_and_role() == ("pinned-model", "chat")

    agent.session.model_override = None
    agent.session.chat_tier = None  # /tier off — assert the plain chat-role fallback
    assert agent.orchestrator_model_and_role() == ("chat-model", "chat")

    model, role = resolve_orchestrator_model(
        cfg=agent.cfg,
        conversational=False,
        howto_mode=True,
    )
    assert role == "help"
    assert model == "help-model"


# --- turn-failure history rollback ------------------------------------------ #

def _raise_on_stream(agent, exc):
    def fake(*args, **kwargs):
        raise exc
    agent._stream_orchestrator_iteration = fake


def test_failed_turn_rolls_its_user_message_out_of_history(tmp_path):
    # A 503'd turn must not leave a ghost question in history — the next
    # successful turn (even in howto) would answer it hours late.
    agent = make_agent(tmp_path)
    before = len(agent.history)
    _raise_on_stream(agent, RuntimeError("upstream connect error"))
    with pytest.raises(RuntimeError):
        agent.run_turn("what's the weather")
    assert len(agent.history) == before
    assert not any(
        h.get("role") == "user" and "weather" in str(h.get("content"))
        for h in agent.history
    )


def test_failed_turn_rollback_covers_partial_tool_entries(tmp_path):
    # First iteration answers with a tool call; the second call raises. The
    # rollback must remove the whole partial turn (user + assistant + tool),
    # not just the user message — a dangling tool_call would poison the API.
    agent = make_agent(tmp_path)
    before = len(agent.history)
    steps = iter([
        (make_msg("", [("read_file", {"path": "x"})]), None, False),
    ])

    def fake(*args, **kwargs):
        try:
            return next(steps)
        except StopIteration:
            raise RuntimeError("upstream reset") from None

    agent._stream_orchestrator_iteration = fake
    with pytest.raises(RuntimeError):
        agent.run_turn("read then die")
    assert len(agent.history) == before


def test_failed_turn_restores_drained_btw_notes(tmp_path):
    # /btw drains clear the inbox before the next model call. If that call
    # raises and rollback deletes the injected history message, the notes must
    # be queued again rather than silently disappearing.
    agent = make_agent(tmp_path)
    agent.session.btw_inbox.extend(["use pathlib"])
    before = len(agent.history)
    _raise_on_stream(agent, RuntimeError("upstream reset"))

    with pytest.raises(RuntimeError):
        agent.run_turn("long task")

    assert len(agent.history) == before
    assert agent.session.btw_inbox == ["use pathlib"]


def test_failed_turn_restores_pending_plan_refresher(tmp_path):
    # /plan continue is another one-shot folded into the user message before
    # history append. If the turn rolls back, retrying must still include it.
    agent = make_agent(tmp_path)
    agent.session.pending_plan_refresher = "PLAN-BODY"
    before = len(agent.history)
    _raise_on_stream(agent, RuntimeError("upstream reset"))

    with pytest.raises(RuntimeError):
        agent.run_turn("continue")

    assert len(agent.history) == before
    assert agent.session.pending_plan_refresher == "PLAN-BODY"


def test_interrupted_turn_keeps_partial_history(tmp_path):
    # KeyboardInterrupt is NOT rolled back: /btw steering after a ctrl-C needs
    # the partial turn as its referent; repair_interrupted_history handles the
    # API-validity trim instead.
    agent = make_agent(tmp_path)
    before = len(agent.history)
    _raise_on_stream(agent, KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        agent.run_turn("long rambling question")
    assert len(agent.history) == before + 1
    assert agent.history[-1]["role"] == "user"


def test_failed_deep_search_turn_rolls_back_too(tmp_path, monkeypatch):
    # The heavy pre-block appends user + assistant(tool_call) before the
    # coordinator runs; a coordinator failure must roll all of it back.
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_tier = "heavy"

    def boom(*a, **k):
        raise RuntimeError("search plane down")

    monkeypatch.setattr("xlii.deep_search.run_deep_search", boom)
    before = len(agent.history)
    with pytest.raises(RuntimeError):
        agent.run_turn("who won last night")
    assert len(agent.history) == before


def test_tier_router_classifies_tier_text_not_augmented_message(tmp_path, monkeypatch):
    """tauri-face live-fire: a persona one-shot fuses journal+wiki AHEAD of the
    prompt, and the ambient's URLs read to the router as a fresh-data ask —
    'hello' fanned out into a heavy deep search. With ``tier_text`` the router
    (and the sigil parser) classify the RAW prompt only."""
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.chat_tier = "auto"
    seen = {}

    def fake_resolve(name, *, user_message=None, cfg=None):
        seen["name"] = name
        seen["msg"] = user_message
        return None  # no tier → plain single turn (which we then abort)

    monkeypatch.setattr("xlii.chat_tiers.resolve_tier", fake_resolve)
    _raise_on_stream(agent, RuntimeError("stop after routing"))

    fused = ("journal: see https://github.com/x/y/pull/1 for the fix\n\n---\n\nhello")
    with pytest.raises(RuntimeError):
        agent.run_turn(fused, tier_text="hello")
    assert seen["msg"] == "hello"          # never the URL-bearing ambient

    # The >>tier sigil rides the head of the RAW prompt (= the blob's tail):
    # parsed from tier_text, stripped from the fused message's tail.
    with pytest.raises(RuntimeError):
        agent.run_turn("AMBIENT\n\n---\n\n>>f hello there",
                       tier_text=">>f hello there")
    assert seen["name"] == "fast"
    assert seen["msg"] == "hello there"
