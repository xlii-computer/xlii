"""Adopt an existing folder as an xlii project — path resolve + face door."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.project_paths import resolve_adopt_path, user_home


def test_relative_name_resolves_against_shell_cwd_not_process(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    desk = tmp_path / "desk"
    target = desk / "legacy"
    target.mkdir(parents=True)
    (tmp_path / "legacy").mkdir()  # decoy in process cwd
    got, err = resolve_adopt_path("legacy", shell_cwd=desk)
    assert err == ""
    assert got == target.resolve()


def test_dot_uses_selected_folder(tmp_path):
    sel = tmp_path / "oldapp"
    sel.mkdir()
    got, err = resolve_adopt_path(".", shell_cwd=tmp_path, selected=sel)
    assert err == "" and got == sel.resolve()


def test_missing_dir_is_an_error(tmp_path):
    got, err = resolve_adopt_path(str(tmp_path / "nope"), shell_cwd=tmp_path)
    assert got is None and "no folder" in err


def test_file_adopts_its_parent(tmp_path):
    f = tmp_path / "main.py"
    f.write_text("x")
    got, err = resolve_adopt_path(str(f), shell_cwd=tmp_path)
    assert err == "" and got == tmp_path.resolve()


def test_refuses_home():
    got, err = resolve_adopt_path(str(user_home()))
    assert got is None and "home directory" in err


def test_tilde_and_file_uri(tmp_path):
    got, err = resolve_adopt_path(f"file://{tmp_path}")
    assert err == "" and got == tmp_path.resolve()


def test_face_create_project_stamps_existing_folder(tmp_path, monkeypatch):
    from xlii import registry as R
    from xlii.serve_face import FaceServer

    monkeypatch.setattr(R, "REGISTRY_FILE", tmp_path / "projects.json")
    old = tmp_path / "legacy"
    old.mkdir()
    (old / "app.py").write_text("print(1)\n")
    sent = []
    state = SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="scratch/home",
                                local_only=True, xli_dir=tmp_path / ".xlii"),
        yolo=False, freeball=False,
        console=SimpleNamespace(print=lambda *a, **k: None),
        workbench=None, scratch=True,
    )
    state.as_context_dict = lambda: {"state": state, "console": state.console}
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = lambda o: sent.append(o)
    monkeypatch.setattr(
        "xlii.repl_cmds.switch.switch_to_code_project", lambda *a, **k: None
    )
    monkeypatch.setattr(FaceServer, "_land_folder", lambda self, p, announce="": True)
    assert server.create_project("legacy", kind="code") is True
    assert (old / ".xlii" / "project.json").is_file()


def test_face_create_project_refuses_missing(tmp_path):
    from xlii.serve_face import FaceServer

    sent = []
    state = SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="p", xli_dir=tmp_path),
        yolo=False, freeball=False, console=None, workbench=None,
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = lambda o: sent.append(o)
    assert server.create_project("ghost-app") is False
    assert any("no folder" in str(o.get("text", "")) for o in sent)


def test_face_create_project_adopts_sftp(tmp_path, monkeypatch):
    from xlii import desk_files as D
    from xlii import registry as R
    from xlii.serve_face import FaceServer

    monkeypatch.setattr(D, "pointer_store_root", lambda: tmp_path / "links")
    monkeypatch.setattr(R, "REGISTRY_FILE", tmp_path / "projects.json")
    sent = []
    state = SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="scratch/home",
                                local_only=True, xli_dir=tmp_path / ".xlii"),
        yolo=False, freeball=False,
        console=SimpleNamespace(print=lambda *a, **k: None),
        workbench=None, scratch=True,
    )
    state.as_context_dict = lambda: {"state": state, "console": state.console}
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = lambda o: sent.append(o)
    switched = []
    monkeypatch.setattr(
        "xlii.repl_cmds.switch.switch_to_code_project",
        lambda ctx, project, **k: switched.append(project),
    )
    monkeypatch.setattr(FaceServer, "_land_folder", lambda self, p, announce="": True)
    assert server.create_project("sftp://appbox/srv/apps/calc") is True
    assert switched and switched[0].files_root == "sftp://appbox/srv/apps/calc"
    assert switched[0].project_root.is_dir()


def test_bind_project_files_points_current_desk(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    _stamp(tmp_path)
    sent = []
    project = __import__("xlii.config", fromlist=["ProjectConfig"]).ProjectConfig.load(tmp_path)
    state = SimpleNamespace(
        shell_cwd=tmp_path, project=project,
        yolo=False, freeball=False, console=None, workbench=None,
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = lambda o: sent.append(o)
    server._deck = None
    assert server.bind_project_files("sftp://appbox/pub") is True
    again = __import__("xlii.config", fromlist=["ProjectConfig"]).ProjectConfig.load(tmp_path)
    assert again.files_root == "sftp://appbox/pub"
    assert any("Files →" in str(o.get("text", "")) for o in sent)


def _stamp(root):
    import json

    d = root / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    (d / "project.json").write_text(json.dumps({
        "name": "t", "collection_id": "", "created_at": "2026-01-01",
        "conversation_id": "x", "local_only": True, "root": str(root.resolve()),
    }))
