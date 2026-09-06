"""Shared PDF render entry for CLI, slash, and agent tool."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from xlii.artifact_preview import open_artifact
from xlii.pdf_render import PdfRenderError, render_markdown_to_pdf
from xlii.pdf_sources import SourceContext, SourceNotFound, resolve_source


def context_from_project(project) -> SourceContext:
    return SourceContext(
        project_root=Path(project.project_root),
        xli_dir=Path(project.xli_dir),
        turns_dir=Path(project.xli_dir) / "turns",
        repl="code",
    )


def run_pdf_from_source(
    source: str,
    *,
    ctx: SourceContext,
    engine: str = "auto",
    out_name: str | None = None,
    do_open: bool = False,
    console: Any = None,
) -> tuple[str, str]:
    """Resolve source, render, optionally preview. Returns ``(rel_path, engine)``."""
    try:
        md, title = resolve_source(source, ctx=ctx)
    except SourceNotFound as e:
        raise PdfRenderError(str(e)) from e

    rel, eng = render_markdown_to_pdf(
        md,
        project_root=ctx.project_root,
        source_label=source,
        engine=engine,
        title=title,
        out_name=out_name,
    )
    abs_path = ctx.project_root / rel
    if console is not None:
        console.print(f"[green]✓[/green] PDF ({eng}) → [cyan]{rel}[/cyan]")
    if do_open:
        open_artifact(abs_path, console=console)
    return rel, eng


def run_pdf_from_content(
    content: str,
    *,
    project_root: Path,
    source_label: str = "create_pdf",
    title: str | None = None,
    engine: str = "auto",
    out_name: str | None = None,
) -> tuple[str, str]:
    return render_markdown_to_pdf(
        content,
        project_root=project_root,
        source_label=source_label,
        engine=engine,
        title=title,
        out_name=out_name,
    )
