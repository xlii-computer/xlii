"""TasksPane + tasks:// provider — the Panel "Tasks" doorway.

Lists saved /tasks pipelines (.xlii/tasks/*.toml); the primary action seeds `/tasks run <name>` into
the command line (review-before-run) via the new PREFILL outcome + InputSink seam. No network.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xlii import tasks as T


@pytest.fixture(autouse=True)
def _no_session(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)


def _session_at(monkeypatch, xli_dir):
    from xlii import active_session

    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(project=SimpleNamespace(xli_dir=xli_dir), attached_docs=[]),
    )


def _save(xli_dir, name):
    T.scaffold_pipeline(xli_dir, name)


# --- provider ---------------------------------------------------------------

def test_tasks_provider_lists_saved_pipelines(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _save(tmp_path, "nightly")
    _save(tmp_path, "deploy")
    from xlii.addressing import resolve, vfs_list

    assert resolve("tasks://").ok
    nodes = vfs_list("tasks://")
    names = {n.name for n in nodes}
    assert {"deploy", "nightly"} <= names
    assert all(n.kind == "leaf" and n.extra["type"] == "task" for n in nodes)


def test_tasks_provider_reads_the_plan(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _save(tmp_path, "nightly")
    from xlii.addressing import vfs_read

    body = vfs_read("tasks://nightly").decode()
    assert "task: nightly" in body


def test_tasks_provider_unknown_and_outside_project(tmp_path, monkeypatch):
    from xlii.addressing import resolve, vfs_list

    # no ambient session (autouse fixture) → the root lists empty, no memory leaks in
    assert vfs_list("tasks://") == []
    _session_at(monkeypatch, tmp_path)
    assert resolve("tasks://ghost").ok is False


# --- pane -------------------------------------------------------------------

def test_tasks_pane_lists_names(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _save(tmp_path, "nightly")
    _save(tmp_path, "deploy")
    from xlii.panes.tasks import TasksPane

    rows = TasksPane("tasks://").render().rows
    texts = {r.text for r in rows}
    assert "deploy" in texts and "nightly" in texts
    assert all(r.kind == "leaf" for r in rows)


def test_tasks_pane_badges_bound_startup_task(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _save(tmp_path, "nightly")
    from xlii.session_boot import StartupBinding, save_startup_binding

    store = tmp_path / "startup.json"
    monkeypatch.setattr("xlii.session_boot.STARTUP_BINDINGS_FILE", store)
    # _session_at only sets xli_dir; badge reads project_root.
    from xlii import active_session

    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(project=SimpleNamespace(
            xli_dir=tmp_path, project_root=tmp_path.parent,
        )),
    )
    save_startup_binding(tmp_path.parent, StartupBinding(task="nightly"))
    from xlii.panes.tasks import TasksPane

    texts = {r.text for r in TasksPane("tasks://").render().rows}
    assert any("nightly" in t and "startup" in t for t in texts)


def test_tasks_vfs_lists_startup_badge(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _save(tmp_path, "nightly")
    _save(tmp_path, "deploy")
    from xlii.session_boot import StartupBinding, save_startup_binding

    store = tmp_path / "startup.json"
    monkeypatch.setattr("xlii.session_boot.STARTUP_BINDINGS_FILE", store)
    from xlii import active_session

    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(project=SimpleNamespace(
            xli_dir=tmp_path, project_root=tmp_path.parent,
        )),
    )
    save_startup_binding(tmp_path.parent, StartupBinding(task="nightly"))
    from xlii.addressing import vfs_list

    nodes = {n.name: n for n in vfs_list("tasks://")}
    assert nodes["nightly"].extra.get("badge") == "startup"
    assert nodes["deploy"].extra.get("badge") is None


def test_tasks_pane_primary_action_loads_run_command(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _save(tmp_path, "nightly")
    from xlii.panes import PREFILL, RETARGET_SLOT, SPAWN_JOB
    from xlii.panes.tasks import TasksPane

    acts = TasksPane("tasks://").actions()
    assert acts[0].name == "new"
    load = next(a for a in acts if a.name == "load")
    assert load.outcome.kind == PREFILL
    assert load.outcome.text == "/tasks run nightly"
    run_bg = next(a for a in acts if a.name == "run-bg")
    assert run_bg.outcome.kind == SPAWN_JOB
    edit = next(a for a in acts if a.name == "edit")
    assert edit.outcome.kind == RETARGET_SLOT
    assert edit.outcome.address == "taskmake://nightly"
    view = next(a for a in acts if a.name == "view")
    assert view.outcome.kind == RETARGET_SLOT and view.outcome.address == "tasks://nightly"


def test_dock_spawn_job_routes_to_the_job_sink(tmp_path, monkeypatch):
    from xlii.panes import SPAWN_JOB, Outcome
    from xlii.panes.dock import Dock

    dock = Dock(slots=("A",))
    spawned: list[tuple[str, str]] = []
    dock.set_job_sink(SimpleNamespace(
        spawn=lambda address, text="": spawned.append((address, text))
    ))
    dock.dispatch(Outcome(SPAWN_JOB, "tasks://nightly", text="nightly"))
    assert spawned == [("tasks://nightly", "nightly")]


def test_dock_spawn_job_without_sink_raises():
    from xlii.panes import SPAWN_JOB, Outcome
    from xlii.panes.dock import Dock

    dock = Dock(slots=("A",))
    with pytest.raises(NotImplementedError):
        dock.dispatch(Outcome(SPAWN_JOB, "tasks://x", text="x"))


def test_spawn_saved_task_dispatches_and_captures_result(tmp_path, monkeypatch):
    """JobSink helper: saved pipeline → JobRegistry; result lands for jobs:// read."""
    from helpers import FakeConsole, make_agent, make_cfg
    from xlii import active_session
    from xlii.addressing import vfs_read
    from xlii.jobs import get_registry
    from xlii.repl_cmds.tasks import spawn_saved_task
    from xlii.repl_state import REPLState

    agent = make_agent(tmp_path, cfg=make_cfg())
    xli = Path(agent.project.xli_dir)
    T.write_pipeline_toml(
        xli, "echo",
        'name = "echo"\n[[step]]\nrun = "printf hi"\n',
    )
    st = REPLState(
        console=FakeConsole(), agent=agent, project=agent.project,
        cfg=agent.cfg, pool=agent.pool,
    )
    monkeypatch.setattr(
        "xlii.repl_cmds.tasks._session_freeball", lambda ctx: True
    )
    monkeypatch.setattr(active_session, "_ACTIVE", st)
    jid = spawn_saved_task(st, "echo", yes=True)
    assert jid
    reg = get_registry(st)
    assert reg is not None
    job = reg.wait(jid, timeout=10)
    assert job is not None and job.status == "done"
    assert job.result is not None and getattr(job.result, "ok", False) is True
    body = vfs_read(f"jobs://{jid}").decode()
    assert "## result" in body and "ok: True" in body and "hi" in body
    assert reg.unseen_done() and reg.unseen_done()[0].job_id == jid
    reg.shutdown(wait=True)


def test_tasks_pane_nav_and_click(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    for n in ("a", "b", "c"):
        _save(tmp_path, n)
    from xlii.panes.tasks import TasksPane

    p = TasksPane("tasks://")
    names = p._names
    assert names[0] == "a"
    assert p.handle("down") and p.selection().node.name == "b"
    assert p.select_index(names.index("c")) and p.selection().node.name == "c"
    assert p.select_index(0) and p.selection().node.name == "a"
    assert p.select_index(len(names)) is False
    assert p.handle("enter") is False      # falls through to the surface (actions)


def test_tasks_pane_mount_selects_the_addressed_task(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    for n in ("a", "b"):
        _save(tmp_path, n)
    from xlii.panes.tasks import TasksPane

    assert TasksPane("tasks://b").selection().node.name == "b"


def test_tasks_pane_empty_outside_a_project(monkeypatch):
    from xlii.panes import RETARGET_SLOT
    from xlii.panes.tasks import TasksPane

    p = TasksPane("tasks://")
    r = p.render()
    assert r.empty and not r.rows
    acts = p.actions()
    names = [a.name for a in acts]
    assert "new" in names
    assert acts[0].name == "new"
    assert acts[0].outcome.kind == RETARGET_SLOT
    assert acts[0].outcome.address == "taskmake://"
    assert p.selection().node is None


# --- dock routing + the PREFILL sink ----------------------------------------

def test_dock_routes_tasks_scheme_to_tasks_pane(tmp_path, monkeypatch):
    _session_at(monkeypatch, tmp_path)
    _save(tmp_path, "nightly")
    from xlii.panes.dock import Dock
    from xlii.panes.view import ViewPane

    dock = Dock()
    assert type(dock.open_address("tasks://")).__name__ == "TasksPane"
    # a single task (a leaf) falls through to the text viewer, showing its plan
    assert isinstance(dock.open_address("tasks://nightly"), ViewPane)


def test_dock_prefill_routes_to_the_input_sink(tmp_path, monkeypatch):
    from xlii.panes import PREFILL, Outcome
    from xlii.panes.dock import Dock

    dock = Dock(slots=("A",))
    seeded: list[str] = []
    dock.set_input_sink(SimpleNamespace(prefill=lambda text: seeded.append(text)))
    dock.dispatch(Outcome(PREFILL, "tasks://nightly", text="/tasks run nightly"))
    assert seeded == ["/tasks run nightly"]


def test_dock_prefill_without_sink_raises():
    from xlii.panes import PREFILL, Outcome
    from xlii.panes.dock import Dock

    dock = Dock(slots=("A",))
    with pytest.raises(NotImplementedError):
        dock.dispatch(Outcome(PREFILL, "tasks://x", text="/tasks run x"))


# --- the app doorway --------------------------------------------------------

def test_alt_t_doorway_opens_the_tasks_pane(tmp_path, monkeypatch):
    """Alt-T (and the Panel-menu Tasks item) open tasks:// in Pane 2."""
    pytest.importorskip("textual")
    import asyncio

    from xlii.agent import SessionState
    from xlii.tui.dock_surface import register_dock_view
    from xlii.tui_textual import XliiApp

    register_dock_view("vfs")            # the panel view the doorway routes through
    _save(tmp_path, "nightly")
    agent = SimpleNamespace(console=None, rail=None, debug=None, plan_mode=False,
                            active_mode=None, howto_mode=False, history=[],
                            model_override=None, session=SessionState())
    st = SimpleNamespace(shell_cwd=tmp_path,
                         project=SimpleNamespace(project_root=tmp_path, name="p", xli_dir=tmp_path),
                         agent=agent, attached_docs=[])
    _session_at(monkeypatch, tmp_path)   # the ambient session the provider/pane read
    app = XliiApp(project_name="p", agent=agent, run_turn=lambda q: ("", set(), None), state=st)

    async def scenario():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("t")
            await pilot.pause()
            await pilot.pause()
            assert app._panel_open
            assert app._current_dock_scheme() == "tasks"
            assert ("doorway:t", "  Tasks", True) in app._menu_items("Panel Workbench")

    asyncio.run(scenario())
