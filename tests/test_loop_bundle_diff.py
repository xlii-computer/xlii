"""Judge bundle diff: the builder's work must reach the judge.

Field failure: the judge kept FAILing with "the diff contains zero change" while
the builder was clearly producing code. `assemble_bundle` used `git diff HEAD`,
which misses (a) work the builder committed and (b) brand-new untracked files —
so the judge saw nothing and the loop could never progress. The diff is now taken
against the loop's baseline commit and includes untracked files.
"""

from __future__ import annotations

import subprocess

import pytest

from xlii.loop import LoopController
from xlii.loop_bundle import assemble_bundle, git_cmd


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "proj"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@test")
    _git(r, "config", "user.name", "t")
    (r / "seed.txt").write_text("seed\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-m", "base")
    return r


def _bundle(repo, base):
    return assemble_bundle(
        project_root=repo, loop_id="L", cycle=1, goal="g",
        success_criteria=[], test_command="true", test_exit_code=0,
        test_output="", base=base,
    )


def test_committed_work_reaches_the_judge(repo):
    """Builder commits its work → working tree clean → old `git diff HEAD` was
    empty. Diffing against the baseline still shows it."""
    base = git_cmd(repo, ["rev-parse", "HEAD"])[0].strip()
    (repo / "feature.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "builder work")

    bundle = _bundle(repo, base)
    assert "feature.py" in bundle.artifact["diff"]
    assert "x = 1" in bundle.artifact["diff"]
    assert "feature.py" in bundle.artifact["files_changed"]


def test_untracked_new_files_reach_the_judge(repo):
    """A brand-new, never-added file is invisible to `git diff` — include it."""
    base = git_cmd(repo, ["rev-parse", "HEAD"])[0].strip()
    (repo / "brandnew.kt").write_text("fun main() {}\n")

    bundle = _bundle(repo, base)
    assert "brandnew.kt" in bundle.artifact["diff"]
    assert "fun main()" in bundle.artifact["diff"]
    assert "brandnew.kt" in bundle.artifact["files_changed"]


def test_no_base_falls_back_to_head_plus_untracked(repo):
    """Pre-existing loops have no recorded base; still surface uncommitted +
    untracked work via the HEAD fallback."""
    (repo / "seed.txt").write_text("seed\nmore\n")     # tracked edit, uncommitted
    (repo / "fresh.py").write_text("y = 2\n")          # untracked

    bundle = _bundle(repo, base="")
    assert "more" in bundle.artifact["diff"]
    assert "fresh.py" in bundle.artifact["diff"]


def test_start_records_base_commit(repo, tmp_path):
    xli = repo / ".xlii"
    xli.mkdir()
    ctrl = LoopController.start(
        xli_dir=xli, goal="g", judges=["tests"], max_cycles=3,
        test_command="true", project_root=repo,
    )
    head = git_cmd(repo, ["rev-parse", "HEAD"])[0].strip()
    assert ctrl.state.base_commit == head
