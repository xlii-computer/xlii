"""Single source of truth for xlii's terminal presentation — palette, icons,
and border styles. Both the `code` and `chat` surfaces import THEME so they
never drift; change a color here and it changes everywhere a block renders.

Deliberately leaf-level: this module imports nothing from xlii, so blocks.py,
renderer.py, and ui.py can all sit on top of it without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    # Icons — reuse xlii's existing vocabulary (→ ✓ ✗ ⎿) so the TUI layer reads
    # as a tightening of what's already on screen, not a reskin.
    prompt: str = "$"        # a shell command line
    announce: str = "→"      # a tool about to run
    ok: str = "✓"
    err: str = "✗"
    gutter: str = "⎿"        # result / body gutter
    meta: str = "•"          # slash / meta message

    # Colors — Rich style strings.
    shell: str = "cyan"
    ok_color: str = "green"
    err_color: str = "red"
    meta_color: str = "magenta"
    dim: str = "grey50"
    rule: str = "grey39"
    # Track H0: speakers must differ — both were "cyan" and collapsed in scrollback.
    # Lean pair (concrete hex bikeshed parked): yellow ask vs cyan answer frame.
    user: str = "yellow"
    assistant: str = "cyan"

    # Textual TUI input chrome — border brightening in scroll mode and the
    # TextArea scrollbar tone (CSS color literals, injected into App CSS).
    tui_input_scroll_border: str = "cyan"
    tui_input_scrollbar: str = "#636363"  # Rich grey39 equivalent

    def status_color(self, returncode: int) -> str:
        """Header rule color for a shell run — green on success, red otherwise."""
        return self.ok_color if returncode == 0 else self.err_color

    def status_icon(self, is_error: bool) -> str:
        return self.err if is_error else self.ok

    def source_label(self, source: str) -> str:
        """Who ran it — collapse the three ShellRan sources to a one-word tag."""
        return "agent" if source == "agent_bash" else "you"


# The default instance. Callers pass `theme=` to override (e.g. a future
# .xlii/theme.toml load); everything else uses this.
THEME = Theme()
