"""Paid-media, delivery, PDF, and repo-map tool handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from xlii.tool_context import (
    ToolContext,
    ToolResult,
)

from ._common import (
    _cap_output,
    _files_mount,
    _iter_remote_files,
    _resolve_in_project,
)


def confirm_paid_action(ctx: ToolContext, description: str, est_cost: str) -> Optional[ToolResult]:
    """The paid-action gate (media-artifacts M2) — the bash gate's money twin.

    ``yolo`` skips it (trust ladder unchanged: background ≠ silent approval,
    but yolo IS the explicit approval tier); headless with no console REFUSES
    (mirrors the bash gate's headless refusal — a paid call must never fire
    on a guess); otherwise a y/N prompt via the injectable ``tools._confirm``.
    Returns a refusal ToolResult, or None to proceed."""
    if ctx.yolo:
        return None
    if ctx.console is None:
        return ToolResult(
            f"{description} refused: a paid action requires confirmation but no "
            "console is attached (running headless). Use --yolo to pre-approve.",
            is_error=True,
        )
    from xlii.tools import _confirm

    prompt = (
        f"approve paid action?\n"
        f"  {description}\n"
        f"  estimated cost: {est_cost}\n"
        f"[y/N] "
    )
    try:
        answer = _confirm(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer != "y":
        return ToolResult(
            f"paid action denied by user ({description}). "
            "Ask the user before trying again.",
            is_error=True,
        )
    return None


def t_generate_image(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """media-artifacts M2 — the agent's gated door to image generation.

    Rides the same client + artifact store as /imagine (client-#1 doctrine).
    Returns concise metadata + the project-relative path(s) — NEVER the
    base64 payload (a blob in the tool result would flood the context)."""
    from xlii.artifacts import write_artifact
    from xlii.media_client import (
        DEFAULT_IMAGE_MODEL,
        _ext_for_mime,
        estimate_image_cost,
        generate_image,
    )

    prompt = (args.get("prompt") or "").strip()
    if not prompt:
        return ToolResult("generate_image: prompt is required", is_error=True)
    try:
        n = max(1, min(int(args.get("n", 1) or 1), 4))
    except (TypeError, ValueError):
        n = 1
    model = str(args.get("model") or DEFAULT_IMAGE_MODEL)

    est = estimate_image_cost(model, n)
    refusal = confirm_paid_action(
        ctx, f'generate {n} image(s) with {model}: "{prompt[:80]}"', est
    )
    if refusal is not None:
        return refusal

    api_key = ""
    try:
        api_key = ctx.clients.chat.api_key
    except Exception:
        # api_key stays empty; the caller reports the missing key rather than crashing here.
        pass
    if not api_key:
        return ToolResult("generate_image: no API key available", is_error=True)

    try:
        images = generate_image(prompt, api_key=api_key, model=model, n=n)
    except Exception as e:
        return ToolResult(f"generate_image failed: {type(e).__name__}: {e}", is_error=True)
    if not images:
        return ToolResult("generate_image: the API returned no images", is_error=True)

    lines = []
    for img in images:
        rel = write_artifact(ctx.project.project_root, img.data,
                             ext=_ext_for_mime(img.mime_type))
        lines.append(rel)

    # A made image is NEVER invisible (tui-media-delivery P0): render at the
    # point of creation through the same maybe_preview seam /imagine uses —
    # renderable sink in the TUI, chafa/sixel/path-line inline. Guarded by
    # has_local_preview so a headless `xlii ask` (the daemon's subprocess —
    # its stdout IS the parsed reply) emits nothing.
    shown = False
    try:
        from xlii.terminal_image import has_local_preview, maybe_preview
        if has_local_preview():
            for rel in lines:
                maybe_preview(ctx.project.project_root / rel, enabled=True,
                              backend="auto", console=ctx.console, force=True)
            shown = True
    except Exception:
        # shown stays False, so the caller falls back to printing the paths.
        pass

    paths = "\n".join(f"  {p}" for p in lines)
    # The tail must be HONEST per surface: on a headless mouth (the phone's
    # `xlii ask`) nothing rendered — claiming "already shown" would talk the
    # model out of calling send_file, and the image would never arrive.
    if shown:
        tail = ("The image is already shown to the user; use send_file to "
                "deliver a copy when a delivery channel exists.")
    else:
        tail = ("The user has NOT seen it yet — use send_file to deliver it "
                "when a delivery channel exists, else tell them the path(s).")
    return ToolResult(
        f"generated {len(lines)} image(s) with {model} (est. {est}):\n{paths}\n{tail}"
    )


def t_send_file(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """media-out — queue a file for delivery to the person in this conversation.

    Copies the named file into the caller-granted outbox (``xlii ask
    --outbox``); the caller — the XMPP daemon — encrypts and uploads it after
    the turn and it lands in the peer's chat as an inline image/audio/file.
    Pure local copy: no network here, and no outbox ⇒ no delivery channel ⇒
    refuse (the schema is only advertised when one is set, this is the
    belt-and-suspenders)."""
    import shutil

    from xlii.media_in import MAX_MEDIA_BYTES

    outbox = getattr(ctx, "outbox_dir", None)
    if outbox is None:
        return ToolResult(
            "send_file: no delivery channel this turn (only available when the "
            "conversation has a mouth that can deliver files, e.g. the XMPP chat).",
            is_error=True,
        )
    raw = (args.get("path") or "").strip()
    if not raw:
        return ToolResult("send_file: path is required", is_error=True)
    try:
        src = _resolve_in_project(ctx, str(Path(raw).expanduser()))
    except ValueError as e:
        return ToolResult(f"send_file: {e}", is_error=True)
    if not src.is_file():
        return ToolResult(f"send_file: no such file: {src}", is_error=True)
    size = src.stat().st_size
    if size > MAX_MEDIA_BYTES:
        return ToolResult(
            f"send_file: {src.name} is {size} bytes — over the "
            f"{MAX_MEDIA_BYTES // (1024 * 1024)}MB delivery cap.",
            is_error=True,
        )
    dest_dir = Path(outbox)
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        n = 1
        while dest.exists():                      # same-name sends both survive
            dest = dest_dir / f"{n}-{src.name}"
            n += 1
        shutil.copy2(src, dest)
    except OSError as e:
        return ToolResult(f"send_file: could not queue {src.name}: {e}", is_error=True)
    return ToolResult(
        f"queued {dest.name} ({size} bytes) — it will be delivered to the user's "
        "chat right after this turn. Don't paste the file's path or contents; "
        "just tell them what you're sending."
    )


def t_create_pdf(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """document-pdf P3 — local PDF render; ungated (no network spend)."""
    from xlii.pdf_render import PdfRenderError
    from xlii.pdf_run import context_from_project, run_pdf_from_content, run_pdf_from_source

    content = (args.get("content") or "").strip()
    source = (args.get("source") or "").strip()
    title = (args.get("title") or "").strip() or None
    out = (args.get("out") or "").strip() or None

    if content and source:
        return ToolResult("create_pdf: provide content OR source, not both", is_error=True)
    if not content and not source:
        return ToolResult("create_pdf: content or source is required", is_error=True)

    try:
        if source:
            rel, eng = run_pdf_from_source(
                source,
                ctx=context_from_project(ctx.project),
                out_name=out,
            )
        else:
            rel, eng = run_pdf_from_content(
                content,
                project_root=ctx.project.project_root,
                source_label="create_pdf",
                title=title,
                out_name=out,
            )
    except PdfRenderError as e:
        return ToolResult(f"create_pdf: {e.message}", is_error=True)

    return ToolResult(
        f"PDF saved with {eng}: {rel}\n"
        "Tell the user the path; they can open it from the file browser or /render --open."
    )


def t_map(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Repo map (proposals/map.md): the project's shape via xlii.repo_map.build_map —
    a thin client of the one engine (client-#1 doctrine), rooted at the project."""
    from xlii.repo_map import build_map

    scope = (args.get("path") or "").strip() or None
    detail = (args.get("detail") or "symbols").strip()
    depth = args.get("depth")
    if depth is not None:
        try:
            depth = int(depth)
        except (TypeError, ValueError):
            return ToolResult("map: depth must be an integer", is_error=True)
        if depth <= 0:
            return ToolResult("map: depth must be a positive integer", is_error=True)
    mount = _files_mount(ctx)
    if mount:
        try:
            lines = [rel for _addr, rel in _iter_remote_files(mount)]
        except Exception as e:
            return ToolResult(f"map: {e}", is_error=True)
        if scope:
            lines = [
                rel for rel in lines
                if rel == scope or rel.startswith(scope.rstrip("/") + "/")
            ]
        if depth is not None:
            lines = [rel for rel in lines if rel.count("/") < depth]
        text = "\n".join(f"- {rel}" for rel in lines) or "(empty mount)"
        return ToolResult(_cap_output(ctx, f"# map: {mount}\n\n{text}"))
    try:
        text = build_map(
            ctx.project.project_root, scope=scope, depth=depth, detail=detail,
        )
    except ValueError as e:
        return ToolResult(f"map: {e}", is_error=True)
    return ToolResult(_cap_output(ctx, text))
