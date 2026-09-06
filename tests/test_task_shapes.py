"""Task+ shape skeletons load, clone stock, /tasks new --shape/--clone."""

from __future__ import annotations

from helpers import FakeConsole, make_agent, make_cfg
from xlii import tasks as T
from xlii.repl_state import REPLState
from xlii.task_shapes import SHAPES, clone_stock, render_shape, write_shaped


def _state(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    return REPLState(
        console=FakeConsole(), agent=agent, project=agent.project,
        cfg=agent.cfg, pool=agent.pool,
    )


def test_every_shape_loads(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    for shape in SHAPES:
        name = f"s-{shape}"
        write_shaped(xli, name, shape)
        p = T.load_pipeline(xli, name)
        assert p.name == name
        assert p.steps


def test_verdict_shape_has_edges():
    text = render_shape("v", "verdict", branches="clean,dirty")
    assert "TASK+ {\"branch\": \"clean\"}" in text
    assert "[[edge]]" in text
    assert 'to = "dirty"' in text


def test_unknown_shape_raises():
    import pytest

    with pytest.raises(T.TaskParseError):
        render_shape("x", "nope")


def test_clone_stock_rewrites_name(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    path = clone_stock(xli, "mine", "git-triage")
    p = T.load_pipeline(xli, "mine")
    assert p.name == "mine"
    assert any(e.from_id == "classify" for e in p.edges)
    assert "name = \"mine\"" in path.read_text()


def test_tasks_new_shape_writes(tmp_path):
    from xlii.repl_cmds.tasks import _tasks_handler

    st = _state(tmp_path)
    _tasks_handler("/tasks new gate --shape verdict --branches yes,no", st.as_context_dict())
    assert "scaffolded" in st.console.text
    p = T.load_pipeline(st.project.xli_dir, "gate")
    assert p.edges


def test_tasks_new_clone(tmp_path):
    from xlii.repl_cmds.tasks import _tasks_handler

    st = _state(tmp_path)
    _tasks_handler("/tasks new mine --clone tests-guard", st.as_context_dict())
    assert "cloned" in st.console.text
    p = T.load_pipeline(st.project.xli_dir, "mine")
    assert any(s.on_failure == "triage" for s in p.steps)


def test_tasks_new_refuses_mixed_modes(tmp_path):
    from xlii.repl_cmds.tasks import _tasks_handler

    st = _state(tmp_path)
    _tasks_handler("/tasks new x --shape linear --clone git-triage", st.as_context_dict())
    assert "only one of" in st.console.text
