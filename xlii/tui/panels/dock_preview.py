"""dock_renderable — the inline preview the dock shows for a selected item.

Rich-only — safe without ``[tui]``. Split out of the one-file ``panels.py``
(V1c decomposition); behavior unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

# Kinds A2's preview registry (seam #2) renders directly; anything else (a plain
# attached file: text/pdf/other) gets the light local preview below.
_PREVIEW_KINDS = ("image", "doc", "skill", "role", "ref")
_DOCK_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".text", ".log", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf", ".py", ".js",
    ".ts", ".tsx", ".jsx", ".sh", ".bash", ".zsh", ".fish", ".c", ".h", ".cpp",
    ".hpp", ".cc", ".rs", ".go", ".rb", ".java", ".kt", ".html", ".css", ".xml",
    ".sql", ".lua", ".pl", ".r", ".jl", ".scala", ".env",
}
_DOCK_TEXT_MAX_LINES = 200
_DOCK_TEXT_MAX_BYTES = 64 * 1024


def _payload_path(payload: Any) -> Optional[Path]:
    raw: Any = None
    if isinstance(payload, dict):
        raw = payload.get("path") or payload.get("target")
    elif isinstance(payload, (tuple, list)) and payload:
        raw = payload[-1]
    elif isinstance(payload, (str, Path)):
        raw = payload
    if not raw:
        return None
    try:
        return Path(str(raw)).expanduser()
    except Exception:
        return None


def _file_preview(payload: Any) -> Any:
    """A light read-only preview for a plain attached file (no A2 provider): the
    head of a text file as Markdown/Text, an image via the chafa-symbols sink, or
    a path/size note for a binary. Rich-only — safe without [tui]."""
    from rich.text import Text

    p = _payload_path(payload)
    if p is None:
        return Text("(nothing to preview)", style="dim italic")
    name = p.name
    suffix = p.suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg"):
        try:
            from xlii import terminal_image

            rendered = terminal_image.image_renderable(p)
        except Exception:
            rendered = None
        if rendered is not None:
            from rich.console import Group

            head = Text(name, style="bold cyan")
            return Group(head, Text(""), rendered)
    if not p.is_file():
        return Text(str(p), style="dim")
    if suffix in _DOCK_TEXT_SUFFIXES:
        try:
            data = p.read_bytes()[:_DOCK_TEXT_MAX_BYTES]
            body = data.decode("utf-8", errors="replace")
        except Exception:
            body = ""
        lines = body.splitlines()
        clipped = "\n".join(lines[:_DOCK_TEXT_MAX_LINES])
        if len(lines) > _DOCK_TEXT_MAX_LINES:
            clipped += f"\n… (+{len(lines) - _DOCK_TEXT_MAX_LINES} more lines)"
        from rich.console import Group

        head = Text(f"{name}", style="bold cyan")
        if suffix in (".md", ".markdown"):
            from rich.markdown import Markdown

            return Group(head, Text(""), Markdown(clipped or "_(empty)_"))
        return Group(head, Text(""), Text(clipped or "(empty)"))
    try:
        size = p.stat().st_size
    except OSError:
        size = 0
    note = Text(f"{name}", style="bold cyan")
    note.append(f"\n{p}\n", style="dim")
    note.append(f"{size} bytes · {suffix or 'no suffix'}", style="dim")
    return note


def dock_renderable(kind: str, payload: Any, *, state: Any = None) -> Any:
    """Build the inline dock preview for a selected item.

    The supported A2 kinds (image/doc/skill/role/ref) render through the published
    ``preview.preview_for`` (seam #2 — *you see what you're about to discuss*); a
    plain attached file (text/pdf/other) degrades to a light local preview. Always
    returns a Rich renderable — a bad provider can never blank the dock."""
    if kind in _PREVIEW_KINDS:
        try:
            from xlii.tui import preview as _preview

            rendered = _preview.preview_for(kind, payload, state=state)
            if rendered is not None:
                return rendered
        except Exception:
            # Per the contract above: a bad provider falls through to the generic renderable.
            pass
    return _file_preview(payload)
