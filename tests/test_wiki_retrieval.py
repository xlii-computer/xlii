"""Wiki retrieval — ranking, section anchors, citations, context blocks, and the
search-address builder (offline; a real .xlii/wiki/ written per test)."""

from __future__ import annotations

from xlii import wiki as W
from xlii.addressing import Address
from xlii.wiki_retrieval import (
    WikiHit,
    best_section,
    format_citations,
    search_self_wiki,
    section_text,
    wiki_context_block,
    wiki_search_address,
)

_GIT_PAGE = """# gitpain

## Stashing
Stash requires a message via ``-m`` or positional; ``-u`` includes untracked.

## Commits
Commit drafts are PREFILLed, never auto-committed.
"""

_TASKS_PAGE = """# tasks-and-pipes

## Split and join
``[[split]]`` arms run under a join step with policy all|any|first_ok.
"""


def _seed_wiki(tmp_path):
    xli = tmp_path / ".xlii"
    (xli / "wiki").mkdir(parents=True, exist_ok=True)
    W.write_page(xli, "gitpain", _GIT_PAGE)
    W.write_page(xli, "tasks-and-pipes", _TASKS_PAGE)
    return xli


def test_best_section_picks_the_titled_section():
    assert best_section(_GIT_PAGE, "how do stash messages work") == "stashing"


def test_best_section_empty_on_operator_only_query():
    assert best_section(_GIT_PAGE, "?? --") == ""


def test_wikihit_address_with_and_without_anchor():
    assert WikiHit("gitpain", "stashing", "", 1.0, False).address == "wiki://gitpain#stashing"
    assert WikiHit("gitpain", "", "", 1.0, False).address == "wiki://gitpain"


def test_search_self_wiki_ranks_and_resolves_anchor(tmp_path):
    xli = _seed_wiki(tmp_path)
    hits = search_self_wiki(xli, "stash message", limit=4)
    assert hits, "expected a wiki match"
    assert hits[0].name == "gitpain"
    assert hits[0].anchor == "stashing"
    assert hits[0].address == "wiki://gitpain#stashing"
    assert hits[0].verified is False


def test_search_self_wiki_empty_without_pages(tmp_path):
    (tmp_path / ".xlii" / "wiki").mkdir(parents=True)
    assert search_self_wiki(tmp_path / ".xlii", "anything", limit=4) == []
    assert search_self_wiki(None, "anything") == []


def test_section_text_returns_the_anchored_span(tmp_path):
    xli = _seed_wiki(tmp_path)
    hit = WikiHit("gitpain", "stashing", "", 1.0, False)
    span = section_text(xli, hit)
    assert span is not None
    assert "## Stashing" in span
    assert "## Commits" not in span  # only that section's span


def test_wiki_context_block_fences_by_address(tmp_path):
    xli = _seed_wiki(tmp_path)
    hits = search_self_wiki(xli, "stash message", limit=4)
    block = wiki_context_block(xli, hits)
    assert "### wiki://gitpain#stashing (unverified — draft)" in block
    assert "## Stashing" in block
    assert wiki_context_block(xli, []) == ""


def test_wiki_context_block_marks_a_verified_page(tmp_path):
    xli = _seed_wiki(tmp_path)
    verified_hit = WikiHit("gitpain", "stashing", "", 1.0, True)
    block = wiki_context_block(xli, [verified_hit])
    assert "### wiki://gitpain#stashing (verified)" in block


def test_format_citations_lists_addresses_and_trust():
    hits = [WikiHit("gitpain", "stashing", "msg via -m", 1.0, False),
            WikiHit("tasks-and-pipes", "", "", 0.5, True)]
    out = format_citations(hits)
    assert "`wiki://gitpain#stashing` (unverified)" in out
    assert "`wiki://tasks-and-pipes` (verified)" in out
    assert format_citations([]) == ""


def test_wiki_search_address_sanitizes_punctuation():
    addr = wiki_search_address("how do stashes work?")
    assert addr == "wiki://?q=how do stashes work"   # trailing ? stripped
    assert Address.parse(addr).query.get("q") == "how do stashes work"
