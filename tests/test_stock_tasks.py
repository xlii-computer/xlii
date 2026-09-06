"""Track E0 — bundled stock_tasks overlay into tasks://."""

from __future__ import annotations

from pathlib import Path

from xlii import tasks as T
from xlii.panes.tasks import TasksPane


def _xli(tmp_path: Path) -> Path:
    d = tmp_path / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_stock_tasks_dir_has_showcase_pipelines():
    names = {p.stem for p in T.stock_tasks_dir().glob("*.toml")}
    assert {"diff-summary", "tree-brief", "echo-hello"} <= names


def test_list_pipeline_entries_merges_stock_when_project_empty(tmp_path):
    xli = _xli(tmp_path)
    entries = T.list_pipeline_entries(xli)
    assert entries
    assert all(origin == "stock" for _n, origin in entries)
    assert "echo-hello" in [n for n, _o in entries]


def test_project_wins_on_name_clash(tmp_path):
    xli = _xli(tmp_path)
    T.tasks_dir(xli).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / "echo-hello.toml").write_text(
        'name = "echo-hello"\n[[step]]\nrun = "printf project"\n'
    )
    entries = dict(T.list_pipeline_entries(xli))
    assert entries["echo-hello"] == "project"
    p = T.load_pipeline(xli, "echo-hello")
    assert p.steps[0].body == "printf project"


def test_load_stock_pipeline_without_project_copy(tmp_path):
    xli = _xli(tmp_path)
    p = T.load_pipeline(xli, "echo-hello")
    assert p.name == "echo-hello"
    assert p.steps[0].kind == T.KIND_SHELL


def test_tasks_pane_badges_stock_rows(tmp_path, monkeypatch):
    xli = _xli(tmp_path)
    monkeypatch.setattr("xlii.active_session.active_xli_dir", lambda: xli)
    pane = TasksPane("tasks://")
    rendered = pane.render()
    assert rendered.rows
    stock_rows = [r.text for r in rendered.rows if "stock" in r.text]
    assert stock_rows
