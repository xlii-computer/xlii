"""Best-effort rolling trace log of every CLI invocation.

A loguru file sink under the config dir (`logs/xlii.log`) captures the command
line, its exit code, and everything already logged through loguru (sync
failures, key errors, …) at DEBUG and up — so debugging a bad run is tailing one
file instead of asking for a re-run and a copy-paste. User-visible stderr
diagnostics are preserved (WARNING and up, loguru's normal format); the extra
trace detail (invocation, exit, DEBUG/INFO) goes only to the file.

Disable entirely with XLII_NO_TRACELOG=1. Best-effort throughout: logging must
never break the CLI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

_DISABLE_ENV = "XLII_NO_TRACELOG"


def enabled() -> bool:
    return os.environ.get(_DISABLE_ENV, "").strip().lower() not in ("1", "true", "yes", "on")


def tracelog_path() -> Path:
    from xlii.config import GLOBAL_CONFIG_DIR
    return GLOBAL_CONFIG_DIR / "logs" / "xlii.log"


def setup_tracelog() -> Optional[int]:
    """Route loguru to a rolling trace file (DEBUG+) while keeping user-visible
    stderr diagnostics (WARNING+). Returns the file sink id, or None if disabled
    or logging couldn't be set up. Best-effort: never raises."""
    if not enabled():
        return None
    try:
        from loguru import logger

        path = tracelog_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Replace loguru's default stderr-at-DEBUG handler: keep visible
        # diagnostics at WARNING+ (matches prior behavior — only sync ERROR /
        # unparsable WARNING were ever shown), send full detail to the file.
        logger.remove()
        logger.add(sys.stderr, level="WARNING")
        return logger.add(
            str(path),
            level="DEBUG",
            rotation="5 MB",
            retention=5,
            enqueue=False,
            backtrace=False,
            diagnose=False,
            format=(
                "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | pid={process} | "
                "{name}:{function}:{line} - {message}"
            ),
        )
    except Exception:
        return None


def log_invocation(argv: list[str]) -> None:
    """Record the command line at the start of a run (file only)."""
    _emit("invoked: {}", " ".join(argv))


def log_exit(code) -> None:
    """Record the exit code at the end of a run (file only)."""
    _emit("exit: {}", code)


def log_crash(exc: BaseException) -> None:
    """Record an uncaught exception with its traceback (file only, so it doesn't
    double up with the natural traceback already printed to the terminal)."""
    if not enabled():
        return
    try:
        from loguru import logger
        logger.bind(cli=True).opt(exception=exc).info("invocation crashed: {}", repr(exc))
    except Exception:
        # Tracing must never raise from a crash handler, and loguru may be absent.
        pass


def _emit(template: str, *args) -> None:
    # INFO so it lands in the file sink (DEBUG+) but not the stderr sink (WARN+).
    if not enabled():
        return
    try:
        from loguru import logger
        logger.bind(cli=True).info(template, *args)
    except Exception:
        # Tracing is optional: loguru may be absent, and logging must never break the caller.
        pass
