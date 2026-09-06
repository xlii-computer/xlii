"""Input chrome — the bottom toolbar + mode-colored prompt for the REPL.

Surfaces mode / cwd / rail / attachments in a strip that doesn't scroll away,
and tints the prompt arrow by input mode. Gated by styled_enabled() (the TUI
rollout flag) and a TTY; XLII_NO_TOOLBAR=1 hides just the toolbar.

This is the only tui module that touches prompt_toolkit — the render kernel
(blocks/renderer) stays rich-only — and it is deliberately NOT imported by
tui/__init__, so `import xlii.tui` doesn't pull prompt_toolkit. It reads
REPLState defensively (getattr) and lazily borrows format_shell_cwd from repl
to avoid an import cycle.
"""

from __future__ import annotations

import os
import sys
from typing import Optional, Union

from prompt_toolkit.auto_suggest import AutoSuggest, Suggestion
from prompt_toolkit.formatted_text import FormattedText

from xlii.tui.shell import styled_enabled
# Single source for the readers; here we only skin them in prompt_toolkit. The
# Textual --tui renders these (enriched) via xlii.tui.status.profile_bar().
from xlii.tui.status import attachments as _attachments
from xlii.tui.status import auto_approve as _auto_approve
from xlii.tui.status import cwd as _cwd
from xlii.tui.status import exit_hint as _exit_hint
from xlii.tui.status import journal as _journal
from xlii.tui.status import mode as _mode
from xlii.tui.status import role as _role
from xlii.tui.status import session_cost as _session_cost

# prompt_toolkit style strings per mode (its color syntax, distinct from Rich's).
_MODE_STYLE = {
    "SHELL": "fg:ansigreen bold",
    "PLAN": "fg:ansiyellow bold",
    "CHAT": "fg:ansicyan bold",
    "HOWTO": "fg:ansiblue bold",
    "YOLO": "fg:ansired bold",
    "RAIL": "fg:ansimagenta bold",
    "DEBUG": "fg:ansicyan bold",
}


def _isatty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


# --------------------------------------------------------------------------- #
#  Shell ghost text (fish-style) — the inline-REPL twin of the Textual --tui's
#  render_line ghost. Both read the SAME xlii.shell_suggest table (AI at compile
#  time, dumb prefix scan at serve time). prompt_toolkit's native AutoSuggest is
#  the mechanism: a dim inline suffix, accepted with right-arrow / ctrl-e / end
#  (its default key bindings). No custom binding needed.
# --------------------------------------------------------------------------- #

class _ShellGhostSuggest(AutoSuggest):
    """Suggest a fish-style completion for bare shell input in the inline REPL.

    The table is loaded ONCE (stale is fine); ``get_suggestion`` is a bounded
    in-memory prefix scan with **no model and no I/O** — the same input-loop law
    the TUI ghost obeys. It fires only when bare input routes to the shell
    (``_is_shell_primary``), so it never shadows an agent-ask turn, a ``/command``,
    or a ``?`` question; an empty/never-compiled table simply yields nothing.
    """

    def __init__(self, state: object, table: "list[str]") -> None:
        self._state = state
        self._table = table

    def get_suggestion(self, buffer, document):  # prompt_toolkit hook
        if not self._table:
            return None
        text = document.text
        if not text or text[0] in "/?:!":
            return None   # /command · ?ask · :shortcode · !explicit-shell — never bare-shell
        try:
            from xlii.repl import _is_shell_primary  # lazy — avoids an import cycle
            if not _is_shell_primary(self._state):
                return None
        except Exception:
            return None   # a bad/absent state must never break the render path
        from xlii import shell_suggest
        suffix = shell_suggest.ghost_suggestion(text, self._table)
        return Suggestion(suffix) if suffix else None


def make_shell_autosuggest(state: object) -> Optional[AutoSuggest]:
    """Build the inline-REPL shell ghost AutoSuggest, loading the served table
    ONCE. A missing/never-compiled table → an AutoSuggest that simply never
    suggests (graceful nothing, exactly like the TUI). Returns None only when the
    suggestion machinery is unavailable, so callers may attach unconditionally."""
    try:
        from xlii import shell_suggest
        table = shell_suggest.load_table()
    except Exception:
        return None
    return _ShellGhostSuggest(state, table)


def toolbar_enabled() -> bool:
    return styled_enabled() and not os.environ.get("XLII_NO_TOOLBAR") and _isatty()


def _prompt_color_enabled() -> bool:
    return styled_enabled() and _isatty()


def render_toolbar(state) -> FormattedText:
    label, key = _mode(state)
    frags: list[tuple[str, str]] = [(_MODE_STYLE.get(key, "bold"), f" {label} ")]
    cwd = _cwd(state)
    if cwd:
        frags += [("", "│ "), ("fg:ansicyan", f"{cwd} ")]
    role = _role(state)
    if role:
        frags += [("", "│ "), ("fg:ansiblue bold", f"{role} ")]
    attach = _attachments(state)
    if attach:
        frags += [("", "│ "), ("fg:ansimagenta", f"{attach} ")]
    approve = _auto_approve(state)
    if approve:
        frags += [("", "│ "), ("fg:ansiyellow", f"{approve} ")]
    cost = _session_cost(state)
    if cost:
        frags += [("", "│ "), ("fg:ansigreen", f"{cost} ")]
    jrnl = _journal(state)
    if jrnl:
        frags += [("", "│ "), ("fg:ansimagenta bold", f"{jrnl} ")]
    # The way out — shown only when there IS one (an overlay/mode → /off, a chat
    # surface → /code). Answers the "didn't know I was in a mode" complaint.
    hint = _exit_hint(state)
    if hint:
        frags += [("", "│ "), ("fg:ansibrightblack", f"{hint} ")]
    return FormattedText(frags)


def toolbar(state) -> Optional[FormattedText]:
    """The bottom-toolbar value for session.prompt(), or None when disabled."""
    if not toolbar_enabled():
        return None
    return render_toolbar(state)


def prompt_message(state, raw_prefix: str) -> Union[str, FormattedText]:
    """Tint the trailing prompt arrow by mode. Falls back to the plain string
    when styling is off or there's no TTY, so non-interactive paths are unchanged."""
    if not _prompt_color_enabled():
        return raw_prefix
    _, key = _mode(state)
    base = raw_prefix
    arrow = "› "
    if base.endswith("› "):
        base = base[: -len("› ")]
    return FormattedText([("", base), (_MODE_STYLE.get(key, "bold"), arrow)])
