"""Honest daemon death — disable systemd restart so ``/kill`` is not a blink."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

UNIT_CANDIDATES = ("xlii-daemon.service", "xlii.service")

RunFn = Callable[..., subprocess.CompletedProcess]


def _systemd_available() -> bool:
    if shutil.which("systemctl") is None:
        return False
    return Path("/run/systemd/system").exists() or Path(
        os.environ.get("XDG_RUNTIME_DIR") or "",
        "systemd",
    ).exists() or Path("/run/user").exists()


def _run(
    run: RunFn,
    args: list[str],
    *,
    user: bool,
) -> subprocess.CompletedProcess:
    cmd = ["systemctl"]
    if user:
        cmd.append("--user")
    cmd.extend(args)
    return run(cmd, capture_output=True, text=True, timeout=8)


def _unit_loaded(run: RunFn, unit: str, *, user: bool) -> bool:
    try:
        proc = _run(run, ["status", unit, "--no-pager"], user=user)
    except (OSError, subprocess.TimeoutExpired):
        return False
    # 0 = running, 3 = loaded but inactive — both mean the unit exists.
    return proc.returncode in (0, 3) or "Loaded:" in (proc.stdout or "")


def disable_systemd_restart(*, run: Optional[RunFn] = None) -> str:
    """Mask/disable the daemon unit so Restart=always cannot revive it.

    Returns a short report. Never raises — kill must still stop the process.
    """
    run = run or subprocess.run
    if os.environ.get("XLII_TEST_HOME"):
        return "test — skip systemd"
    if not _systemd_available():
        return "not systemd — process will exit"
    for user in (True, False):
        for unit in UNIT_CANDIDATES:
            if not _unit_loaded(run, unit, user=user):
                continue
            try:
                _run(run, ["mask", "--now", unit], user=user)
                _run(run, ["disable", "--now", unit], user=user)
            except (OSError, subprocess.TimeoutExpired) as exc:
                return f"systemd {unit}: {exc}"
            where = "user" if user else "system"
            return f"masked {where} unit {unit}"
    return "no xlii-daemon unit — process will exit"
