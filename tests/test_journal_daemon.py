"""Tests for JRN-2 — the bash-wide journal capture daemon (xlii/journal_daemon.py)
and the `xlii journal install/uninstall/serve` CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from xlii import journal as J
from xlii import journal_daemon as jd


def _make_project(root, *, name="proj", code_auto=False):
    """Create a minimal xlii project at `root`, optionally opted into journaling."""
    xli = root / ".xlii"
    xli.mkdir(parents=True, exist_ok=True)
    (xli / "project.json").write_text(json.dumps({
        "name": name, "collection_id": "", "created_at": "2026-06-27T00:00:00Z",
    }))
    if code_auto:
        jdir = xli / "journal"
        jdir.mkdir(parents=True, exist_ok=True)
        (jdir / "config.json").write_text(json.dumps({"code_auto": True}))
    return root


# -- feed parsing -----------------------------------------------------------

def test_parse_feed_line_valid():
    assert jd.parse_feed_line("1700000000\t/home/u/proj\tgit status") == (
        "1700000000", "/home/u/proj", "git status")


def test_parse_feed_line_rejects_malformed():
    assert jd.parse_feed_line("") is None
    assert jd.parse_feed_line("only-two\tfields") is None
    assert jd.parse_feed_line("ts\t/cwd\t") is None        # empty command
    assert jd.parse_feed_line("ts\t\tcmd") is None         # empty cwd


def test_parse_feed_line_keeps_tabs_in_command():
    # only the first two tabs split; the command may itself contain tabs
    assert jd.parse_feed_line("t\t/c\techo a\tb") == ("t", "/c", "echo a\tb")


# -- project routing --------------------------------------------------------

def test_find_project_root_walks_up(tmp_path):
    root = _make_project(tmp_path / "proj")
    deep = root / "a" / "b" / "c"
    deep.mkdir(parents=True)
    assert jd.find_project_root(deep) == root
    assert jd.find_project_root(root) == root


def test_find_project_root_none_outside_project(tmp_path):
    plain = tmp_path / "not-a-project"
    plain.mkdir()
    assert jd.find_project_root(plain) is None


# -- ingest routing + privacy gate -----------------------------------------

class _FakeJournal:
    def __init__(self):
        self.code_on = False
        self.observed = []
        self.flushed = 0

    def observe_turn(self, user_input, dirty, stats, *, cwd=""):
        self.observed.append((user_input, cwd))

    def flush(self):
        self.flushed += 1
        return True


def test_ingest_routes_only_opted_in_projects(tmp_path):
    opted = _make_project(tmp_path / "opted", name="opted", code_auto=True)
    other = _make_project(tmp_path / "other", name="other", code_auto=False)

    made = {}

    def factory(project):
        j = _FakeJournal()
        made[project.name] = j
        return j

    lines = [
        f"1\t{opted}\tgit status",
        f"2\t{opted / 'sub'}\tls -la",
        f"3\t{other}\trm -rf /",        # NOT opted in → dropped
        "bad line with no tabs",          # malformed → dropped
        f"4\t{tmp_path}\techo hi",        # not in any project → dropped
    ]
    counts = jd.ingest_lines(lines, journal_factory=factory)

    assert counts == {str(opted): 2}
    assert "opted" in made and "other" not in made   # gate kept `other` out
    j = made["opted"]
    assert [c for c, _ in j.observed] == ["git status", "ls -la"]
    assert j.code_on is True and j.flushed == 1


# -- tail offset ------------------------------------------------------------

def test_read_new_lines_advances_offset(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    feed = tmp_path / "feed.log"
    monkeypatch.setenv("XLII_JOURNAL_FEED", str(feed))
    feed.write_text("a\tb\tc\nd\te\tf\n")

    first = jd.read_new_lines()
    assert first == ["a\tb\tc", "d\te\tf"]
    assert jd.read_new_lines() == []          # nothing new → offset held

    with feed.open("a") as fh:
        fh.write("g\th\ti\n")
    assert jd.read_new_lines() == ["g\th\ti"]  # only the appended line


def test_read_new_lines_handles_truncation(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    feed = tmp_path / "feed.log"
    monkeypatch.setenv("XLII_JOURNAL_FEED", str(feed))
    # Consume a sizable feed so the saved offset is large…
    feed.write_text("aaaa\tbbbb\tcccc\ndddd\teeee\tffff\n")
    assert len(jd.read_new_lines()) == 2
    # …then shrink it (rotation/truncation): offset > new size → restart from 0.
    feed.write_text("x\ty\tz\n")
    assert jd.read_new_lines() == ["x\ty\tz"]


def test_tick_does_not_advance_offset_when_ingest_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    feed = tmp_path / "feed.log"
    monkeypatch.setenv("XLII_JOURNAL_FEED", str(feed))
    feed.write_text("1\t/cwd\tgit status\n2\t/cwd\tpytest -q\n")

    def boom(records, **kwargs):
        raise RuntimeError("ingest interrupted")

    monkeypatch.setattr(jd, "_ingest_line_records_outcome", boom)
    with pytest.raises(RuntimeError, match="ingest interrupted"):
        jd.tick()

    assert not jd.offset_path().exists()

    seen = []

    def capture(records, **kwargs):
        seen.extend(line for line, _offset in records)
        return jd._IngestOutcome(
            {"/cwd": len(records)},
            committed_offset=records[-1][1],
        )

    monkeypatch.setattr(jd, "_ingest_line_records_outcome", capture)
    assert jd.tick() == 2
    assert seen == ["1\t/cwd\tgit status", "2\t/cwd\tpytest -q"]
    assert jd._read_offset() == feed.stat().st_size


def test_tick_does_not_advance_offset_when_entry_write_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    feed = tmp_path / "feed.log"
    monkeypatch.setenv("XLII_JOURNAL_FEED", str(feed))
    proj = _make_project(tmp_path / "proj", name="proj", code_auto=True)
    feed.write_text(f"1\t{proj}\tgit status\n")

    original_write = J.write_text_atomic
    fail_entries = {"on": True}

    def write_or_fail(path, text, **kwargs):
        if fail_entries["on"] and Path(path).parent.name == "entries":
            raise OSError("simulated durable write failure")
        return original_write(path, text, **kwargs)

    monkeypatch.setattr(J, "write_text_atomic", write_or_fail)

    assert jd.tick() == 0
    assert jd._read_offset() == 0
    entries_dir = proj / ".xlii" / "journal" / "entries"
    assert not (entries_dir.exists() and any(entries_dir.glob("*.md")))

    fail_entries["on"] = False
    assert jd.tick() == 1
    assert jd._read_offset() == feed.stat().st_size
    entries = list(entries_dir.glob("*.md"))
    assert len(entries) == 1
    assert "git status" in entries[0].read_text()


def test_tick_retries_failed_entry_without_replaying_successful_prefix(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    feed = tmp_path / "feed.log"
    monkeypatch.setenv("XLII_JOURNAL_FEED", str(feed))
    proj = _make_project(tmp_path / "proj", name="proj", code_auto=True)
    feed.write_text(f"1\t{proj}\tfirst command\n2\t{proj}\tsecond command\n")

    original_write = J.write_text_atomic
    failed_second = {"done": False}

    def write_or_fail(path, text, **kwargs):
        if (
            not failed_second["done"]
            and Path(path).parent.name == "entries"
            and "second command" in text
        ):
            failed_second["done"] = True
            raise OSError("simulated durable write failure")
        return original_write(path, text, **kwargs)

    monkeypatch.setattr(J, "write_text_atomic", write_or_fail)

    assert jd.tick() == 1
    first_offset = jd._read_offset()
    assert 0 < first_offset < feed.stat().st_size

    entries_dir = proj / ".xlii" / "journal" / "entries"
    first_entries = list(entries_dir.glob("*.md"))
    assert len(first_entries) == 1
    assert "first command" in first_entries[0].read_text()

    assert jd.tick() == 1
    assert jd._read_offset() == feed.stat().st_size
    goals = sorted(
        p.read_text().split("**goal:** ", 1)[1].split("\n", 1)[0]
        for p in entries_dir.glob("*.md")
    )
    assert goals == ["first command", "second command"]


def test_tick_advances_offset_after_successful_ingest(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    feed = tmp_path / "feed.log"
    monkeypatch.setenv("XLII_JOURNAL_FEED", str(feed))
    feed.write_text("1\t/cwd\tgit status\n")

    calls = []

    def capture(records, **kwargs):
        calls.append([line for line, _offset in records])
        return jd._IngestOutcome(
            {"/cwd": len(records)},
            committed_offset=records[-1][1],
        )

    monkeypatch.setattr(jd, "_ingest_line_records_outcome", capture)
    assert jd.tick() == 1
    assert calls == [["1\t/cwd\tgit status"]]
    assert jd._read_offset() == feed.stat().st_size
    assert jd.tick() == 0
    assert calls == [["1\t/cwd\tgit status"]]


# -- PID supervision / liveness --------------------------------------------

def test_daemon_running_true_for_live_pid(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    jd.runtime_dir().mkdir(parents=True, exist_ok=True)
    jd.pidfile_path().write_text(str(os.getpid()))   # our own pid is alive
    assert jd.read_pid() == os.getpid()
    assert jd.daemon_running() is True


def test_daemon_running_false_for_dead_or_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    assert jd.daemon_running() is False               # no pidfile
    jd.runtime_dir().mkdir(parents=True, exist_ok=True)
    jd.pidfile_path().write_text("2147483646")        # almost certainly not a live pid
    assert jd.daemon_running() is False


# -- serve --------------------------------------------------------------

def test_serve_is_idempotent_when_already_running(monkeypatch):
    monkeypatch.setattr(jd, "daemon_running", lambda: True)
    # Must return immediately without writing a pid or looping.
    called = {"tick": 0}
    monkeypatch.setattr(jd, "tick", lambda **kw: called.__setitem__("tick", called["tick"] + 1))
    assert jd.serve() == 0
    assert called["tick"] == 0


def test_serve_run_once_ingests_feed(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    feed = tmp_path / "feed.log"
    monkeypatch.setenv("XLII_JOURNAL_FEED", str(feed))
    monkeypatch.setattr(jd, "_load_cfg_pool", lambda: (None, None))  # no LLM/clients
    proj = _make_project(tmp_path / "proj", name="proj", code_auto=True)
    feed.write_text(f"1\t{proj}\tgit commit\n2\t{proj}\tpytest -q\n")

    assert jd.serve(run_once=True) == 0

    # Raw entries land locally even without clients (summary/archive are skipped).
    entries = list((proj / ".xlii" / "journal" / "entries").glob("*.md"))
    assert len(entries) == 2
    blob = "\n".join(p.read_text() for p in entries)
    assert "git commit" in blob and "pytest -q" in blob
    assert jd.read_pid() is None   # pid cleaned up on exit


# -- hook script ------------------------------------------------------------

def test_hook_script_is_gated_and_forkfree():
    s = jd.hook_script()
    assert 'trap' in s and 'DEBUG' in s
    assert 'XLII_JOURNAL' in s            # gated: no-op unless armed
    assert 'printf' in s                  # builtin append, not `echo`/external
    assert ' ls ' not in s                # no per-command `ls` (would fork)
    assert '__xlii_jrnl*' in s            # recursion guard


# -- install / uninstall (reversible bashrc block) --------------------------

def test_install_then_uninstall_roundtrip(tmp_path, monkeypatch):
    rc = tmp_path / "bashrc"
    hook = tmp_path / "cfg" / "journal.sh"
    rc.write_text("# existing rc\nexport PATH=$PATH\n")
    monkeypatch.setenv("XLII_BASHRC", str(rc))
    monkeypatch.setenv("XLII_JOURNAL_HOOK", str(hook))
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))

    from xlii.cmds import journal as cj

    assert cj.cmd_journal_install(SimpleNamespace()) == 0
    body = rc.read_text()
    assert cj._RC_BEGIN in body and cj._RC_END in body
    assert f'source "{hook}"' in body
    assert "# existing rc" in body          # didn't clobber prior content
    assert hook.is_file() and "trap" in hook.read_text()

    # idempotent: a second install doesn't add a second block
    assert cj.cmd_journal_install(SimpleNamespace()) == 0
    assert rc.read_text().count(cj._RC_BEGIN) == 1

    # uninstall removes exactly the block and the hook file, keeps the rest
    assert cj.cmd_journal_uninstall(SimpleNamespace()) == 0
    after = rc.read_text()
    assert cj._RC_BEGIN not in after and "source" not in after
    assert "# existing rc" in after and "export PATH=$PATH" in after
    assert not hook.exists()


def test_uninstall_noop_when_not_installed(tmp_path, monkeypatch):
    rc = tmp_path / "bashrc"
    rc.write_text("# nothing here\n")
    monkeypatch.setenv("XLII_BASHRC", str(rc))
    monkeypatch.setenv("XLII_JOURNAL_HOOK", str(tmp_path / "hook.sh"))
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    from xlii.cmds import journal as cj
    assert cj.cmd_journal_uninstall(SimpleNamespace()) == 0
    assert rc.read_text() == "# nothing here\n"   # untouched
