import json

from xlii.config import ProjectConfig
from xlii.registry import Registry, RegistryEntry
from xlii.repl_cmds.code import _project_handler
from tests.helpers import FakeConsole


def _write_project(root, name):
    (root / ".xlii").mkdir(parents=True)
    (root / ".xlii" / "project.json").write_text(json.dumps({
        "name": name,
        "root": str(root.resolve()),
        "collection_id": "",
        "created_at": "2026-01-01T00:00:00Z",
        "conversation_id": f"{name}-conv",
        "local_only": True,
    }))
    return ProjectConfig.load(root)


def _registry(root, name):
    return Registry(entries=[RegistryEntry(
        path=str(root.resolve()),
        collection_id="",
        name=name,
        created_at="2026-01-01T00:00:00Z",
    )])


def test_projects_find_reports_match_without_switching(tmp_path, monkeypatch):
    project = _write_project(tmp_path / "api", "api")
    monkeypatch.setattr("xlii.repl_cmds.code.Registry.load", lambda: _registry(project.project_root, "api"))
    calls = []
    monkeypatch.setattr("xlii.repl_cmds.switch.switch_to_code_project", lambda *a, **kw: calls.append((a, kw)))
    console = FakeConsole()

    assert _project_handler("/project find api", {"console": console}) is True

    assert not calls
    assert "project match" in console.text
    assert "/project switch api" in console.text


def test_projects_switch_teleports_to_match(tmp_path, monkeypatch):
    project = _write_project(tmp_path / "api", "api")
    monkeypatch.setattr("xlii.repl_cmds.code.Registry.load", lambda: _registry(project.project_root, "api"))
    calls = []
    monkeypatch.setattr("xlii.repl_cmds.switch.switch_to_code_project", lambda *a, **kw: calls.append((a, kw)))
    console = FakeConsole()

    assert _project_handler("/project switch api --reset --no-reload", {"console": console}) is True

    assert len(calls) == 1
    assert calls[0][0][1].project_root == project.project_root
    assert calls[0][1] == {"reset": True, "reload_surface": False}


def test_project_startup_is_reserved_not_a_filter(tmp_path, monkeypatch):
    """`startup` is a reserved first token; it must not fall through to list-filter."""
    from types import SimpleNamespace

    from xlii import tasks as T

    project = _write_project(tmp_path / "api", "api")
    xli = project.xli_dir
    T.write_pipeline_toml(xli, "nightly", 'name = "nightly"\n[[step]]\nrun = "printf ok"\n')
    store = tmp_path / "startup.json"
    monkeypatch.setattr("xlii.session_boot.STARTUP_BINDINGS_FILE", store)
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    console = FakeConsole()
    state = SimpleNamespace(project=project, agent=None, startup_off=False)
    ctx = {"console": console, "state": state}

    assert _project_handler("/project startup nightly", ctx) is True
    from xlii.session_boot import load_startup_binding

    bound = load_startup_binding(project.project_root)
    assert bound is not None and bound.task == "nightly"
    assert "startup bind snapshot" in console.text

    console = FakeConsole()
    ctx["console"] = console
    assert _project_handler("/project startup --show", ctx) is True
    assert "startup task: nightly" in console.text

    console = FakeConsole()
    ctx["console"] = console
    assert _project_handler("/project startup --off", ctx) is True
    assert state.startup_off is True
    assert "muted" in console.text

    console = FakeConsole()
    ctx["console"] = console
    assert _project_handler("/project startup --clear", ctx) is True
    assert load_startup_binding(project.project_root) is None

    console = FakeConsole()
    ctx["console"] = console
    assert _project_handler("/project startup --auto nightly", ctx) is True
    assert "admin" in console.text
    assert load_startup_binding(project.project_root) is None
