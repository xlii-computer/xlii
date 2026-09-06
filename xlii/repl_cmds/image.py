"""/image — verbs on an EXISTING image: view the latest, edit one, pick a backend.

The namespace sorts by *have vs conjure*: `/imagine` conjures a NEW image
(text → image); `/image` operates on one you HAVE (`latest` = view, `edit` =
transform). Seeing an image is not a state you enter — the old image *mode*
(vision routing that no-oped on every modern model, a standing preamble, the
--shumup bridge) is gone; preview is ambient (tui-media-delivery P1) and
`/off`'s overlay list no longer includes it.

`edit` fronts the existing --editprompt machinery honestly: it sends your
image(s) as references to xAI images/edits — a guided re-render, not a
pixel-precise in-place edit; composition can drift.
"""

from __future__ import annotations

from typing import Any

from xlii.artifacts import read_session, resolve_artifact_path
from xlii.commands import REPLCommand, register_repl_command
from xlii.terminal_image import maybe_preview

_BACKEND_WORDS = {"auto", "graphics", "blocks", "path", "chafa", "timg"}

_USAGE = (
    "[dim]usage:[/dim] [cyan]/image edit \"…\" [--ref <path>][/cyan] "
    "[dim]| [/dim][cyan]/image auto|graphics|blocks|path[/cyan]"
    "[dim]  (view is Canvas;[/dim] [cyan]/image latest[/cyan][dim] still works)[/dim]"
)


def _set_backend(ctx: dict[str, Any], backend: str) -> None:
    for obj in (ctx.get("state"), ctx.get("agent")):
        if obj is None:
            continue
        if hasattr(obj, "image_preview_backend"):
            obj.image_preview_backend = backend
        elif hasattr(obj, "session"):
            obj.session.image_preview_backend = backend


def _get_backend(ctx: dict[str, Any]) -> str:
    state = ctx.get("state") or ctx.get("agent")
    return getattr(state, "image_preview_backend", "auto") or "auto"


def _show_latest(ctx: dict[str, Any], console) -> bool:
    project = ctx.get("project")
    root = getattr(project, "project_root", None)
    if root is None:
        console.print("[red]no project root[/red]")
        return True
    sess = read_session(root)
    if not sess or not sess.paths:
        console.print("[dim](no recent artifact)[/dim]")
        return True
    rel = sess.paths[-1]
    try:
        path = resolve_artifact_path(root, rel)
    except (ValueError, FileNotFoundError) as e:
        console.print(f"[red]invalid artifact:[/red] {e}")
        return True
    console.print(f"[dim]latest:[/dim] [cyan]{rel}[/cyan]")
    # If the file panel is docked, render the image *there* (the converged surface: view it
    # in the pane). Otherwise fall through to the inline/transcript preview.
    from xlii.tui import panels

    if panels.route_to_dock(f"file://{path}"):
        console.print("[dim]→ opened in the file panel[/dim]")
        return True
    # An explicit `/image latest` is a request to see it — force past any
    # off-like XLII_IMAGE_PREVIEW env.
    maybe_preview(path, enabled=True, backend=_get_backend(ctx), console=console, force=True)
    return True


def _edit(ctx: dict[str, Any], rest: str, console) -> bool:
    """`/image edit "prompt" [--from locker|latest] [--ref <path>] [--model m]
    [--yolo]` — the honest front for the editprompt/refs machinery. Default
    source is the LATEST artifact (resolve_imagine_refs auto-latest)."""
    import shlex

    from xlii.imagine_run import ImagineRequest, run_imagine
    from xlii.repl_cmds.imagine import api_key_from_ctx

    project = ctx.get("project")
    root = getattr(project, "project_root", None)
    if root is None:
        console.print("[red]no project root[/red]")
        return True
    try:
        tokens = shlex.split(rest) if rest else []
    except ValueError as e:
        console.print(f"[red]parse error:[/red] {e}")
        return True

    prompt_parts: list[str] = []
    refs: list[str] = []
    from_locker = False
    model = None
    yolo_flag = False
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("--from-locker", "--locker"):
            from_locker = True
        elif t == "--from" and i + 1 < len(tokens):
            i += 1
            src = tokens[i].lower()
            if src == "locker":
                from_locker = True
            elif src != "latest":
                console.print(f"[red]unknown --from source: {src!r}[/red] "
                              "[dim](locker or latest)[/dim]")
                return True
        elif t == "--ref" and i + 1 < len(tokens):
            i += 1
            refs.append(tokens[i])
        elif t == "--model" and i + 1 < len(tokens):
            i += 1
            model = tokens[i]
        elif t == "--yolo":
            yolo_flag = True
        elif t.startswith("-"):
            console.print(f"[red]unknown flag: {t}[/red]")
            return True
        else:
            prompt_parts.append(t)
        i += 1

    prompt = " ".join(prompt_parts).strip().strip('"').strip("'")
    if not prompt:
        console.print("[dim]usage: [/dim][cyan]/image edit \"make the sky stormy\" "
                      "[--from locker|latest | --ref <path>][/cyan]")
        return True

    try:
        key = api_key_from_ctx(ctx)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return True

    state = ctx.get("state")
    req = ImagineRequest(edit_prompt=prompt, refs=refs, from_locker=from_locker)
    if model:
        req.model = model
    console.print("[dim]edit sends your image(s) as references — a guided "
                  "re-render, not a pixel-precise edit; composition can drift.[/dim]")
    run_imagine(
        root, req, api_key=key, console=console,
        preview_backend=_get_backend(ctx),
        yolo=yolo_flag or bool(getattr(state, "yolo", False)),
        state=state,
    )
    return True


def _image_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    parts = line.split(maxsplit=2)
    arg = parts[1].strip().lower() if len(parts) > 1 else ""

    if arg in _BACKEND_WORDS:
        _set_backend(ctx, arg)
        console.print(
            f"[green]✓[/green] preview backend set to [cyan]{arg}[/cyan] "
            f"[dim](/imagine)[/dim]"
        )
        return True

    if arg == "latest":
        return _show_latest(ctx, console)

    if arg == "edit":
        return _edit(ctx, parts[2] if len(parts) > 2 else "", console)

    console.print(_USAGE)
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="image",
            handler=_image_handler,
            description="Edit an existing image (Focus or --ref). View is Canvas.",
            usage="/image edit \"…\" [--ref <path>] | auto|graphics|blocks|path",
            repls=["code", "chat"],
        )
    )
