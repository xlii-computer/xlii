"""Task+ T+P1 — per-step on_success/on_failure gates (shell rc, TOML-only)."""

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


def _write_gate_toml(xli: Path) -> None:
    T.tasks_dir(xli).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / "gate.toml").write_text(
        'name = "gate"\n'
        'description = "pytest gate demo"\n'
        "[[step]]\n"
        'id = "check"\n'
        'run = "false"\n'
        'on_failure = "recover"\n'
        "[[step]]\n"
        'id = "recover"\n'
        'run = "printf recovered"\n'
    )


def test_load_toml_parses_step_ids_and_gates(tmp_path):
    xli = tmp_path / ".xlii"
    _write_gate_toml(xli)
    p = T.load_pipeline(xli, "gate")
    assert p.steps[0].id == "check"
    assert p.steps[0].on_failure == "recover"
    assert T.step_index_maps(p)[0]["recover"] == 1


def test_shell_failure_routes_to_on_failure_arm(tmp_path):
    st = _state(tmp_path)
    xli = tmp_path / ".xlii"
    _write_gate_toml(xli)
    p = T.load_pipeline(xli, "gate")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.ok
    assert out.carry.strip() == "recovered"
    assert [s.resolved for s in out.steps] == ["false", "printf recovered"]


def test_render_plan_names_both_arms(tmp_path):
    xli = tmp_path / ".xlii"
    _write_gate_toml(xli)
    p = T.load_pipeline(xli, "gate")
    text = "\n".join(T.render_plan(p))
    assert "on success → (next)" in text
    assert "on failure → recover" in text


def test_inline_pipe_stays_linear_without_gates():
    p = T.parse_inline("printf a |> printf b")
    assert not T.pipeline_has_branching(p)
    assert all(not s.on_success and not s.on_failure for s in p.steps)


def test_linear_pipe_unchanged_when_no_gates(tmp_path):
    st = _state(tmp_path)
    p = T.parse_inline("printf hi |> tr a-z A-Z")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.ok and out.carry == "HI"
    assert len(out.steps) == 2
