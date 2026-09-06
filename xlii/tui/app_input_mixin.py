"""Input frame paint, command palette, history, completion popups.

Mixin extracted from :mod:`xlii.tui.app` (grades plan Phase 5).
"""
from __future__ import annotations

import time
from typing import Any, Optional

from rich.text import Text
from textual.containers import Horizontal
from textual.widgets import OptionList, Static, TextArea
from textual.widgets.option_list import Option

from xlii.tui import blocks
from xlii.tui.events import MetaMessage, UserTurn
from xlii.tui.input_surface import (
    _ChipRow,
    _INPUT_ACTION_WIDTH,
    _InputActionButton,
    _PromptInput,
)
from xlii.tui.theme import THEME
from xlii.hints import resolve_hint as _resolve_hint


import sys as _sys
_app = _sys.modules["xlii.tui.app"]
_RECALL_ARG = _app._RECALL_ARG
_SLASH_TOKEN = _app._SLASH_TOKEN
_AT_FRAG = _app._AT_FRAG
_TUI_SLASH = _app._TUI_SLASH
_TUI_SLASH_CMDS = _app._TUI_SLASH_CMDS
_recall_suggestions = _app._recall_suggestions

class AppInputMixin:
    """Input frame paint, command palette, history, completion popups."""

    def _align_completions_popup(self) -> None:
        """Line #completions up with #input-box — edges AND vertical seat.

        The popup lives on the overlay layer docked to the screen bottom (so
        revealing it never reflows the transcript); the bottom margin lifts it
        to sit exactly on the input frame's top edge, and left/right margins
        share edges with the typed frame — prefix and stop button stay outside
        both. CSS default is ``margin: 0 1 6 1`` (6 ≈ the bottom chrome) until
        real regions land here.
        """
        try:
            ol = self.query_one("#completions", OptionList)
            box = self.query_one("#input-box", Horizontal)
        except Exception:
            return
        try:
            app_w = int(self.size.width)
            app_h = int(self.size.height)
            box_x = int(box.region.x)
            box_w = int(box.region.width)
            # Rows below the input frame's top edge — where the popup's bottom
            # margin must reach so it floats directly above the frame.
            bottom = max(0, app_h - int(box.region.y))
            if app_w <= 0 or box_w <= 0 or bottom >= app_h:
                ol.styles.margin = (0, 1, 6, 1)
                return
            # Docked widgets ignore the right margin for auto width (they span
            # the full edge) — pin the width explicitly and place with the left
            # margin so the popup's frame shares both edges with #input-box.
            ol.styles.width = box_w
            ol.styles.margin = (0, 0, bottom, max(0, box_x))
        except Exception:
            try:
                ol.styles.margin = (0, 1, 6, 1)
            except Exception:
                # Best-effort UI fallback: ignore if widget/style is unavailable
                # during transient layout/unmount states.
                pass

    def _reveal_completions(self) -> None:
        """Show #completions and align its frame to #input-box after layout."""
        ol = self.query_one("#completions", OptionList)
        ol.display = True
        self._popup_open = True
        self._align_completions_popup()
        # Region is reliable only after the next layout pass.
        self.call_after_refresh(self._align_completions_popup)

    def _paint_input_frame(self) -> None:
        """Frame the input with a uniform mode-colored round border; chip row below."""
        try:
            box = self.query_one("#input-box", Horizontal)
            inp = self.query_one("#input", _PromptInput)
            action = self.query_one("#input-action", _InputActionButton)
        except Exception:
            return  # not mounted yet
        try:
            from xlii.tui.status import frame_mode, frame_tabs
            _label, color = frame_mode(self._state)
            tabs = frame_tabs(self._state)
        except Exception:
            return
        if inp.has_class("-scroll-mode"):
            frame_color = THEME.tui_input_scroll_border
        else:
            frame_color = color or "green"
        border = ("round", frame_color)
        box.styles.border_left = border
        box.styles.border_right = border
        box.styles.border_top = border
        box.styles.border_bottom = border
        action.styles.border_left = border
        action.styles.border_right = border
        action.styles.border_top = border
        action.styles.border_bottom = border
        try:
            box_h = max(1, box.region.height)
            action.styles.height = box_h
            action.styles.width = _INPUT_ACTION_WIDTH
        except Exception:
            # Best-effort sizing: region/style values can be temporarily unavailable
            # during mount/layout; continue so prompt/chip row still repaint.
            _ = None
        self._paint_input_prompt(inp, frame_color)
        self._paint_chip_row(tabs, frame_color)

    def _toggle_ask_primary(self) -> None:
        """The [$]/[M] flipmode button (click target: #input-prefix): flip
        bare-line routing between shell-first and ask-first. Session-scoped,
        never persisted — a fresh session always starts shell-first. Inside a
        talk-primary mode (persona/plan/howto/…) the surface stays ask-first
        regardless; `!` remains the force-shell escape."""
        st = self._state
        st.ask_primary = not bool(getattr(st, "ask_primary", False))
        self._paint_input_frame()

    def _paint_input_prompt(self, inp: _PromptInput, frame_color: str) -> None:
        """Shell vs mojo prefix ($ / M) and the mode-aware placeholder hint."""
        try:
            shell = self._bare_is_shell()
            prefix = self.query_one("#input-prefix", Static)
            prefix.update("$ " if shell else "M ")
            prefix.styles.color = frame_color
            inp.placeholder = _resolve_hint(self._state, shell_primary=shell)
        except Exception:
            # Best-effort repaint: ignore transient widget/state errors during
            # mount/unmount or layout updates so input handling stays responsive.
            pass

    def _paint_chip_row(self, tabs: list[tuple], color: str) -> None:
        """Repaint the attachment chip row below the input (right-aligned symbols)."""
        try:
            row = self.query_one("#input-chips", _ChipRow)
        except Exception:
            return
        try:
            row.set_tabs(tabs, color, max(1, self.size.width - 2))
        except Exception as exc:
            # Keep repaint failures non-fatal, but don't silently swallow errors.
            print(f"_paint_chip_row: failed to set tabs: {exc}", file=_sys.stderr)

    def action_quit(self) -> None:
        """ctrl+d → full quit (A2). Set the disposition before tearing the app
        down so the inline loop ends the process rather than dropping back to the
        inline prompt — ctrl+d is a quit verb, not a "go back a layer"."""
        if self._state is not None:
            self._state.quit_requested = True
        self.exit()

    def action_open_palette(self) -> None:
        if self._palette_active:
            self._close_palette()
        else:
            self._open_palette()

    def action_open_tool_drawer(self) -> None:
        """Ctrl-T — the tools select. The old modal drawer is gone (V2c audit): the
        command palette already lists the same discoverable tools inline (same
        ``list_discoverable_tools`` backend), so one inline select surface owns the
        verb — no input-trapping popup."""
        self.action_open_palette()

    def _open_palette(self) -> None:
        from xlii.tui.discover import collect_palette_items

        self._palette_active = True
        self._palette_all = collect_palette_items(self._state)
        self._popup_kind = "palette"
        inp = self.query_one("#input", _PromptInput)
        self._suppress_popup = True
        inp.text = ""
        self._suppress_popup = False
        self._update_palette_popup("")
        inp.focus()

    def _close_palette(self) -> None:
        self._palette_active = False
        self._palette_all = []
        self._popup_hide()

    def _update_palette_popup(self, needle: str) -> None:
        from xlii.tui.discover import filter_palette

        matches = filter_palette(self._palette_all, needle.strip())
        if not matches:
            self._popup_hide()
            self._palette_active = bool(self._palette_all)
            return
        self._popup_kind = "palette"
        self._popup_matches = matches
        self._popup_index = 0
        ol = self.query_one("#completions", OptionList)
        ol.clear_options()
        options = []
        for item in matches[:30]:
            lead = f"/{item.name}" if item.kind == "command" else item.name
            label = Text.assemble(
                (lead, "cyan"),
                (f"  [{item.kind}]", "yellow"),
                ("  " + (item.description or ""), "dim"),
            )
            options.append(Option(label, id=item.name))
        ol.add_options(options)
        ol.highlighted = 0
        self._reveal_completions()

    def _update_at_popup(self, partial: str) -> None:
        from xlii.tui.discover import at_suggestions

        sugg = at_suggestions(self._state, partial)
        if not sugg:
            self._popup_hide()
            return
        self._popup_kind = "at"
        self._popup_matches = sugg
        self._popup_index = 0
        ol = self.query_one("#completions", OptionList)
        ol.clear_options()
        ol.add_options([
            Option(
                Text.assemble(
                    (s.label, "cyan"),
                    ("  [file]" if s.kind == "file" else "  [url]", "dim"),
                ),
                id=s.value,
            )
            for s in sugg
        ])
        ol.highlighted = 0
        self._reveal_completions()

    def _palette_run_selected(self) -> None:
        if not self._popup_matches:
            self._close_palette()
            return
        item = self._popup_matches[self._popup_index]
        inp = self.query_one("#input", _PromptInput)
        self._close_palette()
        if item.kind == "tool":
            inp.text = item.action
            lines = inp.text.split("\n")
            inp.cursor_location = (len(lines) - 1, len(lines[-1]))
            inp._sync_height()
            inp.focus()
            return
        if item.action == "__session_resume__":
            self._open_episode_resume_picker()
            return
        text = item.action
        inp.text = ""
        inp._sync_height()
        if text in _TUI_SLASH:
            self._submit_prompt(text)
            return
        self._history_add(text)
        self._transcript.write(blocks.user_block(UserTurn(text)))
        if self._busy:
            self._transcript.write(blocks.meta_block(
                MetaMessage("busy — wait for the current turn to finish", "warn")))
            return
        self._turn_started = time.monotonic()
        self._busy = True
        self._process(text)

    def _open_episode_resume_picker(self) -> None:
        """Palette → resume: single episode runs immediately; several open a picker."""
        from xlii import episode as ep

        xli = getattr(getattr(self._state, "project", None), "xli_dir", None)
        records = ep.list_episodes(xli) if xli is not None else []
        if not records:
            self.write_block(blocks.meta_block(MetaMessage(
                "no stored episodes — /session on to start one", "info")))
            return
        if len(records) == 1:
            self._submit_prompt(f"/session resume {records[0]['id']}")
            return
        try:
            from xlii.tui.panels import ModelPickerModal
        except Exception:
            # No textual modal — fall through to numbered list via the command.
            self._submit_prompt("/session resume")
            return
        options = [
            (
                str(r.get("id")),
                f"  {r.get('turns', 0)} turn(s) · {r.get('updated_at', '')}",
            )
            for r in records
            if r.get("id")
        ]
        current = str(getattr(self._state, "episode_id", "") or "")

        def _picked(eid: Optional[str]) -> None:
            if eid:
                self._submit_prompt(f"/session resume {eid}")

        self.push_screen(
            ModelPickerModal("episode", options, current=current, title="resume episode"),
            _picked,
        )

    def _at_accept(self) -> None:
        if not self._popup_matches:
            return
        from xlii.tui.discover import resolve_at_selection

        sug = self._popup_matches[self._popup_index]
        inp = self.query_one("#input", _PromptInput)
        text = inp.text
        m = _AT_FRAG.search(text)
        if m:
            at_pos = text.rfind("@", 0, m.end())
            inp.text = text[:at_pos] if at_pos >= 0 else text[: m.start()]
            inp._sync_height()
        try:
            msg = resolve_at_selection(self._state, sug)
            self.write_block(blocks.meta_block(MetaMessage(msg, "info")))
        except Exception as e:
            self.write_block(blocks.meta_block(MetaMessage(f"pin failed: {e}", "error")))
        self._popup_hide()
        self._on_main(self._refresh_status)
        inp.focus()

    # -- command history (up/down) ---------------------------------------

    def _load_history(self) -> None:
        """Load the shared .xlii/repl_history (prompt_toolkit FileHistory format,
        same file the inline REPL writes) into _hist, oldest→newest. No-ops with
        an empty in-memory history when there's no project dir (e.g. test fakes)."""
        try:
            from pathlib import Path

            from prompt_toolkit.history import FileHistory

            xli_dir = getattr(getattr(self._state, "project", None), "xli_dir", None)
            if xli_dir is None:
                return
            self._history = FileHistory(str(Path(xli_dir) / "repl_history"))
            # load_history_strings() yields most-recent-first → reverse for nav.
            self._hist = list(self._history.load_history_strings())[::-1]
        except Exception:
            self._history = None
        self._hist_pos = len(self._hist)

    def _history_add(self, text: str) -> None:
        """Record a submitted line (in-memory + the shared file), de-duping a
        consecutive repeat, and reset the navigation cursor to the newest end."""
        if not self._hist or self._hist[-1] != text:
            self._hist.append(text)
            if self._history is not None:
                try:
                    self._history.append_string(text)
                except Exception as e:
                    self.write_block(
                        blocks.meta_block(
                            MetaMessage(f"history save failed: {e}", "error")
                        )
                    )
        self._hist_pos = len(self._hist)
        self._hist_draft = ""

    def _history_prev(self) -> None:
        """Up — step to an older command (readline semantics)."""
        if not self._hist:
            return
        inp = self.query_one("#input", _PromptInput)
        if self._hist_pos == len(self._hist):
            self._hist_draft = inp.text  # stash the in-progress line
        self._hist_pos = max(0, self._hist_pos - 1)
        self._set_input(inp, self._hist[self._hist_pos])

    def _history_next(self) -> None:
        """Down — step toward newer; past the newest restores the draft."""
        if self._hist_pos >= len(self._hist):
            return
        inp = self.query_one("#input", _PromptInput)
        self._hist_pos += 1
        value = (self._hist_draft if self._hist_pos == len(self._hist)
                 else self._hist[self._hist_pos])
        self._set_input(inp, value)

    def _set_input(self, inp: _PromptInput, value: str) -> None:
        # Setting .text fires TextArea.Changed; suppress the popup for that one
        # update so recalling a `/command` from history doesn't pop the menu and
        # hijack the next up/down (which would then drive the popup, not history).
        # Only arm the flag when the value actually changes — an unchanged set
        # fires no Changed, so the flag would otherwise stay armed and eat the
        # popup on the next real keystroke.
        if value != inp.text:
            self._suppress_popup = True
        inp.text = value
        if value:
            lines = value.split("\n")
            inp.cursor_location = (len(lines) - 1, len(lines[-1]))
        else:
            inp.cursor_location = (0, 0)
        inp._sync_height()

    # -- quick-reference popup -------------------------------------------

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        event.text_area._sync_height()
        if getattr(event.text_area, "claim_active", False):
            # A claimed line is a panel's input, not the REPL (the minibuffer
            # rule): no slash/palette/@ popup may open over an ask. One boolean
            # can't suppress a chained claim's double Changed, so gate on the
            # claim itself; the armed flag stays for the release repaint.
            self._popup_hide()
            return
        if self._suppress_popup:
            self._suppress_popup = False
            self._popup_hide()
            return
        self._update_popup(event.text_area.text)

    def _update_popup(self, value: str) -> None:
        """Show/refresh the popup while a bare `/command` token — or a `/recall`
        argument — is being typed."""
        if self._palette_active:
            self._update_palette_popup(value)
            return
        ra = _RECALL_ARG.match(value)
        if ra is not None:
            self._update_recall_popup(ra.group(1))
            return
        m = _SLASH_TOKEN.match(value)
        if m is not None:
            prefix = m.group(1)
            from xlii.commands import _REPL_COMMANDS

            seen: dict[str, Any] = {}
            for fe in _TUI_SLASH_CMDS:
                if fe.name.startswith(prefix):
                    seen[fe.name] = fe
            scope = getattr(self._state, "command_scope", None) or "code"
            for cmd in _REPL_COMMANDS:
                if scope not in cmd.repls:
                    continue
                if any(n.startswith(prefix) for n in [cmd.name, *cmd.aliases]):
                    seen.setdefault(cmd.name, cmd)
            matches = sorted(seen.values(), key=lambda c: c.name)
            if not matches:
                self._popup_hide()
                return

            self._popup_kind = "command"
            self._popup_matches = matches
            self._popup_index = 0
            ol = self.query_one("#completions", OptionList)
            ol.clear_options()
            # One batched add — N single add_option calls each invalidate
            # content tracking; bare "/" rebuilds ~90 rows per keystroke.
            ol.add_options([
                Option(
                    Text.assemble(
                        (f"/{cmd.name}", "cyan"),
                        ("  " + (cmd.description or ""), "dim"),
                    ),
                    id=cmd.name,
                )
                for cmd in matches
            ])
            ol.highlighted = 0
            self._reveal_completions()
            return
        at_m = _AT_FRAG.search(value)
        if at_m is not None and "@" in value:
            self._update_at_popup(at_m.group(1))
            return
        self._popup_hide()

    def _update_recall_popup(self, partial: str) -> None:
        """Populate the popup with `<persona>:<mark>` candidates for /recall."""
        sugg = _recall_suggestions(self._state, partial)
        if not sugg:
            self._popup_hide()
            return
        self._popup_kind = "recall"
        self._popup_matches = sugg          # list of (candidate, timestamp)
        self._popup_index = 0
        ol = self.query_one("#completions", OptionList)
        ol.clear_options()
        ol.add_options([
            Option(Text.assemble((cand, "cyan"), ("  " + ts, "dim")), id=cand)
            for cand, ts in sugg
        ])
        ol.highlighted = 0
        self._reveal_completions()

    def _popup_move(self, delta: int) -> None:
        if not self._popup_matches:
            return
        n = len(self._popup_matches)
        self._popup_index = (self._popup_index + delta) % n
        self.query_one("#completions", OptionList).highlighted = self._popup_index

    def _popup_accept(self) -> None:
        """Fill the input with the highlighted suggestion (ready for args/Enter).
        For a command that's the bare `/name `; for the recall picker it's the
        full `/recall <persona>:<mark>` (ready to submit)."""
        if not self._popup_matches:
            return
        item = self._popup_matches[self._popup_index]
        inp = self.query_one("#input", _PromptInput)
        if self._popup_kind == "recall":
            inp.text = f"/recall {item[0]}"
        elif self._popup_kind == "at":
            self._at_accept()
            return
        elif self._popup_kind == "palette":
            if item.kind == "tool":
                inp.text = item.action
            else:
                inp.text = f"/{item.name} "
        else:
            inp.text = f"/{item.name} "
        lines = inp.text.split("\n")
        inp.cursor_location = (len(lines) - 1, len(lines[-1]))
        inp._sync_height()
        self._popup_hide()
        inp.focus()

    def _popup_hide(self) -> None:
        was_palette = self._popup_kind == "palette" or self._palette_active
        self._popup_open = False
        self._popup_matches = []
        self._popup_index = 0
        self._popup_kind = "command"
        if was_palette:
            self._palette_active = False
            self._palette_all = []
        try:
            self.query_one("#completions", OptionList).display = False
        except LookupError:
            # Completions list may be unavailable during mount/unmount transitions.
            return
        except Exception:
            self.log.exception("Unexpected error while hiding completions popup")
            return

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # Mouse click on a suggestion — accept it and return focus to the input.
        if event.option_index is not None:
            self._popup_index = event.option_index
        self._popup_accept()

    # -- input ------------------------------------------------------------

