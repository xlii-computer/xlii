"""Task+ T+P3 — split/join fan-out/fan-in (concurrent shell branches, TOML-only)."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from helpers import FakeConsole, make_agent, make_cfg, script_iterations
from xlii import tasks as T
from xlii.repl_state import REPLState


def _state(tmp_path: Path, *, agent=None) -> REPLState:
    agent = agent or make_agent(tmp_path, cfg=make_cfg())
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    return REPLState(
        console=FakeConsole(), agent=agent, project=agent.project,
        cfg=agent.cfg, pool=agent.pool,
    )


def _split_pipe(policy="all", bodies=("printf AAA", "printf BBB", "printf CCC"),
                sumbody="cat") -> T.Pipeline:
    steps = [T.Step(T.KIND_SHELL, "", id="fan", split=["a", "b", "c"], join="sum", policy=policy)]
    for sid, body in zip(("a", "b", "c"), bodies):
        steps.append(T.Step(T.KIND_SHELL, body, id=sid))
    steps.append(T.Step(T.KIND_SHELL, sumbody, id="sum"))
    return T.Pipeline(name="gate", steps=steps)


# --- parsing + validation --------------------------------------------------- #

def test_split_toml_parses(tmp_path):
    xli = tmp_path / ".xlii"
    T.tasks_dir(xli).mkdir(parents=True)
    (T.tasks_dir(xli) / "g.toml").write_text(
        'name="g"\n'
        '[[step]]\nid="fan"\nsplit=["a","b"]\njoin="sum"\npolicy="any"\n'
        '[[step]]\nid="a"\nrun="printf A"\n'
        '[[step]]\nid="b"\nrun="printf B"\n'
        '[[step]]\nid="sum"\nrun="cat"\n'
    )
    p = T.load_pipeline(xli, "g")
    assert p.steps[0].is_split()
    assert p.steps[0].split == ["a", "b"]
    assert p.steps[0].join == "sum" and p.steps[0].policy == "any"
    assert T.pipeline_has_branching(p)


@pytest.mark.parametrize("body,msg", [
    ('[[step]]\nid="fan"\nsplit=["x"]\njoin="sum"\n[[step]]\nid="sum"\nrun="cat"\n',
     "not a step id"),
    ('[[step]]\nid="fan"\nsplit=["a"]\njoin="nope"\n[[step]]\nid="a"\nrun="x"\n[[step]]\nid="sum"\nrun="cat"\n',
     "join targets unknown"),
    ('[[step]]\nid="fan"\nsplit=["a"]\njoin="sum"\npolicy="weird"\n[[step]]\nid="a"\nrun="x"\n[[step]]\nid="sum"\nrun="cat"\n',
     "policy"),
    ('[[step]]\nid="fan"\nsplit=["a"]\njoin="sum"\n[[step]]\nid="a"\nask="hi"\n[[step]]\nid="sum"\nrun="cat"\n',
     "must be a shell step"),
    ('[[step]]\nid="fan"\nsplit=["sum"]\njoin="sum"\n[[step]]\nid="sum"\nrun="cat"\n',
     "cannot be the split step or its join"),
    ('[[step]]\nid="fan"\nsplit=["a"]\n[[step]]\nid="a"\nrun="x"\n[[step]]\nid="sum"\nrun="cat"\n',
     "needs a join target"),
])
def test_split_validation_rejects(tmp_path, body, msg):
    xli = tmp_path / ".xlii"
    T.tasks_dir(xli).mkdir(parents=True)
    (T.tasks_dir(xli) / "g.toml").write_text('name="g"\n' + body)
    with pytest.raises(T.TaskParseError) as e:
        T.load_pipeline(xli, "g")
    assert msg in str(e.value)


# --- execution -------------------------------------------------------------- #

def test_split_runs_branches_concurrently(tmp_path):
    st = _state(tmp_path)
    p = _split_pipe(bodies=("sleep 0.3; printf A", "sleep 0.3; printf B", "sleep 0.3; printf C"))
    t0 = time.monotonic()
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    dt = time.monotonic() - t0
    assert out.ok
    assert dt < 0.7, f"branches did not run concurrently (wall {dt:.2f}s)"


def test_split_headers_carry(tmp_path):
    st = _state(tmp_path)
    out = T.run_pipeline(_split_pipe(sumbody="cat"), st.as_context_dict(), confirm_shell=False)
    assert "### arm a (ok)" in out.carry and "AAA" in out.carry
    assert "### arm c (ok)" in out.carry and "CCC" in out.carry


def test_split_branch_id_binding(tmp_path):
    st = _state(tmp_path)
    out = T.run_pipeline(_split_pipe(sumbody="printf '%s' {{arm.b}}"),
                         st.as_context_dict(), confirm_shell=False)
    assert out.carry == "BBB"


def test_policy_all_fails_closed_by_default(tmp_path):
    st = _state(tmp_path)
    out = T.run_pipeline(_split_pipe(bodies=("printf A", "exit 7", "printf C")),
                         st.as_context_dict(), confirm_shell=False)
    assert not out.ok and out.had_errors
    assert not any(s.index == 5 for s in out.steps)  # join (sum) NOT reached


def test_policy_all_keep_going_reaches_join(tmp_path):
    st = _state(tmp_path)
    out = T.run_pipeline(_split_pipe(bodies=("printf A", "exit 7", "printf C"), sumbody="cat"),
                         st.as_context_dict(), confirm_shell=False, keep_going=True)
    assert any(s.index == 5 for s in out.steps)  # join reached → triage runs
    assert out.had_errors


def test_policy_any_succeeds_if_one_ok(tmp_path):
    st = _state(tmp_path)
    out = T.run_pipeline(_split_pipe("any", bodies=("exit 1", "printf B", "exit 1")),
                         st.as_context_dict(), confirm_shell=False)
    assert out.ok


def test_policy_first_ok_carries_first_success(tmp_path):
    st = _state(tmp_path)
    out = T.run_pipeline(
        _split_pipe("first_ok", bodies=("exit 1", "printf BBB", "sleep 0.2; printf CCC"),
                    sumbody="cat"),
        st.as_context_dict(), confirm_shell=False,
    )
    assert out.carry == "BBB"


def test_branches_run_exactly_once_via_split(tmp_path):
    st = _state(tmp_path)
    out = T.run_pipeline(_split_pipe(sumbody="cat"), st.as_context_dict(), confirm_shell=False)
    idxs = [s.index for s in out.steps]
    assert idxs.count(2) == 1 and idxs.count(3) == 1 and idxs.count(4) == 1


def test_split_gates_on_untrusted_carry(tmp_path, monkeypatch):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(agent, ("some analysis", None))
    st = _state(tmp_path, agent=agent)
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_AGENT, "analyze", id="an"),
        T.Step(T.KIND_SHELL, "", id="fan", split=["a", "b"], join="sum", policy="all"),
        T.Step(T.KIND_SHELL, "printf A", id="a"),
        T.Step(T.KIND_SHELL, "printf B", id="b"),
        T.Step(T.KIND_SHELL, "cat", id="sum"),
    ])
    prompts: list[str] = []
    monkeypatch.setattr("xlii.tools._confirm", lambda pr: (prompts.append(pr), "n")[1])
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=True, yes=True)
    # Under yes=True the only gate that can still fire is the untrusted-carry one,
    # so a prompt at all proves the split re-gated on the agent-tainted carry.
    assert not out.ok
    assert prompts


# --- persistence + resume (D6 node-id + per-arm carries) -------------------- #

def test_split_carries_persisted_on_stop(tmp_path):
    st = _state(tmp_path)
    xli = tmp_path / ".xlii"
    p = _split_pipe(sumbody="exit 1")  # join step fails → run stops, checkpoint
    run = T.new_run(p)
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False, run=run, xli_dir=xli)
    assert not out.ok
    saved = T.latest_run(xli, statuses={"failed"})
    assert saved is not None
    assert saved.split_carries == {"a": "AAA", "b": "BBB", "c": "CCC"}


def test_resume_rebinds_split_carries(tmp_path):
    st = _state(tmp_path)
    p = _split_pipe(sumbody="printf '%s' {{arm.a}}")
    run = T.new_run(p)
    run.split_carries = {"a": "AAA", "b": "BBB", "c": "CCC"}
    run.cursor = 4
    run.cursor_id = "sum"
    out = T.run_pipeline(p, st.as_context_dict(), start_index=4, confirm_shell=False, run=run)
    assert out.ok
    assert out.carry == "AAA"


def test_render_plan_shows_split(tmp_path):
    plan = "\n".join(T.render_plan(_split_pipe("any")))
    assert "[split] a, b, c" in plan
    assert "policy any → join sum" in plan


# --- hardening from the adversarial review ---------------------------------- #

def test_non_utf8_branch_output_does_not_crash(tmp_path):
    st = _state(tmp_path)
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_SHELL, "", id="fan", split=["a", "b"], join="s", policy="any"),
        T.Step(T.KIND_SHELL, r"printf '\xff\xfe'", id="a"),  # non-UTF-8 bytes
        T.Step(T.KIND_SHELL, "printf OK", id="b"),
        T.Step(T.KIND_SHELL, "cat", id="s"),
    ])
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)  # must not raise
    assert "OK" in out.carry


def _validate(tmp_path, body):
    xli = tmp_path / ".xlii"
    T.tasks_dir(xli).mkdir(parents=True)
    (T.tasks_dir(xli) / "g.toml").write_text('name="g"\n' + body)
    return T.load_pipeline(xli, "g")


def test_branch_cannot_be_a_routing_target(tmp_path):
    body = ('[[step]]\nid="p"\nrun="probe"\non_failure="fix"\n'
            '[[step]]\nid="fix"\nrun="apply"\n[[step]]\nid="o"\nrun="o"\n'
            '[[step]]\nsplit=["fix","o"]\njoin="d"\n[[step]]\nid="d"\nrun="cat"\n')
    with pytest.raises(T.TaskParseError) as e:
        _validate(tmp_path, body)
    assert "routing target" in str(e.value)


def test_branch_id_cannot_collide_with_prev(tmp_path):
    body = ('[[step]]\nsplit=["prev","b"]\njoin="s"\n'
            '[[step]]\nid="prev"\nrun="x"\n[[step]]\nid="b"\nrun="y"\n[[step]]\nid="s"\nrun="cat"\n')
    with pytest.raises(T.TaskParseError) as e:
        _validate(tmp_path, body)
    assert "collides with the carry" in str(e.value)


def test_branch_id_cannot_collide_with_param(tmp_path):
    body = ('[params.region]\ndefault="x"\n'
            '[[step]]\nsplit=["region","b"]\njoin="s"\n'
            '[[step]]\nid="region"\nrun="x"\n[[step]]\nid="b"\nrun="y"\n[[step]]\nid="s"\nrun="cat"\n')
    with pytest.raises(T.TaskParseError):
        _validate(tmp_path, body)


def test_join_cannot_be_split_itself(tmp_path):
    body = ('[[step]]\nid="s"\nsplit=["a","b"]\njoin="s"\n'
            '[[step]]\nid="a"\nrun="x"\n[[step]]\nid="b"\nrun="y"\n')
    with pytest.raises(T.TaskParseError) as e:
        _validate(tmp_path, body)
    assert "cannot be the split step itself" in str(e.value)


def test_branch_cannot_declare_on_failure(tmp_path):
    body = ('[[step]]\nsplit=["a","b"]\njoin="s"\n'
            '[[step]]\nid="a"\nrun="x"\non_failure="s"\n'
            '[[step]]\nid="b"\nrun="y"\n[[step]]\nid="s"\nrun="cat"\n')
    with pytest.raises(T.TaskParseError) as e:
        _validate(tmp_path, body)
    assert "on_success/on_failure" in str(e.value)


def test_split_continue_on_error_reaches_join(tmp_path):
    st = _state(tmp_path)
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_SHELL, "", id="fan", split=["a", "b"], join="s",
               policy="all", continue_on_error=True),
        T.Step(T.KIND_SHELL, "exit 1", id="a"),
        T.Step(T.KIND_SHELL, "printf B", id="b"),
        T.Step(T.KIND_SHELL, "printf TRIAGED", id="s"),
    ])
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert any(s.resolved == "printf TRIAGED" for s in out.steps)  # join reached despite fail


def test_branch_authored_before_split_runs_once(tmp_path):
    st = _state(tmp_path)
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_SHELL, "printf A", id="a"),   # branch BEFORE its split
        T.Step(T.KIND_SHELL, "printf B", id="b"),
        T.Step(T.KIND_SHELL, "", id="fan", split=["a", "b"], join="fin", policy="all"),
        T.Step(T.KIND_SHELL, "printf DONE", id="fin"),
    ])
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert sum(1 for s in out.steps if s.resolved == "printf A") == 1  # not double-run
    assert any(s.resolved == "printf DONE" for s in out.steps)


def test_split_gate_shows_resolved_branch_command(tmp_path):
    st = _state(tmp_path)
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_SHELL, "", id="fan", split=["a"], join="s", policy="all"),
        T.Step(T.KIND_SHELL, "grep DANGER {{prev}}", id="a"),
        T.Step(T.KIND_SHELL, "cat", id="s"),
    ])
    emits: list[tuple[str, str]] = []
    T.run_pipeline(p, st.as_context_dict(), confirm_shell=False,
                   emit=lambda ev, **k: emits.append((ev, k.get("resolved", ""))))
    split_begin = next(r for ev, r in emits if ev == "step_begin" and r.startswith("split"))
    assert "grep DANGER" in split_begin


def test_policy_first_ok_cancels_remaining(tmp_path):
    st = _state(tmp_path)
    p = _split_pipe("first_ok", bodies=("exit 1", "printf BBB", "sleep 5; printf CCC"), sumbody="cat")
    t0 = time.monotonic()
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.ok and out.carry == "BBB"
    assert time.monotonic() - t0 < 2.5
    assert any(s.detail == "cancelled" for s in out.steps)


def test_join_digest_marks_failed_arms(tmp_path):
    st = _state(tmp_path)
    out = T.run_pipeline(
        _split_pipe(bodies=("printf A", "exit 7", "printf C"), sumbody="cat"),
        st.as_context_dict(), confirm_shell=False, keep_going=True,
    )
    assert "### arm b (fail)" in out.carry


def test_nested_split_refused_at_parse(tmp_path):
    body = (
        '[[step]]\nid="outer"\nsplit=["inner"]\njoin="done"\n'
        '[[step]]\nid="inner"\nsplit=["a"]\njoin="x"\n'
        '[[step]]\nid="a"\nrun="x"\n[[step]]\nid="x"\nrun="y"\n[[step]]\nid="done"\nrun="cat"\n'
    )
    with pytest.raises(T.TaskParseError) as e:
        _validate(tmp_path, body)
    assert "no nesting" in str(e.value)


def test_parallelism_cap_min_workers_and_arms(tmp_path, monkeypatch):
    import concurrent.futures
    seen: list[int] = []
    real_tpe = concurrent.futures.ThreadPoolExecutor

    class _Spy(real_tpe):
        def __init__(self, max_workers=None, **kw):
            seen.append(max_workers)
            super().__init__(max_workers=max_workers, **kw)

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", _Spy)
    st = _state(tmp_path)
    st.cfg.max_parallel_workers = 8
    T.run_pipeline(_split_pipe(), st.as_context_dict(), confirm_shell=False)
    assert seen and seen[0] == 3


def test_resume_mid_split_restores_node_id(tmp_path):
    st = _state(tmp_path)
    p = _split_pipe(sumbody="printf '%s' {{arm.a}}")
    run = T.new_run(p)
    run.split_carries = {"a": "AAA", "b": "BBB", "c": "CCC"}
    run.cursor = 99
    run.cursor_id = "sum"
    out = T.run_pipeline(p, st.as_context_dict(), start_index=99, confirm_shell=False, run=run)
    assert out.ok and out.carry == "AAA"
