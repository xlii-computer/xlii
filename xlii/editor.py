"""Shared `$EDITOR` handoff helpers."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

# POSIX "command not found" — returned when no editor is configured / on PATH so
# a caller can surface a "set $EDITOR" hint instead of claiming a successful edit.
EDITOR_UNAVAILABLE = 127
# TTY editor (nano/vim/…) launched with no terminal — the face has none.
EDITOR_NEEDS_TTY = 126
# GUI editor spawned and left running (pluma/gedit/code). Not "edited".
EDITOR_DETACHED = 125

# First-token names that read/write a tty. GUI apps (pluma, gedit, code) are
# everything else on the Config cycle ring.
_TTY_BINS = frozenset({
    "vi", "vim", "nvim", "nano", "micro", "emacs", "emacsclient",
    "hx", "helix", "ed", "joe", "ne",
})

# A full-screen host (the Textual TUI) installs a launcher so the external editor
# runs with the host SUSPENDED — otherwise the editor and the TUI read the same
# tty at once and the editor's escapes/keystrokes garble the app's input box.
# Unset by default: the inline REPL already owns the terminal, so a plain
# subprocess is correct there.
_LAUNCHER: Optional[Callable[[list[str]], int]] = None


def set_editor_launcher(
    fn: Optional[Callable[[list[str]], int]],
) -> Optional[Callable[[list[str]], int]]:
    """Install (or clear) the suspend-aware launcher; returns the previous one so
    the caller can restore it. The TUI does this around ``launch()``."""
    global _LAUNCHER
    prev = _LAUNCHER
    _LAUNCHER = fn
    return prev


def _active_cfg() -> Any:
    try:
        from xlii.active_session import active_cfg

        return active_cfg()
    except Exception:
        return None


def _editor_candidates(cfg: Any = None) -> list[tuple[str, str]]:
    """Ordered (source_label, command_line) pairs for resolve_editor."""
    if cfg is None:
        cfg = _active_cfg()
    out: list[tuple[str, str]] = []
    line = str(getattr(cfg, "editor", "") or "").strip() if cfg is not None else ""
    if line:
        out.append(("config", line))
    for label, cand in (("$EDITOR", os.environ.get("EDITOR")),
                        ("$VISUAL", os.environ.get("VISUAL")),
                        ("vi", "vi")):
        if cand and str(cand).strip():
            out.append((label, str(cand).strip()))
    return out


def resolve_editor(cfg: Any = None) -> Optional[str]:
    """The editor command line to use (cfg if non-empty, then $EDITOR, $VISUAL,
    vi), or None when none resolves to something on PATH. Supports editors-with-args
    like ``code -w`` (only the program is PATH-checked)."""
    for _src, cand in _editor_candidates(cfg):
        parts = shlex.split(cand)
        if parts and shutil.which(parts[0]):
            return cand
    return None


def editor_source(cfg: Any = None) -> str:
    """Which source won for :func:`resolve_editor` — ``config``, ``$EDITOR``,
    ``$VISUAL``, ``vi``, or ``''`` when none are available."""
    for src, cand in _editor_candidates(cfg):
        parts = shlex.split(cand)
        if parts and shutil.which(parts[0]):
            return src
    return ""


def editor_unavailable_hint() -> str:
    """One-line hint when no editor resolves — mentions both config and env."""
    return (
        "[yellow]no editor configured[/yellow] — set one in [cyan]/config[/cyan] "
        "(editor row) or [cyan]$EDITOR[/cyan] "
        "(e.g. [cyan]export EDITOR=nano[/cyan]), then try again."
    )


def editor_needs_tty_hint() -> str:
    """Face / headless: nano/vim have nowhere to draw."""
    return (
        "[yellow]that editor needs a terminal[/yellow] — the face has none. "
        "Set Config → editor to a windowed app (pluma, gedit, code)."
    )


def _editor_bin(cmdline: str) -> str:
    try:
        parts = shlex.split(cmdline or "")
    except ValueError:
        return ""
    return Path(parts[0]).name if parts else ""


def needs_tty(cmdline: str) -> bool:
    """True for nano/vim/… — false for pluma/gedit/code."""
    return _editor_bin(cmdline) in _TTY_BINS


def waits_for_exit(cmdline: str) -> bool:
    """Wait for TTY editors, or GUI editors launched with ``-w`` / ``--wait``."""
    try:
        parts = shlex.split(cmdline or "")
    except ValueError:
        parts = []
    if any(a in ("-w", "--wait", "-W") for a in parts[1:]):
        return True
    return needs_tty(cmdline)


def _have_tty() -> bool:
    """Delegated to the console seam — kernel code must not probe the terminal
    itself (``scripts/check_contracts.py`` isatty ratchet)."""
    from xlii.console_prompt import stdin_is_tty

    return stdin_is_tty()


def _spawn(cmd: list[str]) -> int:
    """Run the editor command — through the host's suspend-wrapped launcher when
    one is installed (the TUI), else a plain foreground subprocess."""
    if _LAUNCHER is not None:
        return _LAUNCHER(cmd)
    return subprocess.call(cmd)


def _spawn_detached(cmd: list[str]) -> int:
    """Launch a GUI editor and return immediately so the face stays alive."""
    try:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return EDITOR_UNAVAILABLE
    try:
        rc = proc.wait(timeout=0.25)
    except subprocess.TimeoutExpired:
        return EDITOR_DETACHED
    # Existing-instance handoff (pluma/gedit/code) exits 0 right away.
    return EDITOR_DETACHED if rc == 0 else rc


def open_for_edit(path: Path, *, cfg: Any = None) -> int:
    """Open ``path`` in the resolved editor and return its exit status, or
    ``EDITOR_UNAVAILABLE`` when no editor is configured / available (the caller
    should hint "set $EDITOR" rather than report a successful edit). Under the
    TUI the launch is suspend-wrapped (see :func:`set_editor_launcher`) so the
    editor doesn't fight the app for the terminal.

    On the face (no tty) a TTY editor is refused; a GUI editor is detached.
    """
    editor = resolve_editor(cfg)
    if editor is None:
        return EDITOR_UNAVAILABLE
    cmd = shlex.split(editor) + [str(path)]
    if _LAUNCHER is None and needs_tty(editor) and not _have_tty():
        return EDITOR_NEEDS_TTY
    try:
        if _LAUNCHER is None and not waits_for_exit(editor):
            return _spawn_detached(cmd)
        return _spawn(cmd)
    except FileNotFoundError:
        return EDITOR_UNAVAILABLE
