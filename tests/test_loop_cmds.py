"""REPL /loop command handler tests."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.repl_cmds import loop as loop_cmd
from tests.helpers import FakeConsole


def _ctx(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir()
    agent = SimpleNamespace(rail=None, plan_mode=False, debug=None)

    def set_mode(new):
        agent.rail = None
        agent.debug = None
        if new is not None:
            agent.plan_mode = False

    agent.set_mode = set_mode
    state = SimpleNamespace(
        agent=agent,
        project=SimpleNamespace(xli_dir=xli, project_root=tmp_path),
        cfg=SimpleNamespace(judges={}, loop_defaults={}),
        loop=None,
        plan_mode=False,
    )
    return {"console": FakeConsole(), "state": state, "agent": agent, "project": state.project}


def test_loop_start_sets_rewrite(tmp_path):
    ctx = _ctx(tmp_path)
    handled = loop_cmd.h_loop('/loop "fix the tests" --test true --max 2', ctx)
    assert handled is False
    assert "_loop_rewritten" in ctx
    assert ctx["state"].loop is not None
    assert "fix the tests" in ctx["_loop_rewritten"]


def test_loop_status_no_active(tmp_path):
    ctx = _ctx(tmp_path)
    assert loop_cmd.h_loop("/loop status", ctx) is True


def test_loop_blocks_while_rail_active(tmp_path):
    ctx = _ctx(tmp_path)
    ctx["agent"].rail = object()
    assert loop_cmd.h_loop('/loop "x"', ctx) is True


def test_parse_loop_start_push_flag(tmp_path):
    ctx = _ctx(tmp_path)
    opts, err = loop_cmd._parse_loop_start('"ship" --push each --commit each', ctx)
    assert err is None
    assert opts["push_mode"] == "each"
    assert opts["commit_mode"] == "each"


def test_parse_loop_start_rejects_bad_push(tmp_path):
    ctx = _ctx(tmp_path)
    _opts, err = loop_cmd._parse_loop_start('"ship" --push sometimes', ctx)
    assert err is not None
    assert "push" in err.lower()


def _git_repo_with_commit(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=tmp_path, check=True, capture_output=True,
        )

    git("init")
    (tmp_path / "a.txt").write_text("one\n")
    git("add", "-A")
    git("commit", "-m", "baseline")
    return git


def test_loop_refuses_dirty_tree(tmp_path):
    # The dirty-tree rail: a builder cycle judges (and may 'clean') the whole
    # tree — on 2026-07-10 one hard-reset staged work it decided was noise.
    ctx = _ctx(tmp_path)
    _git_repo_with_commit(tmp_path)
    (tmp_path / "a.txt").write_text("dirty\n")

    handled = loop_cmd.h_loop('/loop "x" --test true --max 2', ctx)
    assert handled is True                      # refused — no loop turn started
    assert ctx["state"].loop is None
    out = "\n".join(ctx["console"].lines)
    assert "not clean" in out
    assert "--allow-dirty" in out


def test_loop_allow_dirty_overrides(tmp_path):
    ctx = _ctx(tmp_path)
    _git_repo_with_commit(tmp_path)
    (tmp_path / "a.txt").write_text("dirty\n")

    handled = loop_cmd.h_loop('/loop "x" --test true --max 2 --allow-dirty', ctx)
    assert handled is False                     # loop started despite dirty tree
    assert ctx["state"].loop is not None
    out = "\n".join(ctx["console"].lines)
    assert "dirty tree" in out                  # but it warned loudly


def test_loop_clean_tree_starts_without_dirty_warning(tmp_path):
    ctx = _ctx(tmp_path)
    _git_repo_with_commit(tmp_path)

    handled = loop_cmd.h_loop('/loop "x" --test true --max 2', ctx)
    assert handled is False
    assert ctx["state"].loop is not None
    out = "\n".join(ctx["console"].lines)
    assert "not clean" not in out


def test_loop_refuses_when_git_status_unavailable(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    _git_repo_with_commit(tmp_path)
    monkeypatch.setattr(loop_cmd, "_dirty_tree_summary", lambda root: None)

    handled = loop_cmd.h_loop('/loop "x" --test true --max 2', ctx)
    assert handled is True
    assert ctx["state"].loop is None
    out = "\n".join(ctx["console"].lines)
    assert "could not verify working tree is clean" in out
