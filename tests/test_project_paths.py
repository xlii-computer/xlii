"""Shared project-path resolver + jail (edit-command.md P2 / project-browser.md)."""

import os

import pytest

from xlii.project_paths import PathOutsideProject, resolve_project_path, within_root


def test_resolve_relative_to_root(tmp_path):
    (tmp_path / "a").mkdir()
    p = resolve_project_path("a/x.py", tmp_path)
    assert p == (tmp_path / "a" / "x.py").resolve()


def test_absolute_inside_root_ok(tmp_path):
    target = tmp_path / "x.py"
    assert resolve_project_path(str(target), tmp_path) == target.resolve()


def test_escape_is_refused(tmp_path):
    with pytest.raises(PathOutsideProject):
        resolve_project_path("../escape.py", tmp_path)
    with pytest.raises(PathOutsideProject):
        resolve_project_path("/etc/passwd", tmp_path)


def test_cwd_used_when_inside_root(tmp_path):
    (tmp_path / "sub").mkdir()
    p = resolve_project_path("x.py", tmp_path, cwd=tmp_path / "sub")
    assert p == (tmp_path / "sub" / "x.py").resolve()


def test_cwd_outside_root_falls_back_to_root(tmp_path):
    p = resolve_project_path("x.py", tmp_path, cwd=tmp_path.parent)
    assert p == (tmp_path / "x.py").resolve()


def test_symlinked_root_not_defeated_or_tripped(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "f.py").write_text("x\n")
    link = tmp_path / "link"
    os.symlink(real, link)

    p = resolve_project_path("f.py", link)  # root is the symlink (unresolved)
    assert within_root(link, p)
    with pytest.raises(PathOutsideProject):
        resolve_project_path("../outside.py", link)
