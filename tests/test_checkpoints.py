"""Tests for turn-level checkpoints (object-snapshot + scoped restore)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from xlii.checkpoints import (
    begin_turn,
    diff_since,
    end_turn,
    load_ledger,
    manual_checkpoint,
    object_snapshot,
    project_dirty_to_repo_paths,
    restore_full,
    restore_scoped,
    rewind,
    write_paths,
)
from xlii.loop_bundle import git_cmd


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def git_project(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    xli = root / ".xlii"
    xli.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@test")
    _git(root, "config", "user.name", "t")
    (root / "seed.txt").write_text("seed\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "base")
    return root, xli


def test_object_snapshot_includes_untracked(git_project):
    root, _xli = git_project
    (root / "brandnew.py").write_text("x = 1\n")
    sha, err = object_snapshot(root)
    assert err is None
    assert sha
    out, _ = git_cmd(root, ["ls-tree", "-r", "--name-only", sha])
    assert "brandnew.py" in out


def test_write_turn_records_checkpoint(git_project):
    root, xli = git_project
    tree, _ = begin_turn(root)
    assert tree
    (root / "newmod.py").write_text("hello\n")
    end_turn(xli, root, tree, {"newmod.py"})
    ledger = load_ledger(xli)
    assert len(ledger) == 1
    assert ledger[0].dirty_paths == ["newmod.py"]
    assert ledger[0].tree_sha == tree


def test_read_only_turn_no_checkpoint(git_project):
    root, xli = git_project
    tree, _ = begin_turn(root)
    end_turn(xli, root, tree, {"__rescan__"})
    assert load_ledger(xli) == []


def test_scoped_rewind_restores_and_deletes(git_project):
    root, xli = git_project
    tree, _ = begin_turn(root)
    (root / "added.py").write_text("new\n")
    (root / "seed.txt").write_text("seed\nchanged\n")
    end_turn(xli, root, tree, {"added.py", "seed.txt"})

    entry = load_ledger(xli)[0]
    err = restore_scoped(root, entry.tree_sha, set(entry.dirty_paths))
    assert err is None
    assert not (root / "added.py").exists()
    assert (root / "seed.txt").read_text() == "seed\n"


def test_rewind_command_pops_ledger(git_project):
    root, xli = git_project

    tree1, _ = begin_turn(root)
    (root / "a.py").write_text("a\n")
    end_turn(xli, root, tree1, {"a.py"})

    tree2, _ = begin_turn(root)
    (root / "b.py").write_text("b\n")
    end_turn(xli, root, tree2, {"b.py"})

    assert (root / "a.py").exists()
    assert (root / "b.py").exists()

    ok, _msg = rewind(xli, root, 1, confirm=lambda _p: True)
    assert ok
    assert (root / "a.py").exists()
    assert not (root / "b.py").exists()
    assert len(load_ledger(xli)) == 1


def test_rewind_two_write_turns(git_project):
    root, xli = git_project

    tree1, _ = begin_turn(root)
    (root / "a.py").write_text("a\n")
    end_turn(xli, root, tree1, {"a.py"})

    tree2, _ = begin_turn(root)
    (root / "b.py").write_text("b\n")
    end_turn(xli, root, tree2, {"b.py"})

    ok, _msg = rewind(xli, root, 2, confirm=lambda _p: True)
    assert ok
    assert not (root / "a.py").exists()
    assert not (root / "b.py").exists()
    assert load_ledger(xli) == []


def test_manual_checkpoint_and_diff(git_project):
    root, xli = git_project
    entry, err = manual_checkpoint(xli, root, label="before experiment")
    assert err is None
    assert entry is not None
    (root / "touched.py").write_text("x\n")
    diff, err = diff_since(xli, root, n=1)
    assert err is None
    assert "touched.py" in diff


def test_non_git_graceful(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    xli = root / ".xlii"
    xli.mkdir()
    _entry, err = manual_checkpoint(xli, root)
    assert _entry is None
    assert "not a git" in (err or "").lower()


def test_write_paths_filters_rescan():
    assert write_paths({"__rescan__", "foo.py"}) == {"foo.py"}
    assert write_paths({"__rescan__"}) == set()


def test_restore_full(git_project):
    root, _xli = git_project
    sha, _ = object_snapshot(root)
    (root / "gone.py").write_text("bye\n")
    (root / "seed.txt").write_text("mutated\n")
    err = restore_full(root, sha or "")
    assert err is None
    assert not (root / "gone.py").exists()
    assert (root / "seed.txt").read_text() == "seed\n"


@pytest.fixture
def git_subproject(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    sub = repo / "sub"
    sub.mkdir()
    xli = sub / ".xlii"
    xli.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@test")
    _git(repo, "config", "user.name", "t")
    (sub / "keep.txt").write_text("orig\n")
    (repo / "notes.txt").write_text("root notes\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    return repo, sub, xli


def test_subdirectory_project_paths_and_rewind(git_subproject):
    repo, proj, xli = git_subproject
    tree, _ = begin_turn(proj)
    (proj / "keep.txt").write_text("mutated\n")
    (proj / "new.py").write_text("new\n")
    end_turn(xli, proj, tree, {"keep.txt", "new.py"})

    entry = load_ledger(xli)[0]
    assert entry.dirty_paths == ["sub/keep.txt", "sub/new.py"]

    err = restore_scoped(repo, entry.tree_sha, set(entry.dirty_paths))
    assert err is None
    assert (proj / "keep.txt").read_text() == "orig\n"
    assert not (proj / "new.py").exists()
    assert (repo / "notes.txt").read_text() == "root notes\n"


def test_project_dirty_to_repo_paths(git_subproject):
    repo, proj, _xli = git_subproject
    mapped = project_dirty_to_repo_paths(repo, proj, {"keep.txt"})
    assert mapped == ["sub/keep.txt"]


def test_binary_restore_round_trip(git_project):
    root, xli = git_project
    payload = bytes([0, 1, 0x80, 255, 0])
    (root / "bin.dat").write_bytes(payload)
    tree, _ = begin_turn(root)
    (root / "bin.dat").write_bytes(b"wrong")
    end_turn(xli, root, tree, {"bin.dat"})
    entry = load_ledger(xli)[0]
    err = restore_scoped(root, entry.tree_sha, set(entry.dirty_paths))
    assert err is None
    assert (root / "bin.dat").read_bytes() == payload


def test_executable_mode_preserved(git_project):
    root, xli = git_project
    script = root / "run.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    _git(root, "add", "run.sh")
    _git(root, "commit", "-m", "script")
    tree, _ = begin_turn(root)
    script.write_text("#!/bin/sh\necho mutated\n")
    end_turn(xli, root, tree, {"run.sh"})
    entry = load_ledger(xli)[0]
    err = restore_scoped(root, entry.tree_sha, set(entry.dirty_paths))
    assert err is None
    assert (script.stat().st_mode & 0o777) == 0o755
    assert "mutated" not in script.read_text()
