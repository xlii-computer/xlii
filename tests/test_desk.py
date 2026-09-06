"""Desk app preference rings (editor / image / browser / terminal)."""

from __future__ import annotations

import os
from types import SimpleNamespace

from xlii.desk import (
    EDITOR_CANDIDATES,
    apply_terminal_cwd_path,
    compact_home_path,
    cycle_pref,
    cycle_term_cwd,
    desk_context,
    downloads_dir,
    drop_matches,
    installed,
    is_desk_nav,
    listing_from_shell,
    name_matches,
    pref_label,
    resolve_browser,
    resolve_image_editor,
    resolve_terminal_cwd,
    term_cwd_label,
)


def test_cycle_pref_auto_then_candidates_then_wrap():
    assert cycle_pref("", ["nano", "pluma"]) == "nano"
    assert cycle_pref("nano", ["nano", "pluma"]) == "pluma"
    assert cycle_pref("pluma", ["nano", "pluma"]) == ""


def test_cycle_pref_keeps_custom_command_in_ring():
    # custom sits between auto and the installed list
    assert cycle_pref("code -w", ["nano"]) == "nano"
    assert cycle_pref("nano", ["nano"]) == ""


def test_pref_label_auto_when_empty():
    assert pref_label("") == "auto"
    assert pref_label("gimp") == "gimp"


def test_installed_filters_path(monkeypatch):
    monkeypatch.setattr("xlii.desk.shutil.which", lambda c: "/usr/bin/nano" if c == "nano" else None)
    assert installed(EDITOR_CANDIDATES) == ["nano"]


def test_resolve_image_editor_and_browser(monkeypatch):
    monkeypatch.setattr("xlii.desk.shutil.which", lambda c: f"/usr/bin/{c}")
    cfg = SimpleNamespace(image_editor="gimp", browser="firefox")
    assert resolve_image_editor(cfg) == "gimp"
    assert resolve_browser(cfg) == "firefox"
    assert resolve_image_editor(SimpleNamespace(image_editor="")) is None


def test_drop_zone_lists_newest_matching_download(tmp_path, monkeypatch):
    down = tmp_path / "Downloads"
    down.mkdir()
    old = down / "cursor_3.2.deb"
    new = down / "Cursor-3.15.19-x86_64.AppImage"
    old.write_bytes(b"old")
    new.write_bytes(b"new")
    # mtime: new is newer
    import os
    os.utime(old, (1, 1))
    os.utime(new, (9_999_999_999, 9_999_999_999))
    monkeypatch.setenv("XDG_DOWNLOAD_DIR", str(down))
    assert downloads_dir() == down
    hits = drop_matches("install the newest cursor from Downloads")
    assert hits and hits[0].name.startswith("Cursor-3.15")
    note = desk_context("install cursor AppImage", cwd=tmp_path / "proj")
    assert "Drop zone:" in note
    assert "never dpkg an AppImage" in note
    assert "Cursor-3.15" in note
    assert str(tmp_path / "proj") in note


def test_is_desk_nav_accepts_cd_ls_not_prose():
    assert is_desk_nav("cd Downloads")
    assert is_desk_nav("cd ~/Downloads")
    assert is_desk_nav("ls")
    assert is_desk_nav("ls -la")
    assert is_desk_nav("pwd")
    assert not is_desk_nav("cd to the downloads folder")
    assert not is_desk_nav("cd a && ls")
    assert not is_desk_nav("install the newest cursor")


def test_cwd_matches_and_last_listing_in_desk_context(tmp_path):
    here = tmp_path / "here"
    here.mkdir()
    hit = here / "Cursor-3.15.19-x86_64.AppImage"
    hit.write_bytes(b"x")
    ev = SimpleNamespace(command="ls -la", stdout=hit.name + "\nnotes.txt\n")
    note = desk_context(
        "install the newest cursor",
        cwd=here,
        last_listing=listing_from_shell(ev),
    )
    assert "Working directory (CWD):" in note
    assert str(hit) in note
    assert "Last listing" in note
    assert "Cursor-3.15.19" in note
    assert name_matches("cursor", here)[0] == hit


def test_terminal_cwd_resolves_project_home_root_custom(tmp_path, monkeypatch):
    from pathlib import Path

    from xlii.project_paths import user_home

    desk = tmp_path / "desk"
    desk.mkdir()
    custom = tmp_path / "elsewhere"
    custom.mkdir()
    state = SimpleNamespace(shell_cwd=desk, project=SimpleNamespace(project_root=desk), cfg=None)
    assert Path(resolve_terminal_cwd(state, SimpleNamespace(tui_terminal_cwd="project"))) == desk

    cfg = SimpleNamespace(tui_terminal_cwd="home", tui_terminal_cwd_path="")
    assert Path(resolve_terminal_cwd(state, cfg)) == user_home()

    cfg.tui_terminal_cwd = "root"
    assert resolve_terminal_cwd(state, cfg) == os.sep

    cfg.tui_terminal_cwd = "custom"
    cfg.tui_terminal_cwd_path = str(custom)
    assert Path(resolve_terminal_cwd(state, cfg)) == custom.resolve()

    cfg.tui_terminal_cwd_path = str(tmp_path / "missing")
    assert Path(resolve_terminal_cwd(state, cfg)) == desk

    assert term_cwd_label(SimpleNamespace(tui_terminal_cwd="project")) == "this project"
    assert term_cwd_label(SimpleNamespace(tui_terminal_cwd="home")) == "home folder"
    assert term_cwd_label(SimpleNamespace(tui_terminal_cwd="root")) == "root"
    assert term_cwd_label(SimpleNamespace(
        tui_terminal_cwd="custom", tui_terminal_cwd_path=str(user_home() / "src"),
    )) == "~/src"
    assert compact_home_path(str(user_home())) == "~"


def test_cycle_and_apply_terminal_cwd_path(tmp_path):
    saves = []
    cfg = SimpleNamespace(tui_terminal_cwd="project", tui_terminal_cwd_path="", save=lambda: saves.append(1))
    assert cycle_term_cwd(cfg) == "home"
    assert cycle_term_cwd(cfg) == "root"
    assert cycle_term_cwd(cfg) == "custom"
    assert cycle_term_cwd(cfg) == "project"

    ok, msg = apply_terminal_cwd_path(cfg, str(tmp_path))
    assert ok
    assert cfg.tui_terminal_cwd == "custom"
    assert cfg.tui_terminal_cwd_path == str(tmp_path)
    assert "new terminal" in msg
    assert saves

    ok, msg = apply_terminal_cwd_path(cfg, "clear")
    assert ok
    assert cfg.tui_terminal_cwd == "project"
    assert cfg.tui_terminal_cwd_path == ""
