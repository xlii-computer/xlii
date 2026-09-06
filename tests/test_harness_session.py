"""Vector C — harness modes & orchestration: persistent sessions, the registry,
harness-as-a-mode routing, seam #5 (HarnessSpec label/model_selectable + hints),
the Tier-2 handoff, and the Tier-3 capture seam.

ACP turns run against the offline fake server (tests/acp_fake_server.py), so no
network or real harness binary is needed.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from xlii.harness import detect
from xlii.harness import session as hs
from xlii.harness.mcp_context import build_handoff_context
from xlii.harness.session import (
    HarnessSessionRegistry,
    PersistentHarnessSession,
    drive_foreground_session,
    get_registry,
)
from xlii.repl import REPLState, process_repl_input
from tests.helpers import FakeConsole
from tests.test_rail import _bare_agent

FAKE = str(Path(__file__).parent / "acp_fake_server.py")


@pytest.fixture
def fake_argv(monkeypatch):
    """Point persistent ACP sessions at the offline fake server."""
    monkeypatch.setattr(hs, "resolve_acp_argv", lambda name: [sys.executable, FAKE])


def _state(tmp_path) -> REPLState:
    agent = _bare_agent()
    agent.history = [{"role": "system", "content": "s"}]
    xli = tmp_path / ".xlii"
    xli.mkdir(exist_ok=True)
    proj = SimpleNamespace(project_root=tmp_path, xli_dir=xli, local_only=True, name="proj")
    st = REPLState(
        console=FakeConsole(),
        agent=agent,
        project=proj,
        cfg=SimpleNamespace(orchestrator_temp=lambda: 0.7),
        pool=[],
    )
    st.shell_cwd = tmp_path.resolve()
    return st


# --------------------------------------------------------------------------- #
#  Contract: the one co-owned field + the foreground read
# --------------------------------------------------------------------------- #

def test_replstate_has_cursor_sessions_field(tmp_path):
    st = _state(tmp_path)
    assert st.cursor_sessions is None  # lazily created, ephemeral
    assert st.harness_foreground is None


def test_get_registry_lazily_creates_and_persists(tmp_path):
    st = _state(tmp_path)
    reg = get_registry(st)
    assert isinstance(reg, HarnessSessionRegistry)
    assert get_registry(st) is reg  # same instance on second access
    assert st.cursor_sessions is reg


def test_get_registry_none_for_stateless_ctx():
    assert get_registry(None) is None
    assert get_registry(SimpleNamespace()) is None  # no cursor_sessions slot


def test_harness_foreground_reflects_registry(tmp_path):
    st = _state(tmp_path)
    reg = get_registry(st)
    assert st.harness_foreground is None
    reg.foreground = "cursor"
    assert st.harness_foreground == "cursor"


# --------------------------------------------------------------------------- #
#  Seam #5 — HarnessSpec label + model_selectable + hints
# --------------------------------------------------------------------------- #

def test_harness_spec_label_and_model_selectable():
    assert detect.harness_label("cursor") == "cursor"
    assert detect.harness_label("claude") == "claude code"
    assert detect.harness_label("grok") == "grok build"
    assert detect.harness_model_selectable("cursor") is True
    assert detect.harness_model_selectable("claude") is False
    assert detect.harness_model_selectable("grok") is False


def test_harness_label_to_name_round_trip():
    assert detect.harness_label_to_name("claude code") == "claude"
    assert detect.harness_label_to_name("cursor") == "cursor"
    assert detect.harness_label_to_name("nope") is None


def test_harness_meta_includes_seam5_fields():
    meta = detect.harness_meta("cursor")
    assert meta["label"] == "cursor"
    assert meta["model_selectable"] is True


def test_community_spec_defaults_are_backward_compatible():
    # A harness.local.py-style spec omits the new fields; defaults must hold.
    spec = detect.HarnessSpec(
        name="opencode", binaries=("opencode",), binary_env="X",
        tier="cross_agent", auth_hint="login", default_model="auto",
    )
    assert spec.label == ""
    assert spec.model_selectable is False


def test_register_harness_hints(monkeypatch):
    from xlii import hints

    monkeypatch.setattr(hints, "_REGISTERED", {})
    detect.register_harness_hints()
    regd = hints.registered_hints()
    assert "cursor" in regd and "drive cursor" in regd["cursor"]
    assert "/off" in regd["cursor"]
    assert "/cursor off" not in regd["cursor"]
    assert "claude code" in regd
    # The hint resolves through the public seam for the harness mode word.
    assert hints.mode_hint("cursor") == regd["cursor"]


# --------------------------------------------------------------------------- #
#  Tier 1 — persistent named ACP sessions
# --------------------------------------------------------------------------- #

def test_persistent_session_multi_turn_reuses_client(tmp_path, fake_argv):
    s = PersistentHarnessSession("cursor", "build", project_root=tmp_path, mode="agent")
    t1 = s.ask("first task")
    assert t1.error is None
    assert "mode=agent" in t1.text
    assert s.is_live  # the ACP subprocess stayed up
    client = s._client
    t2 = s.ask("second task")
    assert t2.error is None
    assert s._client is client  # same live session reused across turns
    assert s.turns == 2
    s.close()
    assert not s.is_live


def test_persistent_session_accumulates_files(tmp_path, fake_argv):
    s = PersistentHarnessSession("cursor", "build", project_root=tmp_path)
    turn = s.ask("do it")
    # the fake server writes out.txt via fs/write_text_file
    assert any(f.endswith("out.txt") for f in turn.files_touched)
    assert any(f.endswith("out.txt") for f in s.files_touched)
    s.close()


def test_session_error_is_returned_not_raised(tmp_path, monkeypatch):
    from xlii.acp_client import AcpError

    def boom(_name):
        raise AcpError("cursor-agent CLI not found")

    monkeypatch.setattr(hs, "resolve_acp_argv", boom)
    s = PersistentHarnessSession("cursor", "build", project_root=tmp_path)
    turn = s.ask("do it")
    assert turn.error is not None
    assert "not found" in turn.error
    assert s.turns == 1  # the failed turn still counted


# --------------------------------------------------------------------------- #
#  The registry
# --------------------------------------------------------------------------- #

def test_registry_open_get_close(tmp_path):
    reg = HarnessSessionRegistry()
    s = reg.open("cursor", "build", project_root=tmp_path)
    assert reg.get("build") is s
    assert reg.names() == ["build"]
    assert reg.active_name == "build"
    assert reg.for_harness("cursor") == [s]
    closed = reg.close("build")
    assert closed is True
    assert reg.get("build") is None
    closed_again = reg.close("build")
    assert closed_again is False


def test_registry_enter_and_leave_mode(tmp_path):
    reg = HarnessSessionRegistry()
    s = reg.enter_mode("cursor", project_root=tmp_path)
    assert reg.foreground == "cursor"
    assert reg.active_name == s.name == "cursor"  # default session named for harness
    assert reg.foreground_session() is s
    left = reg.leave_mode()
    assert left == "cursor"
    assert reg.foreground is None
    assert reg.foreground_session() is None
    # leaving mode keeps the session alive
    assert reg.get("cursor") is s


def test_registry_enter_mode_reuses_named_session(tmp_path):
    reg = HarnessSessionRegistry()
    built = reg.open("cursor", "build", project_root=tmp_path)
    same = reg.enter_mode("cursor", "build", project_root=tmp_path)
    assert same is built
    assert reg.foreground == "cursor"


def test_registry_close_all(tmp_path):
    reg = HarnessSessionRegistry()
    reg.open("cursor", "a", project_root=tmp_path)
    reg.open("grok", "b", project_root=tmp_path)
    reg.foreground = "cursor"
    reg.close_all()
    assert reg.names() == []
    assert reg.foreground is None


# --------------------------------------------------------------------------- #
#  Tier 1.5 — bare-input routing through process_repl_input
# --------------------------------------------------------------------------- #

def test_drive_foreground_returns_false_without_mode(tmp_path):
    st = _state(tmp_path)
    assert drive_foreground_session(st, "hi") is False  # no mode active → fall through


def test_routing_drives_session_in_mode(tmp_path, fake_argv):
    st = _state(tmp_path)
    reg = get_registry(st)
    reg.open("cursor", "cursor", project_root=tmp_path)
    reg.enter_mode("cursor", "cursor", project_root=tmp_path)

    # Bare input in harness mode → the session, not xlii's agent/shell.
    out = process_repl_input(st, "make a change")
    assert out == (None, True)
    assert reg.get("cursor").turns == 1
    assert "mode=" in st.console.text  # the fake's echo streamed to the console


def test_mode_escapes_still_work(tmp_path, fake_argv):
    st = _state(tmp_path)
    reg = get_registry(st)
    reg.enter_mode("cursor", "cursor", project_root=tmp_path)

    # `?` still summons xlii's own AI (not the harness).
    assert process_repl_input(st, "?ask xlii") == ("ask xlii", False)
    assert reg.get("cursor").turns == 0  # the harness session was not driven


def test_leaving_mode_restores_normal_routing(tmp_path, fake_argv, monkeypatch):
    st = _state(tmp_path)
    reg = get_registry(st)
    reg.enter_mode("cursor", "cursor", project_root=tmp_path)
    reg.leave_mode()

    calls: list = []
    monkeypatch.setattr("xlii.repl.subprocess.call", lambda cmd, **kw: calls.append(cmd) or 0)
    out = process_repl_input(st, "ls")
    assert out == (None, True)  # back to shell-primary
    assert calls  # ran as a shell command, not a harness turn
    assert reg.get("cursor").turns == 0


def test_prefixed_task_in_mode_continues_live_session(tmp_path, fake_argv):
    # Break 2: in foreground mode, a prefixed `/cursor <task>` (typed out of habit)
    # must CONTINUE the live session — coherence parity with bare input in mode —
    # not spawn a fresh, contextless one-shot.
    from xlii.repl_cmds.delegate import run_delegate_command

    st = _state(tmp_path)
    reg = get_registry(st)
    reg.enter_mode("cursor", "cursor", project_root=tmp_path)
    assert reg.get("cursor").turns == 0

    run_delegate_command("/cursor keep going", st.as_context_dict(), default_harness="cursor")
    assert reg.get("cursor").turns == 1  # drove the live session, not a one-shot


def test_prefixed_task_without_mode_stays_one_shot(tmp_path, fake_argv, monkeypatch):
    # The gate is tight: with no foreground mode, `/cursor <task>` is still a fresh
    # one-shot delegate — an open-but-not-foreground session is left untouched.
    from xlii.repl_cmds import delegate

    st = _state(tmp_path)
    reg = get_registry(st)
    reg.open("cursor", "cursor", project_root=tmp_path)  # session exists; mode is NOT on
    assert reg.foreground is None

    hit: dict = {}

    def _fake_one_shot(*a, **k):
        hit["one_shot"] = True
        return SimpleNamespace(error=None, text="", notes=[], files_touched=[],
                               stop_reason="end_turn", harness="cursor")

    monkeypatch.setattr(delegate, "run_delegate", _fake_one_shot)
    delegate.run_delegate_command("/cursor do a thing", st.as_context_dict(), default_harness="cursor")
    assert hit.get("one_shot") is True     # the one-shot path ran
    assert reg.get("cursor").turns == 0    # the live session was not driven


# --------------------------------------------------------------------------- #
#  Tier 2 — context handoff
# --------------------------------------------------------------------------- #

def test_handoff_empty_for_bare_state(tmp_path):
    st = _state(tmp_path)
    assert build_handoff_context(st) == ""
    assert build_handoff_context(None) == ""


def test_handoff_forwards_manifest(tmp_path):
    st = _state(tmp_path)
    st.attached_docs = [("notes", "BODY-TEXT"), ("skill:fmt", "SKILL-BODY")]
    st.attached_refs = [("alice", "")]   # bookmark shape (manifest lists names only)
    st.active_role = "reviewer"
    st.agent.history = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "build the parser"},
        {"role": "assistant", "content": "parser built"},
    ]
    ctx = build_handoff_context(st)
    assert "Active role: reviewer" in ctx
    assert "notes" in ctx and "BODY-TEXT" in ctx
    assert "skill:fmt" not in ctx and "SKILL-BODY" not in ctx  # skills excluded
    assert "alice" in ctx
    assert "build the parser" in ctx and "parser built" in ctx


def test_session_with_context_seeds_first_turn(tmp_path, fake_argv):
    st = _state(tmp_path)
    st.active_role = "reviewer"
    reg = get_registry(st)
    sess = reg.open("cursor", "build", project_root=tmp_path, with_context=True)
    captured = {}
    orig = sess.ask

    def spy(task, **kw):
        captured["task"] = task
        return orig(task, **kw)

    sess.ask = spy  # type: ignore
    hs.run_session_turn(st, sess, "do the work")
    assert "Context handed off from xlii" in captured["task"]
    assert "do the work" in captured["task"]
    sess.close()


# --------------------------------------------------------------------------- #
#  Tier 3 — capture seam (defensive)
# --------------------------------------------------------------------------- #

def test_capture_is_noop_without_seam(tmp_path):
    st = _state(tmp_path)
    # B's capture_output isn't present yet — must be a silent no-op, never raise.
    hs.capture_harness_output(st, "harness:cursor", "some text", into_history=True)


def test_capture_calls_seam_when_present(tmp_path, monkeypatch):
    import xlii.shell_toolkit as shtk

    seen = {}
    monkeypatch.setattr(
        shtk, "capture_output",
        lambda state, text, *, source, into_history=False: seen.update(
            state=state, text=text, source=source, into_history=into_history
        ),
        raising=False,
    )
    st = _state(tmp_path)
    hs.capture_harness_output(st, "harness:cursor", "RESULT", into_history=True)
    assert seen["text"] == "RESULT"
    assert seen["source"] == "harness:cursor"
    assert seen["into_history"] is True


# --------------------------------------------------------------------------- #
#  Seams #1/#2 — the session as a context tab + preview (defensive)
# --------------------------------------------------------------------------- #

def test_session_tab_provider(tmp_path):
    st = _state(tmp_path)
    assert hs.session_tab(st) is None  # no foreground session
    reg = get_registry(st)
    reg.enter_mode("cursor", "cursor", project_root=tmp_path)
    tab = hs.session_tab(st)
    assert tab is not None
    label, kind, payload = tab
    assert label == "cursor" and kind == "session"
    assert payload is reg.get("cursor")


def test_register_session_seams_is_safe_without_a1_a2():
    # A1/A2 seams aren't in this branch — registration must no-op, not raise.
    hs.register_session_seams()


# --------------------------------------------------------------------------- #
#  Command surface — /cursor session sub-verbs through the real handler
# --------------------------------------------------------------------------- #

def _ctx(st):
    return {"console": st.console, "state": st, "project": st.project}


def test_cursor_new_and_ls(tmp_path):
    from xlii.repl_cmds.cursor import _cursor_handler

    st = _state(tmp_path)
    assert _cursor_handler("/cursor new build", _ctx(st)) is True
    reg = get_registry(st)
    assert reg.get("build") is not None
    assert "ready" in st.console.text

    st.console.lines.clear()
    _cursor_handler("/cursor ls", _ctx(st))
    assert "build" in st.console.text


def test_cursor_at_name_drives_session(tmp_path, fake_argv):
    from xlii.repl_cmds.cursor import _cursor_handler

    st = _state(tmp_path)
    _cursor_handler("/cursor @build implement the parser", _ctx(st))
    reg = get_registry(st)
    sess = reg.get("build")
    assert sess is not None and sess.turns == 1
    assert "mode=" in st.console.text  # the harness streamed back


def test_cursor_at_name_bg_dispatches_background_job(tmp_path, fake_argv):
    """`/cursor @name --bg <task>` runs the turn as a session-owned job (Vector J)
    instead of blocking the REPL — flag stripped, un-streamed, turn still runs."""
    import xlii.jobs as J
    from xlii.repl_cmds.cursor import _cursor_handler

    st = _state(tmp_path)
    hreg = get_registry(st)
    sess = hreg.open("cursor", "build", project_root=tmp_path)
    seen = {}
    orig = sess.ask

    def spy(task, **kw):
        seen["task"] = task
        seen["on_event"] = kw.get("on_event", "MISSING")
        return orig(task, **kw)

    sess.ask = spy  # type: ignore

    assert _cursor_handler("/cursor @build --bg implement the parser", _ctx(st)) is True

    jobreg = J.get_registry(st)
    jobs = jobreg.jobs()
    assert len(jobs) == 1
    assert jobs[0].kind == J.KIND_HARNESS and jobs[0].name == "cursor @build"
    assert "background job" in st.console.text and "/jobs" in st.console.text

    job = jobreg.wait(jobs[0].job_id, timeout=5)
    assert job.status == J.DONE
    # --bg stripped from the task; the bg turn runs un-streamed (on_event is None).
    assert seen["task"] == "implement the parser"
    assert seen["on_event"] is None
    assert sess.turns == 1
    jobreg.shutdown(wait=True)


def test_cursor_on_off_toggles_mode(tmp_path, fake_argv):
    from xlii.repl_cmds.cursor import _cursor_handler

    st = _state(tmp_path)
    _cursor_handler("/cursor on", _ctx(st))
    assert st.harness_foreground == "cursor"
    assert "mode" in st.console.text.lower()
    _cursor_handler("/cursor off", _ctx(st))
    assert st.harness_foreground is None


def test_cursor_close(tmp_path):
    from xlii.repl_cmds.cursor import _cursor_handler

    st = _state(tmp_path)
    _cursor_handler("/cursor new build", _ctx(st))
    _cursor_handler("/cursor close build", _ctx(st))
    assert get_registry(st).get("build") is None


def test_cursor_oneshot_still_works_and_captures(tmp_path, monkeypatch):
    """A plain `/cursor <task>` is unchanged (one-shot) and now folds its output
    into B's capture seam by default (Tier 3)."""
    import xlii.shell_toolkit as shtk
    from xlii.repl_cmds.cursor import _cursor_handler

    monkeypatch.setattr(
        "xlii.harness.acp_session.resolve_acp_argv", lambda name: [sys.executable, FAKE]
    )
    seen = {}
    monkeypatch.setattr(
        shtk, "capture_output",
        lambda state, text, *, source, into_history=False: seen.update(source=source, text=text),
        raising=False,
    )
    st = _state(tmp_path)
    assert _cursor_handler("/cursor build a thing", _ctx(st)) is True
    assert "[cursor ·" in st.console.text  # the one-shot banner
    assert seen.get("source") == "harness:cursor"  # captured into B's seam


def test_cursor_swarm_dry_run(tmp_path):
    from xlii.repl_cmds.cursor import _cursor_handler

    st = _state(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text(
        "# P\n\n## Vector A1 — Input\nbody\n\n## Vector C — Harness\nbody\n"
    )
    _cursor_handler("/cursor swarm plan.md", _ctx(st))
    out = st.console.text
    assert "swarm plan" in out
    assert "A1" in out and "C" in out
    assert "dry run" in out
