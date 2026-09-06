"""Graceful exit sequence — announced, ordered, idempotent, best-effort."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.exit_sequence import run_graceful_exit


def _printer():
    lines: list[str] = []
    return lines, lines.append


def _state(**kw):
    st = SimpleNamespace(save=lambda: None, journal=None)
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def test_announces_each_step_in_order(monkeypatch):
    compiled = []
    monkeypatch.setattr("xlii.shell_suggest.session_close_compile",
                        lambda *a, **k: compiled.append(True))
    saved = []
    flushed = []
    st = _state(save=lambda: saved.append(True),
                journal=SimpleNamespace(flush=lambda: flushed.append(True)))

    lines, printer = _printer()
    run_graceful_exit(st, printer=printer)

    joined = "\n".join(lines)
    assert "saving session" in joined
    assert "journal" in joined
    assert "shell habits" in joined
    assert lines[-1].strip().endswith("bye") or "bye" in lines[-1]
    # the work actually ran
    assert saved == [True] and flushed == [True] and compiled == [True]
    # order: save announced before journal announced before shell
    assert joined.index("saving session") < joined.index("journal") < joined.index("shell habits")


def test_idempotent_second_call_is_a_noop(monkeypatch):
    monkeypatch.setattr("xlii.shell_suggest.session_close_compile", lambda *a, **k: None)
    saves = []
    st = _state(save=lambda: saves.append(True))

    lines1, p1 = _printer()
    run_graceful_exit(st, printer=p1)
    lines2, p2 = _printer()
    run_graceful_exit(st, printer=p2)

    assert saves == [True]          # saved once, not twice
    assert lines2 == []             # second call printed nothing


def test_late_on_exit_hook_runs_after_tui_teardown_once(monkeypatch):
    """Nested /tui exits run teardown before the inline hook is available.

    The later inline unwind still needs its hook for lightweight session cleanup
    such as marking an active episode clean, but must not replay the public
    teardown or run the hook more than once.
    """
    monkeypatch.setattr("xlii.shell_suggest.session_close_compile", lambda *a, **k: None)
    hooks = []
    st = _state()

    lines1, p1 = _printer()
    run_graceful_exit(st, printer=p1)
    assert "bye" in "\n".join(lines1)

    lines2, p2 = _printer()
    run_graceful_exit(st, printer=p2, on_exit=lambda: hooks.append("clean"))
    run_graceful_exit(st, printer=p2, on_exit=lambda: hooks.append("again"))

    assert hooks == ["clean"]
    assert lines2 == []


def test_on_exit_hook_used_for_journal_when_provided(monkeypatch):
    monkeypatch.setattr("xlii.shell_suggest.session_close_compile", lambda *a, **k: None)
    hook = []
    flushed = []
    st = _state(journal=SimpleNamespace(flush=lambda: flushed.append(True)))

    lines, printer = _printer()
    run_graceful_exit(st, printer=printer, on_exit=lambda: hook.append(True))

    assert hook == [True]           # inline path: the caller's hook ran…
    assert flushed == []            # …not the direct journal flush


def test_a_failing_step_never_raises(monkeypatch):
    monkeypatch.setattr("xlii.shell_suggest.session_close_compile", lambda *a, **k: None)

    def _boom():
        raise RuntimeError("disk full")

    st = _state(save=_boom, journal=SimpleNamespace(flush=_boom))
    lines, printer = _printer()
    # must not raise despite two failing steps
    run_graceful_exit(st, printer=printer, on_exit=_boom)
    assert "bye" in "\n".join(lines)   # still reached the end


def test_journal_step_prefers_defer_flush(monkeypatch):
    """Fast exit: a journal with defer_flush leaves its tail for the next
    session's catch-up job — the exit sequence must NOT pay the flush."""
    monkeypatch.setattr("xlii.shell_suggest.session_close_compile", lambda *a, **k: None)
    deferred, flushed = [], []
    st = _state(journal=SimpleNamespace(
        defer_flush=lambda: deferred.append(True) or 1,
        flush=lambda: flushed.append(True)))
    lines, printer = _printer()
    run_graceful_exit(st, printer=printer)
    assert deferred == [True]      # the fast arm ran…
    assert flushed == []           # …and the LLM+upload flush did not


def test_no_journal_skips_the_journal_line(monkeypatch):
    monkeypatch.setattr("xlii.shell_suggest.session_close_compile", lambda *a, **k: None)
    st = _state(journal=None)
    lines, printer = _printer()
    run_graceful_exit(st, printer=printer)
    assert "journal" not in "\n".join(lines)


def test_winds_down_the_job_registry(monkeypatch):
    """Queued jobs are cancelled at exit (a queued catch-up stays spooled for
    next open); a still-running one is announced so the brief post-'bye' linger
    while the interpreter joins the worker thread is explained, not a mystery."""
    monkeypatch.setattr("xlii.shell_suggest.session_close_compile", lambda *a, **k: None)
    calls = {}

    class _Reg:
        def jobs(self, *, active=None):
            return [SimpleNamespace(status="running"), SimpleNamespace(status="pending")]

        def shutdown(self, *, wait=False):
            calls["wait"] = wait

    st = _state(job_registry=_Reg())
    lines, printer = _printer()
    run_graceful_exit(st, printer=printer)
    joined = "\n".join(lines)
    assert calls == {"wait": False}                 # executor torn down, non-blocking
    assert "winding down background jobs" in joined
    assert "1 background job(s) still finishing" in joined   # running counted, pending not
    assert joined.index("winding down") < joined.index("bye")


def test_no_registry_skips_the_wind_down_line(monkeypatch):
    monkeypatch.setattr("xlii.shell_suggest.session_close_compile", lambda *a, **k: None)
    st = _state()                                   # no job_registry attr at all
    lines, printer = _printer()
    run_graceful_exit(st, printer=printer)
    assert "winding down" not in "\n".join(lines)
