"""/git — the review-before-run source-control mutator (the ``git://`` doorway's write side).

The GitPane seeds these into the command line; here we drive them directly and assert the repo
actually changed: stage/unstage/discard/commit (+ stage-all), an empty-message commit is refused,
and a non-git dir degrades to a nudge, not a crash.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from xlii.loop_bundle import git_cmd


def _init_repo(root: Path) -> Path:
    git_cmd(root, ["init"])
    git_cmd(root, ["config", "user.email", "t@e"])
    git_cmd(root, ["config", "user.name", "T"])
    (root / "tracked.txt").write_text("a\n")
    git_cmd(root, ["add", "tracked.txt"])
    git_cmd(root, ["commit", "-m", "init"])
    return root


def _ctx(cwd: Path) -> dict:
    from rich.console import Console

    state = SimpleNamespace(shell_cwd=Path(cwd),
                            project=SimpleNamespace(project_root=Path(cwd), xli_dir=Path(cwd) / ".xlii"))
    return {"console": Console(), "state": state}


def _dispatch(line: str, ctx: dict) -> bool:
    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    return dispatch_repl_command(line, ctx)


def _staged(root: Path) -> dict:
    from xlii.git_status import staged_status_map

    return staged_status_map(root)


def _subject(root: Path) -> str:
    out, _ = git_cmd(root, ["log", "-1", "--pretty=%s"])
    return out.strip()


def test_stage_then_commit(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    ctx = _ctx(tmp_path)
    _dispatch("/git stage tracked.txt", ctx)
    assert "tracked.txt" in _staged(tmp_path)
    _dispatch("/git commit added line b", ctx)
    assert _subject(tmp_path) == "added line b"
    assert _staged(tmp_path) == {}      # nothing left staged after the commit


def test_unstage(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["add", "tracked.txt"])
    ctx = _ctx(tmp_path)
    assert "tracked.txt" in _staged(tmp_path)
    _dispatch("/git unstage tracked.txt", ctx)
    assert "tracked.txt" not in _staged(tmp_path)


def test_discard_restores_working_tree(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nGONE\n")
    _dispatch("/git discard tracked.txt", _ctx(tmp_path))
    assert (tmp_path / "tracked.txt").read_text() == "a\n"


def test_stage_all(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    (tmp_path / "new.txt").write_text("n\n")
    _dispatch("/git stage-all", _ctx(tmp_path))
    staged = _staged(tmp_path)
    assert "tracked.txt" in staged and "new.txt" in staged


def test_commit_without_message_is_refused(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["add", "tracked.txt"])
    _dispatch("/git commit", _ctx(tmp_path))       # no message → no-op
    assert "tracked.txt" in _staged(tmp_path)      # still staged
    assert _subject(tmp_path) == "init"            # no new commit


def _head(root: Path) -> str:
    out, _ = git_cmd(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    return out.strip()


def test_branch_create_then_switch_existing(tmp_path):
    _init_repo(tmp_path)
    start = _head(tmp_path)
    ctx = _ctx(tmp_path)
    _dispatch("/git branch feature-x", ctx)     # new name → create + switch
    assert _head(tmp_path) == "feature-x"
    _dispatch(f"/git branch {start}", ctx)       # existing name → plain switch
    assert _head(tmp_path) == start


def test_stash_then_pop(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    ctx = _ctx(tmp_path)
    _dispatch("/git stash", ctx)
    assert (tmp_path / "tracked.txt").read_text() == "a\n"   # the change is stashed away
    out, _ = git_cmd(tmp_path, ["stash", "list"])
    assert out.strip()                                       # a stash exists
    _dispatch("/git stash pop", ctx)
    assert (tmp_path / "tracked.txt").read_text() == "a\nb\n"  # restored


def _fake_completer(reply: str):
    """Patch the session completer so /git generate uses a canned model reply (no network)."""
    return lambda state, **k: (lambda messages: reply)


def test_generate_seeds_commit_for_review(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["add", "tracked.txt"])
    import xlii.wiki_author as WA
    monkeypatch.setattr(WA, "session_completer", _fake_completer("`feat: add line b`"))
    ctx = _ctx(tmp_path)
    _dispatch("/git generate", ctx)
    # the model draft is cleaned (backticks stripped) and seeded for review — NOT committed
    assert ctx["state"].pending_input == "/git commit feat: add line b"
    assert _subject(tmp_path) == "init"        # nothing committed yet


def test_generate_without_model_is_a_nudge(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["add", "tracked.txt"])
    import xlii.wiki_author as WA
    monkeypatch.setattr(WA, "session_completer", lambda state, **k: None)
    ctx = _ctx(tmp_path)
    _dispatch("/git generate", ctx)
    assert getattr(ctx["state"], "pending_input", "") == ""   # nothing seeded without a model


def test_generate_nothing_to_describe(tmp_path, monkeypatch):
    _init_repo(tmp_path)  # clean tree
    import xlii.wiki_author as WA
    called = []
    monkeypatch.setattr(WA, "session_completer", lambda state, **k: called.append(1) or _fake_completer("x")(state))
    ctx = _ctx(tmp_path)
    _dispatch("/git generate", ctx)
    assert not called                                          # bailed before reaching the model
    assert getattr(ctx["state"], "pending_input", "") == ""


def test_git_outside_a_repo_is_a_nudge(tmp_path):
    # a plain dir → the command reports "not inside a git repository" and returns handled (no crash)
    assert _dispatch("/git status", _ctx(tmp_path)) is True
