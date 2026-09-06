"""/gitpain — Gitpanel source control (GP0–GP2 + sweep).

Stash requires a message on the primary path (positional or ``-m``); ``/git`` remains a
deprecated alias; ``sweep`` seeds cleanup commands (vs the DEFAULT branch, worktrees
removed before their branches); ``commit journal`` needs the code project journal, and
the post-commit journal line only ever records a verified commit.
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


def _ctx(cwd: Path, *, journal=None) -> dict:
    from rich.console import Console

    state = SimpleNamespace(
        shell_cwd=Path(cwd),
        project=SimpleNamespace(project_root=Path(cwd), xli_dir=Path(cwd) / ".xlii"),
        journal=journal,
    )
    return {"console": Console(), "state": state}


def _dispatch(line: str, ctx: dict) -> bool:
    from xlii.commands import dispatch_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    return dispatch_repl_command(line, ctx)


def _stash_count(root: Path) -> int:
    out, _ = git_cmd(root, ["stash", "list"])
    return len([ln for ln in out.splitlines() if ln.strip()])


def test_gitpain_stash_without_message_is_refused(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    ctx = _ctx(tmp_path)
    _dispatch("/gitpain stash", ctx)
    assert _stash_count(tmp_path) == 0


def test_gitpain_stash_with_message(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    ctx = _ctx(tmp_path)
    _dispatch('/gitpain stash -m "pause: auth race"', ctx)
    assert _stash_count(tmp_path) == 1
    out, _ = git_cmd(tmp_path, ["stash", "list"])
    assert "pause: auth race" in out


def test_git_alias_still_stashes_without_message(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    ctx = _ctx(tmp_path)
    _dispatch("/git stash", ctx)
    assert _stash_count(tmp_path) == 1


def test_gitpain_and_git_stage_equivalence(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["checkout", "-b", "feature"])
    _dispatch("/gitpain stage tracked.txt", _ctx(tmp_path))
    from xlii.git_status import staged_status_map

    assert "tracked.txt" in staged_status_map(tmp_path)
    git_cmd(tmp_path, ["reset", "-q", "HEAD"])
    _dispatch("/git stage tracked.txt", _ctx(tmp_path))
    assert "tracked.txt" in staged_status_map(tmp_path)


def test_sweep_prefills_cleanup_commands(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    git_cmd(tmp_path, ["checkout", "-b", "merged-feature"])
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["commit", "-am", "feature work"])
    git_cmd(tmp_path, ["checkout", "master"])
    git_cmd(tmp_path, ["merge", "--no-ff", "merged-feature", "-m", "merge"])
    ctx = _ctx(tmp_path)
    _dispatch("/gitpain sweep", ctx)
    seeded = getattr(ctx["state"], "pending_input", "")
    assert "git branch -d merged-feature" in seeded


def test_commit_journal_offline_without_journal(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["add", "tracked.txt"])
    import xlii.wiki_author as WA

    called = []
    monkeypatch.setattr(WA, "session_completer", lambda state, **k: called.append(1) or None)
    ctx = _ctx(tmp_path, journal=None)
    _dispatch("/gitpain commit journal", ctx)
    assert not called
    assert getattr(ctx["state"], "pending_input", "") == ""


def test_commit_journal_drafts_when_journal_present(tmp_path, monkeypatch):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["add", "tracked.txt"])

    class _FakeJournal:
        def read_summary(self):
            return "fixed the race in auth refresh"

        def _recent_local_entries(self, *, limit: int):
            return "- tuned token TTL"

        def is_recording(self):
            return False

    import xlii.wiki_author as WA

    monkeypatch.setattr(WA, "session_completer", lambda state, **k: (lambda msgs: "feat(auth): refresh tokens"))
    ctx = _ctx(tmp_path, journal=_FakeJournal())
    _dispatch("/gitpain commit journal", ctx)
    assert ctx["state"].pending_input == "/gitpain commit feat(auth): refresh tokens"

def test_gitpain_stash_with_positional_message(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    ctx = _ctx(tmp_path)
    _dispatch("/gitpain stash pause: auth race", ctx)
    assert _stash_count(tmp_path) == 1
    out, _ = git_cmd(tmp_path, ["stash", "list"])
    assert "pause: auth race" in out


class _RecordingJournal:
    """The journal surface _maybe_journal_commit touches: recording on, observe captured."""

    def __init__(self):
        self.observed = []

    def is_recording(self):
        return True

    def observe_turn(self, text, refs, meta, *, cwd=None):
        self.observed.append(text)


def test_failed_commit_does_not_journal(tmp_path):
    """Nothing staged → the commit does not happen → no journal line (the fail-open
    'committed <old-hash>' bug)."""
    _init_repo(tmp_path)
    journal = _RecordingJournal()
    ctx = _ctx(tmp_path, journal=journal)
    _dispatch("/gitpain commit nothing was staged", ctx)
    out, _ = git_cmd(tmp_path, ["log", "--oneline"])
    assert "nothing was staged" not in out
    assert journal.observed == []


def test_successful_commit_journals_the_new_hash(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / "tracked.txt").write_text("a\nb\n")
    git_cmd(tmp_path, ["add", "tracked.txt"])
    journal = _RecordingJournal()
    ctx = _ctx(tmp_path, journal=journal)
    _dispatch("/gitpain commit fix: tighten auth", ctx)
    assert len(journal.observed) == 1
    line = journal.observed[0]
    out, _ = git_cmd(tmp_path, ["rev-parse", "--short", "HEAD"])
    assert line == f"committed {out.strip()} — fix: tighten auth"
