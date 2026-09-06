"""three-faces Q5 — scratch --tauri home desk + scratch path detection."""

from __future__ import annotations

from pathlib import Path

from xlii.serve_face import _is_scratch_desk_root


def test_is_scratch_desk_root_under_home_store(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    desk = home / ".xlii" / "scratch" / "home"
    desk.mkdir(parents=True)
    assert _is_scratch_desk_root(desk) is True
    other = tmp_path / "proj"
    other.mkdir()
    assert _is_scratch_desk_root(other) is False


def test_home_desk_roam_cwd_is_user_home(tmp_path, monkeypatch):
    """Config at ~/.xlii/scratch/home; shell opens at ~."""
    from types import SimpleNamespace

    from xlii.project_paths import is_home_desk_project, scratch_home_roam_cwd

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    desk = home / ".xlii" / "scratch" / "home"
    desk.mkdir(parents=True)
    proj = SimpleNamespace(name="scratch/home", project_root=desk)
    assert is_home_desk_project(proj) is True
    assert scratch_home_roam_cwd(proj) == home.resolve()
    named = SimpleNamespace(name="scratch/notes", project_root=home / ".xlii" / "scratch" / "notes")
    assert is_home_desk_project(named) is False
    assert scratch_home_roam_cwd(named) is None


def test_scratch_cli_registers_tauri_flag():
    import inspect
    from xlii.cmds.project import register as reg_mod

    # The --tauri flag is declared on the scratch parser in register.py
    src = inspect.getsource(reg_mod)
    assert "--tauri" in src and "scratch" in src


def test_ensure_home_desk_creates_local_project(tmp_path, monkeypatch):
    from xlii.cmds.project import scratch as sc
    from xlii.config import ProjectConfig
    from xlii.workbench import load_active_type

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    root = sc._ensure_home_desk()
    assert root == (home / ".xlii" / "scratch" / "home").resolve()
    assert ProjectConfig.load(root) is not None
    # Home desk prefers the join-first pack.
    assert load_active_type(root / ".xlii") == "home"
    # second call is idempotent
    assert sc._ensure_home_desk() == root


def test_launch_scratch_session_tauri_uses_home_desk(tmp_path, monkeypatch):
    from xlii.cmds.project import scratch as sc

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    seen = []

    def fake_tauri(target, **kwargs):
        seen.append(Path(target).resolve())
        return 0

    monkeypatch.setattr("xlii.cmds.sessions.code.launch_tauri", fake_tauri)
    rc = sc._launch_scratch_session(
        str(home), yolo=False, preview=True, launch=False, tauri=True,
    )
    assert rc == 0
    assert seen and "scratch" in str(seen[0]) and seen[0].name == "home"
