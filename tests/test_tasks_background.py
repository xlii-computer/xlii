"""Vector J — `/tasks run --background`: the SAME run_pipeline call wrapped as a
session-owned job (xlii.jobs), reusing F's on-disk TaskRun as the durable per-job
state. Verifies the flag parse, non-blocking dispatch, completion + notify, the
TaskRun persistence, and the fail-closed gate (a detached run never hangs)."""

from __future__ import annotations

from pathlib import Path

import pytest

import xlii.jobs as J
from helpers import FakeConsole, make_agent, make_cfg
from xlii import tasks as T
from xlii.repl_cmds import register_all
from xlii.repl_cmds.jobs import _jobs_handler
from xlii.repl_cmds.tasks import _do_run, _parse_run_flags, _tasks_handler
from xlii.repl_state import REPLState

register_all()


@pytest.fixture(autouse=True)
def _reset_listener():
    J.set_job_listener(None)
    yield
    J.set_job_listener(None)


def _state(tmp_path: Path, scope: str = "code") -> REPLState:
    agent = make_agent(tmp_path, cfg=make_cfg(max_parallel_workers=3))
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    st = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                   cfg=agent.cfg, pool=agent.pool)
    st.command_scope = scope
    return st


# --------------------------------------------------------------------------- #
#  Flag parse
# --------------------------------------------------------------------------- #

def test_background_flag_parsed():
    opts, target = _parse_run_flags("--background --yes 'printf hi'")
    assert opts["background"] is True and opts["yes"] is True
    assert target == "printf hi"


def test_bg_alias_parsed():
    opts, target = _parse_run_flags("--bg printf hi")
    assert opts["background"] is True
    assert target == "printf hi"


def test_no_background_flag_is_false_by_default():
    opts, _ = _parse_run_flags("--yes 'printf hi'")
    assert opts["background"] is False


# --------------------------------------------------------------------------- #
#  Dispatch + completion
# --------------------------------------------------------------------------- #

def test_background_dispatches_job_and_returns_immediately(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks run --background --yes 'printf hi |> tr a-z A-Z'",
                   st.as_context_dict())
    # The handler announced a job and did NOT run the pipe synchronously.
    assert "background job" in st.console.text
    assert "pipeline complete" not in st.console.text
    reg = J.get_registry(st)
    assert len(reg.jobs()) == 1
    reg.shutdown(wait=True)


def test_background_pipeline_runs_to_completion(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks run --background --yes 'printf hi |> tr a-z A-Z'",
                   st.as_context_dict())
    reg = J.get_registry(st)
    jid = reg.jobs()[0].job_id
    job = reg.wait(jid, timeout=10)
    assert job.status == J.DONE
    outcome = job.result                      # the PipelineOutcome
    assert outcome.ok and outcome.carry == "HI"
    assert job.kind == J.KIND_TASK and job.name == "inline"
    reg.shutdown(wait=True)


def test_background_reuses_taskrun_persistence(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks run --background --yes 'printf done'", st.as_context_dict())
    reg = J.get_registry(st)
    reg.wait(reg.jobs()[0].job_id, timeout=10)
    # F's on-disk TaskRun is the durable per-job state — /tasks status reads it.
    run = T.latest_run(st.project.xli_dir)
    assert run is not None and run.status == "done"
    st.console.lines.clear()
    _tasks_handler("/tasks status", st.as_context_dict())
    assert "done" in st.console.text
    reg.shutdown(wait=True)


def test_background_notifies_on_completion(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks run --background --yes 'printf hi'", st.as_context_dict())
    reg = J.get_registry(st)
    jid = reg.jobs()[0].job_id
    reg.wait(jid, timeout=10)
    assert "done" in st.console.text and jid in st.console.text
    reg.shutdown(wait=True)


def test_background_job_appears_in_jobs_list(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks run --background --yes 'printf hi'", st.as_context_dict())
    reg = J.get_registry(st)
    jid = reg.jobs()[0].job_id
    reg.wait(jid, timeout=10)
    st.console.lines.clear()
    _jobs_handler("/jobs", st.as_context_dict())
    out = st.console.text
    assert jid in out and "task" in out and "done" in out
    reg.shutdown(wait=True)


# --------------------------------------------------------------------------- #
#  Safety — a detached run never hangs on a gate (fail-closed)
# --------------------------------------------------------------------------- #

def test_background_shell_gate_fails_closed_without_yes(tmp_path):
    st = _state(tmp_path)
    # No --yes → the ordinary shell gate is active. On the job thread the confirm
    # fails closed (returns 'n'), so the step is BLOCKED and the pipe stops — it
    # must never block a detached thread on input().
    _tasks_handler("/tasks run --background 'printf hi'", st.as_context_dict())
    reg = J.get_registry(st)
    jid = reg.jobs()[0].job_id
    job = reg.wait(jid, timeout=10)
    assert job.status == J.DONE          # run_pipeline returned (didn't raise/hang)
    outcome = job.result
    assert outcome.ok is False
    assert outcome.steps and outcome.steps[0].blocked
    # The on-disk run records the stop, so /tasks resume can finish it in the fg.
    run = T.latest_run(st.project.xli_dir)
    assert run is not None and run.status == "failed"
    reg.shutdown(wait=True)


def test_background_with_yes_runs_shell_without_prompting(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks run --background --yes 'printf approved'", st.as_context_dict())
    reg = J.get_registry(st)
    job = reg.wait(reg.jobs()[0].job_id, timeout=10)
    assert job.status == J.DONE and job.result.ok and job.result.carry == "approved"
    reg.shutdown(wait=True)


def test_background_needs_live_session(tmp_path):
    # _dispatch_background guards a stateless ctx (no job_registry slot).
    from types import SimpleNamespace

    console = FakeConsole()
    ctx = {"console": console, "state": SimpleNamespace(), "command_scope": "code"}
    # Drive _do_run directly with a stateless ctx so get_registry returns None.
    _do_run("--background --yes 'printf hi'", ctx)
    assert "needs a live session" in console.text
