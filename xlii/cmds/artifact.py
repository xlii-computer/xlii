"""`xlii artifact` — headless media artifact commands (media-artifacts M1)."""

from __future__ import annotations

import argparse
from pathlib import Path

from xlii.config import ProjectConfig
from xlii.imagine_run import (
    api_key_from_ctx,
    parse_imagine_tokens,
    poll_video_job,
    run_edit,
    run_imagine,
    run_video_start,
)
from xlii.ui import console


def _project_root(path: str) -> Path:
    root = Path(path).resolve()
    proj = ProjectConfig.load(root)
    return proj.project_root if proj else root


def _api_key_from_config() -> str:
    """CLI key resolution rides the kernel fallback (GlobalConfig on disk)."""
    return api_key_from_ctx({})


def cmd_artifact_video(args: argparse.Namespace) -> int:
    """media-artifacts M4 — async, command-first: kick off / poll a video job.
    Deliberately NOT an agent tool in v1 (never block run_turn on a
    multi-minute render)."""
    root = _project_root(args.path)

    if args.prompt == "status":
        if not args.request_id:
            console.print("[red]usage: xlii artifact video status <request_id>[/red]")
            return 1
        try:
            key = _api_key_from_config()
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            return 1
        return poll_video_job(root, args.request_id, api_key=key, wait_s=args.wait, console=console)

    if not args.prompt:
        console.print('[red]usage: xlii artifact video "<prompt>" | status <id>[/red]')
        return 1

    try:
        key = _api_key_from_config()
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    request_id = run_video_start(root, args.prompt, api_key=key, model=args.model, console=console)
    if request_id is None:
        return 1
    if args.wait > 0:
        return poll_video_job(root, request_id, api_key=key, wait_s=args.wait, console=console)
    console.print(
        f"[dim]async by default — check with[/dim] "
        f"[cyan]xlii artifact video status {request_id}[/cyan]"
    )
    return 0


def cmd_artifact_edit(args: argparse.Namespace) -> int:
    """media-artifacts M5 — reference-image edits. Up to MAX_REFERENCE_IMAGES
    references (explicit paths or `locker:<name>`) + a prompt → a new artifact.
    A PAID action: confirms unless --yolo (the shared kernel spend gate)."""
    root = _project_root(args.path)
    from xlii.media_client import MAX_REFERENCE_IMAGES

    if not args.prompt:
        console.print('[red]usage: xlii artifact edit "<prompt>" --ref <path|locker:name> …[/red]')
        return 1
    if not args.ref:
        console.print("[red]edit needs at least one --ref reference image[/red]")
        return 1
    if len(args.ref) > MAX_REFERENCE_IMAGES:
        console.print(f"[red]too many references ({len(args.ref)}); max {MAX_REFERENCE_IMAGES}[/red]")
        return 1

    try:
        key = _api_key_from_config()
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    return run_edit(
        root,
        args.prompt,
        args.ref,
        api_key=key,
        model=args.model,
        assume_yes=args.yolo,
        console=console,
    )


def cmd_artifact_image(args: argparse.Namespace) -> int:
    root = _project_root(args.path)
    tokens: list[str] = []
    if args.redo:
        tokens.append("--redo")
    if args.edit_prompt:
        tokens.extend(["--edit-prompt", args.edit_prompt])
    for ref in (args.ref or []):
        tokens.extend(["--ref", ref])
    if args.save:
        tokens.extend(["--save", args.save])
    if args.last:
        tokens.append("--last")
    if args.no_preview:
        tokens.append("--no-preview")
    if args.inline:
        tokens.append("--inline")
    if args.yolo:
        tokens.append("--yolo")
    if args.model:
        tokens.extend(["--model", args.model])
    if args.prompt:
        tokens.append(args.prompt)

    try:
        req = parse_imagine_tokens(tokens)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    try:
        key = _api_key_from_config()
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    if args.inline:
        req.force_preview = True
    ok = run_imagine(
        root,
        req,
        api_key=key,
        console=console,
        preview_backend="auto",
        yolo=args.yolo,
    )
    return 0 if ok else 1


def cmd_artifact_pdf(args: argparse.Namespace) -> int:
    """document-pdf P2 — render a source alias or path to PDF."""
    root = _project_root(args.path)
    if not args.source:
        console.print('[red]usage: xlii artifact pdf <source> [--out NAME] [--engine ENGINE] [--open][/red]')
        return 1
    from xlii.pdf_render import PdfRenderError
    from xlii.pdf_sources import SourceContext
    from xlii.pdf_run import run_pdf_from_source

    ctx = SourceContext(
        project_root=root,
        xli_dir=root / ".xlii",
        turns_dir=root / ".xlii" / "turns",
        repl="code",
    )
    try:
        run_pdf_from_source(
            args.source,
            ctx=ctx,
            engine=args.engine or "auto",
            out_name=args.out,
            do_open=args.open,
            console=console,
        )
    except PdfRenderError as e:
        console.print(f"[red]{e.message}[/red]")
        return 1
    return 0


def register(sub) -> None:
    artifact = sub.add_parser("artifact", help="Generate and manage local media artifacts.")
    art_sub = artifact.add_subparsers(dest="artifact_cmd", required=True)

    p_img = art_sub.add_parser("image", help="Generate an image via xAI Imagine.")
    p_img.add_argument("prompt", nargs="?", help="Text prompt")
    p_img.add_argument("--path", default=".", help="Project directory (default: cwd)")
    p_img.add_argument("--redo", action="store_true", help="Regenerate with the last prompt")
    p_img.add_argument("--edit-prompt", dest="edit_prompt", help="New prompt (iteration; uses last artifact as ref)")
    p_img.add_argument("--ref", action="append", default=[], metavar="PATH",
                       help="Reference image for edits (repeatable, max 3)")
    p_img.add_argument("--save", help="Copy last artifact to a project path")
    p_img.add_argument("--last", action="store_true", help="Show last artifact metadata")
    p_img.add_argument("--no-preview", action="store_true", help="Skip inline terminal preview")
    p_img.add_argument("--inline", action="store_true", help="Force inline preview")
    p_img.add_argument("--model", help="Imagine model id")
    p_img.add_argument("--yolo", action="store_true", help="Skip paid-action confirmation")
    p_img.set_defaults(func=cmd_artifact_image)

    p_vid = art_sub.add_parser(
        "video",
        help="Generate a video via xAI Imagine (async: returns a resumable request id).",
        description="Kick off an async video job, or `status <id>` to poll one. "
                    "Async by default — pass --wait N to poll up to N seconds now.",
    )
    p_vid.add_argument("prompt", nargs="?",
                       help='Text prompt, or the literal word "status"')
    p_vid.add_argument("request_id", nargs="?",
                       help="Request id (with `status`)")
    p_vid.add_argument("--path", default=".", help="Project directory (default: cwd)")
    p_vid.add_argument("--model", help="Imagine video model id")
    p_vid.add_argument("--wait", type=int, default=0, metavar="SECS",
                       help="Poll up to SECS seconds before returning (default 0 = async)")
    p_vid.set_defaults(func=cmd_artifact_video)

    p_edit = art_sub.add_parser(
        "edit",
        help="Edit / remix reference image(s) via xAI Imagine (paid).",
        description="Up to 3 reference image paths plus a prompt produce a new "
                    "artifact (paid; confirms unless --yolo).",
    )
    p_edit.add_argument("prompt", nargs="?", help="What to make from the references")
    p_edit.add_argument("--ref", action="append", metavar="PATH",
                        help="A reference image path (repeatable, up to 3)")
    p_edit.add_argument("--path", default=".", help="Project directory (default: cwd)")
    p_edit.add_argument("--model", help="Imagine model id")
    p_edit.add_argument("--yolo", action="store_true", help="Skip paid-action confirmation")
    p_edit.set_defaults(func=cmd_artifact_edit)

    p_pdf = art_sub.add_parser(
        "pdf",
        help="Render markdown/text to a local PDF in .xlii/artifacts/ (free, ungated).",
        description="Resolve a source alias (last, verify, peer, loop, plan) or file path "
                    "to markdown, render to PDF with the first available engine, and print "
                    "the saved path. Use --open to launch the OS viewer.",
    )
    p_pdf.add_argument("source", nargs="?", help="Alias or path (e.g. verify, peer, README.md)")
    p_pdf.add_argument("--path", default=".", help="Project directory (default: cwd)")
    p_pdf.add_argument("--out", help="Output filename under .xlii/artifacts/")
    p_pdf.add_argument(
        "--engine",
        choices=["auto", "weasyprint", "pandoc", "wkhtmltopdf", "fpdf2"],
        default="auto",
        help="PDF engine (default: auto-detect)",
    )
    p_pdf.add_argument("--open", action="store_true", help="Open the PDF in the OS viewer")
    p_pdf.set_defaults(func=cmd_artifact_pdf)
