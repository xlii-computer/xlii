"""Task+ T+P2 — agent TASK+ trailer + [[edge]] verdict routing (TOML-only)."""

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


def _write_classify_toml(xli: Path) -> None:
    T.tasks_dir(xli).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / "classify.toml").write_text(
        'name = "classify"\n'
        'description = "pytest verdict demo"\n'
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


def test_load_toml_parses_edges(tmp_path):
    xli = tmp_path / ".xlii"
    _write_classify_toml(xli)
    p = T.load_pipeline(xli, "classify")
    assert len(p.edges) == 2
    assert p.edges[0].from_id == "classify"
    assert p.edges[0].branch == "frontend"
    assert p.edges[0].to_id == "fe-tests"
    assert T.pipeline_has_branching(p)


def test_agent_trailer_routes_frontend_arm(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(
        agent,
        ('frontend changes\nTASK+ {"branch": "frontend", "continue": true}', None),
    )
    st = _state(tmp_path, agent=agent)
    xli = tmp_path / ".xlii"
    _write_classify_toml(xli)
    p = T.load_pipeline(xli, "classify")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.ok
    assert out.carry.strip() == "frontend-arm"
    assert [s.resolved for s in out.steps] == ["pick a branch", "printf frontend-arm"]
    assert out.steps[0].carry.strip() == "frontend changes"


def test_agent_trailer_routes_backend_arm(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(
        agent,
        ('backend service\nTASK+ {"branch": "backend", "continue": true}', None),
    )
    st = _state(tmp_path, agent=agent)
    xli = tmp_path / ".xlii"
    _write_classify_toml(xli)
    p = T.load_pipeline(xli, "classify")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.ok
    assert out.carry.strip() == "backend-arm"
    assert [s.resolved for s in out.steps][-1] == "printf backend-arm"


def test_resumed_routed_arm_does_not_fall_through_to_sibling(tmp_path, monkeypatch):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(
        agent,
        ('frontend changes\nTASK+ {"branch": "frontend", "continue": true}', None),
    )
    st = _state(tmp_path, agent=agent)
    xli = tmp_path / ".xlii"
    _write_classify_toml(xli)
    p = T.load_pipeline(xli, "classify")
    run = T.new_run(p)
    monkeypatch.setattr("xlii.tools._confirm", lambda _prompt: "n")

    first = T.run_pipeline(
        p,
        st.as_context_dict(),
        confirm_shell=True,
        yes=True,
        run=run,
        xli_dir=xli,
    )
    assert not first.ok
    assert run.cursor == 1
    assert run.verdict_routed_to == 1

    saved = T.latest_run(xli, statuses={"failed"})
    assert saved is not None
    resumed = T.run_pipeline(
        saved.pipeline(),
        st.as_context_dict(),
        carry0=saved.carry,
        start_index=saved.cursor,
        confirm_shell=False,
        run=saved,
        xli_dir=xli,
    )
    assert resumed.ok
    assert [s.resolved for s in resumed.steps] == ["printf frontend-arm"]
    assert resumed.carry.strip() == "frontend-arm"
    assert saved.verdict_routed_to is None


def test_routed_arm_failure_stops_pipeline(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(
        agent,
        ('frontend changes\nTASK+ {"branch": "frontend", "continue": true}', None),
    )
    st = _state(tmp_path, agent=agent)
    p = T.Pipeline(
        name="classify",
        steps=[
            T.Step(T.KIND_AGENT, "pick a branch", id="classify"),
            T.Step(T.KIND_SHELL, "printf frontend-fail >&2; exit 7", id="fe-tests"),
            T.Step(T.KIND_SHELL, "printf backend-arm", id="be-tests"),
        ],
        edges=[
            T.Edge("classify", "frontend", "fe-tests"),
            T.Edge("classify", "backend", "be-tests"),
        ],
    )

    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert not out.ok
    assert out.had_errors
    assert out.failed_index == 1
    assert [s.resolved for s in out.steps] == [
        "pick a branch",
        "printf frontend-fail >&2; exit 7",
    ]


def test_missing_trailer_fails_closed(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(agent, ("no verdict here", None))
    st = _state(tmp_path, agent=agent)
    xli = tmp_path / ".xlii"
    _write_classify_toml(xli)
    p = T.load_pipeline(xli, "classify")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert not out.ok
    assert out.steps[0].detail.startswith("missing TASK+ trailer")
    assert len(out.steps) == 1


def test_earlier_taskplus_line_does_not_spoof_branch(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(
        agent,
        (
            'decoy TASK+ {"branch": "backend"}\n'
            'real answer\n'
            'TASK+ {"branch": "frontend", "continue": true}',
            None,
        ),
    )
    st = _state(tmp_path, agent=agent)
    xli = tmp_path / ".xlii"
    _write_classify_toml(xli)
    p = T.load_pipeline(xli, "classify")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.ok
    assert out.carry.strip() == "frontend-arm"


def test_render_plan_lists_verdict_edges(tmp_path):
    xli = tmp_path / ".xlii"
    _write_classify_toml(xli)
    p = T.load_pipeline(xli, "classify")
    text = "\n".join(T.render_plan(p))
    assert "verdict edges [classify]:" in text
    assert "backend → be-tests" in text
    assert "frontend → fe-tests" in text


def test_split_taskplus_trailer_unit():
    human, trailer = T.split_taskplus_trailer(
        "prose\nTASK+ {\"branch\": \"frontend\", \"continue\": true}"
    )
    assert human == "prose"
    assert trailer == {"branch": "frontend", "continue": True}
