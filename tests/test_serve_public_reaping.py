"""textual-serve child reaping — ``stop()`` must always terminate the app.

Upstream 1.1.3 stops a child cooperatively (send ``quit``, then await the
reader with no timeout and no kill), so a child that ignores stdin never dies
and ``stop()`` blocks forever inside the ``finally`` that was supposed to clean
it up. That leaked one process per dropped connection until a 928 MB box
thrashed itself unreachable (2026-07-24).

These tests drive REAL subprocesses — a fake that exits politely would pass
against the upstream bug and prove nothing. The uncooperative child here traps
SIGTERM and ignores stdin, so only the process-group SIGKILL can end it.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import pytest


def _need_web() -> None:
    pytest.importorskip("textual_serve")


def _need_posix_groups() -> None:
    if not (hasattr(os, "getpgid") and hasattr(os, "killpg")):
        pytest.skip("POSIX process groups unavailable on this platform")


# A child that CANNOT be stopped cooperatively: it ignores stdin entirely and
# traps SIGTERM, so it dies only to SIGKILL. This is the shape of an xlii TUI
# blocked in a synchronous inference call or a tool's bash step.
UNCOOPERATIVE = (
    "import signal, sys, time; "
    "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "sys.stdout.write('up\\n'); sys.stdout.flush(); "
    "time.sleep(600)"
)

# A well-behaved child: quits the moment anything arrives on stdin, which is
# what a real Textual app does with the ``quit`` meta packet.
COOPERATIVE = (
    "import sys; "
    "sys.stdout.write('up\\n'); sys.stdout.flush(); "
    "sys.stdin.buffer.read(1); "
)


async def _start_raw(svc):
    """Start the child WITHOUT textual-serve's packet reader.

    ``AppService.start()`` also launches ``run()``, which speaks the binary
    packet protocol on the child's stdout — a plain test child desyncs it and
    its output would be swallowed. ``stop()`` depends on ``_task`` only as
    "a task that completes when the child does", so model precisely that and
    leave stdout readable for the tests.
    """
    proc = await svc._open_app_process(80, 24)
    svc._task = asyncio.create_task(proc.wait())
    return proc


def _service(command: str, *, quit_grace=0.4, signal_grace=0.4):
    """Build a ReapingAppService around ``command`` with short graces."""
    from xlii.serve_public import _install_reaping_app_service

    cls = _install_reaping_app_service()

    class _NullDownloads:
        async def cancel_app_downloads(self, *, app_service_id):
            return None

    async def _noop_bytes(_data):
        return None

    async def _noop_str(_data):
        return None

    async def _noop_close():
        return None

    svc = cls(
        command,
        write_bytes=_noop_bytes,
        write_str=_noop_str,
        close=_noop_close,
        download_manager=_NullDownloads(),
    )
    svc.quit_grace_s = quit_grace
    svc.signal_grace_s = signal_grace
    return svc


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


# --------------------------------------------------------------------------- #
# installation
# --------------------------------------------------------------------------- #


def test_install_is_idempotent_and_rebinds_the_name():
    """The installer swaps the name upstream's handle_websocket constructs."""
    _need_web()
    from textual_serve import server as ts_server
    from textual_serve.app_service import AppService

    from xlii.serve_public import _install_reaping_app_service

    first = _install_reaping_app_service()
    second = _install_reaping_app_service()

    assert first is second, "re-installing must not build a second class"
    assert ts_server.AppService is first, "upstream must construct ours"
    assert issubclass(first, AppService)


def test_building_a_public_server_installs_the_reaper(tmp_path):
    """The wiring, not just the mechanism.

    Every other test here calls the installer directly, so all of them keep
    passing if the call site in ``make_public_server`` is ever dropped — and
    the leak comes back silently. Pin the wiring itself.
    """
    _need_web()
    pytest.importorskip("aiohttp")
    from textual_serve import server as ts_server

    from xlii.serve_gate import GateStore
    from xlii.serve_public import make_public_server

    # Reset to upstream so this proves make_public_server did the install
    # rather than inheriting a patch from an earlier test.
    from textual_serve.app_service import AppService as Upstream

    ts_server.AppService = Upstream
    if hasattr(ts_server, "_xlii_reaping_app_service"):
        del ts_server._xlii_reaping_app_service

    make_public_server(
        "true",
        gate=GateStore(lockout_threshold=5, lockout_window_s=300,
                       lockout_duration_s=300),
        base_url="https://example.test",
        state_dir=tmp_path,
        audit_log=tmp_path / "audit.log",
        sweep_interval_s=3600,
    )

    assert ts_server.AppService is not Upstream, (
        "make_public_server did not install the reaper — children will leak"
    )
    assert hasattr(ts_server.AppService, "_signal_group")


# --------------------------------------------------------------------------- #
# the bug: stop() must terminate a child that ignores every polite request
# --------------------------------------------------------------------------- #


def test_stop_kills_a_child_that_ignores_quit_and_sigterm():
    """The regression that took the box down.

    Against upstream's ``stop()`` this hangs forever and the process survives.
    """
    _need_web()

    async def _main():
        svc = _service(f"{sys.executable} -c \"{UNCOOPERATIVE}\"")
        await _start_raw(svc)
        assert svc._process is not None
        pid = svc._process.pid

        # Confirm it really booted before asking it to die.
        assert await asyncio.wait_for(svc._process.stdout.readline(), 10) == b"up\n"

        await asyncio.wait_for(svc.stop(), timeout=15)

        await asyncio.wait_for(svc._process.wait(), timeout=5)
        assert not _alive(pid), "child survived stop() — this is the leak"

    asyncio.run(_main())


def test_stop_returns_promptly_rather_than_blocking():
    """``stop()`` is called from a ``finally``; if it blocks, so does cleanup."""
    _need_web()

    async def _main():
        svc = _service(
            f"{sys.executable} -c \"{UNCOOPERATIVE}\"",
            quit_grace=0.3,
            signal_grace=0.3,
        )
        await _start_raw(svc)
        assert await asyncio.wait_for(svc._process.stdout.readline(), 10) == b"up\n"

        loop = asyncio.get_running_loop()
        began = loop.time()
        await asyncio.wait_for(svc.stop(), timeout=10)
        elapsed = loop.time() - began

        # quit grace + SIGTERM grace + a little slack; nowhere near the
        # child's 600 s sleep.
        assert elapsed < 5, f"stop() took {elapsed:.1f}s — it is still blocking"

    asyncio.run(_main())


def test_cooperative_child_is_not_force_killed():
    """A healthy app still exits on its own; escalation is a backstop only."""
    _need_web()

    async def _main():
        svc = _service(f"{sys.executable} -c \"{COOPERATIVE}\"", quit_grace=5.0)
        await _start_raw(svc)
        assert await asyncio.wait_for(svc._process.stdout.readline(), 10) == b"up\n"
        proc = svc._process

        await asyncio.wait_for(svc.stop(), timeout=10)
        await asyncio.wait_for(proc.wait(), timeout=5)

        # Exited of its own accord (stdin EOF), not by signal. Signalled
        # deaths surface as a negative returncode.
        assert proc.returncode is not None and proc.returncode >= 0, (
            f"cooperative child was signalled (rc={proc.returncode})"
        )

    asyncio.run(_main())


def test_stop_escalates_if_quit_meta_write_blocks():
    """A wedged ``send_meta`` must not consume the whole shutdown forever."""
    _need_web()

    async def _main():
        svc = _service(
            f"{sys.executable} -c \"{UNCOOPERATIVE}\"",
            quit_grace=0.3,
            signal_grace=0.3,
        )
        await _start_raw(svc)
        assert await asyncio.wait_for(svc._process.stdout.readline(), 10) == b"up\n"
        pid = svc._process.pid

        async def _blocked_send_meta(_meta):
            await asyncio.Event().wait()

        svc.send_meta = _blocked_send_meta

        loop = asyncio.get_running_loop()
        began = loop.time()
        await asyncio.wait_for(svc.stop(), timeout=10)
        elapsed = loop.time() - began

        await asyncio.wait_for(svc._process.wait(), timeout=5)
        assert elapsed < 5, f"stop() took {elapsed:.1f}s with wedged send_meta"
        assert not _alive(pid), "child survived wedged send_meta path"

    asyncio.run(_main())


def test_stop_is_idempotent():
    """The cleanup path calls stop() twice (server.py:341 and :351)."""
    _need_web()

    async def _main():
        svc = _service(f"{sys.executable} -c \"{UNCOOPERATIVE}\"")
        await _start_raw(svc)
        assert await asyncio.wait_for(svc._process.stdout.readline(), 10) == b"up\n"

        await asyncio.wait_for(svc.stop(), timeout=15)
        await asyncio.wait_for(svc.stop(), timeout=5)  # must be a no-op

        assert svc._task is None

    asyncio.run(_main())


def test_cancelled_stop_still_reaps_on_retry():
    """A cancelled first stop() must not leave a live child for a second stop().

    Upstream's handle_websocket finally can call stop() again after the first
    await is cancelled; clearing _task early made the retry a no-op.
    """
    _need_web()

    async def _main():
        svc = _service(
            f"{sys.executable} -c \"{UNCOOPERATIVE}\"",
            quit_grace=2.0,
            signal_grace=0.4,
        )
        await _start_raw(svc)
        assert await asyncio.wait_for(svc._process.stdout.readline(), 10) == b"up\n"
        pid = svc._process.pid

        first = asyncio.create_task(svc.stop())
        # Let stop enter the cooperative grace, then cancel the waiter only.
        await asyncio.sleep(0.15)
        first.cancel()
        try:
            await first
        except asyncio.CancelledError:
            # Expected: the task was cancelled on the line above.
            pass

        # Retry — the in-flight reap (or a fresh one) must finish the job.
        await asyncio.wait_for(svc.stop(), timeout=15)
        await asyncio.wait_for(svc._process.wait(), timeout=5)
        assert not _alive(pid), "child survived cancelled-then-retried stop()"
        assert svc._task is None

    asyncio.run(_main())


# --------------------------------------------------------------------------- #
# the second defect: the handle points at /bin/sh, not the app
# --------------------------------------------------------------------------- #


def test_child_leads_its_own_process_group():
    """``create_subprocess_shell`` hands back ``/bin/sh``, which forks the real
    app. Without ``start_new_session`` a kill reaches only the shell and
    orphans the app to init, so the leak survives the fix."""
    _need_web()
    _need_posix_groups()

    async def _main():
        svc = _service(f"{sys.executable} -c \"{UNCOOPERATIVE}\"")
        await _start_raw(svc)
        assert await asyncio.wait_for(svc._process.stdout.readline(), 10) == b"up\n"
        pid = svc._process.pid

        # New session => the child is its own group leader, so pgid == pid and
        # killpg cannot reach back into the server's own group.
        assert os.getpgid(pid) == pid
        assert os.getpgid(pid) != os.getpgid(os.getpid())

        await asyncio.wait_for(svc.stop(), timeout=15)

    asyncio.run(_main())


def test_grandchild_in_the_group_dies_too():
    """The app's own children (a tool's bash step) must go with it — that is
    the point of signalling the group rather than the process."""
    _need_web()
    _need_posix_groups()

    # Parent spawns a long-lived grandchild, prints both pids, then ignores
    # everything. Only a process-group kill reaps the pair.
    prog = (
        "import os, signal, subprocess, sys, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "k = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
        "sys.stdout.write(f'{os.getpid()} {k.pid}\\n'); sys.stdout.flush(); "
        "time.sleep(600)"
    )

    async def _main():
        svc = _service(f"{sys.executable} -c \"{prog}\"")
        await _start_raw(svc)
        line = await asyncio.wait_for(svc._process.stdout.readline(), 15)
        child_pid, grandchild_pid = (int(x) for x in line.split())

        await asyncio.wait_for(svc.stop(), timeout=15)
        await asyncio.wait_for(svc._process.wait(), timeout=5)

        # Give the group kill a moment to land on the grandchild.
        for _ in range(50):
            if not _alive(grandchild_pid):
                break
            await asyncio.sleep(0.1)

        assert not _alive(child_pid)
        assert not _alive(grandchild_pid), (
            "grandchild outlived the group kill — a tool subprocess would leak"
        )

    asyncio.run(_main())


# --------------------------------------------------------------------------- #
# guard rails
# --------------------------------------------------------------------------- #


def test_stop_without_start_is_a_no_op():
    _need_web()

    async def _main():
        svc = _service("true")
        await asyncio.wait_for(svc.stop(), timeout=5)

    asyncio.run(_main())


def test_signal_group_tolerates_a_dead_child():
    """Reaping races the child exiting on its own; that must not raise."""
    _need_web()

    async def _main():
        svc = _service(f"{sys.executable} -c \"{COOPERATIVE}\"")
        # No _start_raw: this exercises _signal_group directly, and a
        # background proc.wait() task would race for the same transport.
        proc = await svc._open_app_process(80, 24)
        assert await asyncio.wait_for(proc.stdout.readline(), 10) == b"up\n"

        # Take the group down, then let the reaper try anyway. This is the
        # real race: a child that exits between the grace expiring and the
        # signal landing. Reaping must be total, not merely usually-correct.
        svc._signal_group(signal.SIGKILL)
        for _ in range(50):
            if not svc._group_alive():
                break
            await asyncio.sleep(0.1)
        assert not svc._group_alive(), "group survived an explicit SIGKILL"

        svc._signal_group(signal.SIGTERM)  # must not raise
        svc._signal_group(signal.SIGKILL)  # must not raise
        assert not svc._group_alive()

    asyncio.run(_main())


def test_missing_getpgid_uses_compatible_fallback(monkeypatch):
    _need_web()
    monkeypatch.delattr(os, "getpgid", raising=False)
    monkeypatch.delattr(os, "killpg", raising=False)

    async def _main():
        svc = _service(
            f"{sys.executable} -c \"{UNCOOPERATIVE}\"",
            quit_grace=0.3,
            signal_grace=0.3,
        )
        await _start_raw(svc)
        assert await asyncio.wait_for(svc._process.stdout.readline(), 10) == b"up\n"

        await asyncio.wait_for(svc.stop(), timeout=10)
        await asyncio.wait_for(svc._process.wait(), timeout=5)
        assert getattr(svc, "_pgid", None) is None

    asyncio.run(_main())


def test_missing_sigkill_force_path_does_not_raise(monkeypatch):
    """Windows has no signal.SIGKILL; force-kill must still terminate the child."""
    _need_web()
    monkeypatch.delattr(signal, "SIGKILL", raising=False)

    async def _main():
        svc = _service(
            f"{sys.executable} -c \"{UNCOOPERATIVE}\"",
            quit_grace=0.3,
            signal_grace=0.3,
        )
        await _start_raw(svc)
        assert await asyncio.wait_for(svc._process.stdout.readline(), 10) == b"up\n"
        pid = svc._process.pid

        await asyncio.wait_for(svc.stop(), timeout=10)
        await asyncio.wait_for(svc._process.wait(), timeout=5)
        assert not _alive(pid)

    asyncio.run(_main())
