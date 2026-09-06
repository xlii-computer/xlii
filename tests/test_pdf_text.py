"""pdf_text — per-page extract, optional pypdf."""

from __future__ import annotations

import pytest

from xlii.pdf_text import extract_pdf_pages, extract_pdf_text, reflow_pdf_text
from tests.test_panes_pdf import _minimal_pdf


def test_no_extractor_returns_none(monkeypatch):
    monkeypatch.setattr("xlii.pdf_text.extract_pdf_pages", lambda *a, **k: None)
    assert extract_pdf_text("x.pdf") is None


def test_empty_pages_become_empty_text(monkeypatch):
    monkeypatch.setattr("xlii.pdf_text.extract_pdf_pages", lambda *a, **k: ["", ""])
    assert extract_pdf_text("x.pdf") == ""


def test_joins_pages(monkeypatch):
    monkeypatch.setattr("xlii.pdf_text.extract_pdf_pages", lambda *a, **k: ["A", "B"])
    assert extract_pdf_text("x.pdf") == "A\n\nB"


def test_real_pages_when_pypdf_present(tmp_path):
    pytest.importorskip("pypdf")
    p = tmp_path / "hello.pdf"
    p.write_bytes(_minimal_pdf("Hello PDF World"))
    pages = extract_pdf_pages(p)
    assert pages is not None
    assert any("Hello PDF World" in (pg or "") for pg in pages)


def test_reflow_joins_word_per_line():
    dumped = "\n".join(["The", "quick", "brown", "fox", "jumps",
                        "over", "the", "lazy", "dog", ""])
    out = reflow_pdf_text(dumped)
    assert "\nThe\n" not in f"\n{out}\n"
    assert "quick brown fox" in out


def test_reflow_keeps_real_paragraphs():
    prose = (
        "This is a full sentence that already wraps like prose.\n"
        "A second sentence stays on its own line too.\n"
        "A third line keeps the paragraph shape intact.\n"
        "And a fourth so the page is clearly not a word dump.\n"
        "Fifth line of ordinary wrapped text here.\n"
        "Sixth line of ordinary wrapped text here.\n"
        "Seventh line of ordinary wrapped text here.\n"
        "Eighth line of ordinary wrapped text here.\n"
    )
    assert reflow_pdf_text(prose) == prose


def test_render_pdf_page_png_when_pdftoppm_present(tmp_path):
    import shutil

    from xlii.pdf_text import render_pdf_page

    if not shutil.which("pdftoppm"):
        pytest.skip("pdftoppm")
    p = tmp_path / "hello.pdf"
    p.write_bytes(_minimal_pdf("Hello"))
    raw = render_pdf_page(p, 1)
    assert raw and raw[:8] == b"\x89PNG\r\n\x1a\n"
    assert render_pdf_page(p, 0) is None
