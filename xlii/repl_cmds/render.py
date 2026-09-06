"""/render — markdown/text sources to shareable PDFs (document-pdf)."""

from __future__ import annotations

import shlex
from typing import Any

from xlii.commands import REPLCommand, register_repl_command
from xlii.pdf_render import PdfRenderError
from xlii.pdf_run import run_pdf_from_source
from xlii.pdf_sources import source_context_from_state


def _parse_render_args(args: list[str]) -> tuple[str, dict[str, str]]:
    if not args:
        raise ValueError("usage: /render <source> [--out NAME] [--engine ENGINE] [--open]")
    source = args[0]
    flags: dict[str, str] = {}
    i = 1
    while i < len(args):
        tok = args[i]
        if tok == "--open":
            flags["open"] = "1"
            i += 1
        elif tok in ("--out", "--engine") and i + 1 < len(args):
            flags[tok[2:]] = args[i + 1]
            i += 2
        else:
            raise ValueError(f"unknown or incomplete flag: {tok}")
    return source, flags


def _render_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    state = ctx.get("state")
    if state is None:
        console.print("[red]/render: no active session[/red]")
        return True
    try:
        parts = shlex.split(line.strip())
        source, flags = _parse_render_args(parts[1:])
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return True
    try:
        sctx = source_context_from_state(state)
        run_pdf_from_source(
            source,
            ctx=sctx,
            engine=flags.get("engine", "auto"),
            out_name=flags.get("out"),
            do_open="open" in flags,
            console=console,
        )
    except PdfRenderError as e:
        console.print(f"[red]{e.message}[/red]")
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="render",
            handler=_render_handler,
            description="Render markdown/text to PDF (.xlii/artifacts/)",
            usage="/render <source> [--out NAME] [--engine ENGINE] [--open]",
            category="session",
        )
    )
