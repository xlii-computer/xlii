"""Vector J — background jobs: the registry, the two published seams (J3
set_job_listener + J4 summary_segment), the frame-tab fallback (Q4), the
REPLState field, the /jobs command surface, and the non-interactive confirm
guard. No network; jobs run on a real ThreadPoolExecutor with deterministic
event-gated fns + reg.wait()."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

import xlii.jobs as J
from helpers import FakeConsole, make_agent, make_cfg
from xlii.repl_state import REPLState


@pytest.fixture(autouse=True)
def _reset_listener():
    """Keep the module-global repaint listener out of other tests."""
    J.set_job_listener(None)
    yield
    J.set_job_listener(None)


def _state(tmp_path: Path, *, scope: str = "code") -> REPLState:
    agent = make_agent(tmp_path, cfg=make_cfg(max_parallel_workers=3))
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    st = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                   cfg=agent.cfg, pool=agent.pool)
    st.command_scope = scope
    return st


# --------------------------------------------------------------------------- #
#  BackgroundJob model
# --------------------------------------------------------------------------- #

def test_job_defaults_and_glyph():
    job = J.BackgroundJob(job_id="t1", kind=J.KIND_TASK, name="build")
    assert job.status == J.PENDING
    assert job.active and job.glyph == J._GLYPH[J.PENDING]
    assert job.notify is True
    assert job.result is None and job.error is None
    d = job.to_dict()
    assert d["job_id"] == "t1" and d["kind"] == "task" and d["status"] == "pending"


def test_job_kinds_are_open_strings():
    # The contract: kind stays an open string so III's Conductor can register a
    # `fleet` job (and a community caller a new kind) without J changing.
    for k in (J.KIND_TASK, J.KIND_LOOP, J.KIND_HARNESS, J.KIND_FLEET):
        assert isinstance(k, str)
    fleet = J.BackgroundJob(job_id="f9", kind=J.KIND_FLEET, name="vectoring")
    assert fleet.kind == "fleet"


# --------------------------------------------------------------------------- #
#  JobRegistry — dispatch / lifecycle
# --------------------------------------------------------------------------- #

def test_dispatch_runs_and_completes():
    reg = J.JobRegistry(max_workers=2)
    jid = reg.dispatch(J.KIND_TASK, "build", lambda: {"ok": True})
    job = reg.wait(jid, timeout=5)
    assert job is not None
    assert job.status == J.DONE
    assert job.result == {"ok": True}
    assert job.glyph == "✓"
    assert not job.active
    reg.shutdown(wait=True)


def test_dispatch_failure_is_captured_not_raised():
    reg = J.JobRegistry(max_workers=2)

    def boom():
        raise ValueError("kaboom")

    jid = reg.dispatch(J.KIND_TASK, "explode", boom)
    job = reg.wait(jid, timeout=5)
    assert job.status == J.FAILED
    assert "ValueError" in job.error and "kaboom" in job.error
    assert job.result is None
    reg.shutdown(wait=True)


def test_jobs_listing_order_and_filters():
    reg = J.JobRegistry(max_workers=3)
    a = reg.dispatch(J.KIND_TASK, "a", lambda: 1)
    b = reg.dispatch(J.KIND_LOOP, "b", lambda: 2)
    reg.wait(a, timeout=5)
    reg.wait(b, timeout=5)
    ids = [j.job_id for j in reg.jobs()]
    assert ids == [a, b]  # dispatch order preserved
    assert [j.job_id for j in reg.jobs(kind=J.KIND_LOOP)] == [b]
    assert reg.jobs(active=False) == reg.jobs()  # both terminal now
    assert reg.active_jobs() == []
    reg.shutdown(wait=True)


def test_get_unknown_and_clear_finished():
    reg = J.JobRegistry(max_workers=2)
    assert reg.get("nope") is None
    jid = reg.dispatch(J.KIND_TASK, "x", lambda: 1)
    reg.wait(jid, timeout=5)
    assert reg.clear_finished() == 1
    assert reg.jobs() == []
    reg.shutdown(wait=True)


def test_cancel_pending_job_before_it_starts():
    # One worker + one slow job pins the pool, so the second stays PENDING and
    # can be cancelled before it ever runs.
    reg = J.JobRegistry(max_workers=1)
    release = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        release.wait(5)
        return "done"

    ran = {"second": False}

    def second():
        ran["second"] = True
        return "ran"

    a = reg.dispatch(J.KIND_TASK, "slow", slow)
    started.wait(5)
    b = reg.dispatch(J.KIND_TASK, "queued", second)
    assert reg.cancel(b) is True
    assert reg.get(b).status == J.CANCELLED
    release.set()
    reg.wait(a, timeout=5)
    # The cancelled job's fn never ran.
    assert ran["second"] is False
    assert reg.cancel("ghost") is False        # unknown id
    assert reg.cancel(a) is False              # already finished
    reg.shutdown(wait=True)


def test_running_job_marked_cancelled_when_cooperative():
    reg = J.JobRegistry(max_workers=2)
    release = threading.Event()
    started = threading.Event()

    def work():
        started.set()
        release.wait(5)
        return "finished"

    jid = reg.dispatch(J.KIND_TASK, "long", work)
    started.wait(5)
    assert reg.cancel(jid) is True
    assert reg.get(jid).cancel_requested is True
    release.set()
    job = reg.wait(jid, timeout=5)
    # The thread wasn't killed, but the result is discarded → CANCELLED.
    assert job.status == J.CANCELLED
    reg.shutdown(wait=True)


# --------------------------------------------------------------------------- #
#  Seam J3 — set_job_listener (the set_renderable_sink shape)
# --------------------------------------------------------------------------- #

def test_set_job_listener_returns_previous():
    a = lambda: None
    b = lambda: None
    assert J.set_job_listener(a) is None
    assert J.set_job_listener(b) is a
    assert J.set_job_listener(None) is b


def test_listener_fires_on_each_state_change():
    fires = {"n": 0}
    J.set_job_listener(lambda: fires.__setitem__("n", fires["n"] + 1))
    reg = J.JobRegistry(max_workers=2)
    jid = reg.dispatch(J.KIND_TASK, "x", lambda: 1)
    reg.wait(jid, timeout=5)
    # pending (dispatch) + running + done ⇒ at least three repaints.
    assert fires["n"] >= 3
    reg.shutdown(wait=True)


def test_listener_exception_never_kills_the_job():
    def bad_listener():
        raise RuntimeError("repaint blew up")

    J.set_job_listener(bad_listener)
    reg = J.JobRegistry(max_workers=2)
    jid = reg.dispatch(J.KIND_TASK, "x", lambda: "ok")
    job = reg.wait(jid, timeout=5)
    assert job.status == J.DONE and job.result == "ok"
    reg.shutdown(wait=True)


# --------------------------------------------------------------------------- #
#  Seam J4 — summary_segment (S places the call; J owns the fn)
# --------------------------------------------------------------------------- #

def test_summary_segment_none_when_no_registry_or_idle(tmp_path):
    from types import SimpleNamespace

    assert J.summary_segment(None) is None
    assert J.summary_segment(SimpleNamespace()) is None  # no job_registry slot
    st = _state(tmp_path)
    assert J.summary_segment(st) is None                 # registry not even created
    J.get_registry(st)
    assert J.summary_segment(st) is None                 # created but empty


def test_summary_segment_shows_running_job(tmp_path):
    st = _state(tmp_path)
    reg = J.get_registry(st)
    release = threading.Event()
    started = threading.Event()

    def work():
        started.set()
        release.wait(5)

    jid = reg.dispatch(J.KIND_TASK, "build", work)
    started.wait(5)
    seg = J.summary_segment(st)
    assert seg is not None
    text = str(seg)
    assert "task" in text and "build" in text and "⠹" in text
    release.set()
    reg.wait(jid, timeout=5)
    assert J.summary_segment(st) is None  # collapses when idle
    reg.shutdown(wait=True)


def test_summary_segment_renders_fleet_detail_and_progress(tmp_path):
    st = _state(tmp_path)
    reg = J.get_registry(st)
    release = threading.Event()
    started = threading.Event()

    def work():
        started.set()
        release.wait(5)

    jid = reg.dispatch(J.KIND_FLEET, "vectoring", work)
    started.wait(5)
    job = reg.get(jid)
    job.detail = "A1 ✓ A2 ⠹ B …"
    job.progress = (3, 6)
    text = str(J.summary_segment(st))
    assert "fleet" in text and "A1 ✓ A2 ⠹ B" in text and "3/6" in text
    release.set()
    reg.wait(jid, timeout=5)
    reg.shutdown(wait=True)


def test_summary_segment_many_jobs_tally(tmp_path):
    st = _state(tmp_path)
    reg = J.get_registry(st)
    release = threading.Event()
    started = [threading.Event() for _ in range(2)]

    def make(i):
        def work():
            started[i].set()
            release.wait(5)
        return work

    j0 = reg.dispatch(J.KIND_TASK, "a", make(0))
    j1 = reg.dispatch(J.KIND_LOOP, "b", make(1))
    for ev in started:
        ev.wait(5)
    text = str(J.summary_segment(st))
    assert "2 jobs" in text
    release.set()
    reg.wait(j0, timeout=5)
    reg.wait(j1, timeout=5)
    reg.shutdown(wait=True)


def test_summary_segment_never_raises_on_garbage_state():
    from types import SimpleNamespace

    bad = SimpleNamespace(job_registry="not a registry")
    assert J.summary_segment(bad) is None


# --------------------------------------------------------------------------- #
#  Q4 — frame-tab fallback through A1's published seam
# --------------------------------------------------------------------------- #

def test_register_job_seams_is_idempotent_and_safe():
    from xlii.tui.status import _FRAME_TAB_PROVIDERS, unregister_frame_tab

    J.register_job_seams()
    J.register_job_seams()
    assert _FRAME_TAB_PROVIDERS.count(J.job_frame_tab) == 1
    unregister_frame_tab(J.job_frame_tab)


def test_job_frame_tab_reflects_active_jobs(tmp_path):
    st = _state(tmp_path)
    assert J.job_frame_tab(st) is None  # registry not created yet
    reg = J.get_registry(st)
    assert J.job_frame_tab(st) is None  # created, empty
    release = threading.Event()
    started = threading.Event()

    def work():
        started.set()
        release.wait(5)

    jid = reg.dispatch(J.KIND_TASK, "build", work)
    started.wait(5)
    tabs = J.job_frame_tab(st)
    assert tabs is not None
    assert len(tabs) == 1
    label, kind, payload = tabs[0]
    assert label == "1 job" and kind == "jobs" and payload is reg
    release.set()
    reg.wait(jid, timeout=5)
    # After finish: active chip gone, done-tab appears (completed-unseen).
    tabs = J.job_frame_tab(st)
    assert tabs is not None
    assert len(tabs) == 1
    label, kind, payload = tabs[0]
    assert kind == "job_done" and payload == jid
    assert jid in label
    assert reg.mark_seen(jid) is True
    assert J.job_frame_tab(st) is None
    reg.shutdown(wait=True)


def test_job_done_tab_dismisses_on_mark_seen(tmp_path):
    st = _state(tmp_path)
    reg = J.get_registry(st)
    jid = reg.dispatch(J.KIND_TASK, "quick", lambda: "ok")
    reg.wait(jid, timeout=5)
    job = reg.get(jid)
    assert job is not None and job.result == "ok"
    assert [j.job_id for j in reg.unseen_done()] == [jid]
    tabs = J.job_frame_tab(st)
    assert tabs and tabs[0][1] == "job_done"
    reg.mark_seen(jid)
    assert reg.unseen_done() == []
    assert J.job_frame_tab(st) is None
    reg.shutdown(wait=True)


def test_job_tab_appears_in_frame_tabs_when_active(tmp_path):
    # End-to-end through A1's frame_tabs() reader: a registered provider's tab
    # shows up alongside the built-ins when a job is in flight.
    from xlii.tui.status import frame_tabs, unregister_frame_tab

    st = _state(tmp_path)
    J.register_job_seams()
    reg = J.get_registry(st)
    release = threading.Event()
    started = threading.Event()

    def work():
        started.set()
        release.wait(5)

    jid = reg.dispatch(J.KIND_TASK, "build", work)
    started.wait(5)
    try:
        kinds = [t[1] for t in frame_tabs(st)]
        assert "jobs" in kinds
    finally:
        release.set()
        reg.wait(jid, timeout=5)
        unregister_frame_tab(J.job_frame_tab)
        reg.shutdown(wait=True)


# --------------------------------------------------------------------------- #
#  REPLState field + get_registry (J's half of the two-pointer)
# --------------------------------------------------------------------------- #

def test_replstate_has_job_registry_field(tmp_path):
    st = _state(tmp_path)
    assert st.job_registry is None  # lazily created, ephemeral


def test_get_registry_lazy_and_caps_from_cfg(tmp_path):
    st = _state(tmp_path)  # make_cfg(max_parallel_workers=3)
    reg = J.get_registry(st)
    assert isinstance(reg, J.JobRegistry)
    assert reg._max_workers == 3
    assert J.get_registry(st) is reg          # same instance on second access
    assert st.job_registry is reg


def test_get_registry_none_for_stateless_ctx():
    from types import SimpleNamespace

    assert J.get_registry(None) is None
    assert J.get_registry(SimpleNamespace()) is None  # no job_registry slot


def test_get_registry_cap_defaults_when_cfg_missing():
    from types import SimpleNamespace

    st = SimpleNamespace(job_registry=None, cfg=None, console=FakeConsole())
    reg = J.get_registry(st)
    assert reg._max_workers == 8  # safe default


def test_completion_notice_written_to_live_console(tmp_path):
    st = _state(tmp_path)
    reg = J.get_registry(st)
    jid = reg.dispatch(J.KIND_TASK, "build", lambda: "ok")
    reg.wait(jid, timeout=5)
    assert "done" in st.console.text and jid in st.console.text
    reg.shutdown(wait=True)


def test_failed_job_notice_names_the_error(tmp_path):
    st = _state(tmp_path)
    reg = J.get_registry(st)

    def boom():
        raise RuntimeError("nope")

    jid = reg.dispatch(J.KIND_TASK, "x", boom)
    reg.wait(jid, timeout=5)
    assert "failed" in st.console.text and "nope" in st.console.text
    reg.shutdown(wait=True)


def test_notify_false_suppresses_completion_notice(tmp_path):
    st = _state(tmp_path)
    reg = J.get_registry(st)
    jid = reg.dispatch(J.KIND_TASK, "quiet", lambda: "ok", notify=False)
    reg.wait(jid, timeout=5)
    assert st.console.text == ""  # nothing announced
    reg.shutdown(wait=True)


# --------------------------------------------------------------------------- #
#  /jobs command surface
# --------------------------------------------------------------------------- #

def test_jobs_command_registered_in_both_repls():
    from xlii.commands import find_repl_command
    from xlii.repl_cmds import register_all

    register_all()
    assert find_repl_command("/jobs", "code") is not None
    assert find_repl_command("/jobs", "chat") is not None


def test_jobs_list_empty(tmp_path):
    from xlii.repl_cmds.jobs import _jobs_handler

    st = _state(tmp_path)
    _jobs_handler("/jobs", st.as_context_dict())
    assert "no background jobs" in st.console.text


def test_jobs_list_show_and_cancel(tmp_path):
    from xlii.repl_cmds.jobs import _jobs_handler

    st = _state(tmp_path)
    reg = J.get_registry(st)
    jid = reg.dispatch(J.KIND_TASK, "build", lambda: {"carry": "RESULT"})
    reg.wait(jid, timeout=5)

    st.console.lines.clear()
    _jobs_handler("/jobs", st.as_context_dict())
    out = st.console.text
    assert jid in out and "task" in out and "build" in out and "done" in out

    st.console.lines.clear()
    _jobs_handler(f"/jobs show {jid}", st.as_context_dict())
    assert jid in st.console.text

    st.console.lines.clear()
    _jobs_handler(f"/jobs cancel {jid}", st.as_context_dict())
    assert "nothing to cancel" in st.console.text  # already finished

    st.console.lines.clear()
    _jobs_handler("/jobs clear", st.as_context_dict())
    assert "cleared 1" in st.console.text
    assert reg.jobs() == []
    reg.shutdown(wait=True)


def test_jobs_show_unknown_and_cancel_active(tmp_path):
    from xlii.repl_cmds.jobs import _jobs_handler

    st = _state(tmp_path)
    reg = J.get_registry(st)
    _jobs_handler("/jobs show zzz", st.as_context_dict())
    assert "no job zzz" in st.console.text

    release = threading.Event()
    started = threading.Event()

    def work():
        started.set()
        release.wait(5)

    jid = reg.dispatch(J.KIND_TASK, "long", work)
    started.wait(5)
    st.console.lines.clear()
    _jobs_handler(f"/jobs cancel {jid}", st.as_context_dict())
    assert "cancel requested" in st.console.text
    release.set()
    reg.wait(jid, timeout=5)
    reg.shutdown(wait=True)


def test_jobs_unknown_subcommand(tmp_path):
    from xlii.repl_cmds.jobs import _jobs_handler

    st = _state(tmp_path)
    _jobs_handler("/jobs frobnicate", st.as_context_dict())
    assert "unknown /jobs subcommand" in st.console.text


# --------------------------------------------------------------------------- #
#  Non-interactive confirm guard — a detached job never hangs on a gate
# --------------------------------------------------------------------------- #

def test_background_confirm_is_fail_closed_on_job_thread_only():
    import xlii.tools as tools

    original = tools._confirm
    tools._confirm = lambda prompt="": "FOREGROUND"  # the live (foreground) confirm
    try:
        reg = J.JobRegistry(max_workers=1)
        entered = threading.Event()
        release = threading.Event()
        seen = {}

        def work():
            # On the JOB thread the guard makes confirm fail closed ('n').
            seen["job_thread"] = tools._confirm("run? (y/N): ")
            entered.set()
            release.wait(5)
            return "ok"

        jid = reg.dispatch(J.KIND_TASK, "gated", work)
        entered.wait(5)
        # While the job holds the guard, a confirm on THIS (foreground) thread
        # still delegates to the live confirm — the REPL is not clobbered.
        assert tools._confirm("anything") == "FOREGROUND"
        release.set()
        reg.wait(jid, timeout=5)
        assert seen["job_thread"] == "n"
        # Guard restored after the last job finished.
        assert tools._confirm("anything") == "FOREGROUND"
        reg.shutdown(wait=True)
    finally:
        tools._confirm = original
