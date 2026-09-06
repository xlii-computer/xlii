"""Markdown/text → PDF rendering (document-pdf proposal).

Layered engine chain: weasyprint → pandoc → wkhtmltopdf → fpdf2 → install hint.
Tests stub ``html_to_pdf`` via ``_PDF_RENDER_FN`` — never require a real engine.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from xlii.artifacts import write_artifact
from xlii.multimodal import _MAX_TEXT_BYTES

# Injectable test seam — when set, ``html_to_pdf`` delegates here.
_PDF_RENDER_FN: Optional[Callable[[str, str], Optional[bytes]]] = None

DEFAULT_CSS = """
body { font-family: system-ui, sans-serif; font-size: 11pt; line-height: 1.45;
       margin: 2cm; color: #111; }
h1,h2,h3 { margin-top: 1.2em; }
pre, code { font-family: ui-monospace, monospace; font-size: 0.9em; }
pre { background: #f4f4f4; padding: 0.6em; overflow-x: auto; }
table { border-collapse: collapse; }
th, td { border: 1px solid #ccc; padding: 0.25em 0.5em; }
footer, .pdf-footer { font-size: 0.85em; color: #555; margin-top: 2em; }
"""

ENGINE_ORDER = ("weasyprint", "pandoc", "wkhtmltopdf", "fpdf2")
INSTALL_HINT = (
    "no PDF engine available — install one of: "
    "pip install \"xlii[pdf]\" (weasyprint + fpdf2), or system pandoc / wkhtmltopdf"
)


@dataclass(frozen=True)
class PdfRenderResult:
    data: Optional[bytes]
    engine: Optional[str]
    hint: Optional[str] = None


class PdfRenderError(Exception):
    """User-facing render failure (missing engine, oversize input, etc.)."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _cap_markdown(md: str) -> str:
    raw = md.encode("utf-8")
    if len(raw) <= _MAX_TEXT_BYTES:
        return md
    return raw[:_MAX_TEXT_BYTES].decode("utf-8", errors="ignore") + "\n\n… [truncated]"


def markdown_to_html(md: str, *, css: str | None = None) -> str:
    """Markdown → HTML document with bundled CSS."""
    try:
        import markdown as md_lib
    except ImportError:
        body = "<pre>" + _escape_html(md) + "</pre>"
    else:
        body = md_lib.markdown(
            md,
            extensions=["fenced_code", "tables", "nl2br"],
        )
    style = css if css is not None else DEFAULT_CSS
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<style>{style}</style></head><body>{body}</body></html>"
    )


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _engines_to_try(engine: str) -> list[str]:
    eng = (engine or "auto").strip().lower()
    if eng == "auto":
        return list(ENGINE_ORDER)
    if eng not in ENGINE_ORDER:
        raise PdfRenderError(f"unknown PDF engine: {engine!r} (choose auto or one of {ENGINE_ORDER})")
    return [eng]


def _try_weasyprint(html: str) -> Optional[bytes]:
    try:
        from weasyprint import HTML  # noqa: WPS433 — guarded import
    except Exception:
        return None
    try:
        return HTML(string=html).write_pdf()
    except Exception:
        return None


def _try_pandoc(html: str) -> Optional[bytes]:
    if not shutil.which("pandoc"):
        return None
    with tempfile.TemporaryDirectory() as td:
        html_path = Path(td) / "in.html"
        pdf_path = Path(td) / "out.pdf"
        html_path.write_text(html, encoding="utf-8")
        try:
            subprocess.run(
                ["pandoc", str(html_path), "-o", str(pdf_path)],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if pdf_path.is_file():
            return pdf_path.read_bytes()
    return None


def _try_wkhtmltopdf(html: str) -> Optional[bytes]:
    if not shutil.which("wkhtmltopdf"):
        return None
    with tempfile.TemporaryDirectory() as td:
        html_path = Path(td) / "in.html"
        pdf_path = Path(td) / "out.pdf"
        html_path.write_text(html, encoding="utf-8")
        try:
            subprocess.run(
                ["wkhtmltopdf", str(html_path), str(pdf_path)],
                check=True,
                capture_output=True,
                timeout=120,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if pdf_path.is_file():
            return pdf_path.read_bytes()
    return None


def _try_fpdf2(html: str) -> Optional[bytes]:
    try:
        from fpdf import FPDF
    except ImportError:
        return None
    text = re.sub(r"<[^>]+>", "", html)
    text = re.sub(r"\s+\n", "\n", text).strip()
    if not text:
        text = "(empty)"
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in text.splitlines() or [""]:
        try:
            pdf.multi_cell(0, 6, line)
        except Exception:
            pdf.multi_cell(0, 6, line.encode("latin-1", errors="replace").decode("latin-1"))
    out = pdf.output()
    return out if isinstance(out, bytes) else out.encode("latin-1")


# Engine dispatch table. ``html_to_pdf`` looks engines up here at call time, so
# tests must patch through the dict (``monkeypatch.setitem(_ENGINE_FN, ...)``) —
# rebinding the module-level ``_try_*`` names does NOT affect dispatch.
_ENGINE_FN = {
    "weasyprint": _try_weasyprint,
    "pandoc": _try_pandoc,
    "wkhtmltopdf": _try_wkhtmltopdf,
    "fpdf2": _try_fpdf2,
}


def html_to_pdf(html: str, *, engine: str = "auto") -> PdfRenderResult:
    """Render HTML to PDF bytes using the layered engine chain.

    Engine selection is per-call only (``--engine`` / this ``engine`` arg). The
    proposal's global ``pdf_engine`` config knob was not built; ``auto`` walks
    ``ENGINE_ORDER`` and there is no GlobalConfig default to honor here.
    """
    if _PDF_RENDER_FN is not None:
        data = _PDF_RENDER_FN(html, engine)
        if data:
            return PdfRenderResult(data=data, engine=engine if engine != "auto" else "stub")
        return PdfRenderResult(data=None, engine=None, hint=INSTALL_HINT)

    skipped: list[str] = []
    for name in _engines_to_try(engine):
        fn = _ENGINE_FN.get(name)
        if fn is None:
            continue
        data = fn(html)
        if data:
            return PdfRenderResult(data=data, engine=name)
        skipped.append(name)
    detail = f" (tried: {', '.join(skipped)})" if skipped else ""
    return PdfRenderResult(data=None, engine=None, hint=INSTALL_HINT + detail)


def extract_title(md: str, *, fallback: str = "document") -> str:
    for line in md.splitlines():
        m = re.match(r"^#\s+(.+?)\s*$", line.strip())
        if m:
            return m.group(1).strip()
    return fallback


def append_provenance_footer(md: str, *, source: str, engine: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    footer = f"\n\n---\n*Source: {source} · Engine: {engine} · {ts}*"
    return md.rstrip() + footer


def render_markdown_to_pdf(
    md: str,
    *,
    project_root: Path,
    source_label: str = "inline",
    engine: str = "auto",
    title: str | None = None,
    out_name: str | None = None,
    record_provenance: bool = True,
) -> tuple[str, str]:
    """Render markdown to ``.xlii/artifacts/*.pdf``. Returns ``(rel_path, engine)``."""
    md = _cap_markdown(md)
    html = markdown_to_html(md)
    result = html_to_pdf(html, engine=engine)
    if not result.data:
        raise PdfRenderError(result.hint or INSTALL_HINT)

    eng = result.engine or engine
    pdf_bytes = result.data
    if record_provenance:
        md_footer = append_provenance_footer(md, source=source_label, engine=eng)
        footer_result = html_to_pdf(markdown_to_html(md_footer), engine=eng)
        if footer_result.data:
            pdf_bytes = footer_result.data
            eng = footer_result.engine or eng

    root = Path(project_root)
    if out_name:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", out_name).strip("-") or "document"
        if not safe.lower().endswith(".pdf"):
            safe += ".pdf"
        d = root / ".xlii" / "artifacts"
        d.mkdir(parents=True, exist_ok=True)
        path = d / safe
        path.write_bytes(pdf_bytes)
        rel = path.relative_to(root).as_posix()
    else:
        rel = write_artifact(root, pdf_bytes, ext="pdf", prefix="pdf")
    _ = title
    return rel, eng
