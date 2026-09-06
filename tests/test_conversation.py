"""Kernel convergence — the live Conversation (xlii/conversation.py).

Pins the review fixes (2026-07-04):
- complete_turn records IN MEMORY only — the TurnStore policy stays the single
  persistence path (the double-write bug).
- ensure_conversation is the one get-or-create: every surface observes the SAME
  instance the turn pipeline drives (the three-instances bug).
- Seeding is bounded (the unbounded full-history load).
- KernelTurnExecutor never returns a prior turn's reply for a streamed empty
  text (the stale turns[-1] fallback), aborts in-flight on error, and the sink
  never swallows errors silently.
- AppTurnSink no longer starts turns (the truncated-prompt mis-pairing race) —
  the lifecycle belongs to _agent_turn/_finish_agent_turn.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.conversation import (
    DEFAULT_SEED_LIMIT,
    Conversation,
    KernelTurnExecutor,
    KernelTurnSink,
    conversation_for_profile,
    ensure_conversation,
)
from xlii.transcript import write_turn


def _turn_files(turns_dir):
    return sorted(turns_dir.glob("*.md")) if turns_dir.exists() else []


# --- lifecycle: record, never write -------------------------------------------------


def test_complete_turn_records_in_memory_and_never_writes(tmp_path):
    conv = Conversation(turns_dir=tmp_path / "turns")
    conv.start_turn("what is the nav bug?")
    conv.append_chunk("it's the ")
    conv.append_chunk("z-index")
    turn = conv.complete_turn()
    assert turn is not None and turn.assistant == "it's the z-index"
    assert conv.turns[-1] is turn and not conv.has_in_flight()
    # THE double-persist fix: the Conversation never touches disk on completion.
    assert _turn_files(tmp_path / "turns") == []


def test_explicit_reply_wins_over_chunks_and_empty_reply_records_nothing(tmp_path):
    conv = Conversation(turns_dir=tmp_path / "turns")
    conv.start_turn("q1")
    assert conv.complete_turn("full reply").assistant == "full reply"
    # Streamed turns can complete with empty text: in-flight clears, nothing recorded.
    conv.start_turn("q2")
    assert conv.complete_turn("") is None
    assert not conv.has_in_flight()
    assert [t.user for t in conv.turns] == ["q1"]
    # Completing with no in-flight is a no-op.
    assert conv.complete_turn("stray") is None


def test_abort_turn_clears_in_flight(tmp_path):
    conv = Conversation(turns_dir=tmp_path / "turns")
    conv.start_turn("doomed")
    conv.abort_turn()
    assert not conv.has_in_flight()
    # A later completion cannot mis-pair the aborted prompt with its reply.
    assert conv.complete_turn("late reply") is None
    assert conv.turns == []


# --- bounded seeding -----------------------------------------------------------------


def test_seed_is_bounded(tmp_path):
    turns_dir = tmp_path / "turns"
    for i in range(DEFAULT_SEED_LIMIT + 10):
        write_turn(turns_dir, f"q{i}", f"a{i}")
    conv = Conversation(turns_dir=turns_dir)
    assert len(conv.turns) == DEFAULT_SEED_LIMIT
    assert conv.turns[-1].user == f"q{DEFAULT_SEED_LIMIT + 9}"  # the newest tail
    small = Conversation(turns_dir=turns_dir, seed_limit=5)
    assert len(small.turns) == 5
    small.refresh_from_disk()
    assert len(small.turns) == 5  # refresh honors the bound too


def test_conversation_for_profile_uses_turnstore_dir_and_seed_limit(tmp_path):
    profile = SimpleNamespace(memory=SimpleNamespace(turns_dir=tmp_path / "turns", seed_limit=7))
    conv = conversation_for_profile(profile)
    assert conv.turns_dir == tmp_path / "turns" and conv.seed_limit == 7
    # No turns_dir anywhere → None, never a guessed/cwd-relative path.
    assert conversation_for_profile(SimpleNamespace()) is None
    assert conversation_for_profile(None) is None


# --- ensure_conversation: one instance per session ------------------------------------


def test_ensure_conversation_reuses_the_attached_instance(tmp_path):
    existing = Conversation(turns_dir=tmp_path / "turns")
    state = SimpleNamespace(conversation=existing)
    assert ensure_conversation(state) is existing


def test_ensure_conversation_creates_once_then_shares(tmp_path):
    state = SimpleNamespace(
        profile=SimpleNamespace(memory=SimpleNamespace(turns_dir=tmp_path / "turns", seed_limit=9)),
    )
    conv = ensure_conversation(state)
    assert conv is not None and conv.seed_limit == 9
    assert state.conversation is conv
    # The pane/TUI call sites get the SAME object — the three-instances bug.
    assert ensure_conversation(state) is conv


def test_ensure_conversation_stateless_returns_none():
    assert ensure_conversation(None) is None
    assert ensure_conversation(SimpleNamespace()) is None


# --- the kernel executor (unwired, but must be correct before Phase 3 wires it) -------


def _seeded_conv(tmp_path):
    turns_dir = tmp_path / "turns"
    write_turn(turns_dir, "old question", "OLD ANSWER FROM A PRIOR TURN")
    return Conversation(turns_dir=turns_dir)


def test_executor_streamed_empty_reply_is_not_a_prior_turns_answer(tmp_path):
    conv = _seeded_conv(tmp_path)
    ex = KernelTurnExecutor(conv, lambda q: ("", set(), None))  # streamed: empty text
    res = ex.execute("new question")
    # The stale turns[-1] fallback returned "OLD ANSWER FROM A PRIOR TURN" here.
    assert res.reply == ""
    assert not conv.has_in_flight()
    assert [t.user for t in conv.turns] == ["old question"]


def test_executor_records_and_persists_via_injected_policy(tmp_path):
    conv = _seeded_conv(tmp_path)
    persisted = []
    ex = KernelTurnExecutor(conv, lambda q: ("the reply", {"a.py"}, "stats"),
                            persist=lambda user, reply: persisted.append((user, reply)))
    res = ex.execute("new question")
    assert res.reply == "the reply" and res.dirty == {"a.py"} and res.stats == "stats"
    assert conv.turns[-1].user == "new question"
    assert persisted == [("new question", "the reply")]
    # Executor itself wrote nothing — persistence is the injected policy's job.
    assert len(_turn_files(tmp_path / "turns")) == 1


def test_executor_aborts_in_flight_on_error(tmp_path):
    conv = Conversation(turns_dir=tmp_path / "turns")

    def boom(q):
        raise RuntimeError("model down")

    ex = KernelTurnExecutor(conv, boom)
    with pytest.raises(RuntimeError):
        ex.execute("doomed")
    assert not conv.has_in_flight()  # no phantom live row, no later mis-pair


def test_sink_routes_errors_instead_of_swallowing(tmp_path):
    conv = Conversation(turns_dir=tmp_path / "turns")

    def boom(q):
        raise RuntimeError("model down")

    errors: list = []
    KernelTurnSink(KernelTurnExecutor(conv, boom), on_error=errors.append).submit("q")
    assert len(errors) == 1 and "model down" in str(errors[0])
    with pytest.raises(RuntimeError):  # no handler → propagate, never silent
        KernelTurnSink(KernelTurnExecutor(conv, boom)).submit("q")

    results: list = []
    ok = KernelTurnExecutor(conv, lambda q: ("hi", set(), None))
    KernelTurnSink(ok, result_callback=results.append).submit("q")
    assert results and results[0].reply == "hi"


# --- surfaces observe the shared instance ----------------------------------------------


def test_transcript_pane_renders_live_row_from_shared_conversation(tmp_path):
    from xlii.panes.transcript import TranscriptPane

    conv = Conversation(turns_dir=tmp_path / "turns")
    pane = TranscriptPane(live_conv=conv)
    assert not any("▶" in r.text for r in pane.render().rows)
    conv.start_turn("fix the nav")
    conv.append_chunk("looking at index.html")
    rows = pane.render().rows
    assert rows and rows[-1].accent and "fix the nav" in rows[-1].text
    conv.complete_turn("done")
    assert not any("▶" in r.text for r in pane.render().rows)


def test_app_turn_sink_no_longer_starts_turns(tmp_path):
    from xlii.tui.dock_surface import AppTurnSink

    conv = Conversation(turns_dir=tmp_path / "turns")
    submitted = []
    app = SimpleNamespace(_submit_prompt=submitted.append,
                          _state=SimpleNamespace(conversation=conv),
                          _conversation=conv)
    AppTurnSink(app).submit("re-ask this", context="")
    assert submitted == ["re-ask this"]
    # The mis-pairing race fix: the sink must NOT touch the live Conversation —
    # _agent_turn owns start (full prompt), _finish_agent_turn owns complete.
    assert not conv.has_in_flight()


# --------------------------------------------------------------------------- #
#  drive_turn — THE turn owner (kernel convergence Phase 3)
# --------------------------------------------------------------------------- #


class _RecConsole:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))


def _drive_state(tmp_path, *, with_profile=True):
    """A minimal REPLState-shaped fake that exercises drive_turn's whole spine."""
    xli = tmp_path / ".xlii"
    (xli / "turns").mkdir(parents=True, exist_ok=True)
    conv = Conversation(turns_dir=xli / "turns")
    persisted = []

    from xlii.agent import SessionState

    state = SimpleNamespace(
        console=_RecConsole(),
        project=SimpleNamespace(xli_dir=xli, project_root=tmp_path),
        agent=SimpleNamespace(history=[], session=SessionState()),
        conversation=conv,
        journal=None,
        loop=None,
        live_attachment_paths=lambda: [],
    )
    if with_profile:
        def _persist(history, q, dirty):
            persisted.append((q, tuple(sorted(dirty))))
            return dirty
        state.profile = SimpleNamespace(memory=SimpleNamespace(persist=_persist))
    else:
        state.profile = None
    return state, conv, persisted


def test_drive_turn_happy_path_renders_persists_once_completes(tmp_path):
    from xlii.conversation import drive_turn

    state, conv, persisted = _drive_state(tmp_path)
    rendered = []
    errors = []

    res = drive_turn(
        state, "add a login endpoint",
        lambda q: ("done: added it", {"login.py"}, SimpleNamespace(tool_calls=2, total_cost=None)),
        render=lambda r, q: rendered.append((r.reply, q)),
        on_error=errors.append,
    )
    assert res is not None and res.reply == "done: added it"
    assert rendered == [("done: added it", "add a login endpoint")]
    assert errors == []
    assert persisted == [("add a login endpoint", ("login.py",))]   # exactly once
    assert not conv.has_in_flight()                                  # completed
    assert conv.turns[-1].assistant == "done: added it"


def test_drive_turn_error_aborts_conv_no_render_no_persist(tmp_path):
    from xlii.conversation import drive_turn

    state, conv, persisted = _drive_state(tmp_path)
    rendered, errors = [], []

    def _boom(q):
        raise RuntimeError("model unavailable")

    res = drive_turn(state, "q", _boom,
                     render=lambda r, q: rendered.append(r),
                     on_error=errors.append)
    assert res is None
    assert rendered == [] and persisted == []
    assert len(errors) == 1 and "model unavailable" in str(errors[0])
    assert not conv.has_in_flight()                                  # aborted, not stuck


def test_drive_turn_keyboard_interrupt_aborts_and_reraises(tmp_path):
    from xlii.conversation import drive_turn

    state, conv, _ = _drive_state(tmp_path)

    def _interrupt(q):
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        drive_turn(state, "q", _interrupt,
                   render=lambda r, q: None, on_error=lambda e: None)
    assert not conv.has_in_flight()


def test_drive_turn_profileless_uses_bounded_code_persist(tmp_path):
    from xlii.conversation import drive_turn

    state, conv, _ = _drive_state(tmp_path, with_profile=False)
    # persist_code_turn reads the final reply from agent history
    state.agent.history = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "the reply"},
    ]
    drive_turn(state, "q",
               lambda q: ("the reply", set(), SimpleNamespace(tool_calls=0, total_cost=None)),
               render=lambda r, q: None, on_error=lambda e: None)
    files = list((tmp_path / ".xlii" / "turns").glob("*"))
    assert files, "persist_code_turn should have written a turn file"


def test_drive_turn_render_failure_still_persists(tmp_path):
    from xlii.conversation import drive_turn

    state, conv, persisted = _drive_state(tmp_path)
    errors = []

    def _boom_render(result, prompt):
        raise RuntimeError("ui mount failed")

    res = drive_turn(
        state, "q",
        lambda q: ("ok", set(), SimpleNamespace(tool_calls=0, total_cost=None)),
        render=_boom_render,
        on_error=errors.append,
    )
    assert res is not None and res.reply == "ok"
    assert persisted == [("q", ())]
    assert not conv.has_in_flight()
    assert len(errors) == 1 and "ui mount failed" in str(errors[0])


def test_drive_turn_feeds_stream_chunks_into_conversation(tmp_path):
    """Phase 6: console.on_content_chunk → append_chunk during run_turn."""
    from xlii.conversation import drive_turn

    state, conv, _ = _drive_state(tmp_path)
    seen = []

    def _run(q):
        # Mimic Agent streaming: the duck-typed hook drive_turn installed.
        cb = getattr(state.console, "on_content_chunk", None)
        assert cb is not None, "chunk feed must be installed for the turn"
        cb("hello ")
        cb("world")
        seen.append(conv.in_flight.assistant_so_far if conv.in_flight else None)
        return ("hello world", set(), SimpleNamespace(tool_calls=0, total_cost=None))

    drive_turn(state, "q", _run, render=lambda r, q: None, on_error=lambda e: None)
    assert seen == ["hello world"]
    assert not conv.has_in_flight()
    assert conv.turns[-1].assistant == "hello world"
    # Hook restored after the turn.
    assert getattr(state.console, "on_content_chunk", None) in (None, )


def test_conversation_listener_throttles_chunks(tmp_path, monkeypatch):
    from xlii import conversation as conv_mod

    calls = []
    conv_mod.set_conversation_listener(lambda: calls.append(1))
    try:
        conv = Conversation(turns_dir=tmp_path / "turns")
        conv.start_turn("q")
        assert len(calls) == 1  # force on start
        monkeypatch.setattr(conv_mod, "_LAST_CONV_NOTIFY", 0.0)
        conv.append_chunk("a")
        first = len(calls)
        # Immediate second chunk within the throttle window is dropped.
        conv.append_chunk("b")
        assert len(calls) == first
        conv.complete_turn("done")
        assert len(calls) == first + 1  # force on complete
    finally:
        conv_mod.set_conversation_listener(None)
