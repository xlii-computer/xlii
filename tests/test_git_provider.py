"""``git://`` provider — the working tree's source-control state as a read-only VFS.

Roots on the ambient session's cwd (:func:`xlii.active_session.active_cwd`); lists the changed set
(staged / modified / untracked) at the root, per-file diffs at ``git://diff|staged/<path>``, and the
commit log at ``git://log``. Read-only — mutations go through ``/git`` (see test_repl_git). Every fact
is from the local ``git`` binary, so a non-git dir degrades to empty rather than raising.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xlii.loop_bundle import git_cmd


@pytest.fixture(autouse=True)
def _no_session(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(active_session, "_ACTIVE", None)


def _session_at(monkeypatch, cwd):
    from xlii import active_session

    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(shell_cwd=Path(cwd),
                        project=SimpleNamespace(project_root=Path(cwd), xli_dir=Path(cwd) / ".xlii")),
    )


def _init_repo(root: Path) -> Path:
    git_cmd(root, ["init"])
    git_cmd(root, ["config", "user.email", "t@example.com"])
    git_cmd(root, ["config", "user.name", "Tester"])
    (root / "tracked.txt").write_text("a\n")
    git_cmd(root, ["add", "tracked.txt"])
    git_cmd(root, ["commit", "-m", "init"])
    return root


def test_git_root_lists_changed_and_staged(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")     # unstaged modification
    (tmp_path / "new.txt").write_text("x\n")            # untracked
    (tmp_path / "staged.txt").write_text("s\n")
    git_cmd(tmp_path, ["add", "staged.txt"])            # staged add
    _session_at(monkeypatch, tmp_path)
    from xlii.addressing import resolve, vfs_list

    assert resolve("git://").ok
    seen = {(n.extra.get("status"), n.name, n.extra.get("staged")) for n in vfs_list("git://")}
    assert ("M", "tracked.txt", False) in seen
    assert ("?", "new.txt", False) in seen
    assert ("A", "staged.txt", True) in seen


def test_git_diff_sublisting_and_read(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    _session_at(monkeypatch, tmp_path)
    from xlii.addressing import vfs_list, vfs_read

    assert sorted(n.name for n in vfs_list("git://diff")) == ["tracked.txt"]
    diff = vfs_read("git://diff/tracked.txt").decode()
    assert "+b" in diff and "tracked.txt" in diff


def test_git_staged_diff_read(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["add", "tracked.txt"])
    _session_at(monkeypatch, tmp_path)
    from xlii.addressing import vfs_read

    assert "+b" in vfs_read("git://staged/tracked.txt").decode()


def test_git_untracked_reads_as_additions(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "new.txt").write_text("hello there\n")
    _session_at(monkeypatch, tmp_path)
    from xlii.addressing import vfs_read

    # an untracked file has no tracked diff — the provider shows it as all-additions
    assert "hello there" in vfs_read("git://diff/new.txt").decode()


def test_git_log_lists_and_shows_commits(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    _session_at(monkeypatch, tmp_path)
    from xlii.addressing import vfs_list, vfs_read

    commits = vfs_list("git://log")
    assert commits and commits[0].kind == "leaf" and commits[0].extra["type"] == "commit"
    sha = commits[0].address.split("git://log/", 1)[1]
    assert "init" in vfs_read(f"git://log/{sha}").decode()


def test_git_capabilities_read_only():
    from xlii.addressing import supports_vfs, supports_write

    assert supports_vfs("git") is True
    assert supports_write("git") is False  # read-only; mutations go through /git


def test_git_empty_outside_a_repo(tmp_path, monkeypatch):
    # a plain (non-git) directory → the root lists empty and resolve is not ok
    _session_at(monkeypatch, tmp_path)
    from xlii.addressing import resolve, vfs_list

    assert vfs_list("git://") == []
    assert resolve("git://").ok is False
