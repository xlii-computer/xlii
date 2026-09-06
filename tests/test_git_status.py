"""Project browser P1: local git snapshot (git_status.py).

Forge-agnostic, local-git-only. Each test builds a throwaway repo under tmp_path;
the whole module is skipped if git isn't installed.
"""

import shutil
import subprocess

import pytest

from xlii.git_status import (
    ahead_behind,
    branch_name,
    diff_stat,
    find_repo_root,
    git_snapshot,
    is_git_repo,
    last_commit_oneline,
    remotes,
    short_status_map,
    staged_status_map,
)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


def _init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "commit.gpgsign", "false")
    return path


def test_non_git_dir_degrades_gracefully(tmp_path):
    assert find_repo_root(tmp_path) is None
    assert is_git_repo(tmp_path) is False
    snap = git_snapshot(tmp_path)
    assert snap.is_repo is False
    assert snap.status == {}
    assert snap.branch is None
    assert snap.changed_count == 0


def test_branch_root_and_clean_status(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("hello\n")
    _git(repo, "add", "a.txt")
    _git(repo, "commit", "-m", "init")

    assert find_repo_root(repo).resolve() == repo.resolve()
    assert branch_name(repo) == "main"
    assert short_status_map(repo) == {}
    assert (last_commit_oneline(repo) or "").endswith("init")

    snap = git_snapshot(repo)
    assert snap.is_repo and snap.branch == "main" and snap.changed_count == 0


def test_status_map_classifies_changes(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "tracked.txt").write_text("v1\n")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "init")

    (repo / "tracked.txt").write_text("v2\n")   # modified, unstaged
    (repo / "staged.txt").write_text("new\n")
    _git(repo, "add", "staged.txt")             # added, staged
    (repo / "untracked.txt").write_text("u\n")  # untracked

    st = short_status_map(repo)
    assert st["tracked.txt"] == "M"
    assert st["untracked.txt"] == "?"
    assert st["staged.txt"] == "A"

    staged = staged_status_map(repo)
    assert staged.get("staged.txt") == "A"
    assert "untracked.txt" not in staged
    assert "tracked.txt" not in staged  # modified but not staged


def test_rename_reports_destination_path(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "old.txt").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    _git(repo, "mv", "old.txt", "new.txt")

    st = short_status_map(repo)
    assert "new.txt" in st          # destination, not "old.txt -> new.txt"
    assert "old.txt -> new.txt" not in st
    assert st["new.txt"] == "R"


def test_spaced_filename_is_unquoted(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "my file.txt").write_text("x\n")  # space -> git quotes the path
    st = short_status_map(repo)
    assert "my file.txt" in st
    assert st["my file.txt"] == "?"


def test_unicode_filename_is_decoded(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "café.txt").write_text("x\n")      # non-ASCII -> git octal-escapes it
    st = short_status_map(repo)
    assert "café.txt" in st                     # decoded back from \303\251, not raw escapes


def test_filename_containing_arrow_not_mangled(tmp_path):
    repo = _init_repo(tmp_path / "r")
    name = "weird -> arrow.txt"                 # literal " -> " in a non-rename path
    (repo / name).write_text("x\n")
    st = short_status_map(repo)
    assert name in st
    assert st[name] == "?"


def test_remotes_displayed_verbatim(tmp_path):
    repo = _init_repo(tmp_path / "r")
    _git(repo, "remote", "add", "origin", "git@forge:acme/api.git")
    assert remotes(repo).get("origin") == "git@forge:acme/api.git"
    assert git_snapshot_origin(repo) == "git@forge:acme/api.git"


def git_snapshot_origin(repo):
    return git_snapshot(repo).remotes.get("origin")


def test_diff_stat_shows_changes_and_none_when_clean(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("v1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    assert diff_stat(repo) is None  # clean working tree

    (repo / "a.txt").write_text("v2\nv3\n")
    stat = diff_stat(repo)
    assert stat and "a.txt" in stat
    assert "a.txt" in (diff_stat(repo, "a.txt") or "")


def test_ahead_behind_none_without_upstream(tmp_path):
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("x\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "c")
    assert ahead_behind(repo) is None


def test_ahead_behind_with_upstream(tmp_path):
    bare = tmp_path / "bare.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "-b", "main")

    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("1\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "c1")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-u", "origin", "main")
    assert ahead_behind(repo) == (0, 0)

    (repo / "b.txt").write_text("2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "c2")
    assert ahead_behind(repo) == (1, 0)  # one local commit ahead, none behind


def _commit_all(repo, msg):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", msg)


def test_sweep_merged_is_vs_default_branch_not_head(tmp_path):
    """On a topic branch, sweep must not list the topic's ancestors (a fleet base,
    sibling branches) as merged — "merged" means merged into the DEFAULT branch."""
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo, "init")
    # genuinely merged into main → sweepable
    _git(repo, "checkout", "-b", "merged-feature")
    (repo / "a.txt").write_text("hello\nmore\n")
    _commit_all(repo, "feature")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "merged-feature", "-m", "merge")
    # the fleet shape: a base branch with unmerged work, a topic branch on top of
    # it — the base is an ancestor of the topic's HEAD but NOT merged into main
    _git(repo, "checkout", "-b", "fleet-base")
    (repo / "b.txt").write_text("base\n")
    _commit_all(repo, "base work")
    _git(repo, "checkout", "-b", "topic")
    (repo / "c.txt").write_text("topic\n")
    _commit_all(repo, "topic work")

    from xlii.git_status import sweep_report

    report = sweep_report(repo)  # HEAD = topic
    assert "merged-feature" in report.merged_local
    assert "fleet-base" not in report.merged_local  # ancestor of HEAD ≠ merged
    assert "topic" not in report.merged_local
    assert "main" not in report.merged_local


def test_sweep_cleanup_worktree_remove_before_branch_delete(tmp_path):
    """git refuses `branch -d` while the branch is checked out in a worktree, so
    the seeded `&&` chain must remove the worktree first — and the whole chain
    must actually run end-to-end (paths shell-quoted, spaces included)."""
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo, "init")
    _git(repo, "checkout", "-b", "wt-branch")
    (repo / "a.txt").write_text("hello\nmore\n")
    _commit_all(repo, "wt work")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "wt-branch", "-m", "merge")
    wt = tmp_path / "wt tree"  # a space in the path — must be shell-quoted
    _git(repo, "worktree", "add", str(wt), "wt-branch")

    from xlii.git_status import sweep_cleanup_commands, sweep_report

    report = sweep_report(repo)
    assert any(w.branch == "wt-branch" for w in report.stale_worktrees)
    cmds = sweep_cleanup_commands(report)
    wt_i = next(i for i, c in enumerate(cmds) if c.startswith("git worktree remove"))
    br_i = next(i for i, c in enumerate(cmds) if c == "git branch -d wt-branch")
    assert wt_i < br_i

    chain = " && ".join(cmds)
    res = subprocess.run(["bash", "-c", chain], cwd=str(repo), capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = subprocess.run(["git", "branch", "--format=%(refname:short)"],
                         cwd=str(repo), capture_output=True, text=True).stdout
    assert "wt-branch" not in out
    assert not wt.exists()


def test_sweep_uses_origin_head_when_declared(tmp_path):
    """With a remote whose HEAD is declared, sweep measures against origin/<default>
    and lists a merged remote branch for deletion (but never the mainline itself)."""
    bare = tmp_path / "bare.git"
    bare.mkdir()
    _git(bare, "init", "--bare", "-b", "main")
    repo = _init_repo(tmp_path / "r")
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo, "init")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-u", "origin", "main")
    _git(repo, "remote", "set-head", "origin", "-a")

    from xlii.git_status import default_branch_ref, sweep_report

    assert default_branch_ref(repo) == "origin/main"

    _git(repo, "checkout", "-b", "done-feature")
    (repo / "a.txt").write_text("hello\nmore\n")
    _commit_all(repo, "feature")
    _git(repo, "push", "-u", "origin", "done-feature")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "done-feature", "-m", "merge")
    _git(repo, "push", "origin", "main")

    report = sweep_report(repo)
    assert "done-feature" in report.merged_local
    assert "origin/done-feature" in report.merged_remote
    assert "origin/main" not in report.merged_remote
