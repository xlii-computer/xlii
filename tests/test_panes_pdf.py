"""PdfViewPane — a PDF leaf renders as extracted pages, never as decoded bytes."""

from __future__ import annotations

import io

import pytest

from xlii.panes import Rendered, RenderedMedia
from xlii.panes.dock import Dock
from xlii.panes.pdf import PdfViewPane, _human_size


def _minimal_pdf(text: str = "Hello PDF World") -> bytes:
    content = b"BT /F1 24 Tf 72 700 Td (%s) Tj ET" % text.encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        + b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_pos = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        pdf += b"%010d 00000 n \n" % off
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref_pos)
    return pdf


def _pdf(tmp_path, name="doc.pdf", text="Hello PDF World"):
    p = tmp_path / name
    p.write_bytes(_minimal_pdf(text))
    return p


def test_render_produces_pdf_media_not_bytes(tmp_path):
    p = _pdf(tmp_path)
    r = PdfViewPane(f"file://{p}").render()
    assert isinstance(r, Rendered)
    assert r.media is not None and r.media.kind in ("pdf", "image")
    if r.media.kind == "pdf":
        assert r.media.path == str(p)
    assert "doc.pdf" in r.media.caption
    assert not r.empty
    assert any(row.kind == "caption" for row in r.rows)


def test_refuses_a_container(tmp_path):
    (tmp_path / "d").mkdir()
    with pytest.raises(IsADirectoryError):
        PdfViewPane(f"file://{tmp_path}/d")


def test_dock_routes_pdf_leaf_to_pdf_pane(tmp_path):
    p = _pdf(tmp_path)
    (tmp_path / "notes.txt").write_text("hi")
    dock = Dock()
    assert type(dock.open_address(f"file://{p}", slot="A")).__name__ == "PdfViewPane"
    assert type(dock.open_address(f"file://{tmp_path}/notes.txt", slot="B")).__name__ == "ViewPane"


def test_summarize_action(tmp_path):
    pane = PdfViewPane(f"file://{_pdf(tmp_path)}")
    assert pane.selection().node is not None
    acts = pane.actions()
    assert acts and acts[0].outcome.text == "Summarize this PDF."


def test_page_turn_is_local_nav(tmp_path):
    pane = PdfViewPane(f"file://{_pdf(tmp_path)}")
    pane._pages = ("one", "two", "three")
    pane._page = 1
    assert pane.handle("right") is True and pane.page == 2
    assert pane.handle("left") is True and pane.page == 1
    assert pane.handle("left") is False and pane.page == 1
    assert pane.handle("end") is True and pane.page == 3
    assert pane.handle("home") is True and pane.page == 1
    assert pane.handle("back") is False  # already on page 1 — surface may close


def test_select_page_n_restores(tmp_path):
    pane = PdfViewPane(f"file://{_pdf(tmp_path)}")
    pane._pages = ("a", "b", "c")
    pane._page = 1
    pane.mount(pane.address, select="page:3")
    assert pane.page == 3


def test_extracts_text_when_pypdf_present(tmp_path):
    pytest.importorskip("pypdf")
    p = _pdf(tmp_path, text="Hello PDF World")
    pane = PdfViewPane(f"file://{p}")
    assert "Hello PDF World" in pane._page_text()


def test_human_size():
    assert _human_size(None) == "?"
    assert _human_size(512) == "512 B"
    assert _human_size(2048) == "2.0 KB"


def test_slot_renderable_shows_pdf_rows():
    from rich.console import Console

    from xlii.tui.dock_surface import slot_renderable

    r = Rendered(
        title="file://x/doc.pdf",
        rows=(),
        media=RenderedMedia(kind="pdf", address="file://x/doc.pdf", path="", caption="doc.pdf 1 page"),
    )
    out = io.StringIO()
    Console(file=out, width=50).print(slot_renderable(r))
    s = out.getvalue()
    assert "doc.pdf" in s


def test_dock_add_slot():
    d = Dock(slots=("A",))
    d.add_slot("pdf")
    assert "pdf" in d.slot_ids
    d.add_slot("pdf")
    assert d.slot_ids.count("pdf") == 1


def test_blank_page_rasters_when_pdftoppm_present(tmp_path):
    import base64
    import shutil

    pytest.importorskip("pypdf")
    if not shutil.which("pdftoppm"):
        pytest.skip("pdftoppm")
    from pypdf import PdfWriter

    p = tmp_path / "scan.pdf"
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    with p.open("wb") as fh:
        w.write(fh)
    r = PdfViewPane(f"file://{p}").render()
    assert r.media is not None and r.media.kind == "image"
    assert r.media.b64
    assert base64.b64decode(r.media.b64)[:8] == b"\x89PNG\r\n\x1a\n"
    assert any("page image" in row.text for row in r.rows)


def test_text_page_also_rasters(tmp_path, monkeypatch):
    from xlii.panes import pdf as pdf_pane

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
    )
    called = []

    def _render(*a, **k):
        called.append(1)
        return png

    monkeypatch.setattr(pdf_pane, "render_pdf_page", _render)
    r = PdfViewPane(f"file://{_pdf(tmp_path)}").render()
    assert called == [1]
    assert r.media is not None and r.media.kind == "image"
    assert r.media.b64
    assert any("page image" in row.text for row in r.rows)


def test_empty_page_uses_raster_bytes(tmp_path, monkeypatch):
    from xlii.panes import pdf as pdf_pane

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
    )
    monkeypatch.setattr(pdf_pane, "render_pdf_page", lambda *a, **k: png)
    pane = PdfViewPane(f"file://{_pdf(tmp_path)}")
    pane._pages = ("",)
    pane._page = 1
    r = pane.render()
    assert r.media.kind == "image" and r.media.b64
    assert any(row.text == "page image" for row in r.rows)
