"""Stock system task ``new-folder`` — create, init, switch."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from xlii import tasks as T


def _xli(tmp_path: Path) -> Path:
    d = tmp_path / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_new_folder_is_system_stock(tmp_path):
    p = T.load_pipeline(_xli(tmp_path), "new-folder")
    assert p.task_class == T.SYSTEM_TASK_CLASS
    assert p.name == "new-folder"
    by = {x.name: x for x in p.params}
    assert by["name"].required
    assert by["kind"].enum == ["code", "collection"]
    assert by["kind"].default == "code"
    assert p.steps[0].kind == T.KIND_SHELL
    assert "xlii new" in p.steps[0].body
    assert p.steps[1].kind == T.KIND_SLASH
    assert p.steps[1].body.startswith("/project switch")


def test_new_folder_command_line():
    assert T.new_folder_command("plugin-test") == (
        "/tasks run new-folder plugin-test --yes"
    )
    assert T.new_folder_command("notes", kind="collection") == (
        "/tasks run new-folder notes kind=collection --yes"
    )
    with pytest.raises(T.TaskParseError):
        T.new_folder_command("has/slash")
    with pytest.raises(T.TaskParseError):
        T.new_folder_command("has space")
    with pytest.raises(T.TaskParseError):
        T.new_folder_command("notes", kind="sandbox")


def test_listing_badge_system_wins():
    assert T.listing_badge("stock", "system") == "system"
    assert T.listing_badge("stock", "") == "stock"
    assert T.listing_badge("project", "") == ""
    assert T.listing_badge("project", "system") == "system"


def test_bad_class_fails_closed(tmp_path):
    xli = _xli(tmp_path)
    T.tasks_dir(xli).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / "weird.toml").write_text(
        'name = "weird"\nclass = "Nope"\n[[step]]\nrun = "true"\n'
    )
    with pytest.raises(T.TaskParseError, match="class"):
        T.load_pipeline(xli, "weird")


def test_cmd_new_local_kind(tmp_path, monkeypatch):
    from xlii import registry as R
    from xlii.cmds.project.init import cmd_new

    monkeypatch.setattr(R, "REGISTRY_FILE", tmp_path / "projects.json")
    monkeypatch.chdir(tmp_path)
    rc = cmd_new(Namespace(name="lab-one", path=str(tmp_path), local=True, kind="code"))
    assert rc == 0
    root = tmp_path / "lab-one"
    assert (root / ".xlii" / "project.json").is_file()
    text = (root / ".xlii" / "project.json").read_text()
    assert '"local_only": true' in text or '"local_only":true' in text
    assert "lab-one" in text


def test_cmd_new_refuses_path_name(tmp_path):
    from xlii.cmds.project.init import cmd_new

    assert cmd_new(Namespace(name="a/b", path=str(tmp_path), local=True, kind="code")) == 1


def test_render_plan_shows_class(tmp_path):
    plan = "\n".join(T.render_plan(T.load_pipeline(_xli(tmp_path), "new-folder")))
    assert "class: system" in plan
    assert "{{name}}" in plan


def test_tasks_pane_badges_system(tmp_path, monkeypatch):
    from xlii.panes.tasks import TasksPane

    xli = _xli(tmp_path)
    monkeypatch.setattr("xlii.active_session.active_xli_dir", lambda: xli)
    pane = TasksPane("tasks://")
    rendered = pane.render()
    labels = [r.text for r in rendered.rows]
    assert any(t == "new-folder · system" for t in labels)
    assert any("echo-hello · stock" in t or t == "echo-hello · stock" for t in labels)
