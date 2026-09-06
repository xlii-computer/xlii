"""Shared /imagine execution for REPL slash command and `xlii artifact image` CLI."""

from __future__ import annotations

import contextlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from xlii.artifacts import (
    edit_and_store,
    imagine_and_store,
    read_session,
    read_video_record,
    resolve_artifact_path,
    save_artifact_to,
    write_artifact,
    write_video_record,
)
from xlii.media_client import (
    DEFAULT_IMAGE_MODEL,
    MAX_REFERENCE_IMAGES,
)
from xlii.terminal_image import maybe_preview, preview_mode
from xlii.ui import confirm


_ITERATION_HINT = (
    "[dim]→[/dim] [cyan]/imagine --redo[/cyan] [dim]·[/dim] "
    "[cyan]/imagine --editprompt \"…\"[/cyan] [dim]·[/dim] "
    "[cyan]/imagine --ref <path|name>[/cyan] [dim]·[/dim] "
    "[cyan]/imagine --save <path>[/cyan]"
)


@dataclass
class ImagineRequest:
    prompt: Optional[str] = None
    redo: bool = False
    edit_prompt: Optional[str] = None
    save_path: Optional[str] = None
    show_last: bool = False
    no_preview: bool = False
    force_preview: bool = False
    model: str = DEFAULT_IMAGE_MODEL
    assume_yes: bool = False
    # M5: explicit reference images (paths, locker names, or locker:<name>).
    # --editprompt with no --ref auto-uses the last artifact as the sole ref.
    refs: list[str] = field(default_factory=list)
    # Pull every enabled image locker entry as refs (capped at MAX_REFERENCE_IMAGES).
    from_locker: bool = False
    # ``--save dest --from <artifact-name>`` copies that store file, not just last.
    from_name: Optional[str] = None


def parse_imagine_tokens(tokens: list[str]) -> ImagineRequest:
    """Parse flags + trailing prompt from a token list (slash or CLI argv tail)."""
    req = ImagineRequest()
    i = 0
    prompt_parts: list[str] = []
    while i < len(tokens):
        t = tokens[i]
        if t == "--redo":
            req.redo = True
        elif t in ("--editprompt", "--edit-prompt"):
            i += 1
            if i < len(tokens):
                req.edit_prompt = tokens[i]
        elif t == "--ref":
            i += 1
            if i < len(tokens):
                req.refs.append(tokens[i])
        elif t in ("--from-locker", "--locker"):
            req.from_locker = True
        elif t == "--from" and i + 1 < len(tokens):
            i += 1
            req.from_name = tokens[i]
        elif t == "--save":
            i += 1
            if i < len(tokens):
                req.save_path = tokens[i]
        elif t == "--last":
            req.show_last = True
        elif t in ("--no-preview", "--no_preview"):
            req.no_preview = True
        elif t == "--inline":
            req.force_preview = True
        elif t == "--yolo":
            req.assume_yes = True
        elif t == "--model" and i + 1 < len(tokens):
            i += 1
            req.model = tokens[i]
        elif t.startswith("--model="):
            req.model = t.split("=", 1)[1]
        elif t.startswith("-"):
            raise ValueError(f"unknown flag: {t}")
        else:
            prompt_parts.append(t)
        i += 1
    if prompt_parts:
        req.prompt = " ".join(prompt_parts).strip().strip('"').strip("'")
    return req


def _preview_enabled(req: ImagineRequest) -> bool:
    if req.no_preview:
        return False
    if req.force_preview:
        return True
    mode = preview_mode()
    return mode not in ("", "off", "0", "never", "path", "false", "no")


def confirm_spend(console, cost_line: str, *, assume_yes: bool, verb: str = "Generate image") -> bool:
    """The one paid-action gate: --yolo bypass; an installed confirm hook
    (face bar / TUI modal) is the channel even when stdin is not a tty;
    bare ``input()`` on a non-tty refuses (a headless body must never
    block on a spend prompt); else y/N confirm."""
    if assume_yes:
        return True
    from xlii import tools as _tools

    hook = getattr(_tools, "_confirm", None)
    hooked = hook is not None and hook is not input
    if not hooked and not sys.stdin.isatty():
        console.print("[red]refused:[/red] image generation needs a console (or --yolo / yolo mode)")
        return False
    return confirm(f"{verb} {cost_line}? [y/N] ")


def _generating_status(console, label: str = "generating image…"):
    """A spinner while a (multi-second) API call runs, so the REPL doesn't look
    frozen. Falls back to a one-shot printed line on consoles without `.status`
    (test fakes, the Textual transcript) and never raises."""
    status = getattr(console, "status", None)
    if callable(status):
        try:
            return status(f"[dim]{label}[/dim]", spinner="dots")
        except Exception:
            # Per the docstring: fall through to the printed line and null context below, never raising.
            pass
    console.print(f"[dim]{label}[/dim]")
    return contextlib.nullcontext()


def _print_result(console, meta, cost_line: str) -> None:
    console.print(
        f"[green]✓[/green] saved [cyan]{meta.rel_path}[/cyan] "
        f"[dim]({meta.bytes:,} bytes, {cost_line})[/dim]"
    )


def _enabled_locker_images(state: Any) -> list[Path]:
    """Paths of enabled image entries currently riding the session locker."""
    out: list[Path] = []
    for entry in list(getattr(state, "attached_files", None) or []):
        if not isinstance(entry, dict):
            continue
        if entry.get("enabled") is False:
            continue
        kind = (entry.get("kind") or "").lower()
        path_s = str(entry.get("path") or "")
        if kind != "image" and Path(path_s).suffix.lower() not in {
            ".png", ".jpg", ".jpeg", ".gif", ".webp",
        }:
            continue
        raw = entry.get("path")
        if not raw:
            continue
        p = Path(str(raw)).expanduser()
        if p.exists():
            out.append(p)
    return out


def _canvas_locker_images(state: Any) -> list[Path]:
    """Enabled locker images that are the canvas work (durable pin)."""
    out: list[Path] = []
    for entry in list(getattr(state, "attached_files", None) or []):
        if not isinstance(entry, dict) or entry.get("role") != "canvas":
            continue
        if entry.get("enabled") is False:
            continue
        raw = entry.get("path")
        if not raw:
            continue
        p = Path(str(raw)).expanduser()
        if p.exists() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            out.append(p)
    return out


def _focused_locker_images(state: Any) -> list[Path]:
    """Enabled locker images Focus staged (``once=True``)."""
    out: list[Path] = []
    for entry in list(getattr(state, "attached_files", None) or []):
        if not isinstance(entry, dict) or not entry.get("once"):
            continue
        if entry.get("enabled") is False:
            continue
        raw = entry.get("path")
        if not raw:
            continue
        p = Path(str(raw)).expanduser()
        if p.exists() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".webp"}:
            out.append(p)
    return out


def resolve_one_ref(
    root: Path,
    ref: str,
    *,
    state: Any = None,
) -> Path:
    """Resolve one ``--ref`` token: absolute/relative path, ``locker:<name>``,
    canvas/artifacts address, or a bare name in the locker / artifacts store."""
    token = (ref or "").strip()
    if not token:
        raise FileNotFoundError("empty --ref")
    # locker:<name> or bare name against the live locker.
    name = token[7:] if token.lower().startswith("locker:") else token
    if state is not None:
        for entry in list(getattr(state, "attached_files", None) or []):
            if not isinstance(entry, dict):
                continue
            if entry.get("enabled") is False:
                continue
            ename = str(entry.get("name") or "")
            epath = str(entry.get("path") or "")
            if name in (ename, Path(epath).name) or token == epath:
                p = Path(epath).expanduser()
                if p.exists():
                    return p
                raise FileNotFoundError(f"locker entry {ename!r} path missing: {epath}")

    def _in_artifacts(filename: str) -> Path | None:
        from xlii.artifacts import artifacts_dir

        art = artifacts_dir(root) / Path(filename).name
        return art if art.is_file() else None

    if "://" in token:
        scheme, rest = token.split("://", 1)
        if scheme in ("canvas", "artifacts"):
            hit = _in_artifacts(rest)
            if hit is not None:
                return hit
    p = Path(token).expanduser()
    if not p.is_absolute():
        p = root / p
    if p.exists() and p.is_file():
        return p
    if "/" not in token and "\\" not in token and "://" not in token:
        hit = _in_artifacts(token)
        if hit is not None:
            return hit
    raise FileNotFoundError(f"reference image not found: {token}")


def resolve_imagine_refs(
    root: Path,
    req: ImagineRequest,
    *,
    state: Any = None,
    prior_session: Any = None,
) -> list[Path]:
    """Collect the reference images for an edit (M5 in-session half).

    Order of sources:
    1. Explicit ``--ref`` tokens (paths / locker names / artifact names).
    2. ``--from-locker`` / ``--locker`` → enabled image locker entries.
    3. ``--editprompt`` / ``/image edit`` with no refs yet → canvas work
       (locker ``role=canvas``), then Focus pins (``once`` images), else
       the last artifact.

    Caps at :data:`~xlii.media_client.MAX_REFERENCE_IMAGES`. Dedupes by resolved path.
    """
    refs: list[Path] = []
    seen: set[Path] = set()

    def _add(p: Path) -> None:
        key = p.resolve()
        if key in seen:
            return
        seen.add(key)
        refs.append(p)

    for token in req.refs:
        _add(resolve_one_ref(root, token, state=state))
    if req.from_locker:
        for p in _enabled_locker_images(state):
            _add(p)
    # /image edit with no --ref: canvas work, then Focus pin, then last artifact.
    if req.edit_prompt and not refs:
        for p in _canvas_locker_images(state):
            _add(p)
    if req.edit_prompt and not refs:
        for p in _focused_locker_images(state):
            _add(p)
    if req.edit_prompt and not refs:
        if prior_session and prior_session.paths:
            try:
                _add(resolve_artifact_path(root, prior_session.paths[-1]))
            except (ValueError, FileNotFoundError) as e:
                raise FileNotFoundError(
                    f"--editprompt needs the last artifact as a reference: {e}"
                ) from e
        else:
            raise FileNotFoundError(
                "--editprompt needs a prior /imagine artifact (or pass --ref)"
            )
    if len(refs) > MAX_REFERENCE_IMAGES:
        raise ValueError(
            f"too many reference images ({len(refs)}); max {MAX_REFERENCE_IMAGES}"
        )
    return refs


def run_imagine(
    project_root: Path,
    req: ImagineRequest,
    *,
    api_key: str,
    console: Any,
    preview_backend: str = "auto",
    yolo: bool = False,
    state: Any = None,
) -> bool:
    """Execute an imagine request. Returns True on success.

    ``state`` (optional live ``REPLState``) supplies enabled locker entries for
    M5 ``--ref`` / ``--from-locker`` resolution. Wire shapes for edits are
    faked until the operator live pass.
    """
    assume_yes = req.assume_yes or yolo
    root = Path(project_root)

    if req.show_last:
        sess = read_session(root)
        if not sess or not sess.paths:
            console.print("[dim](no previous /imagine session)[/dim]")
            return False
        rel = sess.paths[-1]
        try:
            path = resolve_artifact_path(root, rel)
        except (ValueError, FileNotFoundError) as e:
            console.print(f"[red]invalid artifact:[/red] {e}")
            return False
        console.print(f"[dim]last:[/dim] [cyan]{rel}[/cyan]  [dim]prompt: {sess.prompt!r}[/dim]")
        if _preview_enabled(req):
            maybe_preview(
                path,
                enabled=True,
                backend=preview_backend,
                console=console,
                force=req.force_preview,
            )
        return True

    if req.save_path:
        from xlii.artifacts import artifact_rel

        rel = ""
        if req.from_name:
            rel = artifact_rel(req.from_name)
        else:
            sess = read_session(root)
            if sess and sess.paths:
                rel = sess.paths[-1]
        if not rel:
            console.print("[red]nothing to save — run /imagine first (or --from <name>)[/red]")
            return False
        try:
            dest = save_artifact_to(root, rel, req.save_path)
        except (OSError, ValueError, FileNotFoundError) as e:
            console.print(f"[red]save failed:[/red] {e}")
            return False
        console.print(f"[green]✓[/green] copied to [cyan]{dest}[/cyan]")
        return True

    prompt = req.prompt
    model = req.model
    aspect = "16:9"
    resolution = "1k"
    prior_session = read_session(root)

    if req.redo:
        if not prior_session or not prior_session.prompt:
            console.print("[red]nothing to redo — run /imagine with a prompt first[/red]")
            return False
        prompt = prior_session.prompt
        model = prior_session.model or model
        aspect = prior_session.aspect_ratio
        resolution = prior_session.resolution
    elif req.edit_prompt:
        prompt = req.edit_prompt.strip()
        if prior_session:
            model = prior_session.model or model
            aspect = prior_session.aspect_ratio
            resolution = prior_session.resolution
    elif not prompt:
        console.print(
            "[dim]usage:[/dim] [cyan]/imagine \"prompt\"[/cyan] [dim]| "
            "[/dim][cyan]--redo[/cyan] [dim]| [/dim][cyan]--editprompt \"…\"[/cyan] "
            "[dim]| [/dim][cyan]--ref <path|name>[/cyan] [dim]| "
            "[/dim][cyan]--from-locker[/cyan] "
            "[dim]| [/dim][cyan]--save <path>[/cyan] [dim]| [/dim][cyan]--last[/cyan]"
        )
        return False

    # M5: resolve reference images. Any non-empty set → edit_image path.
    try:
        ref_paths = resolve_imagine_refs(
            root, req, state=state, prior_session=prior_session
        )
    except (FileNotFoundError, ValueError) as e:
        console.print(f"[red]{e}[/red]")
        return False

    from xlii.media_client import estimate_image_cost

    cost_line = estimate_image_cost(model, 1)
    verb = "Edit image" if ref_paths else "Generate image"
    if not confirm_spend(console, cost_line, assume_yes=assume_yes, verb=verb):
        console.print("[dim]aborted[/dim]")
        return False

    try:
        if ref_paths:
            console.print(
                "[dim]refs:[/dim] "
                + ", ".join(f"[cyan]{p.name}[/cyan]" for p in ref_paths)
            )
            with _generating_status(console, label="editing image…"):
                metas, cost_line, _session = edit_and_store(
                    root,
                    prompt,
                    ref_paths,
                    api_key=api_key,
                    model=model,
                    aspect_ratio=aspect,
                    resolution=resolution,
                )
        else:
            with _generating_status(console):
                metas, cost_line, _session = imagine_and_store(
                    root,
                    prompt,
                    api_key=api_key,
                    model=model,
                    aspect_ratio=aspect,
                    resolution=resolution,
                )
    except Exception as e:
        console.print(f"[red]{'edit' if ref_paths else 'generation'} failed:[/red] {e}")
        return False

    meta = metas[-1]
    _print_result(console, meta, cost_line)

    preview_on = _preview_enabled(req)
    if preview_on:
        maybe_preview(
            meta.abs_path,
            enabled=True,
            backend=preview_backend,
            console=console,
            force=req.force_preview,
        )
        console.print(_ITERATION_HINT)
    else:
        console.print(f"[dim]{meta.rel_path}[/dim]")
        console.print(_ITERATION_HINT)

    return True


def api_key_from_ctx(ctx: dict[str, Any]) -> str:
    pool = ctx.get("pool")
    if pool is not None:
        try:
            return pool.primary().chat.api_key
        except Exception:
            # No primary chat client in the pool -- fall through to the remaining key sources.
            pass
    cfg = ctx.get("cfg")
    if cfg is not None:
        pairs = cfg.key_pairs()
        if pairs:
            return pairs[0].api_key
    from xlii.config import GlobalConfig

    pairs = GlobalConfig.load().key_pairs()
    if not pairs:
        raise RuntimeError("no xAI API key configured — run xlii setup")
    return pairs[0].api_key


def run_video_start(
    project_root: Path,
    prompt: str,
    *,
    api_key: str,
    model: Optional[str] = None,
    console: Any,
) -> Optional[str]:
    """Kick off an async video job and persist its resumable record.

    Returns the request id on success, ``None`` on failure (the error is
    already printed). Deliberately NOT an agent tool in v1 (never block
    run_turn on a multi-minute render).
    """
    from xlii.media_client import DEFAULT_VIDEO_MODEL, estimate_video_cost, generate_video_start

    root = Path(project_root)
    model = model or DEFAULT_VIDEO_MODEL
    cost_line = estimate_video_cost(model)
    console.print(f"[dim]estimated cost:[/dim] {cost_line}")
    try:
        request_id = generate_video_start(prompt, api_key=api_key, model=model)
    except Exception as e:
        console.print(f"[red]video start failed: {e}[/red]")
        return None

    from datetime import datetime, timezone
    try:
        write_video_record(root, {
            "request_id": request_id, "prompt": prompt, "model": model,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "pending",
        })
    except ValueError as e:
        console.print(f"[red]video start failed: {e}[/red]")
        return None
    console.print(
        f"[green]✓[/green] video job started — request [cyan]{request_id}[/cyan] "
        f"[dim]({cost_line})[/dim]"
    )
    return request_id


def poll_video_job(
    project_root: Path,
    request_id: str,
    *,
    api_key: str,
    wait_s: int,
    console: Any,
) -> int:
    """Poll one async job; write the file when ready. `wait_s`=0 → one poll.
    Timeout is CLEAN and resumable: the id + status command are re-printed."""
    import time as _time

    from xlii.media_client import (
        VIDEO_DONE,
        VIDEO_FAILED,
        generate_video_status,
    )

    root = Path(project_root)
    try:
        record = read_video_record(root, request_id)
    except ValueError as e:
        console.print(f"[red]video status failed: {e}[/red]")
        return 1
    deadline = _time.monotonic() + max(0, wait_s)
    while True:
        try:
            status, data = generate_video_status(request_id, api_key=api_key)
        except Exception as e:
            console.print(f"[red]video status failed: {e}[/red]")
            return 1
        if status == VIDEO_DONE and data:
            rel = write_artifact(root, data, ext="mp4")
            record.update(status="done", artifact=rel)
            write_video_record(root, record)
            console.print(f"[green]✓[/green] video ready → [cyan]{rel}[/cyan]")
            return 0
        if status == VIDEO_FAILED:
            record.update(status="failed")
            write_video_record(root, record)
            console.print(f"[red]video generation failed[/red] (request {request_id})")
            return 1
        if _time.monotonic() >= deadline:
            record.update(status="pending")
            write_video_record(root, record)
            console.print(
                f"[yellow]still rendering[/yellow] — resume anytime with "
                f"[cyan]xlii artifact video status {request_id}[/cyan]"
            )
            return 0                      # a pending async job is not a failure
        _time.sleep(min(5, max(1, wait_s // 10 or 1)))


def run_edit(
    project_root: Path,
    prompt: str,
    refs: list[str],
    *,
    api_key: str,
    model: Optional[str] = None,
    assume_yes: bool = False,
    console: Any,
) -> int:
    """Reference-image edit orchestration (M5): resolve refs → spend gate →
    edit → store. Returns the CLI exit code; usage errors stay with the caller.
    The gate is the shared kernel one — unlike the old inline copy it refuses
    outright on a non-tty stdin instead of prompting a headless body."""
    from xlii.media_client import (
        DEFAULT_IMAGE_MODEL,
        _ext_for_mime,
        edit_image,
        estimate_image_cost,
    )

    root = Path(project_root)
    try:
        ref_paths = [resolve_one_ref(root, r) for r in refs]
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    model = model or DEFAULT_IMAGE_MODEL
    est = estimate_image_cost(model, 1)
    if not confirm_spend(console, est, assume_yes=assume_yes, verb="Edit image"):
        console.print("[yellow]cancelled[/yellow]")
        return 0

    try:
        images = edit_image(prompt, ref_paths, api_key=api_key, model=model)
    except Exception as e:
        console.print(f"[red]edit failed: {type(e).__name__}: {e}[/red]")
        return 1

    for img in images:
        rel = write_artifact(root, img.data, ext=_ext_for_mime(img.mime_type))
        console.print(f"[green]✓[/green] edited image → [cyan]{rel}[/cyan]")
    return 0
