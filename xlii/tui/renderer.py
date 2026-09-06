"""The one print path. Callers build a typed event and call renderer.emit();
the renderer picks the block, applies the Theme, and writes to the shared
Console. New slash command? `renderer.meta(...)`. New tool? Add a formatter in
blocks.py. No more scattered console.print.

Plain mode (xlii ask / daemon) degrades every block to flat, unstyled text so
the captured output stays clean. The stdout/stderr split that `ask` relies on
is a wiring concern for later phases; the kernel here just guarantees a faithful
plain branch.

Imports sibling blocks + kernel leaves (xlii.turn_events, xlii.theme) — never
xlii.tui's package __init__ or xlii.ui.
"""

from __future__ import annotations

import os
from typing import Optional

from rich.console import Console

from xlii.theme import THEME, Theme
from xlii.tui import blocks
from xlii.turn_events import (
    AssistantAnswer,
    MetaMessage,
    ShellRan,
    ToolFinished,
    ToolStarted,
    UserTurn,
)

# Sentinel so callers can pass max_body_lines=None to mean "never truncate"
# (faithful, used by tests) distinct from "use the default policy".
_UNSET = object()


def _default_max_body_lines(compact: bool = False) -> Optional[int]:
    """Display line budget for captured output. ~40 keeps a long pytest/diff dump
    from flooding the block; the full text always still reaches the model, so
    this is purely visual. The TUI transcript (``compact``) shows a much tighter
    ~8-line preview — a git-show / cat dump shouldn't bury the turn. Override
    either with XLII_SHELL_MAXLINES (0 or less = never truncate)."""
    default = 8 if compact else 40
    raw = os.environ.get("XLII_SHELL_MAXLINES", "").strip()
    if not raw:
        return default
    try:
        n = int(raw)
    except ValueError:
        return default
    return None if n <= 0 else n


class Renderer:
    """Routes typed events to blocks. One instance is the module singleton
    (built in xlii.tui.__init__ over the shared Console), but tests construct
    their own with a StringIO-backed Console."""

    def __init__(
        self,
        console: Console,
        *,
        plain: bool = False,
        theme: Theme = THEME,
        max_body_lines=_UNSET,
        compact: bool = False,
    ):
        self.console = console
        self.plain = plain
        self.theme = theme
        # Default: the env-configurable line budget (tighter when `compact`, the
        # TUI transcript). Pass an explicit None to disable truncation entirely
        # (faithful), or an int to override.
        self.max_body_lines = (
            _default_max_body_lines(compact) if max_body_lines is _UNSET else max_body_lines
        )

    # -- dispatch ---------------------------------------------------------

    def emit(self, event) -> None:
        if isinstance(event, ShellRan):
            self._shell(event)
        elif isinstance(event, ToolFinished):
            self._tool(event)
        elif isinstance(event, MetaMessage):
            self._meta(event)
        elif isinstance(event, UserTurn):
            self._user(event)
        elif isinstance(event, AssistantAnswer):
            self._answer(event)
        elif isinstance(event, ToolStarted):
            self._tool_started(event)
        else:  # pragma: no cover - guards against a forgotten event type
            raise TypeError(f"Renderer cannot emit {type(event).__name__}")

    # -- convenience constructors ----------------------------------------

    def meta(self, text: str, level: str = "info") -> None:
        self.emit(MetaMessage(text, level))  # type: ignore[arg-type]

    def error(self, text: str) -> None:
        if self.plain:
            self.console.print(text, markup=False, highlight=False)
        else:
            self.console.print(blocks.error_box(text, theme=self.theme))

    # -- per-event handlers ----------------------------------------------

    def _shell(self, ev: ShellRan) -> None:
        if self.plain:
            self.console.print(f"$ {ev.command}", markup=False, highlight=False)
            body = ev.stdout or ""
            if ev.stderr:
                body = f"{body}\n{ev.stderr}" if body else ev.stderr
            if body:
                self.console.print(body, markup=False, highlight=False)
            if ev.returncode != 0:
                self.console.print(f"exit {ev.returncode}", markup=False, highlight=False)
            return
        self.console.print(
            blocks.shell_block(ev, theme=self.theme, max_lines=self.max_body_lines)
        )

    def _tool(self, ev: ToolFinished) -> None:
        if self.plain:
            status = "error" if ev.is_error else "ok"
            head = f"{ev.name} [{status}]"
            if ev.args_preview:
                head += f" {ev.args_preview}"
            self.console.print(head, markup=False, highlight=False)
            if ev.content.strip():
                self.console.print(ev.content, markup=False, highlight=False)
            return
        self.console.print(
            blocks.tool_block(ev, theme=self.theme, max_lines=self.max_body_lines)
        )

    def _tool_started(self, ev: ToolStarted) -> None:
        if self.plain:
            return  # the finished event carries everything plain mode needs
        line = f"  [dim]{self.theme.announce}[/dim] [{self.theme.shell}]{ev.name}[/{self.theme.shell}]"
        if ev.args_preview:
            line += f" {ev.args_preview}"
        self.console.print(line)

    def _meta(self, ev: MetaMessage) -> None:
        if self.plain:
            self.console.print(ev.text, markup=False, highlight=False)
            return
        self.console.print(blocks.meta_block(ev, theme=self.theme))

    def _user(self, ev: UserTurn) -> None:
        if self.plain:
            return  # the user already saw what they typed
        self.console.print(blocks.user_block(ev, theme=self.theme))

    def _answer(self, ev: AssistantAnswer) -> None:
        if ev.streamed:
            return  # body was already streamed live; don't double-print
        if self.plain:
            self.console.print(ev.markdown, markup=False, highlight=False)
            return
        self.console.print(blocks.answer_block(ev, theme=self.theme))
