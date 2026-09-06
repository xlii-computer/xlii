"""Task+ T+P0 — linear default: edge-free pipes stay today's sequential runner."""

from __future__ import annotations

from pathlib import Path

from helpers import FakeConsole, make_agent, make_cfg
from xlii import tasks as T
from xlii.repl_state import REPLState


def _state(tmp_path: Path) -> REPLState:
    agent = make_agent(tmp_path, cfg=make_cfg())
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    return REPLState(
        console=FakeConsole(),
        agent=agent,
        project=agent.project,
        cfg=agent.cfg,
        pool=agent.pool,
    )


def test_taskplus_non_goals_are_documented():
    """T+P0: non-goals live next to the engine so they cannot silently drift."""
    assert T.TASKPLUS_NON_GOALS
    joined = " | ".join(T.TASKPLUS_NON_GOALS)
    assert "/loop" in joined and "/rail" in joined
    assert "BPMN" in joined
    assert "unbounded" in joined


def test_step_and_pipeline_taskplus_surface():
    """T+P3: split/join are now live on Step (the T+P2 guard is lifted)."""
    step_fields = set(T.Step.__dataclass_fields__)
    pipe_fields = set(T.Pipeline.__dataclass_fields__)
    assert "on_success" in step_fields and "on_failure" in step_fields and "id" in step_fields
    assert "edges" in pipe_fields
    # split/join/policy are real Step fields now (T+P3 shipped).
    assert T.TASKPLUS_CONTROL_FIELDS <= step_fields
    assert "policy" in step_fields
    assert T.TASKPLUS_CONTROL_FIELDS == frozenset({"split", "join"})


def test_edge_free_pipe_is_byte_identical_linear_behavior(tmp_path):
    """An edge-free pipe is strictly sequential — same carry + stop semantics."""
    st = _state(tmp_path)
    ctx = st.as_context_dict()

    ok_pipe = T.parse_inline("printf hi |> tr a-z A-Z")
    out = T.run_pipeline(ok_pipe, ctx, confirm_shell=False)
    assert out.ok and not out.had_errors
    assert out.carry == "HI"
    assert [s.kind for s in out.steps] == [T.KIND_SHELL, T.KIND_SHELL]
    assert out.failed_index is None

    fail_pipe = T.parse_inline("false |> printf after")
    stopped = T.run_pipeline(fail_pipe, ctx, confirm_shell=False)
    assert not stopped.ok
    assert stopped.failed_index == 0
    assert len(stopped.steps) == 1
    assert stopped.carry == ""

    kept = T.run_pipeline(fail_pipe, ctx, confirm_shell=False, keep_going=True)
    assert kept.ok and kept.had_errors
    assert kept.carry == "after"


def test_parse_inline_stays_linear_ordered_list():
    """Fat-pipe parse yields an ordered step list — no edges invented."""
    p = T.parse_inline("printf a |> printf b |> printf c")
    assert p.name  # Pipeline always has a name
    assert [s.body for s in p.steps] == ["printf a", "printf b", "printf c"]
    assert not T.pipeline_has_branching(p)
    for s in p.steps:
        assert not s.on_success and not s.on_failure
