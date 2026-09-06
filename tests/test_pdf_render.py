"""document-pdf P0–P4 — render core, sources, CLI, tool (offline; stubbed engine)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import xlii.cmds.artifact as art
import xlii.pdf_render as pr
from xlii.pdf_render import html_to_pdf, markdown_to_html, render_markdown_to_pdf
from xlii.pdf_sources import SourceContext, SourceNotFound, resolve_source
from xlii.tool_context import ToolContext
from xlii.tool_handlers import t_create_pdf
from xlii.tool_schemas import BUILTIN_TOOLS, PLAN_MODE_TOOLS, PARALLEL_SAFE, WORKER_REGISTRY


@pytest.fixture(autouse=True)
def _stub_pdf_engine(monkeypatch):
    def _fake(html: str, engine: str):
        return b"%PDF-stub-" + (engine or "auto").encode()[:20]

    monkeypatch.setattr(pr, "_PDF_RENDER_FN", _fake)


def _force_no_engine(monkeypatch):
    """Disable every real engine at the dispatch seam, no matter what's installed.

    Patches through ``_ENGINE_FN`` (the dict ``html_to_pdf`` actually reads) rather
    than rebinding the module-level ``_try_*`` names, which dispatch never re-reads.
    """
    monkeypatch.setattr(pr, "_PDF_RENDER_FN", None)
    for name in pr.ENGINE_ORDER:
        monkeypatch.setitem(pr._ENGINE_FN, name, lambda html: None)


def test_markdown_to_html_wraps_body():
    html = markdown_to_html("# Title\n\nHello")
    assert "<h1" in html or "Title" in html
    assert "Hello" in html
    assert "<style>" in html


def test_html_to_pdf_stub_returns_bytes():
    result = html_to_pdf("<html><body>x</body></html>")
    assert result.data is not None
    assert result.data.startswith(b"%PDF")


def test_html_to_pdf_no_engine_returns_hint(monkeypatch):
    _force_no_engine(monkeypatch)
    result = html_to_pdf("<html><body>x</body></html>")
    assert result.data is None
    assert result.hint and "pdf engine" in result.hint.lower()


def test_html_to_pdf_tries_engines_in_documented_order(monkeypatch):
    """auto walks weasyprint -> pandoc -> wkhtmltopdf -> fpdf2, in that order."""
    monkeypatch.setattr(pr, "_PDF_RENDER_FN", None)
    attempted: list[str] = []
    for name in pr.ENGINE_ORDER:
        monkeypatch.setitem(
            pr._ENGINE_FN,
            name,
            lambda html, _n=name: attempted.append(_n) or None,
        )
    result = html_to_pdf("<html><body>x</body></html>", engine="auto")
    assert result.data is None
    assert attempted == list(pr.ENGINE_ORDER)
    # the hint records the fall-through trail in precedence order
    assert "tried: weasyprint, pandoc, wkhtmltopdf, fpdf2" in (result.hint or "")


def test_auto_falls_through_to_fpdf2_when_only_fpdf_present(monkeypatch):
    """With the top three engines unavailable, auto reaches the real fpdf2 path."""
    import sys
    import types

    monkeypatch.setattr(pr, "_PDF_RENDER_FN", None)
    for name in ("weasyprint", "pandoc", "wkhtmltopdf"):
        monkeypatch.setitem(pr._ENGINE_FN, name, lambda html: None)

    class _FakeFPDF:
        def set_auto_page_break(self, **kw):
            pass

        def add_page(self):
            pass

        def set_font(self, *a, **kw):
            pass

        def multi_cell(self, *a, **kw):
            pass

        def output(self):
            return b"%PDF-fake-fpdf"

    fake_fpdf = types.ModuleType("fpdf")
    fake_fpdf.FPDF = _FakeFPDF
    monkeypatch.setitem(sys.modules, "fpdf", fake_fpdf)

    # fpdf2 entry stays the REAL _try_fpdf2, which imports our fake `fpdf`.
    result = html_to_pdf("<html><body>hello</body></html>", engine="auto")
    assert result.engine == "fpdf2"
    assert result.data == b"%PDF-fake-fpdf"


def test_render_markdown_writes_pdf_artifact(tmp_path):
    rel, eng = render_markdown_to_pdf("# Doc\n\nBody", project_root=tmp_path)
    assert rel.startswith(".xlii/artifacts/pdf-")
    assert rel.endswith(".pdf")
    assert (tmp_path / rel).read_bytes().startswith(b"%PDF")
    assert eng == "stub"


def test_resolve_verify_alias(tmp_path):
    from tests.helpers import make_project

    project = make_project(tmp_path)
    project.xli_dir.mkdir(parents=True, exist_ok=True)
    verify = project.xli_dir / "verify-last.md"
    verify.write_text("# Verify Report\n\nAll good.\n", encoding="utf-8")
    ctx = SourceContext(project.project_root, project.xli_dir, project.xli_dir / "turns")
    text, title = resolve_source("verify", ctx=ctx)
    assert title == "Verify Report"
    assert "All good" in text


def test_resolve_last_missing_turns(tmp_path):
    from tests.helpers import make_project

    project = make_project(tmp_path)
    ctx = SourceContext(project.project_root, project.xli_dir, project.xli_dir / "turns")
    with pytest.raises(SourceNotFound, match="no turns"):
        resolve_source("last", ctx=ctx)


def test_resolve_explicit_path(tmp_path):
    from tests.helpers import make_project

    project = make_project(tmp_path)
    doc = tmp_path / "notes.md"
    doc.write_text("# Notes\n\nHi\n", encoding="utf-8")
    ctx = SourceContext(project.project_root, project.xli_dir, project.xli_dir / "turns")
    text, title = resolve_source("notes.md", ctx=ctx)
    assert title == "Notes"


def test_artifact_pdf_cli(tmp_path, monkeypatch):
    from tests.helpers import FakeConsole, make_project

    project = make_project(tmp_path)
    project.xli_dir.mkdir(parents=True, exist_ok=True)
    (project.xli_dir / "verify-last.md").write_text("# V\n\nok\n", encoding="utf-8")
    fake = FakeConsole()
    monkeypatch.setattr(art, "console", fake)
    rc = art.cmd_artifact_pdf(
        SimpleNamespace(source="verify", path=str(tmp_path), out=None, engine="auto", open=False)
    )
    assert rc == 0
    assert ".xlii/artifacts/" in fake.text


def test_cli_parser_accepts_artifact_pdf():
    from xlii.cli import build_parser

    args = build_parser().parse_args(["artifact", "pdf", "verify", "--open"])
    assert args.source == "verify"
    assert args.open is True


def test_create_pdf_tool_flags_and_registry():
    tool = next(t for t in BUILTIN_TOOLS if t.name == "create_pdf")
    assert tool.name not in PARALLEL_SAFE
    assert tool.name not in PLAN_MODE_TOOLS
    assert tool.name not in WORKER_REGISTRY
    assert tool.parallel_safe is False
    assert tool.plan_mode_safe is False
    assert tool.worker_safe is False


def test_create_pdf_tool_writes_file(tmp_path):
    from tests.helpers import make_project

    project = make_project(tmp_path)
    ctx = ToolContext(project=project, clients=SimpleNamespace(), cfg=SimpleNamespace())
    result = t_create_pdf(ctx, {"content": "# Resume\n\nSkills: Python"})
    assert not result.is_error
    assert ".xlii/artifacts/" in result.content
    pdfs = list((tmp_path / ".xlii" / "artifacts").glob("pdf-*.pdf"))
    assert len(pdfs) == 1


def test_create_pdf_no_engine_is_clean_error(tmp_path, monkeypatch):
    from tests.helpers import make_project

    _force_no_engine(monkeypatch)
    project = make_project(tmp_path)
    ctx = ToolContext(project=project, clients=SimpleNamespace(), cfg=SimpleNamespace())
    result = t_create_pdf(ctx, {"content": "# X\n"})
    assert result.is_error
    assert "pdf engine" in result.content.lower()


def test_render_slash_registered():
    from xlii.commands import iter_repl_commands
    from xlii.repl_cmds import register_all

    register_all()
    assert any(c.name == "render" for c in iter_repl_commands())


def test_artifact_pdf_open_calls_preview(tmp_path, monkeypatch):
    from tests.helpers import FakeConsole, make_project

    project = make_project(tmp_path)
    project.xli_dir.mkdir(parents=True, exist_ok=True)
    (project.xli_dir / "verify-last.md").write_text("# V\n\nok\n", encoding="utf-8")
    fake = FakeConsole()
    monkeypatch.setattr(art, "console", fake)
    opened: list[str] = []
    monkeypatch.setattr("xlii.pdf_run.open_artifact", lambda p, **kw: opened.append(str(p)) or True)
    rc = art.cmd_artifact_pdf(
        SimpleNamespace(source="verify", path=str(tmp_path), out=None, engine="auto", open=True)
    )
    assert rc == 0
    assert opened and opened[0].endswith(".pdf")
