"""Task+ runner-safety regressions (round-2 adversarial sweep).

Three defects the sweep confirmed against the shipped T+P1/T+P2 runner:

1. **Fail-open verdict routing** — a missing/unmatched ``TASK+`` trailer failed
   closed on a default run but, under ``--keep-going`` / ``continue_on_error``,
   fell through *linearly* and executed **every** branch arm (a "deploy vs
   rollback" split ran both) while reporting ``ok=True``.
2. **Unbounded loop** — only a self-loop (``next_i == i``) was guarded; a
   length-≥2 cycle (``a on_failure→b``, ``b on_failure→a``, or a verdict
   back-edge) spun forever.
3. **Resume dropped the untrusted-carry gate** — a shell/slash arm blocked by
   the untrusted gate in the original run re-ran with **no prompt** on resume,
   because ``untrusted_pending`` was hardcoded ``False`` on entry.

Each test fails on the pre-fix runner and passes after.
"""

from __future__ import annotations

from pathlib import Path

from helpers import FakeConsole, make_agent, make_cfg, script_iterations
from xlii import tasks as T
from xlii.repl_state import REPLState


def _state(tmp_path: Path, *, agent=None) -> REPLState:
    agent = agent or make_agent(tmp_path, cfg=make_cfg())
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    return REPLState(
        console=FakeConsole(),
        agent=agent,
        project=agent.project,
        cfg=agent.cfg,
        pool=agent.pool,
    )


def _branch_pipeline(*, agent_continue: bool = False) -> T.Pipeline:
    """An agent verdict step with two mutually-exclusive shell arms."""
    return T.Pipeline(
        name="classify",
        steps=[
            T.Step(T.KIND_AGENT, "pick a branch", id="classify",
                   continue_on_error=agent_continue),
            T.Step(T.KIND_SHELL, "printf frontend-arm", id="fe"),
            T.Step(T.KIND_SHELL, "printf backend-arm", id="be"),
        ],
        edges=[
            T.Edge("classify", "frontend", "fe"),
            T.Edge("classify", "backend", "be"),
        ],
    )


def _write_classify_toml(xli: Path) -> None:
    T.tasks_dir(xli).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / "classify.toml").write_text(
        'name = "classify"\n'
        "[[step]]\n"
        'id = "classify"\n'
        'ask = "pick a branch"\n'
        "[[step]]\n"
        'id = "fe-tests"\n'
        'run = "printf frontend-arm"\n'
        "[[step]]\n"
        'id = "be-tests"\n'
        'run = "printf backend-arm"\n'
        "[[edge]]\n"
        'from = "classify"\n'
        'when = { branch = "frontend" }\n'
        'to = "fe-tests"\n'
        "[[edge]]\n"
        'from = "classify"\n'
        'when = { branch = "backend" }\n'
        'to = "be-tests"\n'
    )


# --- FIX 1: verdict routing fails CLOSED regardless of keep_going ------------- #

def test_missing_verdict_fails_closed_under_keep_going(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(agent, ("no verdict here", None))
    st = _state(tmp_path, agent=agent)
    out = T.run_pipeline(
        _branch_pipeline(), st.as_context_dict(), confirm_shell=False, keep_going=True
    )
    assert not out.ok
    assert out.steps[0].detail.startswith("missing TASK+ trailer")
    # No linear fall-through: neither arm may run.
    assert [s.resolved for s in out.steps] == ["pick a branch"]


def test_missing_verdict_fails_closed_with_continue_on_error(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(agent, ("no verdict here", None))
    st = _state(tmp_path, agent=agent)
    out = T.run_pipeline(
        _branch_pipeline(agent_continue=True), st.as_context_dict(), confirm_shell=False
    )
    assert not out.ok
    assert [s.resolved for s in out.steps] == ["pick a branch"]


def test_unmatched_verdict_fails_closed_under_keep_going(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(agent, ('answer\nTASK+ {"branch": "sideways"}', None))
    st = _state(tmp_path, agent=agent)
    out = T.run_pipeline(
        _branch_pipeline(), st.as_context_dict(), confirm_shell=False, keep_going=True
    )
    assert not out.ok
    assert "unmatched" in out.steps[0].detail
    assert [s.resolved for s in out.steps] == ["pick a branch"]


# --- FIX 2: cycles of any length are bounded --------------------------------- #

def test_two_step_on_failure_cycle_is_bounded(tmp_path):
    st = _state(tmp_path)
    p = T.Pipeline(
        name="cycle",
        steps=[
            T.Step(T.KIND_SHELL, "exit 1", id="a", on_failure="b"),
            T.Step(T.KIND_SHELL, "exit 1", id="b", on_failure="a"),
        ],
    )
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert not out.ok
    assert out.had_errors
    # a ran once, b ran once, then the back-edge to a is caught — no runaway.
    assert [s.resolved for s in out.steps] == ["exit 1", "exit 1"]
    assert out.failed_index == 1


def test_self_loop_is_bounded(tmp_path):
    st = _state(tmp_path)
    p = T.Pipeline(
        name="selfloop",
        steps=[T.Step(T.KIND_SHELL, "exit 1", id="a", on_failure="a")],
    )
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert not out.ok
    assert len(out.steps) == 1


def test_verdict_back_edge_cycle_is_bounded(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(
        agent,
        ('a\nTASK+ {"branch": "loop", "continue": true}', None),
        ('b\nTASK+ {"branch": "loop", "continue": true}', None),
    )
    st = _state(tmp_path, agent=agent)
    p = T.Pipeline(
        name="pingpong",
        steps=[
            T.Step(T.KIND_AGENT, "step a", id="a"),
            T.Step(T.KIND_AGENT, "step b", id="b"),
        ],
        edges=[T.Edge("a", "loop", "b"), T.Edge("b", "loop", "a")],
    )
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert not out.ok
    # a routes to b, b routes back to a → caught on the revisit. Exactly two
    # steps run (pre-fix this looped forever / exhausted the scripted turns).
    assert len(out.steps) == 2
    assert out.steps[0].carry.strip() == "a"
    assert out.steps[1].carry.strip() == "b"


# --- FIX 3: resume re-asserts the untrusted-carry gate ----------------------- #

def test_resume_reasserts_untrusted_carry_gate(tmp_path, monkeypatch):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(
        agent, ('frontend changes\nTASK+ {"branch": "frontend", "continue": true}', None)
    )
    st = _state(tmp_path, agent=agent)
    xli = tmp_path / ".xlii"
    _write_classify_toml(xli)
    p = T.load_pipeline(xli, "classify")
    run = T.new_run(p)

    prompts: list[str] = []

    def _deny(_prompt: str) -> str:
        prompts.append(_prompt)
        return "n"

    monkeypatch.setattr("xlii.tools._confirm", _deny)

    # Original run: agent routes to the fe-tests shell arm; the untrusted-carry
    # gate fires even under yes=True; the user denies → blocked at the arm.
    first = T.run_pipeline(
        p, st.as_context_dict(), confirm_shell=True, yes=True, run=run, xli_dir=xli
    )
    assert not first.ok
    assert run.cursor == 1
    assert run.untrusted_pending is True
    assert len(prompts) == 1

    saved = T.latest_run(xli, statuses={"failed"})
    assert saved is not None
    assert saved.untrusted_pending is True  # survived the save/load round-trip

    # Resume: the gate MUST re-fire, not silently execute the arm.
    resumed = T.run_pipeline(
        saved.pipeline(),
        st.as_context_dict(),
        carry0=saved.carry,
        start_index=saved.cursor,
        confirm_shell=True,
        yes=True,
        run=saved,
        xli_dir=xli,
    )
    assert not resumed.ok
    assert resumed.steps[0].blocked
    assert len(prompts) == 2  # re-gated on resume
