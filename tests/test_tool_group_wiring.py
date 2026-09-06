"""The agent side of per-step tool folding + interim-reasoning visibility.

Part A — the agent brackets each tool *batch* with the transcript console's
``begin_tool_group`` / ``end_tool_group`` (duck-typed; a plain console has
neither, so the inline REPL is untouched), so each model step folds into its own
collapsible drawer.

Part B — in a styled surface (supports_live=False, like the Textual transcript),
the model's interim narration that accompanies a tool batch is surfaced as a
loose block, so the "why I called these tools" shows between the folded groups.
The FINAL answer (no tool calls) is left for the turn's render slice, so it is
NOT printed here (no double render).

No network — a scripted loop (Part A) and a fake chunk stream (Part B).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from tests.helpers import FakeConsole, make_agent, script_iterations


# --------------------------------------------------------------------------- #
#  Part A — batch bracketing
# --------------------------------------------------------------------------- #

def test_agent_brackets_each_tool_batch(tmp_path):
    events: list[str] = []

    class GroupingConsole(FakeConsole):
        def begin_tool_group(self) -> None:
            events.append("BEGIN")

        def end_tool_group(self) -> None:
            events.append("END")

    (tmp_path / "a.py").write_text("x")
    agent = make_agent(tmp_path, console=GroupingConsole())
    script_iterations(
        agent,
        ("first step", [("read_file", {"path": "a.py"})]),   # batch 1
        ("second step", [("read_file", {"path": "a.py"})]),  # batch 2
        ("done", None),                                       # terminate
    )
    agent.run_turn("go")

    # Two batches → two properly-nested BEGIN/END pairs.
    assert events.count("BEGIN") == 2
    assert events.count("END") == 2
    begins = [i for i, e in enumerate(events) if e == "BEGIN"]
    ends = [i for i, e in enumerate(events) if e == "END"]
    assert begins[0] < ends[0] < begins[1] < ends[1]


def test_plain_console_without_group_hooks_still_runs(tmp_path):
    """The duck-typed guard: a console with no begin/end_tool_group (the inline
    REPL's real rich Console) runs the turn unchanged — no crash, no grouping."""
    (tmp_path / "a.py").write_text("x")
    agent = make_agent(tmp_path, console=FakeConsole())  # no group hooks
    script_iterations(
        agent,
        ("step", [("read_file", {"path": "a.py"})]),
        ("done", None),
    )
    text, _dirty, stats = agent.run_turn("go")
    assert text == "done"
    assert stats.tool_calls == 1


# --------------------------------------------------------------------------- #
#  Part B — interim reasoning between batches (styled surface)
# --------------------------------------------------------------------------- #

class _TuiStyleConsole(FakeConsole):
    """Shapes like the Textual transcript console: no Live, records renderables."""

    supports_live = False
    is_terminal = False

    def __init__(self) -> None:
        super().__init__()
        self.rendered: list[object] = []

    def print(self, *args, **kwargs) -> None:
        super().print(*args, **kwargs)
        self.rendered.extend(args)

    @property
    def markups(self) -> str:
        return " ".join(getattr(o, "markup", str(o)) for o in self.rendered)


def _delta(content=None, tool_calls=None):
    return SimpleNamespace(content=content, reasoning_content=None, tool_calls=tool_calls)


def _chunk(delta):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(delta=delta)])


def _toolcall_delta(index, name, args):
    return SimpleNamespace(
        content=None,
        reasoning_content=None,
        tool_calls=[SimpleNamespace(
            index=index, id=f"call_{index}",
            function=SimpleNamespace(name=name, arguments=json.dumps(args)),
        )],
    )


def _wire_stream(agent, chunks):
    # agent.clients is a read-only property → pool.primary(); fake that.
    create = lambda **k: iter(chunks)  # noqa: E731 — tiny stand-in
    clients = SimpleNamespace(
        chat=SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create))))
    agent.pool.primary = lambda: clients


def _stream_once(agent):
    return agent._stream_orchestrator_iteration(
        model="m", schemas=[], temperature=0.0, cache_hdrs=None)


def test_interim_reasoning_surfaces_when_a_batch_follows(tmp_path):
    console = _TuiStyleConsole()
    agent = make_agent(tmp_path, console=console)
    _wire_stream(agent, [
        _chunk(_delta(content="Let me read the ghost movement code.")),
        _chunk(_toolcall_delta(0, "read_file", {"path": "a.py"})),
    ])

    msg, _usage, streamed = _stream_once(agent)

    assert msg.tool_calls                                   # interim step (has tools)
    assert msg.content == "Let me read the ghost movement code."
    # …and it was surfaced as a loose block, not dropped.
    assert "Let me read the ghost movement code." in console.markups
    assert streamed is False


def test_final_answer_is_not_printed_by_the_stream_helper(tmp_path):
    """The double-render guard: a content-only iteration (no tool calls) is the
    final answer — returned for the render slice, NOT printed here."""
    console = _TuiStyleConsole()
    agent = make_agent(tmp_path, console=console)
    _wire_stream(agent, [_chunk(_delta(content="The ghosts get stuck because…"))])

    msg, _usage, streamed = _stream_once(agent)

    assert msg.content == "The ghosts get stuck because…"
    assert msg.tool_calls is None
    assert "The ghosts get stuck because" not in console.markups  # not double-rendered
    assert streamed is False


def test_stream_live_uses_answer_panel(tmp_path, monkeypatch):
    """H1 regression: the live streaming preview is a framed Panel — and the
    frame actually renders (a function-local rich.panel import elsewhere in the
    method once shadowed Panel and made the closure NameError on the first
    streamed chunk)."""
    from rich.panel import Panel

    updates = []

    class _FakeLive:
        def __init__(self, _r, **kw):
            pass

        def start(self):
            return None

        def update(self, renderable):
            updates.append(renderable)

        def stop(self):
            return None

    monkeypatch.setattr("xlii.agent.Live", _FakeLive)

    class _LiveConsole(FakeConsole):
        supports_live = True

        def __init__(self):
            super().__init__()
            self.size = type("S", (), {"height": 24, "width": 80})()

    agent = make_agent(tmp_path, console=_LiveConsole())
    agent.turn_record = ("plan", "yellow", "")
    _wire_stream(agent, [_chunk(_delta(content="Hello"))])
    _stream_once(agent)
    assert updates and isinstance(updates[0], Panel)
