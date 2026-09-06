"""Stop/cancel kills live shell_run.capture process groups."""

from __future__ import annotations

import threading
import time

from xlii import shell_run
from xlii.shell_run import capture, kill_live_shell, live_shell_running


def _live_capture_count() -> int:
    with shell_run._LIVE_LOCK:
        return sum(
            1 for entry in shell_run._LIVE_PROCS.values()
            if entry.proc.poll() is None
        )


def test_kill_live_shell_unblocks_hung_capture():
    """A long sleep is interrupted by kill_live_shell (Stop semantics)."""
    result: list = []

    def _run():
        cap = capture("sleep 30", cwd=".", timeout=None)
        result.append(cap)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    # Wait until capture registers the live process
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not live_shell_running():
        time.sleep(0.02)
    assert live_shell_running(), "expected sleep capture to be live"
    assert kill_live_shell() is True
    t.join(timeout=5.0)
    assert not t.is_alive(), "capture should have returned after kill"
    assert result, "capture result missing"
    cap = result[0]
    assert cap.cancelled is True
    assert "cancelled" in (cap.stderr or "").lower()
    assert not live_shell_running()


def test_kill_live_shell_noop_when_idle():
    assert live_shell_running() is False
    assert kill_live_shell() is False


def test_kill_live_shell_unblocks_all_concurrent_captures():
    """Stop must kill every overlapping capture, not only the newest one."""
    result: list = []

    def _run(label: str):
        cap = capture("sleep 30", cwd=".", timeout=None)
        result.append((label, cap))

    threads = [
        threading.Thread(target=_run, args=(label,), daemon=True)
        for label in ("first", "second")
    ]
    for t in threads:
        t.start()

    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and _live_capture_count() < 2:
        time.sleep(0.02)
    assert _live_capture_count() == 2, "expected both captures to be live"

    assert kill_live_shell() is True
    for t in threads:
        t.join(timeout=5.0)
    assert all(not t.is_alive() for t in threads), "all captures should return"
    assert len(result) == 2
    assert {label for label, _ in result} == {"first", "second"}
    assert all(cap.cancelled is True for _, cap in result)
    assert not live_shell_running()
