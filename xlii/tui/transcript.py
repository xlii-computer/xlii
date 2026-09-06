"""Textual transcript widgets — structured answer rendering for the TUI.

RichLog renders assistant markdown as flat Rich strips, which makes code fences
impossible to address individually. This module keeps the existing Rich block
grammar for shell/tool/meta output (mounted as Static children) while routing
``TuiAnswer`` payloads through Textual's Markdown tree with copyable fences.

Only imported by ``xlii.tui_textual`` so the inline REPL / render kernel stay
Rich-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import Any, Callable

from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.events import Click
from textual.widget import Widget
from textual.widgets import Static
from textual.widgets.markdown import Markdown, MarkdownFence

from xlii.terminal_image import ImageRef, Tier
from xlii.tui.theme import THEME


@dataclass(frozen=True)
class TuiAnswer:
    """Assistant reply rendered with Textual Markdown (copyable code fences)."""

    markdown: str
    model: str | None = None
    mode: str = ""
    mode_color: str = ""
    role: str = ""
    skill: str = ""


def answer_payload(renderable: Any) -> TuiAnswer | None:
    """Return a ``TuiAnswer`` when ``renderable`` is (or wraps) an assistant reply."""
    if isinstance(renderable, TuiAnswer):
        return renderable
    if isinstance(renderable, Panel):
        from rich.markdown import Markdown as RichMarkdown

        body = renderable.renderable
        if isinstance(body, RichMarkdown):
            model, mode, role, skill = _fields_from_panel_title(renderable.title)
            mode_color = ""
            border_style = getattr(renderable, "border_style", None)
            if isinstance(border_style, str):
                mode_color = border_style
            markup = getattr(body, "markup", None) or str(body)
            return TuiAnswer(
                markdown=markup,
                model=model,
                mode=mode,
                mode_color=mode_color,
                role=role,
                skill=skill,
            )
    return None


def _fields_from_panel_title(title: Any) -> tuple[str | None, str, str, str]:
    """(model, mode, role, skill) from an answer Panel's title.
    Legacy 'xlii · <model>' → model only. Chip '┤ mode[ · role:r][ · skill:s] ├'
    → mode/role/skill, model None (the chip never carries a model)."""
    if title is None or title == "":
        return None, "", "", ""
    plain = title.plain if isinstance(title, Text) else str(title)
    plain = plain.strip()
    if plain.startswith("┤") and plain.endswith("├"):
        inner = plain[1:-1].strip()
        parts = [p.strip() for p in inner.split(" · ") if p.strip()]
        mode = parts[0] if parts else ""
        role = next((p for p in parts[1:] if p.startswith("role:")), "")
        skill = next((p for p in parts[1:] if p.startswith("skill:")), "")
        return None, mode, role, skill
    if " · " in plain:
        return plain.split(" · ", 1)[1].strip() or None, "", "", ""
    return None, "", "", ""


def _rich_to_plain(renderable: Any, *, width: int = 120) -> str:
    buf = StringIO()
    Console(file=buf, width=width, legacy_windows=False, force_terminal=False).print(renderable)
    return buf.getvalue().rstrip("\n")


class CopyableMarkdownFence(MarkdownFence):
    """Code fence with click / ``c`` to copy the raw block to the clipboard."""

    can_focus = True

    DEFAULT_CSS = """
    CopyableMarkdownFence {
        padding: 0;
        margin: 1 0;
        overflow: scroll hidden;
        scrollbar-size-horizontal: 0;
        scrollbar-size-vertical: 0;
        width: 1fr;
        height: auto;
        border: tall $panel;
        &:focus {
            border: tall $accent;
        }
        & > Label {
            padding: 1 2;
        }
    }
    """

    BINDINGS = [
        Binding("c", "copy_code", "Copy code", show=False),
    ]

    def on_click(self, event: Click) -> None:
        event.stop()
        self.action_copy_code()

    def action_copy_code(self) -> None:
        self.app.copy_to_clipboard(self.code)
        self.app.notify("Code copied", timeout=1.5)


class XliiMarkdown(Markdown):
    """Markdown document that uses copyable fences for code blocks."""

    BLOCKS = {
        **Markdown.BLOCKS,
        "fence": CopyableMarkdownFence,
        "code_block": CopyableMarkdownFence,
    }


class AnswerTranscript(Vertical):
    """Framed assistant answer — mirrors ``blocks.answer_block`` chrome."""

    DEFAULT_CSS = f"""
    AnswerTranscript {{
        height: auto;
        width: 1fr;
        border: round {THEME.assistant};
        border-title-align: right;
        padding: 0 1 1 1;
        margin: 0 0 1 0;
    }}
    AnswerTranscript > Static {{
        height: auto;
        padding: 0 0 0 1;
    }}
    AnswerTranscript XliiMarkdown {{
        padding: 0;
    }}
    """

    def __init__(self, payload: TuiAnswer) -> None:
        super().__init__()
        self._payload = payload
        self._record_color = ""
        if payload.mode:
            chip = payload.mode
            if payload.role:
                chip += f" · {payload.role}"
            if payload.skill:
                chip += f" · {payload.skill}"
            self.border_title = f"┤ {chip} ├"
            self._record_color = payload.mode_color or ""
            if self._record_color:
                self.styles.border = ("round", self._record_color)

    def on_focus(self) -> None:
        # styles.border is applied through Color.parse() — CSS variables like
        # `$accent` only resolve inside DEFAULT_CSS / TCSS, not here. Using one
        # crashes the whole TUI on focus (mouse click / tab into a mode-framed
        # SelectableAnswer). Keep a real color literal (theme scroll border).
        if self._record_color:
            self.styles.border = ("round", THEME.tui_input_scroll_border)

    def on_blur(self) -> None:
        if self._record_color:
            self.styles.border = ("round", self._record_color)

    def compose(self):
        if not self._payload.mode:
            title = Text()
            title.append("xlii", style=f"{THEME.assistant} bold")
            if self._payload.model:
                title.append(f" · {self._payload.model}", style=THEME.dim)
            yield Static(title)
        yield XliiMarkdown(self._payload.markdown)

    def plain_text(self) -> str:
        if self._payload.mode:
            chip = self._payload.mode
            if self._payload.role:
                chip += f" · {self._payload.role}"
            if self._payload.skill:
                chip += f" · {self._payload.skill}"
            parts = [f"┤ {chip} ├"]
        else:
            parts = ["xlii"]
            if self._payload.model:
                parts[0] += f" · {self._payload.model}"
        parts.append(self._payload.markdown)
        return "\n".join(parts)


def _activity_verb(plain: str) -> str | None:
    """The tool/shell name from a folded block's plain first line, for the drawer
    summary. ``read_file · pacman.py · 0.1s  ✓`` → ``read_file``; ``shell · … ·
    exit 0`` → ``shell``. Meta/announce/thought lines (``• 3 tool(s) in
    parallel``, ``◆ Thought…``) start with a glyph, not a word, and return None so
    they're stacked but not tallied as steps."""
    stripped = plain.strip()
    if not stripped:
        return None
    head = stripped.splitlines()[0].split("·", 1)[0].strip()
    if not head:
        return None
    verb = head.split()[0]
    return verb if verb[:1].isalnum() else None


class ActivityDrawer(Vertical):
    """One turn's tool/shell 'homework', stacked under a single collapsible
    summary line. Expanded while the turn runs (you watch tools fire); collapsed
    to a one-line ``▸ N tools · …`` summary once the sequence is done, so the
    finished turn reads as answer + receipt, not a wall of tool blocks. Click the
    summary to expand it back.

    The transcript treats the whole drawer as ONE block (one ``_plain_chunks``
    entry carrying every folded line), so drag-select / copy-mode grab the entire
    activity as a unit while the inner blocks stay individually copyable."""

    DEFAULT_CSS = """
    ActivityDrawer {
        height: auto;
        width: 1fr;
    }
    ActivityDrawer > #activity-summary {
        height: auto;
        width: 1fr;
        padding: 0 0 0 1;
    }
    ActivityDrawer > #activity-body {
        height: auto;
        width: 1fr;
    }
    ActivityDrawer.-collapsed > #activity-body {
        display: none;
    }
    """

    def __init__(self, *, block_factory: Callable[[Any, str], Widget]) -> None:
        super().__init__()
        self._block_factory = block_factory
        self._plains: list[str] = []
        self._verbs: list[str] = []
        self._pending: list[Widget] = []  # blocks awaiting the body's mount
        self._collapsed = False
        self._final = False
        self._summary = Static("", id="activity-summary")
        self._body = Vertical(id="activity-body")

    def compose(self):
        yield self._summary
        yield self._body

    def on_mount(self) -> None:
        # The body is now attached — flush any blocks that arrived while the
        # drawer's own mount was still in flight (mounting is async, so the first
        # tool of a turn can land before compose completes).
        self._summary.update(self._summary_text())
        self._flush_pending()

    # -- content ----------------------------------------------------------

    def add_block(self, renderable: Any, plain: str) -> None:
        self._plains.append(plain)
        verb = _activity_verb(plain)
        if verb:
            self._verbs.append(verb)
        self._pending.append(self._block_factory(renderable, plain))
        self._flush_pending()
        if not self._final:
            self._summary.update(self._summary_text())

    def _flush_pending(self) -> None:
        """Mount buffered blocks once the body container is attached. A no-op
        while it isn't (on_mount flushes them then)."""
        if not self._pending or not self._body.is_mounted:
            return
        batch, self._pending = self._pending, []
        self._body.mount(*batch)

    def is_empty(self) -> bool:
        return not self._plains

    def seal(self) -> None:
        """End of THIS batch: freeze the summary to its final tally, but stay
        EXPANDED. The collapse is deferred to a single turn-end pass so the fold
        never shrinks content mid-turn (that repeated reflow is the screen
        flash)."""
        self._final = True
        self._summary.update(self._summary_text())

    def collapse(self) -> None:
        """Turn end: fold to the one-line summary (arrow flips to ▸)."""
        self._collapsed = True
        self.add_class("-collapsed")
        self._summary.update(self._summary_text())

    def finalize(self) -> None:
        """Seal + collapse in one step — the immediate fold (a single-batch unit
        test); the live path seals per batch and collapses once at turn end."""
        self.seal()
        self.collapse()

    def plain_text(self) -> str:
        return "\n".join(self._plains)

    # -- interaction ------------------------------------------------------

    def on_click(self, event: Click) -> None:
        # Inner blocks stop their own clicks (copy), so a click that reaches the
        # drawer landed on the summary/margin — toggle the fold.
        event.stop()
        self.toggle()

    def toggle(self) -> None:
        self._collapsed = not self._collapsed
        self.set_class(self._collapsed, "-collapsed")
        self._summary.update(self._summary_text())

    # -- summary line -----------------------------------------------------

    def _breakdown(self) -> str:
        counts: dict[str, int] = {}
        order: list[str] = []
        for v in self._verbs:
            if v not in counts:
                order.append(v)
                counts[v] = 0
            counts[v] += 1
        parts = [f"{v} ×{counts[v]}" if counts[v] > 1 else v for v in order[:4]]
        if len(order) > 4:
            parts.append(f"+{len(order) - 4} more")
        return " · ".join(parts)

    def _summary_text(self) -> Text:
        arrow = "▸" if self._collapsed else "▾"
        t = Text()
        t.append(f"{arrow} ", style=THEME.assistant)
        if not self._final:
            bd = self._breakdown()
            t.append("working" + (f" · {bd}" if bd else "…"), style=THEME.dim)
            return t
        n = len(self._verbs) or len(self._plains)
        noun = "tool" if self._verbs else "step"
        label = f"{n} {noun}{'' if n == 1 else 's'}"
        bd = self._breakdown()
        t.append(f"{label} · {bd}" if bd else label, style=THEME.dim)
        return t


class TranscriptLog(VerticalScroll):
    """Scrollable transcript: Rich Static blocks plus structured answers.

    During an agent turn the surface brackets the tool/shell homework with
    :meth:`begin_activity` / :meth:`end_activity`; blocks written between the two
    stack into one :class:`ActivityDrawer` that collapses to a summary when the
    turn's answer lands. Outside that bracket (a bare ``!shell`` run, a slash
    message) writes mount loose as before."""

    def __init__(self, *, name: str | None = None, id: str | None = None) -> None:
        super().__init__(name=name, id=id, can_focus=True)
        self._plain_chunks: list[str] = []
        self._activity: ActivityDrawer | None = None
        self._activity_idx: int | None = None
        self._activity_open = False
        # Sealed-but-still-expanded batch drawers awaiting one turn-end collapse.
        self._turn_drawers: list[ActivityDrawer] = []

    # -- block factories (overridden by SelectableTranscriptLog) ----------

    def _make_answer(self, payload: TuiAnswer) -> Widget:
        return AnswerTranscript(payload)

    def _make_block(self, renderable: Any, plain: str) -> Widget:
        return Static(renderable, expand=True)

    # -- turn-activity folding --------------------------------------------

    def begin_activity(self) -> None:
        """Open a fold: tool/shell/meta blocks written until the batch is sealed
        stack into one collapsible drawer instead of N loose transcript blocks.
        The drawer is created lazily on the first block, so a tool-free reply
        leaves nothing behind."""
        self._activity_open = True

    def seal_activity(self) -> None:
        """End of ONE batch: freeze the current drawer's summary and park it —
        still EXPANDED — for a single turn-end collapse. Deferring the fold this
        way is what kills the mid-turn collapse flash. Idempotent."""
        self._activity_open = False
        drawer = self._activity
        self._activity = None
        idx, self._activity_idx = self._activity_idx, None
        if drawer is not None:
            drawer.seal()
            if idx is not None:
                self._plain_chunks[idx] = drawer.plain_text()
            self._turn_drawers.append(drawer)

    def end_activity(self) -> None:
        """Turn end: seal any open batch, then collapse every parked drawer in a
        SINGLE layout pass (one reflow, at the answer boundary — not one per
        batch). Called from the render slice (sequence done) and the turn's
        ``finally`` backstop (error / no-answer turns). Idempotent."""
        self.seal_activity()
        for drawer in self._turn_drawers:
            drawer.collapse()
        self._turn_drawers = []

    def _fold_block(self, renderable: Any) -> None:
        plain = _rich_to_plain(renderable)
        if self._activity is None:
            self._activity = ActivityDrawer(block_factory=self._make_block)
            self._activity_idx = len(self._plain_chunks)
            self._plain_chunks.append("")
            self.mount(self._activity)
        self._activity.add_block(renderable, plain)
        if self._activity_idx is not None:
            self._plain_chunks[self._activity_idx] = self._activity.plain_text()

    def write(self, renderable: Any, *, scroll_end: bool = True) -> None:
        payload = answer_payload(renderable)
        is_result = payload is not None or isinstance(renderable, ImageRef)
        # Mid-turn homework folds into the drawer; the answer/image is the turn's
        # destination and ends the fold so it — and the footer/receipt after it —
        # land as permanent blocks.
        if self._activity_open and not is_result:
            self._fold_block(renderable)
            if scroll_end:
                self.call_after_refresh(self.scroll_end, animate=False)
            return
        if is_result:
            self.end_activity()
        if isinstance(renderable, ImageRef):
            entry: Any = self._image_entry(renderable)
            plain = f"[image: {renderable.path.name}]"
        elif payload is not None:
            entry = self._make_answer(payload)
            plain = entry.plain_text()
        else:
            plain = _rich_to_plain(renderable)
            entry = self._make_block(renderable, plain)
        self._plain_chunks.append(plain)
        self.mount(entry)
        if scroll_end:
            self.call_after_refresh(self.scroll_end, animate=False)

    def _image_entry(self, ref: ImageRef):
        """Build a true graphics image widget (textual-image: sixel/kitty/iterm,
        auto-falling-back to unicode where unsupported) for an ImageRef. If the
        widget can't be built (package missing, file vanished), degrade to the
        chafa-symbols Rich renderable in a Static — never raise into the transcript."""
        entry, _tier = _build_image_entry(ref)
        return entry

    def clear(self) -> None:
        self._plain_chunks.clear()
        self._activity = None
        self._activity_idx = None
        self._activity_open = False
        self._turn_drawers = []
        self.remove_children()

    def plain_text(self) -> str:
        return "\n".join(self._plain_chunks)

    @property
    def lines(self) -> list[str]:
        """Rough RichLog compatibility for tests — one chunk per write."""
        return self._plain_chunks


def image_display_tier(ref: ImageRef) -> Tier:
    """Tier that will actually render for an ``ImageRef`` (widget vs chafa vs path)."""
    _entry, tier = _build_image_entry(ref)
    return tier


def _build_image_entry(ref: ImageRef):
    try:
        from textual_image.widget import Image as _ImageWidget

        img = _ImageWidget(str(ref.path))
        img.styles.width = ref.max_width
        img.styles.height = "auto"
        img.styles.margin = (0, 0, 1, 0)
        return img, "graphics"
    except Exception:
        from xlii.terminal_image import image_renderable

        rend = image_renderable(ref.path, max_width=ref.max_width)
        if rend is not None:
            return Static(rend, expand=True), "blocks"
        return Static(Text(str(ref.path)), expand=True), "path"
