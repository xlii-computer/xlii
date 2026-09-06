"""Large-paste collapse — keep clipboards from flooding the UI."""

from __future__ import annotations

from xlii.paste_collapse import (
    PasteStore,
    collapse_for_display,
    expand_placeholders,
    line_count,
    make_placeholder,
    should_collapse,
)


def test_small_paste_not_collapsed():
    assert not should_collapse("hello\nworld")
    store = PasteStore()
    assert store.maybe_collapse_insert("short") == "short"
    assert store._items == {}


def test_large_by_lines_collapses(monkeypatch):
    monkeypatch.setenv("XLII_PASTE_COLLAPSE_LINES", "3")
    monkeypatch.setenv("XLII_PASTE_COLLAPSE_CHARS", "99999")
    body = "a\nb\nc\nd"
    assert should_collapse(body)
    store = PasteStore()
    ph = store.maybe_collapse_insert(body)
    assert ph == make_placeholder(1, body)
    assert "(pasted 4 lines · #1)" == ph
    assert store.expand(f"? look at this\n{ph}") == f"? look at this\n{body}"


def test_large_by_chars_collapses(monkeypatch):
    monkeypatch.setenv("XLII_PASTE_COLLAPSE_LINES", "999")
    monkeypatch.setenv("XLII_PASTE_COLLAPSE_CHARS", "50")
    body = "x" * 80
    store = PasteStore()
    ph = store.maybe_collapse_insert(body)
    assert ph.startswith("(pasted 1 lines · #")
    assert store.expand(ph) == body


def test_multiple_pastes_expand_independently(monkeypatch):
    monkeypatch.setenv("XLII_PASTE_COLLAPSE_LINES", "2")
    store = PasteStore()
    a = "one\ntwo\nthree"
    b = "four\nfive\nsix"
    pa = store.maybe_collapse_insert(a)
    pb = store.maybe_collapse_insert(b)
    assert pa != pb
    assert store.expand(f"{pa} and {pb}") == f"{a} and {b}"


def test_expand_unknown_placeholder_left_alone():
    text = "(pasted 99 lines · #42)"
    assert expand_placeholders(text, {}) == text


def test_collapse_for_display_safety_net(monkeypatch):
    monkeypatch.setenv("XLII_PASTE_COLLAPSE_LINES", "3")
    big = "l1\nl2\nl3\nl4\nl5"
    out = collapse_for_display(big)
    assert out.startswith("(pasted 5 lines")
    assert "l1" not in out
    # already-placeholder text is left alone
    ph = "(pasted 5 lines · #1)"
    assert collapse_for_display(ph) == ph


def test_line_count_trailing_newline():
    assert line_count("a\nb\n") == 2
    assert line_count("a\nb") == 2
    assert line_count("") == 0
