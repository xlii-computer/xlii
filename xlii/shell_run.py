"""Shared shell execution for the TUI layer — one captured subprocess runner
behind both the human shell paths (repl) and the agent's bash tool, so a command
renders identically no matter who ran it.

The raw inherited-TTY path (pagers, vim, clear) stays in repl as the `!!` escape
hatch — capture can't drive a live terminal, so that escape is deliberate.

`styled_enabled()` gates the rollout (opt-in first): default raw — today's
behavior — and `XLII_SHELL_STYLE=styled` turns on ShellBlock capture.
`XLII_SHELL_STYLE=raw` forces raw.

Leaf module: stdlib + xlii.turn_events only — never imports tools, repl, agent,
or ui, so it can sit under all of them without a cycle.

Kernel home since godzilla-mothra V1ab (was xlii/tui/shell.py — kernel-shaped
code homed in the face package; relocated verbatim, import retargeted).
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from xlii.turn_events import ShellRan, ShellSource

_STYLE_ENV = "XLII_SHELL_STYLE"

# Captured shells can overlap: face/TUI let user shell lines run while a
# background agent turn may also call bash. Stop/cancel targets every registered
# process group so a later capture cannot hide an earlier hung one.
_LIVE_LOCK = threading.Lock()


@dataclass
class _LiveCapture:
    proc: subprocess.Popen
    cancelled: bool = False


_LIVE_PROCS: dict[int, _LiveCapture] = {}


def styled_enabled() -> bool:
    """True when shell output should capture into a ShellBlock. Opt-in first:
    default raw, `XLII_SHELL_STYLE=styled` enables it."""
    return os.environ.get(_STYLE_ENV, "raw").strip().lower() == "styled"


@contextlib.contextmanager
def styled_events():
    """Force styled rendering for the duration. The renderer emits the typed
    tool/shell/answer events only when ``styled_enabled()`` — the TUI turns this
    on (``tui_textual``); a HEADLESS body (the WS head) mirrors that same event
    stream to its client, so it must render styled too or the sink receives only
    chunks + done. Restores the prior value so callers in a mixed process (and
    the test env, which forces ``raw``) aren't left flipped."""
    prev = os.environ.get(_STYLE_ENV)
    os.environ[_STYLE_ENV] = "styled"
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop(_STYLE_ENV, None)
        else:
            os.environ[_STYLE_ENV] = prev


@dataclass
class Capture:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool
    duration_s: float
    cancelled: bool = False  # True when kill_live_shell() ended the job


def live_shell_running() -> bool:
    """True while any :func:`capture` has a live process group."""
    with _LIVE_LOCK:
        return any(entry.proc.poll() is None for entry in _LIVE_PROCS.values())


def kill_live_shell(*, escalate: bool = True) -> bool:
    """Kill process groups for all in-flight :func:`capture` calls.

    SIGTERM first; if *escalate* and any group is still up after a short wait,
    SIGKILL. Unblocks stuck ``communicate()`` callers. Returns True if any live
    process was targeted (even if already reaped mid-kill).
    """
    with _LIVE_LOCK:
        entries = [
            entry for entry in _LIVE_PROCS.values()
            if entry.proc.poll() is None
        ]
        if not entries:
            return False
        for entry in entries:
            entry.cancelled = True
        procs = [entry.proc for entry in entries]
    for proc in procs:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                # Best-effort cleanup: process may already have exited/reaped.
                pass
    if escalate:
        deadline = time.monotonic() + 0.4
        while time.monotonic() < deadline:
            if all(proc.poll() is not None for proc in procs):
                break
            time.sleep(0.05)
        for proc in procs:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        proc.kill()
                    except (ProcessLookupError, OSError):
                        # Best-effort shutdown: process may have already exited/reaped.
                        pass
    return True


# The alt-screen ENTER sequence — the first thing a full-screen program emits.
# If it shows up in captured output, we mis-ran an interactive program.
_ALT_SCREEN_ENTER = "\x1b[?1049h"
# Lowercased substrings programs print to stderr when they detect no real tty.
_NO_TTY_HINTS = (
    "not a terminal", "not a tty", "output is not to a terminal",
    "terminal is not fully functional", "term environment variable",
    "must be run on a terminal", "inappropriate ioctl for device",
    "no controlling terminal",
)


def looks_interactive(stdout: str, stderr: str) -> bool:
    """Heuristic: did a CAPTURED command turn out to be a full-screen / interactive
    program we should have handed the terminal? True if its output carries the
    alt-screen enter sequence or a 'not a terminal'-class complaint. Lets callers
    suggest `!!` / `/interactive add` after the fact (the self-curating hint)."""
    if _ALT_SCREEN_ENTER in (stdout or "") or _ALT_SCREEN_ENTER in (stderr or ""):
        return True
    blob = (stderr or "").lower()
    return any(h in blob for h in _NO_TTY_HINTS)


def capture(
    cmd: str,
    cwd,
    *,
    timeout: Optional[int] = None,
    env: Optional[dict] = None,
) -> Capture:
    """Run `cmd` in its own process group (so a timeout kills the whole tree — a
    bare proc.kill() leaves grandchildren running and the pipe open forever),
    capturing stdout/stderr. The single source of subprocess mechanics: both
    tools.t_bash and run_shell_captured go through here. Raises OSError if the
    process can't be started.

    Registers the process for :func:`kill_live_shell` so face/TUI **Stop** can
    interrupt a hung ``grep -rn`` (not only agent tool-boundary cancel).
    """
    start = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        shell=True,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,   # closed stdin: a mis-captured interactive
        stdout=subprocess.PIPE,     # program (mc/vim/cat) EOFs and EXITS instead
        stderr=subprocess.PIPE,     # of hanging the session forever (the freeze).
        text=True,
        env=env,
        start_new_session=True,
    )
    live_key = id(proc)
    with _LIVE_LOCK:
        _LIVE_PROCS[live_key] = _LiveCapture(proc)
    timed_out = False
    cancelled = False
    try:
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            rc = proc.returncode
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                proc.kill()
            stdout, stderr = proc.communicate()
            rc, timed_out = -1, True
        with _LIVE_LOCK:
            entry = _LIVE_PROCS.get(live_key)
            cancelled = bool(entry and entry.cancelled)
        if cancelled and not timed_out:
            # Signal death often yields negative returncode; normalize for UI.
            if rc is None or rc == 0:
                rc = -15
            note = "cancelled (stop — process group killed; output above is partial)"
            stderr = f"{stderr}\n{note}" if stderr else note
    finally:
        with _LIVE_LOCK:
            _LIVE_PROCS.pop(live_key, None)
    return Capture(
        stdout or "",
        stderr or "",
        rc if rc is not None else -1,
        timed_out,
        time.monotonic() - start,
        cancelled=cancelled,
    )


def run_shell_captured(
    cmd: str,
    cwd,
    *,
    timeout: Optional[int] = None,
    env: Optional[dict] = None,
    source: ShellSource = "user_shell",
    intent: Optional[str] = None,
) -> ShellRan:
    """Capture `cmd` and package it as a ShellRan event ready for renderer.emit.
    A timeout is surfaced in stderr so it shows in the block. Raises OSError if
    the process can't be started — callers print their own shell-error line."""
    cap = capture(cmd, cwd, timeout=timeout, env=env)
    stderr = cap.stderr
    if cap.timed_out:
        note = f"timed out after {timeout}s (process group killed; output above is partial)"
        stderr = f"{stderr}\n{note}" if stderr else note
    # cancelled note already appended in capture()
    return ShellRan(
        command=cmd,
        cwd=Path(cwd),
        stdout=cap.stdout,
        stderr=stderr,
        returncode=cap.returncode,
        duration_s=cap.duration_s,
        source=source,
        intent=intent,
    )
