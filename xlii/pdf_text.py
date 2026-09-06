"""PDF → per-page text. Shared by the locker (turn fold) and PdfViewPane.

Default-to-text, no rasterization. Extractors are optional (the ``[files]`` extra
ships ``pypdf``; PyMuPDF is a fallback if the box has it).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

DEFAULT_MAX_PAGES = 50


def reflow_pdf_text(text: str) -> str:
    """Join word-per-line extracts into paragraphs.

    pypdf's default extract often emits one token per line. A page that
    is mostly 1–2 word lines is reflowed; real line-broken prose is left.
    """
    raw = text or ""
    lines = raw.splitlines()
    nonempty = [ln.strip() for ln in lines if ln.strip()]
    if len(nonempty) < 8:
        return raw
    short = sum(1 for ln in nonempty if len(ln.split()) <= 2)
    if short / len(nonempty) < 0.6:
        return raw
    paras: list[str] = []
    buf: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            if buf:
                paras.append(" ".join(buf))
                buf = []
            continue
        buf.append(s)
    if buf:
        paras.append(" ".join(buf))
    return "\n\n".join(paras)


def _extract_pypdf_page(pg) -> str:
    """Prefer layout mode; fall back to default extract."""
    try:
        text = pg.extract_text(extraction_mode="layout") or ""
    except TypeError:
        text = ""
    except Exception:
        text = ""
    if not text.strip():
        try:
            text = pg.extract_text() or ""
        except Exception:
            text = ""
    return reflow_pdf_text(text)


def extract_pdf_pages(
    path: "Path | str", *, max_pages: int = DEFAULT_MAX_PAGES
) -> Optional[list[str]]:
    """Per-page extracted text.

    ``None`` — no extractor installed (caller can point at ``[files]``).
    A list (possibly empty, or with empty strings) — an extractor ran. Empty
    strings are scanned / encrypted / picture-only pages, not a missing library.
    """
    p = Path(path)
    tried = False

    try:
        from pypdf import PdfReader
    except ImportError:
        PdfReader = None  # type: ignore[assignment]
    if PdfReader is not None:
        tried = True
        try:
            reader = PdfReader(str(p))
            pages = reader.pages[:max_pages]
            return [_extract_pypdf_page(pg) for pg in pages]
        except Exception:
            # pypdf couldn't parse this file -- fall through to the PyMuPDF attempt below.
            pass

    try:
        import fitz  # PyMuPDF
    except ImportError:
        fitz = None  # type: ignore[assignment]
    if fitz is not None:
        tried = True
        try:
            doc = fitz.open(str(p))
            return [
                reflow_pdf_text(page.get_text() or "")
                for i, page in enumerate(doc) if i < max_pages
            ]
        except Exception:
            # PyMuPDF couldn't parse it either -- fall through to the tried-but-empty result below.
            pass

    return [] if tried else None


def extract_pdf_text(path: "Path | str", *, max_pages: int = DEFAULT_MAX_PAGES) -> Optional[str]:
    """Joined page text, or ``None`` when no extractor is installed.

    ``""`` means an extractor ran and the file yielded nothing useful.
    """
    pages = extract_pdf_pages(path, max_pages=max_pages)
    if pages is None:
        return None
    return "\n\n".join(pages).strip()


def pdf_page_count(path: "Path | str") -> Optional[int]:
    """Page count, or ``None`` when nothing on the box can open the file."""
    p = Path(path)
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(p)).pages)
    except Exception:
        # pypdf unavailable or unable to open the file -- try PyMuPDF below.
        pass
    try:
        import fitz

        return int(fitz.open(str(p)).page_count)
    except Exception:
        # PyMuPDF unavailable or unable to open the file -- try pypdfium2 below.
        pass
    try:
        import pypdfium2 as pdfium

        return len(pdfium.PdfDocument(str(p)))
    except Exception:
        # Last backend on the box; per the docstring the count is simply None.
        pass
    return None


def render_pdf_page(
    path: "Path | str", page: int, *, dpi: int = 110
) -> Optional[bytes]:
    """PNG bytes of one page (1-based). ``None`` when no rasterizer is available.

    Tries PyMuPDF, then pypdfium2, then poppler ``pdftoppm``. A scanned page
    has no text layer — this is the only way to show it.
    """
    if page < 1:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    dpi = max(48, min(int(dpi), 200))

    png = _render_fitz(p, page, dpi)
    if png:
        return png
    png = _render_pdfium(p, page, dpi)
    if png:
        return png
    return _render_pdftoppm(p, page, dpi)


def _render_fitz(path: Path, page: int, dpi: int) -> Optional[bytes]:
    try:
        import fitz
    except ImportError:
        return None
    try:
        doc = fitz.open(str(path))
        if page > doc.page_count:
            return None
        pix = doc.load_page(page - 1).get_pixmap(dpi=dpi)
        return pix.tobytes("png")
    except Exception:
        return None


def _render_pdfium(path: Path, page: int, dpi: int) -> Optional[bytes]:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return None
    try:
        import io

        doc = pdfium.PdfDocument(str(path))
        if page > len(doc):
            return None
        pil = doc[page - 1].render(scale=dpi / 72.0).to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


def _render_pdftoppm(path: Path, page: int, dpi: int) -> Optional[bytes]:
    import shutil
    import subprocess
    import tempfile

    if not shutil.which("pdftoppm"):
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="xlii-pdf-") as td:
            prefix = str(Path(td) / "pg")
            proc = subprocess.run(
                [
                    "pdftoppm", "-png",
                    "-r", str(dpi),
                    "-f", str(page), "-l", str(page),
                    str(path), prefix,
                ],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if proc.returncode != 0:
                return None
            hits = sorted(Path(td).glob("pg*.png"))
            return hits[0].read_bytes() if hits else None
    except Exception:
        return None
