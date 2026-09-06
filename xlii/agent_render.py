"""Tool and streaming presentation helpers for the agent loop.

Also hosts the public renderer-tap seam (``RendererTap``, godzilla-mothra B5):
a body wraps an agent's renderer and mirrors every emitted typed event
(``xlii.turn_events``) to its own sink — the TUI keeps the rich rendering,
the WS head serializes to its wire protocol, a daemon logs. No textual/rich
crosses the seam: the sink receives the kernel event dataclasses.
"""

from __future__ import annotations

from typing import Any, Callable

from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from xlii.turn_text import tool_summary


class RendererTap:
    """Wrap a Renderer and mirror every ``emit()`` to an event sink.

    The public renderer-tap seam: ``on_event`` receives each typed event
    (a ``xlii.turn_events`` dataclass) before the inner renderer renders it.
    ``meta``/``error`` delegate straight to the inner renderer (unchanged
    behavior from the private ``_EventTap`` this replaces).

    The ``console`` property delegates too, so installing the tap in an
    Agent's ``_renderer_cache`` survives the cache's ``r.console is not
    self.console`` freshness guard — the tap stays the renderer for the
    whole turn instead of being rebuilt away (or AttributeErroring on a
    console-less wrapper).
    """

    def __init__(self, inner, on_event: Callable[[Any], None]):
        self._inner = inner
        self._on_event = on_event

    @property
    def console(self):
        return self._inner.console

    def emit(self, event) -> None:
        self._on_event(event)
        self._inner.emit(event)

    def meta(self, text: str, level: str = "info") -> None:
        self._inner.meta(text, level)

    def error(self, text: str) -> None:
        self._inner.error(text)


def _tool_arg_preview(name: str, args: dict[str, Any]) -> str:
    """The short arg hint shown beside a tool's name (path / pattern / command)."""
    if name in ("read_file", "write_file", "edit_file", "list_dir"):
        return args.get("path", "")
    if name == "bash":
        return args.get("command", "")[:80]
    if name == "grep":
        return f"/{args.get('pattern', '')}/"
    if name == "glob":
        return args.get("pattern", "")
    if name == "search_project":
        return args.get("query", "")[:80]
    if name == "dispatch_subagent":
        return args.get("task", "")[:80]
    return ""


def _format_tool_preview(name: str, content: str, is_error: bool) -> list[str]:
    """The classic dim `⎿` preview lines (non-styled path). Summarization now
    lives in turn_text.tool_summary — shared with the styled ToolBlock — so
    this only re-adds the `⎿`/markup the non-styled rendering has always used."""
    summary = tool_summary(name, content, is_error)
    if not summary:
        return []
    if is_error:
        return [f"  [red]⎿[/red] [red]{summary[0]}[/red]"]
    out = [f"  [dim]⎿ {summary[0]}[/dim]"]
    out += [f"  [dim]   {ln}[/dim]" for ln in summary[1:]]
    return out


def _streaming_tail(
    text: str,
    console: Console,
    *,
    frame_rows: int = 0,
    frame_cols: int = 0,
) -> Text:
    """Build the live streaming preview: only the last viewport-worth of lines.

    Rendering the *whole* growing buffer in a Live region is what caused
    duplicate/repeating output — a Live can only redraw lines still inside the
    terminal viewport, so once the answer grew taller than the screen the
    scrolled-off lines got re-emitted instead of overwritten. By feeding Live
    only the trailing `height - 2` lines as plain text we keep the live region
    bounded (it never exceeds the viewport, so nothing duplicates) while still
    showing the most recent tokens as they arrive. The full, correctly rendered
    Markdown is printed once after the stream ends.

    ``frame_rows``/``frame_cols`` adjust the reservation when the caller wraps
    the tail in a frame (a Panel: 2 border rows, 4 columns of border+padding):
    the row budget shrinks by the border rows and each logical line is charged
    its wrapped row count at the framed width, so the framed region still fits
    the viewport and Live's crop can't hide the newest tokens.
    """
    height = max(1, (console.size.height or 24) - 2 - frame_rows)
    lines = text.splitlines() or [""]
    if not frame_rows and not frame_cols:
        return Text("\n".join(lines[-height:]), style="dim")

    width = max(1, (console.size.width or 80) - frame_cols)
    tail: list[str] = []
    budget = height
    for line in reversed(lines):
        rows = max(1, -(-cell_len(line) // width))  # ceil division
        if rows > budget:
            if not tail:
                # The newest logical line alone overflows the budget (a long
                # markdown paragraph with no newline yet): keep its trailing
                # budget-worth of cells so the freshest tokens stay visible.
                max_cells = budget * width
                cells = 0
                i = len(line)
                while i > 0 and cells + cell_len(line[i - 1]) <= max_cells:
                    i -= 1
                    cells += cell_len(line[i])
                tail.append(line[i:])
            break
        tail.append(line)
        budget -= rows
        if budget <= 0:
            break
    return Text("\n".join(reversed(tail)), style="dim")
