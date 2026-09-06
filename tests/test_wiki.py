"""The wiki store (xlii/wiki.py) — pages, front matter, trust promotion, section anchors.

Headless over a tmp .xlii dir. The two anti-confabulation guards are data: provenance
(``sources:``) and verify-before-trust (``verified:``, write → refute → promote)."""

from __future__ import annotations

import pytest

from xlii import wiki as W


def test_write_read_roundtrip(tmp_path):
    W.write_page(tmp_path, "arch", "# Architecture\n\nOne kernel.",
                 sources=["file://notes.md#L4-10", "conv://proj/t1"])
    page = W.read_page(tmp_path, "arch")
    assert page.name == "arch"
    assert page.body == "# Architecture\n\nOne kernel.\n" or page.body.startswith("# Architecture")
    assert page.sources == ["file://notes.md#L4-10", "conv://proj/t1"]
    assert page.verified is False                     # born unverified — trust is earned
    assert page.title == "Architecture"


def test_pages_are_born_unverified_and_promote(tmp_path):
    W.write_page(tmp_path, "claim", "The sky is green.")
    assert W.read_page(tmp_path, "claim").verified is False
    W.mark_verified(tmp_path, "claim")                # the promote step
    assert W.read_page(tmp_path, "claim").verified is True
    W.mark_verified(tmp_path, "claim", False)         # revocable
    assert W.read_page(tmp_path, "claim").verified is False


def test_rewrite_resets_verified_unless_reasserted(tmp_path):
    W.write_page(tmp_path, "p", "v1")
    W.mark_verified(tmp_path, "p")
    W.write_page(tmp_path, "p", "v2 — changed claims")  # a rewrite needs a fresh skeptical pass
    assert W.read_page(tmp_path, "p").verified is False


def test_plain_markdown_without_front_matter_is_a_valid_page(tmp_path):
    d = W.wiki_dir(tmp_path)
    d.mkdir(parents=True)
    (d / "manual.md").write_text("# Hand-written\n\nno front matter at all")
    page = W.read_page(tmp_path, "manual")
    assert page.verified is False and page.sources == []
    assert page.title == "Hand-written"


def test_list_pages_sorted_and_delete(tmp_path):
    W.write_page(tmp_path, "beta", "b")
    W.write_page(tmp_path, "alpha", "a")
    assert [p.name for p in W.list_pages(tmp_path)] == ["alpha", "beta"]
    assert W.delete_page(tmp_path, "alpha") is True
    assert W.delete_page(tmp_path, "alpha") is False
    assert [p.name for p in W.list_pages(tmp_path)] == ["beta"]
    assert W.list_pages(tmp_path / "nowhere") == []   # no wiki dir → empty, never raises


def test_invalid_names_are_rejected(tmp_path):
    for bad in ("", "../evil", "a b", "x/y"):
        assert not W.is_valid_name(bad)
        with pytest.raises((ValueError, FileNotFoundError)):
            W.write_page(tmp_path, bad, "body")
    assert W.is_valid_name("kernel-rebuild.notes_v2")


def test_missing_page_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        W.read_page(tmp_path, "ghost")
    assert W.page_exists(tmp_path, "ghost") is False


# --- sections: the wiki://page#section anchor unit -----------------------------


_BODY = """# The page

intro line

## The event seam

inbound work arrives here.

### A nested detail

still inside the event seam.

## Another section

other text
"""


def test_extract_section_spans_to_the_next_same_level_heading():
    s = W.extract_section(_BODY, "the-event-seam")
    assert s.startswith("## The event seam")
    assert "inbound work arrives" in s
    assert "A nested detail" in s                     # deeper headings stay inside the span
    assert "Another section" not in s                 # the next same-level heading ends it


def test_extract_section_no_match_returns_none():
    assert W.extract_section(_BODY, "nope") is None


def test_heading_slug():
    assert W.heading_slug("## The event seam") == "the-event-seam"
    assert W.heading_slug("Écrit — spaces & symbols!") == "crit-spaces-symbols"
