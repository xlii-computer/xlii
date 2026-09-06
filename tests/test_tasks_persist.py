"""Vector F — /tasks saved pipelines (TOML) + TaskRun persistence/resume."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from helpers import FakeConsole, make_agent, make_cfg
from xlii import tasks as T
from xlii.repl_state import REPLState


def _xli(tmp_path: Path) -> Path:
    d = tmp_path / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_scaffold_then_load(tmp_path):
    xli = _xli(tmp_path)
    path = T.scaffold_pipeline(xli, "demo")
    assert path.exists()
    p = T.load_pipeline(xli, "demo")
    assert p.name == "demo"
    assert [s.kind for s in p.steps] == [T.KIND_SHELL]  # only the uncommented step


def test_scaffold_refuses_overwrite(tmp_path):
    xli = _xli(tmp_path)
    T.scaffold_pipeline(xli, "demo")
    with pytest.raises(T.TaskError):
        T.scaffold_pipeline(xli, "demo")


def test_load_pipeline_all_kinds(tmp_path):
    xli = _xli(tmp_path)
    (T.tasks_dir(xli)).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / "rn.toml").write_text(
        'name = "rn"\n'
        'description = "diff to post"\n'
        "[[step]]\nrun = \"git diff --stat\"\n"
        "[[step]]\nask = \"summarize\"\n"
        "[[step]]\nslash = \"/post-on-x --draft {{prev}}\"\n"
    )
    p = T.load_pipeline(xli, "rn")
    assert [s.kind for s in p.steps] == [T.KIND_SHELL, T.KIND_AGENT, T.KIND_SLASH]
    assert p.description == "diff to post"
    assert p.steps[1].body == "summarize"


def test_load_pipeline_requires_exactly_one_key(tmp_path):
    xli = _xli(tmp_path)
    T.tasks_dir(xli).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / "bad.toml").write_text(
        'name = "bad"\n[[step]]\nrun = "x"\nask = "y"\n'
    )
    with pytest.raises(T.TaskParseError):
        T.load_pipeline(xli, "bad")


def test_load_pipeline_slash_must_start_with_slash(tmp_path):
    xli = _xli(tmp_path)
    T.tasks_dir(xli).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / "bad.toml").write_text('name="b"\n[[step]]\nslash = "doc add x"\n')
    with pytest.raises(T.TaskParseError):
        T.load_pipeline(xli, "bad")


def test_missing_pipeline_raises_not_found(tmp_path):
    with pytest.raises(T.TaskNotFound):
        T.load_pipeline(_xli(tmp_path), "nope")


def test_list_pipelines(tmp_path):
    xli = _xli(tmp_path)
    T.scaffold_pipeline(xli, "b")
    T.scaffold_pipeline(xli, "a")
    assert T.list_pipelines(xli)[:2] == ["a", "b"]


def test_task_run_save_and_latest_roundtrip(tmp_path):
    xli = _xli(tmp_path)
    p = T.parse_inline("printf a |> printf b")
    run = T.new_run(p, carry0="seed", keep_going=True)
    run.cursor = 1
    run.carry = "a"
    T.save_run(xli, run)
    got = T.latest_run(xli)
    assert got is not None
    assert got.run_id == run.run_id
    assert got.cursor == 1
    assert got.carry == "a"
    assert got.keep_going is True
    assert [s["kind"] for s in got.steps] == [T.KIND_SHELL, T.KIND_SHELL]


def test_latest_run_filters_by_status(tmp_path):
    xli = _xli(tmp_path)
    done = T.new_run(T.parse_inline("printf a"))
    done.status = "done"
    T.save_run(xli, done)
    assert T.latest_run(xli, statuses={"failed", "active"}) is None
    assert T.latest_run(xli).status == "done"


def _state(tmp_path, scope="code"):
    agent = make_agent(tmp_path, cfg=make_cfg())
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    st = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                   cfg=agent.cfg, pool=agent.pool)
    st.command_scope = scope
    return st


def test_run_persists_checkpoint_on_failure_for_resume(tmp_path):
    xli = _xli(tmp_path)
    st = _state(tmp_path)
    p = T.parse_inline("printf a |> false |> printf c")
    run = T.new_run(p)
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False, run=run, xli_dir=xli)
    assert not out.ok and out.failed_index == 1
    saved = T.latest_run(xli)
    assert saved.status == "failed"
    assert saved.cursor == 1          # the failed step — resume re-enters here
    assert saved.carry == "a"         # carry from the last good step


def test_resume_continues_from_cursor(tmp_path):
    _xli(tmp_path)
    st = _state(tmp_path)
    p = T.parse_inline("printf a |> printf b |> printf c")
    # Simulate a run interrupted after step 1 (cursor=1, carry='a').
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False, start_index=1, carry0="a")
    # Only steps 2 and 3 ran.
    assert [s.index for s in out.steps] == [2, 3]
    assert out.carry == "c"


def test_prune_runs_keeps_recent(tmp_path):
    xli = _xli(tmp_path)
    for i in range(5):
        r = T.new_run(T.parse_inline("printf x"))
        r.run_id = f"id-{i:03d}"
        T.save_run(xli, r)
    removed = T.prune_runs(xli, keep=2)
    assert removed == 3
    assert len(T.iter_runs(xli)) == 2


def test_prune_runs_ignores_stat_errors(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    for i in range(3):
        r = T.new_run(T.parse_inline("printf x"))
        r.run_id = f"id-{i:03d}"
        T.save_run(xli, r)
    victim = next(T.runs_dir(xli).glob("*.json"))
    real_stat = Path.stat

    def _flaky_stat(self: Path, *args, **kwargs):
        if self == victim:
            raise OSError("gone")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _flaky_stat)
    assert T.prune_runs(xli, keep=1) == 1


def test_resolve_tasks_defaults_reads_cfg_then_project(tmp_path):
    cfg = SimpleNamespace(tasks_defaults={"keep_going": True, "carry_max_chars": 99})
    project = SimpleNamespace(tasks_defaults={"carry_max_chars": 5})
    ctx = {"cfg": cfg, "project": project}
    d = T.resolve_tasks_defaults(ctx)
    assert d["keep_going"] is True       # from cfg
    assert d["carry_max_chars"] == 5     # project overrides cfg
    assert d["confirm_shell"] is True    # untouched default


def test_resolve_tasks_defaults_without_config():
    d = T.resolve_tasks_defaults({})
    assert d == {
        "keep_going": False,
        "confirm_shell": True,
        "carry_max_chars": T.DEFAULT_CARRY_MAX_CHARS,
        "agent_step_model": None,
    }
