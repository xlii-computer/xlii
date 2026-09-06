"""The TUI input surface — the bottom text box and the attachment chip row.

Three Textual widgets plus one pure renderable, extracted from the ``tui_textual``
monolith (the-fold Vector D):

* :func:`_chip_row` — pure. Right-aligned attachment chips + click hit-spans.
* :class:`_ChipRow` — one row below the input; click/key interactive chips.
* :class:`_PromptInput` — the multi-line ``TextArea`` input.
* :class:`_InputActionButton` — the labelled `stop` button to the right of the box.

The input wears a single uniform Textual round border (mode-colored via
``_paint_input_frame``). Chips are NOT cut into the frame — they live on their
own row between the input and the F-key bar.

``_PromptInput`` is also the TUI fulfilment of the kernel's ``CLAIM_INPUT`` ask
(godzilla-mothra V0c — the minibuffer rule: no popups; a panel item that needs
input claims THE input line instead). :meth:`_PromptInput.claim_input` morphs
the line for one :class:`xlii.panes.InputClaim`: the in-progress REPL draft is
stashed, the claim's initial text is seeded, and the claim's prompt retitles
the input frame; Enter submits the line to the claim's callback, and Esc ALWAYS
releases the line back to the normal REPL input (draft + prompt restored). The
double-claim rule: the input line is single-tenant — claiming while a claim is
active is REJECTED (``claim_input`` returns False; never queued) and invokes no
callbacks; the dispatcher (the Dock) owns refusal notification.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Callable, Optional

from rich.cells import cell_len
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.strip import Strip
from textual.widgets import Button, Static, TextArea

from xlii import shell_suggest
from xlii.paste_collapse import PasteStore, normalize_paste
from xlii.tui import shortcodes
from xlii.tui.status_strip import _FKEY_KEYS

if TYPE_CHECKING:  # the app these widgets are mounted in; type-only, no runtime edge.
    from xlii.panes import InputClaim  # noqa: F401  (kernel ask-shape; face→kernel direction)
    from xlii.tui_textual import XliiApp  # noqa: F401  (re-exported from app.py post-extraction)


def _default_input_max_lines() -> int:
    """Max visible input lines in the Textual TUI (word-wrapped). Default 5."""
    raw = os.environ.get("XLII_TUI_INPUT_MAX_LINES", "").strip()
    if not raw:
        return 5
    try:
        return max(1, int(raw))
    except ValueError:
        return 5


# Fixed width (terminal columns) for the stop button beside the input — wide
# enough for the "stop" label + its round border + a column of breathing room.
_INPUT_ACTION_WIDTH = 8


_DOOR_ICONS = {
    "skills": "⚡",
    "docs": "📋",
    "mark": "🔖",
    "locker": "🖼",
    "wiki": "📖",
}


def _chip_display(label: str, kind: str, payload: Any) -> str:
    """Short chip text with a leading symbol per kind."""
    if kind == "role":
        return f"◎ {label}"
    if kind == "file":
        return f"📎 {label}"
    if kind == "door":
        scheme = str(payload or (label.split()[0] if label else ""))
        icon = _DOOR_ICONS.get(scheme, "·")
        return f"{icon} {label}"
    return label


def _chip_row(
    width: int,
    tabs: list[tuple],
    color: str,
    focused: Optional[int] = None,
) -> tuple[Text, list[tuple[int, int, str, Any]]]:
    """Right-aligned attachment chips for the row below the input.

    Returns ``(renderable, spans)`` where each span is
    ``(start_x, end_x, kind, payload)`` for click mapping."""
    if not tabs:
        return Text(), []
    sep = "  "
    displays = [
        _chip_display(tab[0], tab[1], tab[2] if len(tab) > 2 else None)
        for tab in tabs
    ]
    body_w = sum(cell_len(d) for d in displays) + len(sep) * max(0, len(displays) - 1)
    pad = max(0, width - body_w)

    out = Text(no_wrap=True, overflow="crop")
    if pad:
        out.append(" " * pad)
    spans: list[tuple[int, int, str, Any]] = []
    x = pad
    for i, tab in enumerate(tabs):
        if i:
            out.append(sep, style="dim")
            x += len(sep)
        display = displays[i]
        kind = tab[1]
        payload = tab[2] if len(tab) > 2 else None
        style = f"bold {color}"
        if focused is not None and i == focused:
            style = f"{style} underline"
        start = x
        out.append(display, style=style)
        x += len(display)
        spans.append((start, x - 1, kind, payload))
    return out, spans


# Back-compat aliases for tests and re-exports that still import the old names.
_folder_tab = _chip_row


class _ChipRow(Static):
    """Attachment chips on the row below the input — click/key target (seam #1)."""

    can_focus = True

    BINDINGS = [
        Binding("left", "cycle(-1)", "Prev tab", show=False),
        Binding("right", "cycle(1)", "Next tab", show=False),
        Binding("enter", "activate", "Open tab", show=False),
        Binding("escape", "leave", "Back to input", show=False),
    ]

    def __init__(
        self,
        *args: Any,
        on_activate_tab: Optional[Callable[[str, Any], None]] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._on_activate_tab = on_activate_tab
        self._tabs: list[tuple] = []
        self._spans: list[tuple[int, int, str, Any]] = []
        self._color = "green"
        self._width = 0
        self._focused_idx = 0
        self._active = False

    def set_tabs(self, tabs: list[tuple], color: str, width: int) -> None:
        """Repaint chips from live ``frame_tabs()`` (called by _paint_chip_row)."""
        self._tabs = list(tabs)
        self._color = color or "green"
        self._width = width
        if self._focused_idx >= len(self._tabs):
            self._focused_idx = max(0, len(self._tabs) - 1)
        self._repaint()

    def _repaint(self) -> None:
        if self._width <= 0:
            return
        if not self._tabs:
            self._spans = []
            self.update(Text(""))
            return
        focused = self._focused_idx if (self._active and self._tabs) else None
        text, spans = _chip_row(self._width, self._tabs, self._color, focused=focused)
        self._spans = spans
        self.update(text)

    def on_click(self, event: events.Click) -> None:
        for start, end, kind, payload in self._spans:
            if start <= event.x <= end:
                self._focused_idx = next(
                    (i for i, (s, *_rest) in enumerate(self._spans) if s == start), 0
                )
                if self._on_activate_tab is not None:
                    self._on_activate_tab(kind, payload)
                event.stop()
                return

    def on_focus(self, event: events.Focus) -> None:
        if self._focused_idx >= len(self._tabs):
            self._focused_idx = 0
        self._active = True
        self._repaint()

    def on_blur(self, event: events.Blur) -> None:
        self._active = False
        self._repaint()

    def action_cycle(self, delta: int) -> None:
        if not self._tabs:
            return
        self._focused_idx = (self._focused_idx + delta) % len(self._tabs)
        self._repaint()

    def action_activate(self) -> None:
        if 0 <= self._focused_idx < len(self._tabs):
            tab = self._tabs[self._focused_idx]
            kind = tab[1]
            payload = tab[2] if len(tab) > 2 else None
            if self._on_activate_tab is not None:
                self._on_activate_tab(kind, payload)

    def action_leave(self) -> None:
        try:
            self.app.query_one("#input", _PromptInput).focus()
        except Exception:
            # No input line to hand focus back to (headless, or teardown).
            pass


_InputCap = _ChipRow  # back-compat alias


class _ModePrefix(Static):
    """The `$`/`M` glyph left of the input — a live flipmode BUTTON, not dead
    chrome (flipmode-visible-repl-shell-toggle v1): click flips bare-line
    routing between shell-first and ask-first via ``state.ask_primary``. The
    glyph/color/hint repaint stays in the app's ``_paint_input_prompt``; the
    auto-``[M]``-on-agent-follow-up half of the proposal is still open."""

    def on_click(self, event) -> None:
        event.stop()
        fn = getattr(self.app, "_toggle_ask_primary", None)
        if callable(fn):
            fn()


class _InputActionButton(Button):
    """Compact stop/force affordance beside the prompt (frame-painted by the app)."""

    can_focus = False

    DEFAULT_CSS = """
    _InputActionButton {
        border: round;
        background: $background;
        color: #808080;
        padding: 0;
        min-height: 1;
        min-width: 1;
        content-align: center middle;
    }
    _InputActionButton:hover {
        background: $panel;
    }
    """


class _PromptInput(TextArea):
    """The bottom input, based on TextArea for multi-line support.

    - Enter submits single-line input; Shift+Enter inserts a newline; Ctrl+Enter
      always submits (including multi-line);
    - while the popup is open, Up/Down move the highlight, Tab accepts it, Esc
      dismisses, Enter accepts/submits via the popup rules;
    - while the popup is closed, Up/Down walk command history on single-line
      input (or from the first/last line of multi-line input); Ctrl+Up/Ctrl+Down
      always walk history regardless of cursor position.

    Input completions (the-fold Vector F) are owned HERE, not on the app: a
    ``:shortcode`` symbol popup (:mod:`xlii.tui.shortcodes`, a capped
    ``_ShortcodePopup`` this widget mounts + drives) and fish-style shell ghost
    text (:mod:`xlii.shell_suggest`, a dim inline suffix rendered in ``render_line``).
    THE LAW: no model and no I/O in the input loop — both are bounded in-memory
    scans over static tables. They never mix with the app's ``/`` slash menu: the
    shortcode popup triggers only on ``:`` + chars, ghost text only in bare-shell
    context, and each hides the moment its token stops matching.
    """

    # TextArea ships border: tall $border-blurred + background: $surface. The app
    # paints a uniform round border in frame_mode() color via inline styles.
    DEFAULT_CSS = """
    _PromptInput {
        border: none;
        background: $background;
    }
    """

    BINDINGS = [
        Binding("ctrl+enter", "submit_prompt", "Submit", show=False),
    ]

    def __init__(
        self,
        *args: Any,
        max_visible_lines: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._max_visible_lines = (
            max_visible_lines if max_visible_lines is not None
            else _default_input_max_lines()
        )
        # :shortcode: popup state (the popup widget is mounted in on_mount).
        self._sc_popup: Optional[shortcodes._ShortcodePopup] = None
        self._sc_open = False
        self._sc_start = 0
        self._sc_matches: list[tuple[str, str]] = []
        self._sc_index = 0
        # Shell ghost text: the served table loaded ONCE (stale is fine — zero I/O
        # in the loop after this), plus the live dim suffix render_line paints.
        self._shell_table: list[str] = []
        self._ghost_suffix = ""
        # Large clipboard pastes → one-line placeholders; full text expanded on submit.
        self._paste_store = PasteStore()
        # CLAIM_INPUT fulfilment (V0c): the active ask, the REPL draft it displaced
        # (text + cursor), and the frame title it replaced.
        self._claim: "Optional[InputClaim]" = None
        self._claim_stash: "Optional[tuple[str, tuple[int, int]]]" = None
        self._claim_prev_title: Any = None

    def on_mount(self) -> None:
        self.call_after_refresh(self._sync_height)
        self._setup_completions()

    async def _on_paste(self, event: events.Paste) -> None:
        """Insert paste text, collapsing large dumps to ``(pasted N lines · #id)``.

        Textual's default ``TextArea._on_paste`` inserts the full clipboard into the
        document. A multi-hundred-line traceback fills the input, grows the chrome,
        and scrolls prior assistant answers out of view. Above the collapse
        threshold we stash the body and insert a one-line placeholder; submit
        expands it so the agent still sees the real text.
        """
        if self.read_only:
            return
        text = event.text
        if not text:
            return
        text = normalize_paste(text)
        insert = self._paste_store.maybe_collapse_insert(text)
        if result := self._replace_via_keyboard(insert, *self.selection):
            self.move_cursor(result.end_location)
            self.focus()
        event.stop()
        self.call_after_refresh(self._sync_height)

    def expand_pastes(self, text: str) -> str:
        """Expand paste placeholders for the agent (submit path)."""
        return self._paste_store.expand(text)

    def clear_paste_store(self) -> None:
        self._paste_store.clear()

    # -- CLAIM_INPUT fulfilment (godzilla-mothra V0c) --------------------------
    # The pane's ask arrives as a kernel InputClaim (pure data + callbacks); this
    # is the surface half: stash the REPL draft, seed the initial text, retitle
    # the frame, own Enter/Esc while claimed, restore everything on release.

    @property
    def claim_active(self) -> bool:
        """True while a pane's ask owns the input line."""
        return self._claim is not None

    def claim_input(self, claim: "InputClaim") -> bool:
        """Morph the input line into ``claim``'s one-line ask.

        Returns ``False`` when the line is already claimed (the single-tenant
        rule: rejected, never queued — and no callbacks are invoked here; the
        dispatcher reports the refusal). On grant: the in-progress draft and
        cursor are stashed, ``claim.initial`` seeds the line, ``claim.prompt``
        retitles the input frame, and the input takes focus."""
        if self._claim is not None:
            return False
        self._claim = claim
        self._claim_stash = (self.text, self.cursor_location)
        self._set_claim_text(str(getattr(claim, "initial", "") or ""))
        self._relabel_prompt(str(getattr(claim, "prompt", "") or ""))
        try:
            self.focus()
        except Exception:
            # Focus is best-effort: a claim must never fail because focus did not stick.
            pass
        return True

    def release_claim(self, *, cancelled: bool = True) -> None:
        """End the active claim and hand the line back to the REPL.

        The stashed draft + cursor and the frame's previous title are restored
        (the release contract: Esc ALWAYS lands you back in the normal REPL
        input). A ``cancelled`` release reports ``claim.on_cancel`` (the ask
        ended without an answer); the submit path releases with
        ``cancelled=False`` because it already delivers ``on_submit``."""
        claim = self._claim
        if claim is None:
            return
        self._claim = None
        text, cursor = self._claim_stash or ("", (0, 0))
        self._claim_stash = None
        self._set_claim_text(text, cursor=cursor)
        self._relabel_prompt(None)
        if cancelled:
            cb = getattr(claim, "on_cancel", None)
            if cb is not None:
                try:
                    cb()
                except Exception:
                    # Intentionally ignore callback failures: claim cancellation must not break input restoration.
                    pass

    def _submit_claim(self) -> None:
        """Enter on a claimed line: deliver the paste-expanded, stripped text to
        the claim's completion callback, then release. An EMPTY line is a no-op —
        an ask needs an answer (seed ``initial`` for a default; Esc is the
        explicit no-answer). Release happens FIRST so the callback sees a free
        line (it may immediately claim again — a chained ask — or prefill)."""
        claim = self._claim
        if claim is None:
            return
        value = self.expand_pastes(self.text).strip()
        if not value:
            return
        self.release_claim(cancelled=False)
        try:
            claim.on_submit(value)
        except Exception:
            # Submit callbacks are best-effort: errors must not leave the input surface wedged.
            pass

    def _set_claim_text(self, value: str, *, cursor: "Optional[tuple[int, int]]" = None) -> None:
        """Set the buffer for a claim transition without popup fallout. While a
        claim is active the app's ``on_text_area_changed`` gates every popup
        shut on ``claim_active`` (one boolean can't suppress a chained claim's
        double Changed); arming ``_suppress_popup`` here is the RELEASE-path
        mirror of ``_set_input``, so the restored draft doesn't reopen a popup
        the moment the line is handed back to the REPL."""
        if value != self.text:
            try:
                self.app._suppress_popup = True
            except AttributeError:
                # App may not expose _suppress_popup during transitional UI states.
                pass
        self.text = value
        if cursor is None:
            lines = value.split("\n")
            cursor = (len(lines) - 1, len(lines[-1]))
        try:
            self.cursor_location = cursor
        except Exception:
            # Cursor placement is best-effort while the buffer is in a transitional state.
            pass
        self._sync_height()
        try:
            self.call_after_refresh(self._refresh_completions)
        except Exception:
            # Completion refresh is best-effort during claim transitions.
            pass

    def _relabel_prompt(self, label: "Optional[str]") -> None:
        """Retitle the input frame for a claim (``label``) or restore it (None).

        The claim's prompt rides ``#input-box``'s border title — chrome the
        app's repaint cycle (``_paint_input_frame``) never writes, so a
        mid-claim repaint can't stomp the relabel."""
        try:
            box = self.app.query_one("#input-box")
        except Exception:
            return
        try:
            if label is not None:
                self._claim_prev_title = getattr(box, "border_title", None)
                box.border_title = label
            else:
                box.border_title = self._claim_prev_title
                self._claim_prev_title = None
        except Exception:
            # Relabel failure is non-fatal: the claim line must stay usable without chrome.
            pass

    def on_resize(self, event: events.Resize) -> None:
        self.call_after_refresh(self._sync_height)

    def _sync_height(self) -> None:
        """Grow with wrapped content up to the configured line cap, then scroll."""
        wrapped = max(1, self.wrapped_document.height)
        visible = min(wrapped, self._max_visible_lines)
        scrolling = wrapped > self._max_visible_lines
        # Content lines only — the round border lives on #input-box now.
        self.styles.height = visible
        self.set_class(scrolling, "-scroll-mode")
        framed = visible + 2  # round border rows on #input-box / #input-action
        try:
            box = self.app.query_one("#input-box")
            box.set_class(scrolling, "-scroll-mode")
            btn = self.app.query_one("#input-action")
            btn.styles.height = framed
            btn.styles.width = _INPUT_ACTION_WIDTH
            btn.set_class(scrolling, "-scroll-mode")
        except Exception:
            # The widget may not be attached to an XliiApp yet (mount timing /
            # tests) — the frame re-syncs on the next resize.
            pass
        # The scroll class just changed who owns the border (scroll color vs the
        # mode color) — repaint the frame so the right one wins. Guarded: the
        # widget may not be attached to an XliiApp yet (mount timing / tests).
        try:
            self.app._paint_input_frame()
        except Exception:
            # Not attached to an XliiApp yet, per the note above; the next resize repaints the frame.
            pass

    def action_submit_prompt(self) -> None:
        if self._claim is not None:
            # Ctrl+Enter on a claimed line answers the ask, never the REPL.
            self._submit_claim()
            return
        app: "XliiApp" = self.app  # type: ignore[assignment]
        app._submit_prompt(self.text)

    def _history_up(self, app: "XliiApp") -> bool:
        """Walk to an older command when Up should mean history, not cursor move."""
        if "\n" not in self.text:
            app._history_prev()
            return True
        if self.cursor_at_first_line and self.cursor_at_start_of_line:
            app._history_prev()
            return True
        return False

    def _history_down(self, app: "XliiApp") -> bool:
        """Walk toward a newer command when Down should mean history."""
        if "\n" not in self.text:
            app._history_next()
            return True
        if self.cursor_at_last_line and self.cursor_at_end_of_line:
            app._history_next()
            return True
        return False

    def on_key(self, event: events.Key) -> None:
        app: "XliiApp" = self.app  # type: ignore[assignment]

        # --- a claimed line owns Enter/Esc (CLAIM_INPUT fulfilment) — FIRST, so
        #     the release contract holds unconditionally: Esc ALWAYS hands the
        #     line back to the REPL, and Enter answers the ask, never a popup.
        #     Any other key falls through to normal editing (shortcode popup and
        #     ghost text keep working as typing aids inside the claimed line). ---
        if self._claim is not None:
            if event.key == "escape":
                self.release_claim(cancelled=True)
                event.stop()
                event.prevent_default()
                return
            if event.key == "enter" and "\n" not in self.text:
                self._submit_claim()
                event.stop()
                event.prevent_default()
                return
            if (event.key in ("up", "down", "ctrl+up", "ctrl+down")
                    and not self._sc_open):
                # REPL command history never walks into a claimed line — the
                # ask's answer is not a command, and the half-typed answer must
                # not leak into the app's _hist_draft. Plain up/down still move
                # the cursor within a multi-line answer (fall through unstopped);
                # with the :shortcode popup open they keep driving its highlight.
                if event.key.startswith("ctrl+") or "\n" not in self.text:
                    event.stop()
                    event.prevent_default()
                return
            if event.key in ("ctrl+k", "ctrl+t"):
                # The palette / tool drawer rewrite the input line — while an
                # ask owns it they stay shut (single-tenant; both are back the
                # moment the claim releases).
                event.stop()
                event.prevent_default()
                return
            if (event.key == "ctrl+b"
                    or event.key in _FKEY_KEYS
                    or event.key in getattr(app, "_doorway_keys", ())
                    or event.key in getattr(app, "_menu_accel_keys", ())):
                # Same single-tenant rule for the rest of the commander surface:
                # the F-row (F7 even pushes a menu), tab-focus, the doorway
                # hotkeys, and the menu-bar accelerators steal focus from /
                # rewrite the claimed line, stranding the ask so Esc can't reach
                # it. Hold them shut while a claim is active — Esc (handled
                # first) is the one way out; they're all back the moment it releases.
                event.stop()
                event.prevent_default()
                return

        # --- :shortcode: popup (owned here; takes priority over the app's slash
        #     menu, which is hidden whenever a :shortcode token is active) ---
        if self._sc_open:
            if event.key == "down":
                self._sc_move(1)
            elif event.key == "up":
                self._sc_move(-1)
            elif event.key in ("tab", "enter"):
                self._sc_accept()
            elif event.key == "escape":
                self._sc_hide()
            else:
                # Any other key edits the token — let it through, then recompute.
                self.call_after_refresh(self._refresh_completions)
                return
            event.stop()
            event.prevent_default()
            return

        # --- shell ghost text: right-arrow at end-of-line accepts the dim suffix
        #     (INSERTS only — Enter is still required to run it) ---
        if event.key == "right" and self._ghost_suffix and self._accept_ghost():
            event.stop()
            event.prevent_default()
            return

        if getattr(app, "_popup_open", False):
            if event.key == "down":
                app._popup_move(1)
            elif event.key == "up":
                app._popup_move(-1)
            elif event.key == "tab":
                app._popup_accept()
            elif event.key == "escape":
                if getattr(app, "_palette_active", False):
                    app._close_palette()
                else:
                    app._popup_hide()
            elif event.key == "enter":
                app._submit_prompt(self.text)
            else:
                return
            event.stop()
            event.prevent_default()
            return

        if event.key == "shift+enter":
            self.insert("\n")
            event.stop()
            event.prevent_default()
            return

        if event.key == "ctrl+k":
            app.action_open_palette()
            event.stop()
            event.prevent_default()
            return

        if event.key == "ctrl+t":
            app.action_open_tool_drawer()
            event.stop()
            event.prevent_default()
            return

        if event.key == "ctrl+b":
            app.action_focus_tabs()
            event.stop()
            event.prevent_default()
            return

        if event.key in _FKEY_KEYS:
            # F-keys never reach the app BINDINGS while the input holds focus, so delegate them
            # here (the commander F-key bar). Wired keys act; the rest are no-ops for now.
            app._handle_fkey(event.key)
            event.stop()
            event.prevent_default()
            return

        if getattr(app, "_commander_hotkey", None) and app._commander_hotkey(event.key):
            # The commander <modifier>+<letter> hotkeys — the underlined menu-bar mnemonics
            # (Alt+P → Project…) AND the content doorways (Alt+F files, Alt+G git…) — are swallowed
            # by the focused input like the F-keys, so delegate them here: one opens a dropdown, the
            # other toggles a content type in Pane 2, straight from the command line.
            event.stop()
            event.prevent_default()
            return

        if event.key == "enter" and "\n" not in self.text:
            app._submit_prompt(self.text)
            event.stop()
            event.prevent_default()
            return

        if event.key == "up":
            if self._history_up(app):
                event.stop()
                event.prevent_default()
        elif event.key == "down":
            if self._history_down(app):
                event.stop()
                event.prevent_default()
        elif event.key == "ctrl+up":
            app._history_prev()
            event.stop()
            event.prevent_default()
        elif event.key == "ctrl+down":
            app._history_next()
            event.stop()
            event.prevent_default()

        # Ordinary editing (and cursor/history moves) fell through — recompute both
        # completers AFTER the edit lands. Pure in-memory scans; never blocks input.
        self.call_after_refresh(self._refresh_completions)

    # -- input completions (the-fold Vector F) ---------------------------------

    def _setup_completions(self) -> None:
        """Wire the :shortcode: popup (composed above #input-row) and load shell table."""
        self._shell_table = shell_suggest.load_table()
        try:
            self._sc_popup = self.app.query_one(
                "#shortcode-popup", shortcodes._ShortcodePopup
            )
            self._sc_popup.display = False
        except Exception:
            # No popup widget → :shortcode: completion simply doesn't appear; the
            # pure query/apply logic and ghost text are unaffected.
            self._sc_popup = None

    def _refresh_completions(self) -> None:
        """Recompute both completers from the live text/cursor. THE LAW: pure +
        in-memory — a subsequence scan (shortcodes) and a prefix scan (ghost),
        no model, no I/O."""
        self._refresh_shortcode()
        self._refresh_ghost()

    def _refresh_shortcode(self) -> None:
        if self._sc_popup is None:
            return
        row, col = self.cursor_location
        line = self.document.get_line(row)
        q = shortcodes.shortcode_query(line, col)
        matches = shortcodes.shortcode_matches(q[1]) if q is not None else []
        if q is None or not matches:
            if self._sc_open:
                self._sc_hide()
            return
        self._sc_start = q[0]
        self._sc_matches = matches
        self._sc_index = 0
        self._sc_popup.set_matches(matches)
        self._sc_popup.display = True
        self._sc_open = True
        self._align_sc_popup()

    def _align_sc_popup(self) -> None:
        """Seat the :shortcode: popup on the input frame's top edge.

        It floats on the overlay layer docked to the screen bottom (reveal must
        not reflow the transcript); the bottom margin lifts it above the frame —
        same geometry contract as the app's #completions popup."""
        if self._sc_popup is None:
            return
        try:
            box = self.app.query_one("#input-box")
            app_h = int(self.app.size.height)
            box_w = int(box.region.width)
            bottom = max(0, app_h - int(box.region.y))
            if 0 < bottom < app_h and box_w > 0:
                # Docked widgets span the full edge for auto width — pin width
                # and place with the left margin (same fix as #completions).
                self._sc_popup.styles.width = box_w
                self._sc_popup.styles.margin = (0, 0, bottom, max(0, int(box.region.x)))
        except Exception:
            # Pre-layout / unmounted: the CSS fallback margin stands.
            pass

    def _refresh_ghost(self) -> None:
        suffix = ""
        if not self._sc_open and self._shell_table:
            text = self.text
            if (
                text
                and "\n" not in text
                and not text.startswith("/")
                and self.cursor_location == self.document.end
                and self._app_is_shell()
            ):
                suffix = shell_suggest.ghost_suggestion(text, self._shell_table) or ""
        if suffix != self._ghost_suffix:
            self._ghost_suffix = suffix
            self.refresh()

    def _app_is_shell(self) -> bool:
        """Ghost text only in bare-shell context — the app's router already knows
        shell vs ``/`` (mirrors the inline REPL's shell-primary toggle)."""
        fn = getattr(self.app, "_bare_is_shell", None)
        if not callable(fn):
            return False
        try:
            return bool(fn())
        except Exception:
            # A malformed/absent state must not crash the render path — no ghost.
            return False

    def _sc_hide(self) -> None:
        self._sc_open = False
        self._sc_matches = []
        self._sc_index = 0
        if self._sc_popup is not None:
            self._sc_popup.display = False

    def _sc_move(self, delta: int) -> None:
        if not self._sc_matches:
            return
        self._sc_index = (self._sc_index + delta) % len(self._sc_matches)
        if self._sc_popup is not None:
            self._sc_popup.highlighted = self._sc_index

    def _sc_accept(self) -> None:
        """Insert the highlighted glyph in place of the ``:partial`` token. Insert
        only — never submits."""
        if not self._sc_matches:
            self._sc_hide()
            return
        _code, glyph = self._sc_matches[self._sc_index]
        row, col = self.cursor_location
        lines = self.text.split("\n")
        lines[row] = shortcodes.apply_shortcode(lines[row], self._sc_start, col, glyph)
        new_col = self._sc_start + len(glyph)
        self._sc_hide()
        self.text = "\n".join(lines)
        self.cursor_location = (row, new_col)
        self._sync_height()

    def _accept_ghost(self) -> bool:
        """Accept the dim shell suffix (right-arrow at end-of-line). INSERTS the
        remembered text — accepting never runs it; Enter is still required."""
        suffix = self._ghost_suffix
        if not suffix or "\n" in self.text or self.cursor_location != self.document.end:
            return False
        self._ghost_suffix = ""
        self.insert(suffix)
        return True

    def render_line(self, y: int) -> Strip:
        """Paint the normal line, then append the dim shell ghost suffix on the
        cursor's line when one is active. Self-correcting: the ghost is only drawn
        while the cursor sits at the end of a single-line input, so a stale suffix
        never lingers."""
        strip = super().render_line(y)
        ghost = self._ghost_suffix
        if not ghost:
            return strip
        try:
            doc = self.document
            if "\n" in self.text or self.cursor_location != doc.end:
                return strip
            row, _col = self.cursor_location
            if row != y + self.scroll_offset.y:
                return strip
            text_end = self.gutter_width + cell_len(doc.get_line(row))
        except Exception:
            # Never let a ghost-geometry hiccup crash the render — drop the ghost.
            return strip
        head = strip.crop(0, text_end)
        ghost_strip = Strip([Segment(ghost, Style(dim=True))])
        return Strip.join([head, ghost_strip]).adjust_cell_length(strip.cell_length, Style())
