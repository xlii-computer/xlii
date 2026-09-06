"""The transcript-side ``rich.Console`` adapter for the Textual TUI.

Turn-time code (the agent's tool blocks, slash-command output, end-of-turn sync)
prints through a ``rich.Console`` handle. Under ``--tui`` that handle is this shim:
it quacks like a Console but routes every renderable to the app's transcript
(``app.write_block``) instead of stdout. ``supports_live``/``is_terminal`` are
False so the agent skips its terminal-only Live preview and ``status()`` is a
no-op — a spinner can't drive a Textual widget.

Extracted from the ``tui_textual`` monolith (the-fold Vector D). Pure over rich +
the duck-typed app handle — no Textual import — so it sits below the widget layer
(``transcript.py``) that renders what it forwards.
"""

from __future__ import annotations

import contextlib
from typing import Any

from rich.console import ConsoleDimensions
from rich.text import Text


class _TranscriptConsole:
    """Quacks like rich.Console for code that prints during a turn (the agent's
    tool blocks, slash-command output, end-of-turn sync), routing every
    renderable to the app's transcript. supports_live is False so the agent skips
    its terminal-only Live preview (see agent.py); status() is a no-op context
    manager for the same reason (a spinner can't drive a Textual widget)."""

    supports_live = False
    is_terminal = False
    # The transcript is a scannable stack, not a terminal: tool/shell bodies
    # render as a tight preview (the agent's Renderer reads this) so a git-show /
    # cat dump can't bury the turn. The full output still reaches the model.
    compact_tool_output = True

    def __init__(self, app: "XliiApp"):  # noqa: F821 — forward ref (app.py), string annotation only
        self._app = app
        self._stream: list[str] = []  # end="" chunk accumulator (see print)

    def print(self, *objects: Any, **kwargs: Any) -> None:
        # Streaming path: `print(chunk, end="")` on plain strings ACCUMULATES.
        # A real terminal joins such chunks on one flowing line; every transcript
        # write here is its own block widget, so emitting per-chunk shredded
        # /cursor · /delegate streams into one word per line hugging the left
        # margin. Buffer until a normal (end="\n") print flushes it as ONE
        # verbatim, full-width-wrapping block.
        end = kwargs.get("end", "\n")
        if end == "" and objects and all(isinstance(o, str) for o in objects):
            self._stream.append(str(kwargs.get("sep", " ")).join(objects))
            return
        self.flush_stream()
        if not objects:
            self._app.write_block("")
            return
        # /help prints its body with markup=False (it carries literal brackets
        # like "/rail [next|back]"); wrap such strings in Text so RichLog renders
        # them verbatim instead of parsing the brackets as style tags.
        markup = kwargs.get("markup", True)
        for obj in objects:
            if markup is False and isinstance(obj, str):
                self._app.write_block(Text(obj))
            else:
                self._app.write_block(obj)

    def flush_stream(self) -> None:
        """Emit accumulated end="" chunks as one verbatim block. Called before any
        non-streaming output (ordering) and after slash dispatch (a stream that
        ends the command must not sit buffered until some later print)."""
        if not self._stream:
            return
        text = "".join(self._stream)
        self._stream.clear()
        if text.strip():
            self._app.write_block(Text(text))

    def print_json(self, json: Any = None, *, data: Any = None, **kwargs: Any) -> None:
        from rich.json import JSON

        self.flush_stream()
        payload = data if data is not None else json
        if isinstance(payload, str):
            self._app.write_block(JSON(payload))
        else:
            self._app.write_block(JSON.from_data(payload))

    def rule(self, title: Any = "", **kwargs: Any) -> None:
        from rich.rule import Rule

        self.flush_stream()
        self._app.write_block(Rule(title))

    # -- tool-activity grouping -------------------------------------------
    # The agent brackets each tool *batch* (one model step's worth of calls)
    # with these; the app folds the blocks written between them into one
    # collapsible drawer. Duck-typed: a real rich Console has neither method, so
    # the agent's getattr-guarded calls are a no-op for the inline REPL.

    def begin_tool_group(self) -> None:
        self.flush_stream()  # a streamed reasoning tail must not land inside the fold
        begin = getattr(self._app, "begin_tool_group", None)
        if callable(begin):
            begin()

    def end_tool_group(self) -> None:
        self.flush_stream()
        end = getattr(self._app, "end_tool_group", None)
        if callable(end):
            end()

    def status(self, *args: Any, **kwargs: Any) -> Any:
        # No spinner in the TUI — the work still runs; just no live status line.
        return contextlib.nullcontext()

    @property
    def size(self) -> ConsoleDimensions:
        # Reflect the live Textual app dimensions so Rich renderables (wrapping,
        # tables, truncation) format correctly in the transcript. Keep a fallback
        # for early lifecycle moments where size may be unavailable.
        with contextlib.suppress(AttributeError, TypeError, ValueError):
            app_size = self._app.size
            width = max(1, int(getattr(app_size, "width", 0)))
            height = max(1, int(getattr(app_size, "height", 0)))
            if width and height:
                return ConsoleDimensions(width, height)
        return ConsoleDimensions(100, 30)
