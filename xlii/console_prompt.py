"""One-line user prompts that are safe on EVERY surface — the request_line seam.

The hazard this module exists for: a REPL command handler that calls ``input()``
or ``getpass()`` works in the inline REPL but **deadlocks the full-screen TUI** —
Textual owns the terminal in raw mode, so stdin stays a tty (``isatty()`` lies
about serviceability) while every keystroke goes to Textual's event loop and the
blocking read never returns. The user sees the busy spinner forever. This is the
same class ``ConfirmModal`` solves for the bash intent-gate; this seam is its
text-input twin for command handlers.

:func:`request_line` resolves, in order:

1. ``console.request_input(prompt, secret=…)`` — a capability the TUI attaches to
   its transcript console (``XliiApp._prompt_via_modal``): the worker thread
   blocks on an Event while the UI thread runs a ``PromptModal``. Any future
   surface that can't service stdin attaches the same capability.
2. Blocking stdin (``input()`` / ``getpass``) — the inline REPL / plain terminal
   path, only when stdin exists and is a tty.
3. ``None`` — no way to ask (piped stdin, no capability); the caller prints its
   own "use the flag form" guidance.

Contract: ``None`` means *could not ask / user cancelled* (Esc in the modal, EOF
on stdin) and callers must abort their flow; ``""`` is a real answer (e.g. "no
secret"). Never raises.
"""

from __future__ import annotations

from typing import Any, Optional


def stdin_is_tty() -> bool:
    """Whether stdin is a terminal. The one probe kernel callers share, so the
    isatty ratchet (``scripts/check_contracts.py``) stays at this seam."""
    import sys

    try:
        return bool(sys.stdin) and sys.stdin.isatty()
    except Exception:
        return False


def can_prompt(console: Any) -> bool:
    """Whether :func:`request_line` has ANY way to ask on this surface."""
    from xlii.human_gate import is_foreground_console

    if not is_foreground_console(console):
        return False
    if callable(getattr(console, "request_input", None)):
        return True
    return stdin_is_tty()


def request_line(console: Any, prompt: str, *, secret: bool = False) -> Optional[str]:
    """Ask the user one line. ``secret=True`` masks input (getpass / password
    field). Returns the answer (possibly ``""``), or ``None`` when the user
    cancelled or no surface can ask."""
    from xlii.human_gate import is_foreground_console

    if not is_foreground_console(console):
        return None
    fn = getattr(console, "request_input", None)
    if callable(fn):
        try:
            return fn(prompt, secret=secret)
        except Exception:
            return None
    import sys

    if not (sys.stdin and sys.stdin.isatty()):
        return None
    try:
        if secret:
            import getpass

            return getpass.getpass(f"{prompt}: ")
        return input(f"{prompt}: ")
    except (EOFError, KeyboardInterrupt):
        return None
