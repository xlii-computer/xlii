"""Face Task+ maker — cycles shapes, seeds /tasks new, no CLAIM_INPUT."""

from __future__ import annotations

from xlii.panes import PREFILL, RETARGET_SLOT
from xlii.panes.task_make import TaskMakePane


def test_defaults_and_scaffold_line():
    pane = TaskMakePane("taskmake://")
    labels = [r.text for r in pane.render().rows]
    assert any(t.startswith("shape · linear") for t in labels)
    assert pane.scaffold_command() == "/tasks new linear --shape linear"


def test_cycle_shape_to_verdict():
    pane = TaskMakePane("taskmake://")
    leaves = [r.address for r in pane.render().rows if r.kind == "leaf"]
    pane.select_index(next(i for i, a in enumerate(leaves) if a.endswith("/shape")))
    assert pane.apply_selection() is True
    assert pane._shape == "params"
    pane.apply_selection()
    assert pane._shape == "verdict"
    assert "--shape verdict" in pane.scaffold_command()
    assert "--branches" in pane.scaffold_command()
    assert any(r.tone == "knob" for r in pane.render().rows if r.address.endswith("/shape"))


def test_clone_hides_shape():
    pane = TaskMakePane("taskmake://")
    pane._clone = "git-triage"
    labels = [r.text for r in pane.render().rows]
    assert not any(t.startswith("shape ·") for t in labels)
    assert pane.scaffold_command() == "/tasks new my-git-triage --clone git-triage"


def test_new_on_tasks_pane_opens_maker():
    from xlii.panes.tasks import TasksPane

    acts = TasksPane("tasks://").actions()
    assert acts[0].name == "new"
    assert acts[0].outcome.kind == RETARGET_SLOT
    assert acts[0].outcome.address == "taskmake://"


def test_edit_address_keeps_the_task_name():
    from xlii.addressing import vfs_stat

    node = vfs_stat("taskmake://bump-note")
    assert node.address.endswith("bump-note")
    assert node.extra.get("task") == "bump-note"


def test_edit_in_maker_loads_existing_steps(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from xlii import active_session, tasks as T
    from xlii.panes.dock import Dock
    from xlii.panes.task_make import TaskMakePane

    T.write_pipeline_spec(tmp_path, {
        "name": "bump-note",
        "description": "real pipe",
        "steps": [
            {"kind": "shell", "id": "say", "body": "echo tangible"},
            {"kind": "slash", "id": "go", "body": "/status"},
        ],
    })
    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(project=SimpleNamespace(xli_dir=tmp_path)),
    )
    pane = Dock().open_address("taskmake://bump-note")
    assert isinstance(pane, TaskMakePane)
    assert pane._edit_name == "bump-note"
    form = pane.render().form
    assert form and form["name"] == "bump-note"
    bodies = [s.get("body") for s in form["steps"]]
    assert "echo tangible" in bodies
    assert "/status" in bodies
    again = pane.render().form
    assert again is form
    assert "echo hello" not in bodies
    assert "echo tangible" in form["html"]


def test_scaffold_row_prefills():
    pane = TaskMakePane("taskmake://")
    leaves = [r for r in pane.render().rows if r.kind == "leaf"]
    i = next(n for n, r in enumerate(leaves) if r.address.endswith("/scaffold"))
    pane.select_index(i)
    acts = pane.actions()
    assert acts[0].outcome.kind == PREFILL
    assert acts[0].outcome.text.startswith("/tasks new ")
