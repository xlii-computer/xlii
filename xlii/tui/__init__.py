"""xlii's terminal presentation layer.

One visual grammar for "something happened and here's the result" — whether the
actor was the user (shell, `!bang`), a `/slash` handler, or the agent (`bash`,
`read_file`, …). Callers build a typed event (events.py) and hand it to the
shared `renderer`; the renderer picks a block (blocks.py), applies the `THEME`
(theme.py), and prints via the shared `console`.

`xlii.ui` re-exports `console`, `confirm`, `format_turn_line`, and `renderer`
from here and is the canonical import for kernel and cmd code (the seam);
existing `from xlii.tui import ...` call sites keep working unchanged.
"""

from __future__ import annotations

from rich.console import Console

from xlii.tui import blocks, events, theme
from xlii.turn_text import turn_footer
from xlii.tui.renderer import Renderer
from xlii.tui.theme import THEME, Theme

# The one Console singleton for the whole app. ui.py re-exports THIS object, so
# `from xlii.tui import console` and `from xlii.tui import console` are the same
# instance — no second Console, no divergent output stream.
console = Console()

# The one print path. Most code reaches it via the module-level singleton; tests
# build their own Renderer over a StringIO Console.
renderer = Renderer(console)

# format_turn_line kept its public name (call sites import it from xlii.ui); the
# implementation now lives in xlii.turn_text.turn_footer (blocks re-exports it).
format_turn_line = turn_footer


def confirm(prompt: str, *, assume_yes: bool = False) -> bool:
    """Ask a destructive y/N question. Returns True only on an explicit yes.

    The single home for confirmation prompts so acceptance is uniform: both `y`
    and `yes` count as yes everywhere. `assume_yes` is the `--yes` bypass. EOF /
    Ctrl-C — and non-interactive stdin — count as "no". Callers print their own
    warning and aborted messages around this.

    Routes through the `xlii.tools._confirm` indirection rather than calling
    `input()` directly: under the Textual TUI a raw `input()` runs on a worker
    thread that can't read the keyboard (Textual owns it) and blocks forever —
    that was the `/imagine` hang. `launch()` points `_tools._confirm` at the
    app's modal-backed confirm while the TUI runs and restores plain `input()`
    for the inline REPL on exit, so both surfaces stay correct here.
    """
    if assume_yes:
        return True
    # Lazy import keeps the tui<->tools import edge one-directional at module load.
    from xlii import tools as _tools

    try:
        answer = _tools._confirm(prompt)
    except (EOFError, KeyboardInterrupt):
        return False
    return (answer or "").strip().lower() in ("y", "yes")


__all__ = [
    "console",
    "renderer",
    "confirm",
    "format_turn_line",
    "turn_footer",
    "Renderer",
    "Theme",
    "THEME",
    "blocks",
    "events",
    "theme",
]
