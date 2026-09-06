"""/imagine — generate images via xAI Imagine into .xlii/artifacts/.

`/imagine` conjures what you don't have (text → image); operating on an image
you DO have is `/image latest` / `/image edit` (the have-vs-conjure split,
tui-media-delivery P1). The old image-mode staging + --shumup bridge are gone —
preview is ambient and generated artifacts have their own door (artifacts://).
"""

from __future__ import annotations

import shlex
from typing import Any

from xlii.commands import REPLCommand, register_repl_command
from xlii.imagine_run import (
    api_key_from_ctx,
    parse_imagine_tokens,
    run_imagine,
)


def _imagine_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx["console"]
    project = ctx.get("project")
    if project is None:
        console.print("[red]/imagine needs an active project session[/red]")
        return True

    body = line.split(maxsplit=1)
    rest = body[1] if len(body) > 1 else ""
    try:
        tokens = shlex.split(rest) if rest else []
    except ValueError as e:
        console.print(f"[red]parse error:[/red] {e}")
        return True

    try:
        req = parse_imagine_tokens(tokens)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return True

    state = ctx.get("state")
    preview_backend = getattr(state, "image_preview_backend", "auto") or "auto"
    yolo = bool(getattr(state, "yolo", False))

    try:
        key = api_key_from_ctx(ctx)
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        return True

    run_imagine(
        project.project_root,
        req,
        api_key=key,
        console=console,
        preview_backend=preview_backend,
        yolo=yolo,
        state=state,
    )
    return True


def register() -> None:
    register_repl_command(
        REPLCommand(
            name="imagine",
            handler=_imagine_handler,
            description="Generate an image via xAI Imagine (local artifact under .xlii/artifacts/)",
            usage=(
                '/imagine "prompt" | --redo | --editprompt "…" | '
                '--ref <path|name> | --from-locker | --save <path> | --last'
            ),
            category="session",
        )
    )
