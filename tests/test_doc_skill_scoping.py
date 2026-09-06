"""Vector D — /doc excludes skills (seam #7).

Skills ride the attached_docs channel under the `skill:` prefix. Before this
change, /doc listed them and /undoc could silently detach a skill the user
didn't know was attached. /doc now only ever surfaces / refreshes / detaches
REAL docs; skills are a /skill concern.

Uses the legacy (non-REPLState) attachment-owner path: a plain object exposing
`attached_docs` is enough to drive the underlying handler.
"""

from xlii.repl_cmds.knowledge import _handle_doc_command, _partition_docs
from xlii.skills import SKILL_ATTACH_PREFIX
from tests.helpers import FakeConsole


class _Owner:
    """Minimal attachment owner (the `is_state=False` path in knowledge.py)."""

    def __init__(self):
        self.attached_docs = []
        self.attached_refs = []


# --------------------------------------------------------------------------- #
#  seam #7 partition helper
# --------------------------------------------------------------------------- #

def test_partition_splits_docs_from_skills():
    entries = [("notes", "B1"), (SKILL_ATTACH_PREFIX + "pr", "B2"), ("api", "B3")]
    docs, skills = _partition_docs(entries)
    assert docs == [("notes", "B1"), ("api", "B3")]
    assert skills == [(SKILL_ATTACH_PREFIX + "pr", "B2")]


def test_partition_empty_is_safe():
    assert _partition_docs([]) == ([], [])
    assert _partition_docs(None) == ([], [])


# --------------------------------------------------------------------------- #
#  /doc listing excludes skills
# --------------------------------------------------------------------------- #

def test_doc_list_hides_skills_and_notes_their_count():
    owner = _Owner()
    owner.attached_docs = [("notes", "BODY"), (SKILL_ATTACH_PREFIX + "pr", "steps")]
    con = FakeConsole()
    _handle_doc_command("/doc", owner, con)
    text = con.text
    assert "notes" in text                          # the real doc shows
    assert "skill:pr" not in text                   # the skill does not
    assert "1 skill attached" in text               # but it's acknowledged


def test_doc_list_with_only_skills_reads_as_empty():
    owner = _Owner()
    owner.attached_docs = [(SKILL_ATTACH_PREFIX + "pr", "steps")]
    con = FakeConsole()
    _handle_doc_command("/doc", owner, con)
    text = con.text
    assert "no docs attached this session" in text
    assert "1 skill attached" in text


# --------------------------------------------------------------------------- #
#  /undoc refuses skills (redirects to /skill)
# --------------------------------------------------------------------------- #

def test_undoc_refuses_prefixed_skill_name():
    owner = _Owner()
    owner.attached_docs = [(SKILL_ATTACH_PREFIX + "pr", "steps")]
    con = FakeConsole()
    _handle_doc_command("/undoc skill:pr", owner, con)
    assert "is a skill, not a doc" in con.text
    assert "/skill off pr" in con.text
    # the skill is NOT detached
    assert any(n == SKILL_ATTACH_PREFIX + "pr" for n, _ in owner.attached_docs)


def test_undoc_refuses_bare_skill_name():
    owner = _Owner()
    owner.attached_docs = [(SKILL_ATTACH_PREFIX + "pr", "steps")]
    con = FakeConsole()
    _handle_doc_command("/undoc pr", owner, con)
    assert "is a skill, not a doc" in con.text
    assert any(n == SKILL_ATTACH_PREFIX + "pr" for n, _ in owner.attached_docs)


def test_undoc_still_detaches_a_real_doc():
    owner = _Owner()
    owner.attached_docs = [("notes", "BODY"), (SKILL_ATTACH_PREFIX + "pr", "steps")]
    con = FakeConsole()
    _handle_doc_command("/undoc notes", owner, con)
    assert "detached" in con.text
    assert not any(n == "notes" for n, _ in owner.attached_docs)
    assert any(n == SKILL_ATTACH_PREFIX + "pr" for n, _ in owner.attached_docs)


# --------------------------------------------------------------------------- #
#  /doc --refresh ignores skills (they have no Doc source to re-read)
# --------------------------------------------------------------------------- #

def test_refresh_with_only_skills_reads_as_empty():
    owner = _Owner()
    owner.attached_docs = [(SKILL_ATTACH_PREFIX + "pr", "steps")]
    con = FakeConsole()
    _handle_doc_command("/doc --refresh", owner, con)
    assert "no docs attached this session" in con.text
    # the skill is left untouched (never treated as a refreshable doc)
    assert owner.attached_docs == [(SKILL_ATTACH_PREFIX + "pr", "steps")]
