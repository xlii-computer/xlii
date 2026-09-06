"""Rich renderables for each block type, in the "rule + gutter" density: a
left-aligned header Rule + an indented body, with full boxes reserved for the
assistant answer and hard errors. Every block shares the header grammar so a
human shell run, an agent bash run, and a slash message read as one surface.

These functions are pure — event in, Rich renderable out. The Renderer owns the
Console and the plain-mode fallback; tests render these to a StringIO Console
and assert on structure (no live terminal needed).

The pure text half (tool_summary, turn_footer) lives kernel-side in
xlii.turn_text (godzilla-mothra V1ab) and is re-exported here for existing
call sites. Imports only kernel leaves (xlii.turn_events, xlii.turn_text,
xlii.theme) — never xlii.tui's package __init__ or xlii.ui, so there is no
import cycle.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich import box
from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from xlii.theme import THEME, Theme
from xlii.turn_events import AssistantAnswer, MetaMessage, ShellRan, ToolFinished, UserTurn
from xlii.turn_text import tool_summary
from xlii.turn_text import turn_footer as turn_footer  # re-export

# Meta-message level → Rich style.
_META_STYLE = {
    "info": "dim",
    "success": "green",
    "warn": "yellow",
    "error": "red",
    "mode": "bold #d0b060",
}


def _fmt_duration(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    return f"{seconds:.1f}s" if seconds < 10 else f"{seconds:.0f}s"


def _short_cwd(cwd: Path) -> str:
    """`~/proj/sub` when under $HOME, absolute path otherwise."""
    try:
        return "~/" + str(cwd.relative_to(Path.home()))
    except (ValueError, RuntimeError):
        return str(cwd)


def _window(lines: list[str], max_lines: Optional[int], tail_lines: int):
    """(head, tail, hidden_count). Head-only when the budget is too small to
    split; head + tail when there's room for both (so a command's verdict at the
    end survives truncation, not just its banner)."""
    if max_lines is None or len(lines) <= max_lines:
        return lines, [], 0
    if tail_lines and max_lines > tail_lines + 1:
        head = max_lines - tail_lines
        return lines[:head], lines[-tail_lines:], len(lines) - head - tail_lines
    return lines[:max_lines], [], len(lines) - max_lines


def _gutter_body(
    text: str,
    *,
    theme: Theme,
    style: Optional[str] = None,
    max_lines: Optional[int] = None,
    tail_lines: int = 0,
) -> Text:
    """Indented body: first line gets the `⎿` gutter, rest align under it. When
    `max_lines` is exceeded, keep the first `head` and last `tail_lines` and drop
    the middle behind a dim `+N lines hidden` marker. The full text always lives
    in the event — this only trims the display."""
    head, tail, hidden = _window(text.splitlines(), max_lines, tail_lines)
    body = Text()
    wrote = False

    def _line(ln: str, gutter: str) -> None:
        nonlocal wrote
        if wrote:
            body.append("\n")
        body.append(gutter, style=theme.dim)
        body.append(ln, style=style)
        wrote = True

    for i, ln in enumerate(head):
        _line(ln, f" {theme.gutter} " if i == 0 else "   ")
    if hidden:
        if wrote:
            body.append("\n")
        body.append(f"   +{hidden} lines hidden", style=theme.dim)
        wrote = True
    for ln in tail:
        _line(ln, "   ")
    return body


def shell_block(
    ev: ShellRan, *, theme: Theme = THEME, max_lines: Optional[int] = None
) -> RenderableType:
    """ShellBlock — a quiet dim header rule (cmd context, with only the exit
    status as a small green/red accent) over the command line and its captured
    output. The line itself is dim so the block reads as a header, not a bar."""
    ctx = ["shell", _short_cwd(ev.cwd), theme.source_label(ev.source)]
    if ev.intent:
        ctx.append(ev.intent)
    title = Text(" · ".join(ctx) + " · ", style=theme.dim)
    title.append(f"exit {ev.returncode}", style=theme.status_color(ev.returncode))
    dur = _fmt_duration(ev.duration_s)
    if dur:
        title.append(f" · {dur}", style=theme.dim)
    header = Rule(title=title, style=theme.rule, align="left")

    body = Text()
    body.append(f" {theme.prompt} ", style=theme.shell)
    body.append(ev.command)

    combined = ev.stdout or ""
    if ev.stderr:
        combined = f"{combined}\n{ev.stderr}" if combined else ev.stderr
    if combined:
        # Output is the content now (not a dim teaser) — render it readable, and
        # keep the tail (verdict/exit summary) when truncating long runs. Scale
        # the tail to the budget so a tight ~8-line preview (the TUI transcript)
        # still shows head AND verdict (~4+4) instead of 32 head + 8 tail; a
        # very small budget stays head-only (the split needs max_lines>tail+1).
        tail_lines = 8 if max_lines is None else min(8, max(2, max_lines // 2))
        tail = _gutter_body(
            combined, theme=theme, style=None, max_lines=max_lines, tail_lines=tail_lines
        )
        body.append("\n")
        body.append_text(tail)
    return Group(header, body)


def tool_block(
    ev: ToolFinished, *, theme: Theme = THEME, max_lines: Optional[int] = None
) -> RenderableType:
    """ToolBlock — for non-shell tools (read_file, grep, …). Like ShellBlock but
    no command line; the ✓/✗ status icon rides the header."""
    ctx = [ev.name]
    if ev.args_preview:
        ctx.append(ev.args_preview)
    dur = _fmt_duration(ev.duration_s)
    if dur:
        ctx.append(dur)
    title = Text(" · ".join(ctx) + "  ", style=theme.dim)
    title.append(
        theme.status_icon(ev.is_error),
        style=theme.err_color if ev.is_error else theme.ok_color,
    )
    header = Rule(title=title, style=theme.rule, align="left")

    summary = tool_summary(ev.name, ev.content, ev.is_error)
    if not summary:
        return header
    body_style = theme.err_color if ev.is_error else None
    body = _gutter_body("\n".join(summary), theme=theme, style=body_style, max_lines=max_lines)
    return Group(header, body)


def meta_block(ev: MetaMessage, *, theme: Theme = THEME) -> RenderableType:
    """MetaBlock — one line, `•` icon + level-colored text. The single print
    path for slash-command output so `/status` stops looking like log spew."""
    t = Text()
    t.append(f"{theme.meta} ", style=theme.meta_color)
    t.append(ev.text, style=_META_STYLE.get(ev.level, "dim"))
    return t


def user_block(ev: UserTurn, *, theme: Theme = THEME) -> RenderableType:
    """UserBlock — opens an agent turn frame with the submitted prompt.

    Large raw dumps (or leftover un-expanded paste bodies) are summarized so a
    paste cannot push the rest of the transcript off-screen.

    Track H0: the ``you`` header uses the speaker color (not the dim rule) and
    bold title so asks read as a distinct speaker from the framed answer.
    """
    from xlii.paste_collapse import collapse_for_display

    shown = collapse_for_display(ev.text)
    header = Rule(
        title=Text("you", style=f"bold {theme.user}"),
        style=theme.user,
        align="left",
    )
    # Body stays near-neutral once the header carries speaker identity.
    return Group(header, Text(f" {shown}"))


def answer_block(ev: AssistantAnswer, *, theme: Theme = THEME) -> RenderableType:
    """AnswerBlock — the assistant's reply in a light rounded panel so it reads as
    the turn's destination, clearly lifted off the dim tool/shell 'homework' that
    precedes it (those stay quiet header rules). The accent-colored frame is what
    sets it apart from the red error box: a colored frame means 'answer', a red
    one means 'the turn failed'. When tense-chrome fields are present, the frame
    border and top-right chip name the mode/role that produced the turn.

    Track H0: always framed (never an unadorned paragraph under the user block);
    border uses ``theme.assistant``, which must differ from ``theme.user``.
    """
    if getattr(ev, "mode", ""):
        chip = ev.mode
        if getattr(ev, "role", ""):
            chip += f" · {ev.role}"
        if getattr(ev, "skill", ""):
            chip += f" · {ev.skill}"
        color = getattr(ev, "mode_color", "") or theme.assistant
        return Panel(
            Markdown(ev.markdown),
            title=Text(f"┤ {chip} ├", style=f"bold {color}"),
            title_align="right",
            border_style=color,
            box=box.ROUNDED,
        )
    title = Text()
    title.append("xlii", style=f"{theme.assistant} bold")
    if ev.model:
        title.append(f" · {ev.model}", style=theme.dim)
    return Panel(
        Markdown(ev.markdown),
        title=title,
        title_align="left",
        border_style=theme.assistant,
        box=box.ROUNDED,
    )


def error_box(text: str, *, theme: Theme = THEME) -> RenderableType:
    """A hard error (turn failed, auth rejected) promoted to a full red box so
    it can't be missed between routine blocks."""
    return Panel(
        Text(text),
        title=f"{theme.err} error",
        title_align="left",
        border_style=theme.err_color,
        box=box.ROUNDED,
    )


def loop_status_block(lines: list[str]) -> RenderableType:
    """Compact loop status panel for between-turn display (L3)."""
    body = Text("\n".join(line.replace("[loop]", "").strip() for line in lines))
    return Panel(
        body,
        title="[cyan]loop[/cyan]",
        border_style="cyan",
        box=box.ROUNDED,
        padding=(0, 1),
    )
