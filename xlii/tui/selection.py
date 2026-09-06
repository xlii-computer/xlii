"""In-app transcript selection & copy — the-fold Vector E.

Textual 8.2.7 already ships a complete selection engine, so this module is a
*delta*, not a from-scratch implementation. The audit (see
``proposals/reports/fold-e.md``) established what the framework already gives us
on the extracted transcript layout:

* ``ALLOW_SELECT`` defaults ``True`` on every widget we use (``Static``,
  ``Markdown``, ``VerticalScroll`` …), and ``Screen._forward_event`` drives
  drag-selection off ``MouseDown``/``MouseMove``/``MouseUp`` with auto-scroll.
* ``App.copy_to_clipboard`` writes **OSC 52** (``\\x1b]52;c;<base64>\\a``) so a
  copy reaches the *terminal's* clipboard and survives SSH.
* ``Screen`` binds ``ctrl+c``/``super+c`` → ``screen.copy_text`` which copies the
  current selection and ``SkipAction``s when nothing is selected (so the app's
  ``ctrl+c`` quit still works with an empty selection).

The one gap the built-ins leave for *this* transcript: blocks are mounted as
``Static(<rich Group/Panel>)`` (shell/tool/user/meta output). Textual's default
``Widget.get_selection`` can only read a ``Text``/``Content`` visual, so a drag
over those blocks highlights but extracts an empty string. We already know each
block's plain text (``TranscriptLog`` records it for ``_plain_chunks``), so we
override ``get_selection`` to slice *that* — reusing Textual's own
``Selection.extract`` — and every block becomes copyable.

On top of the engine we add explicit copy affordances beyond the existing
copyable code fences (``CopyableMarkdownFence``): a whole **answer** and a whole
tool/shell/meta **block** copy with a click or, when focused, ``c`` — the same
gesture the fences already teach.

Owned by Vector E. The only wiring into the app is the compose swap
(``SelectableTranscriptLog`` in place of ``TranscriptLog`` in
``xlii/tui/app.py``); the transcript widgets themselves (``xlii/tui/transcript``)
are specialised here by subclassing, not edited.
"""

from __future__ import annotations

from typing import Any

from textual.binding import Binding
from textual.events import Click
from textual.selection import Selection
from textual.widget import Widget
from textual.widgets import Static

from xlii.tui.transcript import (
    AnswerTranscript,
    TranscriptLog,
    TuiAnswer,
)


class _PlainSelectionMixin:
    """Extract a drag-selection from a widget's *known plain text*.

    Textual's default ``get_selection`` renders the widget and only extracts when
    the visual is ``Text``/``Content``. Our blocks wrap arbitrary rich
    renderables (``Group``/``Panel``), which it reads as empty. We hold the plain
    text captured at write time and slice it with the *same* ``Selection.extract``
    the base class uses, so a drag over a shell block copies real text.
    """

    _plain: str = ""

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        if not self._plain:
            return None
        return selection.extract(self._plain), "\n"


class SelectableStatic(_PlainSelectionMixin, Static):
    """A transcript block that is drag-selectable *and* click/``c``-copyable.

    Mirrors ``CopyableMarkdownFence`` for the non-fence blocks (tool output,
    shell output, meta lines): a plain click — or ``c`` while the block is
    focused — copies the whole block to the clipboard via OSC 52. A drag is
    handled by the screen at the mouse-event layer before ``Click`` is
    synthesised, so drag-to-select and click-to-copy never collide.
    """

    can_focus = True

    DEFAULT_CSS = """
    SelectableStatic {
        height: auto;
    }
    SelectableStatic:focus {
        background: $boost;
    }
    """

    BINDINGS = [
        Binding("c", "copy_block", "Copy block", show=False),
    ]

    def __init__(self, renderable: Any, *, plain: str, **kwargs: Any) -> None:
        super().__init__(renderable, **kwargs)
        self._plain = plain

    def on_click(self, event: Click) -> None:
        # A true click (down+up on the same cell) copies the whole block; a drag
        # never reaches here (no Click is synthesised for it), so selection is
        # untouched. Stop propagation so the click doesn't bubble to the app.
        event.stop()
        self.action_copy_block()

    def action_copy_block(self) -> None:
        text = self._plain
        if not text.strip():
            return
        self.app.copy_to_clipboard(text)
        self.app.notify("Output copied", timeout=1.5)


class SelectableAnswer(AnswerTranscript):
    """An assistant answer with a whole-answer copy affordance.

    Inherits the framed Markdown rendering (and its copyable code fences)
    unchanged; adds a click / ``c`` gesture that copies the answer's raw markdown
    — the most useful thing to paste elsewhere. Clicks on an inner code fence are
    stopped by the fence (copy code); clicks on prose bubble up to here (copy the
    whole answer). Drag-selection inside the answer uses Textual's native
    per-paragraph text selection.
    """

    can_focus = True

    # AnswerTranscript's DEFAULT_CSS already applies (a type selector matches the
    # subclass), so we add only the focus delta. The border is already `round`, so
    # recolouring it on focus costs no extra rows — D's chrome geometry is intact.
    DEFAULT_CSS = """
    SelectableAnswer:focus {
        border: round $accent;
    }
    """

    BINDINGS = [
        Binding("c", "copy_answer", "Copy answer", show=False),
    ]

    def on_click(self, event: Click) -> None:
        event.stop()
        self.action_copy_answer()

    def action_copy_answer(self) -> None:
        text = self._payload.markdown
        if not text.strip():
            return
        self.app.copy_to_clipboard(text)
        self.app.notify("Answer copied", timeout=1.5)


class SelectableTranscriptLog(TranscriptLog):
    """``TranscriptLog`` whose blocks are selectable + copyable.

    Same write contract as the base (records plain text for ``_plain_chunks`` /
    ``plain_text`` / ``.lines``, mounts one child per block, scrolls to end), but
    mounts the selection-aware block widgets. Kept as a subclass so
    ``query_one("#log", TranscriptLog)`` and every existing importer keep working
    unchanged; the app swaps this in at compose time.

    **Copy-mode** (Vector E Round-2): a mouseless, modal *block-range* selection —
    the tmux/less "copy mode" idiom at block granularity. Entered via ``ctrl+r``
    (an app binding); then ``j``/``k`` move the cursor block, ``g``/``G`` jump to
    ends, ``v`` marks the range anchor, ``y`` yanks the selected blocks to the
    clipboard over OSC 52, and ``esc``/``q`` exits. Line-level range selection is
    deliberately out of scope (driving Textual's mouse-offset engine from keys —
    see ``proposals/reports/fold-e.md``); block granularity carries the value for
    mouseless / SSH copy without that complexity. The nav bindings are gated to
    copy-mode via ``check_action`` so they pass through when the transcript is
    merely focused for scrolling.
    """

    BINDINGS = [
        Binding("j", "cm_move(1)", "Down", show=False),
        Binding("k", "cm_move(-1)", "Up", show=False),
        Binding("g", "cm_top", "Top", show=False),
        Binding("G", "cm_bottom", "Bottom", show=False),
        Binding("v", "cm_anchor", "Mark", show=False),
        Binding("y", "cm_yank", "Yank", show=False),
        Binding("escape", "cm_exit", "Exit copy-mode", show=False),
        Binding("q", "cm_exit", "Exit copy-mode", show=False),
    ]

    DEFAULT_CSS = """
    SelectableTranscriptLog > .copy-cursor { background: $accent; color: $text; }
    SelectableTranscriptLog > .copy-range { background: $boost; }
    """

    def __init__(self, *, name: str | None = None, id: str | None = None) -> None:
        super().__init__(name=name, id=id)
        self._copy_mode: bool = False
        self._cm_cursor: int = 0
        self._cm_anchor: int | None = None

    # --- copy-mode ---------------------------------------------------------- #

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        """Enable the ``cm_*`` nav bindings only in copy-mode, so ``j``/``k``/``v``
        /``y`` pass through normally when the transcript is just focused."""
        if action.startswith("cm_"):
            return self._copy_mode
        return True

    def enter_copy_mode(self) -> None:
        n = len(self._plain_chunks)
        if n == 0:
            self.app.notify("Nothing to copy yet", timeout=1.5)
            return
        self._copy_mode = True
        self._cm_cursor = n - 1        # start at the newest block
        self._cm_anchor = None
        self.focus()
        self._cm_render()
        self.app.notify("copy-mode · j/k move · v mark · y yank · esc exit", timeout=3)

    def _cm_range(self) -> tuple[int, int]:
        if self._cm_anchor is None:
            return self._cm_cursor, self._cm_cursor
        return min(self._cm_anchor, self._cm_cursor), max(self._cm_anchor, self._cm_cursor)

    def _cm_render(self) -> None:
        children = list(self.children)
        lo, hi = self._cm_range()
        for i, w in enumerate(children):
            w.remove_class("copy-cursor", "copy-range")
            if i == self._cm_cursor:
                w.add_class("copy-cursor")
            elif lo <= i <= hi:
                w.add_class("copy-range")
        if 0 <= self._cm_cursor < len(children):
            self.scroll_to_widget(children[self._cm_cursor], animate=False)

    def _cm_clear_styles(self) -> None:
        for w in list(self.children):
            w.remove_class("copy-cursor", "copy-range")

    def action_cm_move(self, delta: int) -> None:
        n = len(self._plain_chunks)
        if not n:
            return
        self._cm_cursor = max(0, min(n - 1, self._cm_cursor + delta))
        self._cm_render()

    def action_cm_top(self) -> None:
        self._cm_cursor = 0
        self._cm_render()

    def action_cm_bottom(self) -> None:
        self._cm_cursor = max(0, len(self._plain_chunks) - 1)
        self._cm_render()

    def action_cm_anchor(self) -> None:
        # toggle: drop the range anchor at the cursor, or lift it if already here
        self._cm_anchor = None if self._cm_anchor == self._cm_cursor else self._cm_cursor
        self._cm_render()

    def action_cm_yank(self) -> None:
        lo, hi = self._cm_range()
        n = len(self._plain_chunks)
        lo, hi = max(0, lo), min(n - 1, hi)
        parts = [self._plain_chunks[i] for i in range(lo, hi + 1) if self._plain_chunks[i].strip()]
        text = "\n\n".join(parts)
        count = hi - lo + 1
        self.action_cm_exit()
        if text.strip():
            self.app.copy_to_clipboard(text)
            self.app.notify(f"Copied {count} block{'' if count == 1 else 's'}", timeout=1.5)

    def action_cm_exit(self) -> None:
        self._copy_mode = False
        self._cm_anchor = None
        self._cm_clear_styles()
        try:
            self.app.query_one("#input").focus()
        except Exception:
            # Copy mode is already exited; there may be no input line left to refocus.
            pass

    # Same write contract as the base — including the turn-activity fold
    # (begin_activity/end_activity) — but the loose blocks, the folded blocks
    # inside the ActivityDrawer, and the answer are all the selectable/copyable
    # variants, wired through the base's block factories.

    def _make_answer(self, payload: TuiAnswer) -> Widget:
        return SelectableAnswer(payload)

    def _make_block(self, renderable: Any, plain: str) -> Widget:
        return SelectableStatic(renderable, plain=plain, expand=True)
