"""Parameterized stock tasks — bundled examples that exercise task-args P1."""

from __future__ import annotations

from pathlib import Path

import pytest

from xlii import tasks as T

PARAM_STOCK = {
    "grep-explain", "explain-file", "diff-review", "bump-note",
    # app-serving S3 ops set (decision #13): the appbox verbs ship as tasks.
    "publish", "redeploy", "unpublish", "deploy-status",
    "new-folder",
}


def _xli(tmp_path: Path) -> Path:
    d = tmp_path / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_param_stock_present_and_valid(tmp_path):
    names = {p.stem for p in T.stock_tasks_dir().glob("*.toml")}
    assert PARAM_STOCK <= names
    xli = _xli(tmp_path)
    for name in PARAM_STOCK:
        p = T.load_pipeline(xli, name)
        assert p.name == name and p.params  # each declares params


def test_grep_explain_params_and_injection_safe(tmp_path):
    p = T.load_pipeline(_xli(tmp_path), "grep-explain")
    by = {x.name: x for x in p.params}
    assert by["query"].required
    assert by["scope"].default == "."
    assert T.bind_task_args(p.params, ["needle"]) == {"query": "needle", "scope": "."}
    resolved = T.substitute(
        p.steps[0].body,
        {"prev": "", **T.bind_task_args(p.params, ["a; rm -rf /"])},
        kind=T.KIND_SHELL,
    )
    assert "'a; rm -rf /'" in resolved  # metachars quoted into a single token


def test_explain_file_requires_path(tmp_path):
    p = T.load_pipeline(_xli(tmp_path), "explain-file")
    with pytest.raises(T.TaskParseError):
        T.bind_task_args(p.params, [])
    assert T.bind_task_args(p.params, ["README.md"]) == {"path": "README.md"}


def test_diff_review_defaults_base(tmp_path):
    p = T.load_pipeline(_xli(tmp_path), "diff-review")
    assert T.bind_task_args(p.params, []) == {"base": "HEAD~1"}
    assert T.bind_task_args(p.params, ["main"]) == {"base": "main"}


def test_bump_note_enum(tmp_path):
    p = T.load_pipeline(_xli(tmp_path), "bump-note")
    assert T.bind_task_args(p.params, []) == {"level": "patch"}
    assert T.bind_task_args(p.params, ["minor"]) == {"level": "minor"}
    with pytest.raises(T.TaskParseError):
        T.bind_task_args(p.params, ["banana"])


def test_param_stock_list_as_stock(tmp_path):
    entries = dict(T.list_pipeline_entries(_xli(tmp_path)))
    for name in PARAM_STOCK:
        assert entries.get(name) == "stock"


def test_render_plan_shows_signature(tmp_path):
    plan = "\n".join(T.render_plan(T.load_pipeline(_xli(tmp_path), "grep-explain")))
    assert "params:" in plan and "{{query}}" in plan and "required" in plan
