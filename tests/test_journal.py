"""JRN-1 — Project Shadow, the in-process per-project journal.

Recorder (journalist): one coarse turn-level entry per task, batched into a
rolling summary + a journal Collection. Teacher (/askjo): the same voice,
grounded strictly in what was recorded. Off by default; code REPL only.
"""

from __future__ import annotations

from types import SimpleNamespace

import xlii.journal as J
from xlii.journal import (
    JOURNAL_ENTRIES_KEEP,
    ProjectJournal,
    build_project_journal,
    read_journal_auto,
)
from xlii.config import ProjectConfig
from xlii.sync import init_project
from tests.helpers import make_cfg


# --------------------------------------------------------------------------- #
#  fakes: an xAI clients stub (collections create/upload/search) + a pool
# --------------------------------------------------------------------------- #

class _FakeColls:
    def __init__(self, search_texts=None):
        self.created: list[tuple[str, str]] = []
        self.uploaded: list[tuple[str, str]] = []
        self._search_texts = list(search_texts or [])

    def create(self, name, field_definitions=None):
        cid = f"jcoll-{len(self.created)}"
        self.created.append((cid, name))
        return SimpleNamespace(collection_id=cid)

    def upload_document(self, collection_id, name, data, fields=None):
        self.uploaded.append((collection_id, name))
        return SimpleNamespace(file_metadata=SimpleNamespace(file_id=f"f{len(self.uploaded)}"))

    def search(self, query, collection_ids, limit, retrieval_mode):
        chunks = [SimpleNamespace(text=t) for t in self._search_texts]
        return SimpleNamespace(results=chunks)


def _clients(search_texts=None):
    return SimpleNamespace(xai=SimpleNamespace(collections=_FakeColls(search_texts)), chat=None)


class _Pool:
    def __init__(self, clients, journal_client=None):
        self._c = clients
        self._j = journal_client

    def primary(self):
        return self._c

    def journal_client(self):
        return self._j


def _synced_project(root, name="proj"):
    init_project(None, root, name=name, existing_collection_id="cmain")
    return ProjectConfig.load(root)


def _journal(root, *, name="proj", code_on=False, batch=5, local_only=False,
             clients=None, journal_client=None, cfg=None):
    if local_only:
        init_project(None, root, name=name, local_only=True)
        proj = ProjectConfig.load(root)
    else:
        proj = _synced_project(root, name)
    clients = clients if clients is not None else _clients()
    j = J.ProjectJournal(
        project=proj, pool=_Pool(clients, journal_client), cfg=cfg or make_cfg(),
        code_on=code_on, batch_size=batch,
    )
    return j, proj, clients


# --------------------------------------------------------------------------- #
#  recorder
# --------------------------------------------------------------------------- #

def test_journal_off_by_default_is_a_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, _proj, _c = _journal(tmp_path, code_on=False)
    j.observe_turn("add a feature", {"a.py"}, SimpleNamespace(tool_calls=2))
    assert j.pending() == 0
    assert not (j.entries_dir.exists() and any(j.entries_dir.glob("*.md")))


def test_recording_buffers_and_writes_local_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, _proj, _c = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("wire up auth", {"auth.py", "tests/test_auth.py"},
                   SimpleNamespace(tool_calls=4), cwd=str(tmp_path))
    assert j.pending() == 1
    files = list(j.entries_dir.glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text()
    assert "wire up auth" in body and "auth.py" in body


def test_batched_flush_summarizes_and_archives(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_complete(clients, model, messages, **kw):
        calls["n"] += 1
        return "## Recent focus\nauth\n## Files & areas touched\n- auth.py\n## Patterns & decisions\n-\n## Open threads\n-"

    monkeypatch.setattr(J, "_journal_complete", fake_complete)
    j, proj, clients = _journal(tmp_path, code_on=True, batch=2)
    j.observe_turn("t1", {"a.py"}, SimpleNamespace(tool_calls=1))
    assert clients.xai.collections.uploaded == []     # not yet (below batch)
    j.observe_turn("t2", {"b.py"}, SimpleNamespace(tool_calls=1))  # hits batch → flush
    # summary rolled once, both raw entries archived to a freshly-created collection
    assert calls["n"] == 1
    assert j.read_summary().startswith("## Recent focus")
    assert len(clients.xai.collections.uploaded) == 2
    assert len(clients.xai.collections.created) == 1
    # the journal Collection id is recorded on project.json so A3 can delete it
    assert ProjectConfig.load(tmp_path).journal_collection_id == "jcoll-0"


def test_code_off_flushes_remaining_buffer(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, _proj, clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("one", {"a.py"}, SimpleNamespace(tool_calls=1))
    assert j.pending() == 1
    j.set_code(False)                                  # turning off flushes
    assert j.pending() == 0
    assert len(clients.xai.collections.uploaded) == 1


def test_local_only_keeps_entries_without_a_collection(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, _proj, clients = _journal(tmp_path, code_on=True, batch=1, local_only=True)
    j.observe_turn("local task", {"x.py"}, SimpleNamespace(tool_calls=1))
    assert clients.xai.collections.created == []        # no remote Collection
    assert clients.xai.collections.uploaded == []
    assert list(j.entries_dir.glob("*.md"))            # raw entry still kept locally


def test_local_only_journal_is_searchable_via_local_backend(tmp_path, monkeypatch):
    # A local-only project's journal used to be search-dark (_rag_search returned
    # ""). It now persists flushed entries into its own LocalBackend so /askjo can
    # recall them offline — without ever touching the remote Collection.
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, _proj, clients = _journal(tmp_path, code_on=True, batch=1, local_only=True)
    j.observe_turn("wire up the OAUTHLOGIN flow", {"auth.py"}, SimpleNamespace(tool_calls=2))

    assert clients.xai.collections.created == []        # still fully offline
    # the journal's own store exists, separate from any project-level store
    assert (j.journal_dir / "journal_backend.db").exists()
    assert not (_proj.xli_dir / "local_backend.db").exists()
    # and the flushed entry is now searchable
    hits = j._rag_search(clients, "OAUTHLOGIN", limit=5)
    assert "OAUTHLOGIN" in hits


# --------------------------------------------------------------------------- #
#  auto-enable persistence
# --------------------------------------------------------------------------- #

def test_code_auto_persists_and_reloads(tmp_path):
    proj = _synced_project(tmp_path)
    j = ProjectJournal(project=proj, pool=None, cfg=make_cfg())
    j.set_code(True, persist_auto=True)
    assert read_journal_auto(proj) is True
    # a fresh session for the same project starts ON
    state = SimpleNamespace(project=proj, pool=None, cfg=make_cfg(), agent=None, console=None)
    assert build_project_journal(state).is_recording() is True


def test_code_off_clears_persisted_auto(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    proj = _synced_project(tmp_path)
    j = ProjectJournal(project=proj, pool=_Pool(_clients()), cfg=make_cfg(), code_on=True)
    j.set_code(True, persist_auto=True)
    j.set_code(False, persist_auto=False)
    assert read_journal_auto(proj) is False


# --------------------------------------------------------------------------- #
#  teacher (/askjo)
# --------------------------------------------------------------------------- #

def test_askjo_grounds_in_summary_and_entries(tmp_path, monkeypatch):
    seen = {}

    def fake_complete(clients, model, messages, **kw):
        seen["messages"] = messages
        return "You added auth.py and wired tests — grounded answer."

    monkeypatch.setattr(J, "_journal_complete", fake_complete)
    j, _proj, _c = _journal(tmp_path, code_on=True, batch=1)
    # produce some recorded history
    j.observe_turn("wire up auth", {"auth.py"}, SimpleNamespace(tool_calls=3))
    answer = j.askjo("what did we do with auth?")
    assert "grounded answer" in answer
    # the prompt carried the Shadow identity + the recorded context
    sys_msg = seen["messages"][0]["content"]
    user_msg = seen["messages"][1]["content"]
    assert "Project Shadow" in sys_msg
    assert "auth.py" in user_msg


def test_askjo_with_no_history_declines(tmp_path):
    j, _proj, _c = _journal(tmp_path, code_on=False)
    msg = j.askjo("anything?")
    assert "nothing recorded" in msg.lower() or "haven't recorded" in msg.lower()


def test_recall_context_is_the_block_askjo_grounds_on(tmp_path, monkeypatch):
    # The shared read half: recall_context assembles the same context block that
    # askjo folds into its Shadow prompt — extraction is behavior-preserving.
    seen = {}

    def fake_complete(clients, model, messages, **kw):
        seen["user"] = messages[1]["content"]
        return "grounded."

    monkeypatch.setattr(J, "_journal_complete", fake_complete)
    j, _proj, _c = _journal(tmp_path, code_on=True, batch=1)
    j.observe_turn("wire up auth", {"auth.py"}, SimpleNamespace(tool_calls=3))

    ctx = j.recall_context("what did we do with auth?", wiki_context="WIKI-BLOCK")
    assert "auth.py" in ctx                       # recent entries folded in
    assert "## From the project wiki" in ctx and "WIKI-BLOCK" in ctx
    # askjo's user message embeds exactly that block (byte-for-byte substring).
    j.askjo("what did we do with auth?", wiki_context="WIKI-BLOCK")
    assert ctx in seen["user"]


def test_recall_context_empty_when_nothing_recorded(tmp_path):
    j, _proj, _c = _journal(tmp_path, code_on=False)
    assert j.recall_context("anything?") == ""


# --------------------------------------------------------------------------- #
#  status surface
# --------------------------------------------------------------------------- #

def test_status_badge_lights_only_while_recording(tmp_path):
    j, _proj, _c = _journal(tmp_path, code_on=False)
    assert j.status_badge() == ""
    j.code_on = True
    assert j.status_badge() == "jrnl●"
    # presence only — never a pending count (it reset per session and always
    # under-reported, so it read as lower than what the journal really knows)
    j.buffer.append(J.JournalEntry(ts="t", goal="g", files=[], tool_calls=0))
    assert j.status_badge() == "jrnl●"


def test_status_reader_in_strip(tmp_path):
    from xlii.tui import status as S
    j, _proj, _c = _journal(tmp_path, code_on=True)
    st = SimpleNamespace(journal=j)
    assert S.journal(st) == "jrnl●"
    j.code_on = False
    assert S.journal(st) == "jrnl○"             # always visible when off (D8)


# --------------------------------------------------------------------------- #
#  fast exit — defer_flush + catch-up via the deferred spool
# --------------------------------------------------------------------------- #

def _tick_clock(monkeypatch):
    """Deterministic, strictly-increasing entry timestamps so raw filenames
    (and the pruner's lexical ordering) never collide on same-second entries."""
    counter = {"n": 0}

    def _fake_now():
        counter["n"] += 1
        return f"2026-07-11T10:{counter['n'] // 60:02d}:{counter['n'] % 60:02d}+00:00"

    monkeypatch.setattr(J, "_now_iso", _fake_now)


def _rebuilt(proj, clients, *, batch=5, pool=None):
    """A fresh-session journal over the same project (same fakes)."""
    return ProjectJournal(project=proj, pool=pool if pool is not None else _Pool(clients),
                          cfg=make_cfg(), code_on=True, batch_size=batch)


def test_defer_flush_is_instant_and_next_session_catches_up(tmp_path, monkeypatch):
    _tick_clock(monkeypatch)
    calls = {"n": 0}

    def fake_complete(clients, model, messages, **kw):
        calls["n"] += 1
        return "SUMMARY"

    monkeypatch.setattr(J, "_journal_complete", fake_complete)
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("t1", {"a.py"}, SimpleNamespace(tool_calls=1))
    j.observe_turn("t2", {"b.py"}, SimpleNamespace(tool_calls=1))

    assert j.defer_flush() == 2                        # instant: no LLM, no uploads
    assert calls["n"] == 0 and clients.xai.collections.uploaded == []
    assert j.pending() == 0
    assert len(j._spooled_names()) == 2                # the tail is spooled, durably

    # a fresh session over the same project sees and heals the deferred tail
    j2 = _rebuilt(proj, clients)
    assert j2.has_pending_catchup() is True
    assert j2.catch_up() == 2
    assert calls["n"] == 1                             # one rolled summary
    assert len(clients.xai.collections.uploaded) == 2
    assert j2.read_summary() == "SUMMARY"
    # idempotent: the spool was consumed, nothing re-feeds
    assert j2.has_pending_catchup() is False
    assert j2.catch_up() == 0
    assert calls["n"] == 1
    assert not j2.deferred_spool_path.exists()


def test_defer_flush_twice_spools_once(tmp_path, monkeypatch):
    _tick_clock(monkeypatch)
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("t1", {"a.py"}, SimpleNamespace(tool_calls=1))
    assert j.defer_flush() == 1
    assert j.defer_flush() == 0                        # buffer already drained
    assert len(j._spooled_names()) == 1


def test_defer_flush_keeps_buffer_when_spool_append_fails(tmp_path, monkeypatch):
    _tick_clock(monkeypatch)
    j, _proj, _clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("t1", {"a.py"}, SimpleNamespace(tool_calls=1))
    monkeypatch.setattr(j, "_spool_append", lambda _names: False)

    assert j.defer_flush() == 0
    assert j.pending() == 1
    assert not j.deferred_spool_path.exists()


def test_kill_without_defer_keeps_old_semantics(tmp_path, monkeypatch):
    """A kill (no defer_flush ran) leaves raw files but nothing spooled — the
    tail is NOT auto-summarized (same as before fast exit) and never inferred
    from disk, because on-disk entries are indistinguishable from a live
    session's buffer."""
    _tick_clock(monkeypatch)
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("orphan", {"a.py"}, SimpleNamespace(tool_calls=1))
    # …session dies here: no defer, raw file on disk, spool empty

    j2 = _rebuilt(proj, clients, batch=1)
    assert j2.has_pending_catchup() is False
    j2.observe_turn("new", {"b.py"}, SimpleNamespace(tool_calls=1))  # batch=1 → flush
    names = [n for _cid, n in clients.xai.collections.uploaded]
    assert len(names) == 1                             # only the new entry — no stealing


def test_another_instance_never_steals_live_or_deferred_entries(tmp_path, monkeypatch):
    """The JRN-2 daemon (or a second session) builds its own ProjectJournal over
    the same journal dir. Its plain flush() must consume only its OWN buffer:
    neither the live session's buffered-on-disk entries nor a deferred spool."""
    _tick_clock(monkeypatch)
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    live, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    live.observe_turn("live-buffered", {"a.py"}, SimpleNamespace(tool_calls=1))

    other = _rebuilt(proj, clients, batch=1)           # daemon-like second instance
    other.observe_turn("bash-cmd", {}, SimpleNamespace(tool_calls=0))  # → its flush
    names = [n for _cid, n in clients.xai.collections.uploaded]
    assert len(names) == 1                             # its own entry only
    assert live.pending() == 1                         # live buffer untouched

    live.defer_flush()                                 # now the tail is spooled
    other.observe_turn("bash-cmd-2", {}, SimpleNamespace(tool_calls=0))
    names = [n for _cid, n in clients.xai.collections.uploaded]
    assert len(names) == 2                             # still no spool theft by flush
    assert len(live._spooled_names()) == 1             # spool intact for catch_up


def test_catchup_without_clients_leaves_spool_intact(tmp_path, monkeypatch):
    """Offline / key-less open must NOT burn the deferred tail: catch_up gates
    on clients before claiming, the spool survives, the pruner protects the raw
    files, and a later keyed session completes the work."""
    _tick_clock(monkeypatch)
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("t1", {"a.py"}, SimpleNamespace(tool_calls=1))
    j.observe_turn("t2", {"b.py"}, SimpleNamespace(tool_calls=1))
    j.defer_flush()

    offline = _rebuilt(proj, clients, pool=_Pool(None))  # pool.primary() → None
    offline.pool = None                                  # no clients at all
    assert offline.catch_up() == 0
    assert len(offline._spooled_names()) == 2            # spool untouched
    assert offline._prune_raw_entries(keep=1) == 0       # raw files protected

    keyed = _rebuilt(proj, clients)
    assert keyed.catch_up() == 2                          # later session completes it
    assert len(clients.xai.collections.uploaded) == 2


def test_catchup_skips_pruned_or_missing_spooled_files(tmp_path, monkeypatch):
    _tick_clock(monkeypatch)
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("t1", {"a.py"}, SimpleNamespace(tool_calls=1))
    j.observe_turn("t2", {"b.py"}, SimpleNamespace(tool_calls=1))
    j.defer_flush()
    # one raw file vanishes out-of-band (hand-deleted)
    victim = sorted(j.entries_dir.glob("entry-*.md"))[0]
    victim.unlink()
    j2 = _rebuilt(proj, clients)
    assert j2.catch_up() == 1                             # the survivor, no crash
    assert not j2.deferred_spool_path.exists()


def test_prune_never_deletes_spooled_entries(tmp_path, monkeypatch):
    _tick_clock(monkeypatch)
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    for i in range(3):
        j.observe_turn(f"t{i}", set(), SimpleNamespace(tool_calls=0))
    j.defer_flush()
    assert j._prune_raw_entries(keep=1) == 0           # all 3 spooled → protected
    j2 = _rebuilt(proj, clients)
    assert j2.catch_up() == 3
    assert j2._prune_raw_entries(keep=1) == 2          # consumed → prunable again


def test_prune_without_spool_keeps_old_behavior(tmp_path, monkeypatch):
    _tick_clock(monkeypatch)
    j, _proj, _c = _journal(tmp_path, code_on=True, batch=10)
    for i in range(3):
        j.observe_turn(f"t{i}", set(), SimpleNamespace(tool_calls=0))
    assert j._prune_raw_entries(keep=1) == 2           # nothing spooled: prune the head


def test_spool_claim_is_single_winner(tmp_path, monkeypatch):
    _tick_clock(monkeypatch)
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("t1", {"a.py"}, SimpleNamespace(tool_calls=1))
    j.defer_flush()
    a, b = _rebuilt(proj, clients), _rebuilt(proj, clients)
    first, claim_a = a._spool_claim()
    second, claim_b = b._spool_claim()
    assert len(first) == 1 and second == set()         # exactly one claimant wins
    a._spool_finish_claim(claim_a)
    assert claim_b is None


def test_prune_keeps_claimed_deferred_entries(tmp_path, monkeypatch):
    """After claim, before summarize: prune must not delete the claimed tail."""
    _tick_clock(monkeypatch)
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("deferred", {"a.py"}, SimpleNamespace(tool_calls=1))
    j.defer_flush()
    names, claim = j._spool_claim()
    assert len(names) == 1
    deferred_name = next(iter(names))
    for i in range(JOURNAL_ENTRIES_KEEP + 1):
        j.observe_turn(f"fill{i}", set(), SimpleNamespace(tool_calls=0))
    assert (j.entries_dir / deferred_name).is_file()
    j._spool_finish_claim(claim)


def test_orphaned_claim_is_recovered_on_catch_up(tmp_path, monkeypatch):
    """A crash after claim leaves a claim file; the next open must finish."""
    _tick_clock(monkeypatch)
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("t1", {"a.py"}, SimpleNamespace(tool_calls=1))
    j.defer_flush()
    names, claim = j._spool_claim()
    assert claim is not None and claim.is_file()
    assert not j.deferred_spool_path.exists()

    j2 = _rebuilt(proj, clients)
    assert j2.has_pending_catchup() is True
    assert j2.catch_up() == 1
    assert not j2._orphaned_claim_paths()
    assert j2.read_summary() == "SUMMARY"


def test_catch_up_finishes_claim_when_entry_files_missing(tmp_path, monkeypatch):
    """Missing raw files must not leave an orphan claim blocking future catch-up."""
    _tick_clock(monkeypatch)
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10, local_only=True)
    j.deferred_spool_path.parent.mkdir(parents=True, exist_ok=True)
    j.deferred_spool_path.write_text("entry-missing1.md\nentry-missing2.md\n")
    assert j.has_pending_catchup() is True
    assert j.catch_up() == 0
    assert not j._orphaned_claim_paths()
    assert not j.has_pending_catchup()


def test_dispatch_catchup_runs_via_the_job_registry(tmp_path, monkeypatch):
    _tick_clock(monkeypatch)
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("t1", {"a.py"}, SimpleNamespace(tool_calls=1))
    j.defer_flush()

    class _Reg:
        def __init__(self):
            self.dispatched = []

        def dispatch(self, kind, name, fn, **kw):
            self.dispatched.append((kind, name))
            fn()                                       # run inline for determinism
            return "j1"

    j2 = _rebuilt(proj, clients)
    state = SimpleNamespace(project=proj, journal=j2, job_registry=_Reg())
    assert J.dispatch_catchup(state) == "j1"
    assert state.job_registry.dispatched == [("journal", "journal catch-up")]
    assert j2.caught_up == 1
    assert J.dispatch_catchup(state) is None           # nothing pending → no job


def test_dispatch_catchup_with_a_real_registry(tmp_path, monkeypatch):
    """End-to-end over the real JobRegistry: threaded dispatch, real kind wiring
    (a fake permissive registry would hide a dispatch-signature break)."""
    import time as _time
    _tick_clock(monkeypatch)
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, proj, clients = _journal(tmp_path, code_on=True, batch=10)
    j.observe_turn("t1", {"a.py"}, SimpleNamespace(tool_calls=1))
    j.defer_flush()

    j2 = _rebuilt(proj, clients)
    state = SimpleNamespace(project=proj, journal=j2, job_registry=None,
                            cfg=make_cfg(), console=None)
    job_id = J.dispatch_catchup(state)
    assert job_id is not None
    reg = state.job_registry
    deadline = _time.time() + 10
    while _time.time() < deadline:
        job = reg.get(job_id)
        if job is not None and not job.active:
            break
        _time.sleep(0.02)
    reg.shutdown(wait=True)
    job = reg.get(job_id)
    assert job is not None and job.status == "done" and job.kind == "journal"
    assert j2.caught_up == 1
    assert not j2.deferred_spool_path.exists()


def test_session_only_code_on_tail_is_caught_up_next_open(tmp_path, monkeypatch):
    """A user who enabled the journal session-only (`/journal --code-on`, no
    persisted auto) gets 'deferred — summarized at next open' at exit. The next
    open builds the journal with code_on=False — catch-up must still run, or
    that exit line lied."""
    _tick_clock(monkeypatch)
    monkeypatch.setattr(J, "_journal_complete", lambda *a, **k: "SUMMARY")
    j, proj, clients = _journal(tmp_path, code_on=False, batch=10)
    j.set_code(True)                                   # session-only, like --code-on
    j.observe_turn("t1", {"a.py"}, SimpleNamespace(tool_calls=1))
    j.defer_flush()

    class _Reg:
        def dispatch(self, kind, name, fn, **kw):
            fn()
            return "j1"

    state = SimpleNamespace(project=proj, pool=_Pool(clients), cfg=make_cfg(),
                            agent=None, console=None, job_registry=_Reg())
    state.journal = build_project_journal(state)       # the REAL next-open path
    assert state.journal.code_on is False              # auto was never persisted
    assert J.dispatch_catchup(state) == "j1"           # …and catch-up still ran
    assert state.journal.caught_up == 1
