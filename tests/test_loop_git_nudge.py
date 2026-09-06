"""/loop nudges when there's no git repo — the trap that makes a loop stall on
"zero change" (the judges diff against git, so no repo == no visible work)."""

from __future__ import annotations

import subprocess

from xlii.repl_cmds.loop import _warn_if_not_git
from tests.helpers import FakeConsole


def test_warns_when_not_a_git_repo(tmp_path):
    con = FakeConsole()
    _warn_if_not_git(con, tmp_path)
    assert "not a git repo" in con.text
    assert "git init" in con.text


def test_silent_inside_a_git_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    con = FakeConsole()
    _warn_if_not_git(con, tmp_path)
    assert con.text == ""


def test_silent_in_a_subdir_of_a_repo(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    sub = tmp_path / "pkg" / "src"
    sub.mkdir(parents=True)
    con = FakeConsole()
    _warn_if_not_git(con, sub)              # find_repo_root walks up
    assert con.text == ""
