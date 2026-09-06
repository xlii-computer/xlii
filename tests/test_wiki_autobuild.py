"""journal → wiki auto-build — propose_pages parsing + the create-only autobuilder on flush.

The self-building wiki rides the journalist's flush. The invariants that must never regress:
create-only (an existing page is never modified — nothing you verified or hand-edited is clobbered),
born unverified, gated by wiki_auto, and best-effort (a bad model reply drafts nothing, never
raises)."""

from __future__ import annotations

from types import SimpleNamespace

from xlii import wiki as W
from xlii import wiki_author
from xlii.journal import ProjectJournal, JournalEntry, read_wiki_auto, read_journal_auto


# --- propose_pages (pure, injectable) ----------------------------------------

def test_propose_pages_parses_fenced_json_and_drops_invalid():
    raw = (
        "Sure:\n```json\n"
        '[{"name":"kernel","body":"# Kernel\\n\\nbody","sources":["file://k.py"]},'
        '{"name":"","body":"no name"},{"name":"panes"}]\n```\n'
    )
    props = wiki_author.propose_pages(["existing"], "corpus", lambda m: raw)
    assert [p.name for p in props] == ["kernel"]        # empty-name + body-less dropped
    assert props[0].sources == ["file://k.py"]


def test_propose_pages_caps_at_max_pages():
    raw = "[" + ",".join(f'{{"name":"p{i}","body":"# P{i}\\n\\nx"}}' for i in range(6)) + "]"
    props = wiki_author.propose_pages([], "corpus", lambda m: raw, max_pages=3)
    assert len(props) == 3


def test_propose_pages_garbage_reply_yields_nothing():
    assert wiki_author.propose_pages([], "corpus", lambda m: "sorry, no JSON here") == []
    assert wiki_author.propose_pages([], "corpus", lambda m: "") == []


# --- the autobuilder on flush ------------------------------------------------

def _clients_returning(content: str):
    def create(model, messages, temperature):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    return SimpleNamespace(chat=SimpleNamespace(chat=SimpleNamespace(
        completions=SimpleNamespace(create=create))))


def _journal(xli_dir, *, content="[]", wiki_auto=True, has_clients=True):
    clients = _clients_returning(content) if has_clients else None
    pool = SimpleNamespace(journal_client=lambda: None, primary=lambda: clients) if has_clients else None
    proj = SimpleNamespace(xli_dir=xli_dir, name="t", local_only=True, journal_collection_id=None)
    return ProjectJournal(
        project=proj, pool=pool,
        cfg=SimpleNamespace(worker=lambda: "cheap", chat=lambda: "chat"),
        wiki_auto=wiki_auto,
    )


def _entry():
    return JournalEntry(ts="2026-07-03T10:00:00", goal="work", files=["a.py"], tool_calls=1)


def test_autobuild_drafts_new_pages_born_unverified(tmp_path):
    content = '[{"name":"pipeline","body":"# Pipeline\\n\\nthe turn pipeline","sources":["file://a.py"]}]'
    j = _journal(tmp_path, content=content)
    j.buffer = [_entry()]
    j.flush()
    assert j.wiki_drafts == 1
    page = W.read_page(tmp_path, "pipeline")
    assert page.verified is False and page.sources == ["file://a.py"]


def test_autobuild_is_create_only_and_never_clobbers(tmp_path):
    # a verified, human page named 'kernel' — the autobuilder proposes the same name + a new one.
    W.write_page(tmp_path, "kernel", "# Kernel\n\nHUMAN CONTENT", sources=["conv://p/t1"])
    W.mark_verified(tmp_path, "kernel")
    content = ('[{"name":"kernel","body":"# Kernel\\n\\nOVERWRITE ATTEMPT"},'
               '{"name":"pipeline","body":"# Pipeline\\n\\nnew"}]')
    j = _journal(tmp_path, content=content)
    j.buffer = [_entry()]
    j.flush()
    kernel = W.read_page(tmp_path, "kernel")
    assert kernel.verified is True and "HUMAN CONTENT" in kernel.body   # untouched
    assert j.wiki_drafts == 1 and W.page_exists(tmp_path, "pipeline")   # only the new one landed


def test_autobuild_noop_when_wiki_auto_off(tmp_path):
    content = '[{"name":"pipeline","body":"# Pipeline\\n\\nx"}]'
    j = _journal(tmp_path, content=content, wiki_auto=False)
    j.buffer = [_entry()]
    j.flush()
    assert j.wiki_drafts == 0 and W.list_pages(tmp_path) == []


def test_autobuild_noop_without_a_model(tmp_path):
    j = _journal(tmp_path, has_clients=False)
    j.buffer = [_entry()]
    j.flush()   # must not raise
    assert j.wiki_drafts == 0 and W.list_pages(tmp_path) == []


# --- toggle persistence (key-preserving) -------------------------------------

def test_wiki_auto_persists_alongside_code_auto(tmp_path):
    proj = SimpleNamespace(xli_dir=tmp_path, name="t", local_only=True, journal_collection_id=None)
    j = ProjectJournal(project=proj)
    j.set_code(True, persist_auto=True)   # writes code_auto
    j.set_wiki_auto(True)                 # writes wiki_auto — must not drop code_auto
    assert read_journal_auto(proj) is True
    assert read_wiki_auto(proj) is True
    j.set_wiki_auto(False)
    assert read_wiki_auto(proj) is False and read_journal_auto(proj) is True
