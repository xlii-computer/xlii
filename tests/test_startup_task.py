"""startup-task — capture default + ``--auto`` trust-tier gate (D17).

The ``apply_startup_task`` tests run against a REAL ``REPLState`` over a real
``Agent``/``SessionState`` (the ``make_agent`` pattern), so the tier gate is
exercised through the true production attribute path
(``state.agent.session.trust_tier``) — never a monkeypatched shim.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from helpers import FakeConsole, make_agent, make_cfg
from xlii import tasks as T
from xlii.repl_state import REPLState
from xlii.session_boot import (
    STARTUP_MODE_AUTO,
    STARTUP_MODE_CAPTURE,
    StartupBinding,
    apply_startup_task,
    bind_startup_task,
    clear_startup_binding,
    load_startup_binding,
    mute_startup,
    note_startup_journal,
    pipeline_file_hash,
    resolve_startup_mode,
    save_startup_binding,
    startup_journal_line,
    startup_run_command,
    startup_show_lines,
)


@pytest.fixture
def startup_store(tmp_path, monkeypatch):
    path = tmp_path / "startup.json"
    monkeypatch.setattr("xlii.session_boot.STARTUP_BINDINGS_FILE", path)
    return path


def _project(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir(exist_ok=True)
    T.write_pipeline_toml(
        xli, "nightly",
        'name = "nightly"\n[[step]]\nrun = "printf ok"\n',
    )
    return SimpleNamespace(project_root=tmp_path, xli_dir=xli, name="p")


def _real_state(tmp_path, *, tier: str = "safe") -> REPLState:
    """A real ``REPLState`` whose tier lives where production reads it:
    ``state.agent.session.trust_tier`` (REPLState itself has NO trust_tier)."""
    agent = make_agent(tmp_path, cfg=make_cfg())
    agent.session.trust_tier = tier
    return REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                     cfg=agent.cfg, pool=agent.pool)


def test_binding_round_trip(startup_store, tmp_path):
    binding = StartupBinding(
        task="nightly", mode=STARTUP_MODE_AUTO, bound_hash="abc", tier="yolo",
    )
    save_startup_binding(tmp_path, binding)
    assert load_startup_binding(tmp_path) == binding
    clear_startup_binding(tmp_path)
    assert load_startup_binding(tmp_path) is None


@pytest.mark.parametrize(
    "session_tier,binding_tier,expected",
    [
        ("safe", "safe", STARTUP_MODE_AUTO),
        ("yolo", "safe", STARTUP_MODE_AUTO),
        ("yolo", "yolo", STARTUP_MODE_AUTO),
        ("safe", "yolo", STARTUP_MODE_CAPTURE),
        ("freeball", "yolo", STARTUP_MODE_AUTO),
    ],
)
def test_auto_tier_gate_matrix(session_tier, binding_tier, expected):
    binding = StartupBinding(task="nightly", mode=STARTUP_MODE_AUTO, tier=binding_tier)
    mode, _ = resolve_startup_mode(
        binding, session_trust_tier=session_tier, live_hash="same", pipeline_exists=True,
    )
    assert mode == expected


def test_hash_mismatch_downgrades_to_capture():
    binding = StartupBinding(
        task="nightly", mode=STARTUP_MODE_AUTO, bound_hash="old", tier="safe",
    )
    mode, notice = resolve_startup_mode(
        binding, session_trust_tier="safe", live_hash="new", pipeline_exists=True,
    )
    assert mode == STARTUP_MODE_CAPTURE
    assert "pipeline changed" in notice


def test_capture_default_seeds_pending_input(startup_store, tmp_path):
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(
        task="nightly", mode=STARTUP_MODE_CAPTURE, bound_hash=h,
    ))
    state = _real_state(tmp_path)
    result = apply_startup_task(state, project, console=FakeConsole(), interactive=True)
    assert result.action == "prefill"
    assert state.pending_input == startup_run_command("nightly")


def test_auto_always_prints_exact_line_before_run(startup_store, tmp_path):
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(
        task="nightly", mode=STARTUP_MODE_AUTO, bound_hash=h, tier="safe",
    ))
    con = FakeConsole()
    state = _real_state(tmp_path)
    result = apply_startup_task(state, project, console=con, interactive=True)
    assert result.action == "auto_run"
    assert con.lines[0] == startup_run_command("nightly")
    assert getattr(state, "pending_input", "") == ""


def test_auto_refused_when_real_session_is_safe(startup_store, tmp_path):
    # The D17 gate against the TRUE attribute path: a safe session must refuse
    # an elevated-tier auto binding (downgrade to capture).
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(
        task="nightly", mode=STARTUP_MODE_AUTO, bound_hash=h, tier="yolo",
    ))
    state = _real_state(tmp_path, tier="safe")
    assert state.agent.session.trust_tier == "safe"
    result = apply_startup_task(state, project, console=FakeConsole(), interactive=True)
    assert result.action == "downgrade"
    assert state.pending_input == startup_run_command("nightly")


@pytest.mark.parametrize("session_tier", ["yolo", "freeball"])
def test_auto_runs_elevated_task_when_real_session_tier_suffices(
    startup_store, tmp_path, session_tier,
):
    # The inverse of the refusal: an elevated session (tier set on the real
    # agent.session, as /yolo does) auto-runs a yolo-tier binding.
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(
        task="nightly", mode=STARTUP_MODE_AUTO, bound_hash=h, tier="yolo",
    ))
    con = FakeConsole()
    state = _real_state(tmp_path, tier=session_tier)
    result = apply_startup_task(state, project, console=con, interactive=True)
    assert result.action == "auto_run"
    assert con.lines[0] == startup_run_command("nightly")
    assert getattr(state, "pending_input", "") == ""


def test_unknown_tier_in_store_skips_boot_fail_soft(startup_store, tmp_path):
    # A corrupt tier string in startup.json must not crash session boot:
    # skip with one printed line, nothing seeded, nothing run.
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(
        task="nightly", mode=STARTUP_MODE_AUTO, bound_hash=h, tier="trusted",
    ))
    con = FakeConsole()
    state = _real_state(tmp_path)
    result = apply_startup_task(state, project, console=con, interactive=True)
    assert result.action == "none"
    assert "unknown trust tier" in con.text
    assert getattr(state, "pending_input", "") == ""


def test_malformed_bound_toml_skips_boot_fail_soft(startup_store, tmp_path):
    # A malformed bound .toml must not crash session boot either.
    project = _project(tmp_path)
    T.write_pipeline_toml(project.xli_dir, "nightly", "name = [broken\n")
    save_startup_binding(tmp_path, StartupBinding(task="nightly"))
    con = FakeConsole()
    state = _real_state(tmp_path)
    result = apply_startup_task(state, project, console=con, interactive=True)
    assert result.action == "none"
    assert "skipped" in con.text
    assert getattr(state, "pending_input", "") == ""


# --------------------------------------------------------------------------- #
# binding WRITE surface
# --------------------------------------------------------------------------- #


def test_bind_confirm_yes_writes_machine_store_not_repo(startup_store, tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    con = FakeConsole()
    assert bind_startup_task(project, "nightly", console=con) is True
    bound = load_startup_binding(tmp_path)
    assert bound is not None
    assert bound.task == "nightly"
    assert bound.mode == STARTUP_MODE_CAPTURE
    assert bound.bound_hash == pipeline_file_hash(project.xli_dir, "nightly")
    # Binding never travels with the clone.
    assert startup_store.is_file()
    proj_json = tmp_path / ".xlii" / "project.json"
    if proj_json.exists():
        assert "startup" not in proj_json.read_text()
    assert "startup bind snapshot" in con.text


def test_bind_confirm_no_writes_nothing(startup_store, tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "n")
    assert bind_startup_task(project, "nightly", console=FakeConsole()) is False
    assert load_startup_binding(tmp_path) is None


def test_bind_auto_deny_fails_closed(startup_store, tmp_path, monkeypatch):
    from xlii.tools import auto_deny

    project = _project(tmp_path)
    monkeypatch.setattr("xlii.tools._confirm", auto_deny)
    assert bind_startup_task(project, "nightly", console=FakeConsole()) is False
    assert load_startup_binding(tmp_path) is None


def test_bind_capturing_console_fails_closed(startup_store, tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    con = FakeConsole()
    con.xlii_foreground = False
    assert bind_startup_task(project, "nightly", console=con) is False
    assert load_startup_binding(tmp_path) is None
    assert "foreground confirm" in con.text


def test_bind_missing_pipeline_refused(startup_store, tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    con = FakeConsole()
    assert bind_startup_task(project, "ghost", console=con) is False
    assert load_startup_binding(tmp_path) is None
    assert "no saved pipeline" in con.text


def test_bind_auto_refused_when_unelevated(startup_store, tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    con = FakeConsole()
    assert bind_startup_task(project, "nightly", console=con, auto=True, elevated=False) is False
    assert load_startup_binding(tmp_path) is None
    assert "admin" in con.text


def test_bind_auto_elevated_records_tier(startup_store, tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    assert bind_startup_task(
        project, "nightly", console=FakeConsole(),
        auto=True, elevated=True, tier="yolo",
    ) is True
    bound = load_startup_binding(tmp_path)
    assert bound is not None
    assert bound.mode == STARTUP_MODE_AUTO
    assert bound.tier == "yolo"


def test_show_flags_missing_and_drift(startup_store, tmp_path):
    project = _project(tmp_path)
    save_startup_binding(tmp_path, StartupBinding(task="ghost"))
    lines = "\n".join(startup_show_lines(project))
    assert "ghost" in lines
    assert "missing" in lines

    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(
        task="nightly", bound_hash="stale-hash",
    ))
    lines = "\n".join(startup_show_lines(project))
    assert "changed since binding" in lines
    assert h not in lines or "stale-hash" in lines


def test_clear_and_off_and_no_startup_guard(startup_store, tmp_path):
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(task="nightly", bound_hash=h))
    clear_startup_binding(tmp_path)
    assert load_startup_binding(tmp_path) is None

    save_startup_binding(tmp_path, StartupBinding(task="nightly", bound_hash=h))
    state = _real_state(tmp_path)
    mute_startup(state)
    assert state.startup_off is True
    result = apply_startup_task(state, project, console=FakeConsole(), interactive=True)
    assert result.action == "none"
    assert state.pending_input == ""

    state.startup_off = False
    result = apply_startup_task(
        state, project, console=FakeConsole(), interactive=True, no_startup=True,
    )
    assert result.action == "none"
    assert state.pending_input == ""


def test_startup_off_is_not_persisted(startup_store, tmp_path):
    state = _real_state(tmp_path)
    state.startup_off = True
    state.pending_input = "/tasks run nightly"
    state.save()
    raw = (tmp_path / ".xlii" / "session.json").read_text()
    assert "startup_off" not in raw
    assert "pending_input" not in raw
    other = _real_state(tmp_path)
    other.load()
    assert other.startup_off is False
    assert other.pending_input == ""


def test_yield_and_hint_and_inflight_skip_seed(startup_store, tmp_path):
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(task="nightly", bound_hash=h))

    state = _real_state(tmp_path)
    state.pending_input = "queued"
    con = FakeConsole()
    result = apply_startup_task(state, project, console=con, interactive=True)
    assert result.action == "none"
    assert state.pending_input == "queued"
    assert "queued input" in con.text
    assert tmp_path.resolve() not in getattr(state, "_startup_fired_roots", set())

    state.pending_input = ""
    result = apply_startup_task(state, project, console=FakeConsole(), interactive=True)
    assert result.action == "prefill"

    state2 = _real_state(tmp_path)
    state2.launch_hint = True
    con = FakeConsole()
    result = apply_startup_task(state2, project, console=con, interactive=True)
    assert result.action == "none"
    assert state2.pending_input == ""
    assert state2.launch_hint is False
    assert "competing launch hint" in con.text

    from xlii.tasks import engine as task_engine

    state3 = _real_state(tmp_path)
    task_engine._PIPELINE_DEPTH.n = 1
    try:
        result = apply_startup_task(state3, project, console=FakeConsole(), interactive=True)
        assert result.action == "none"
        assert state3.pending_input == ""
    finally:
        task_engine._PIPELINE_DEPTH.n = 0


def test_chat_scope_does_not_seed(startup_store, tmp_path):
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(task="nightly", bound_hash=h))
    state = _real_state(tmp_path)
    state.command_scope = "chat"
    result = apply_startup_task(state, project, console=FakeConsole(), interactive=True)
    assert result.action == "none"
    assert state.pending_input == ""


def test_capture_drift_notice_still_prefills(startup_store, tmp_path):
    project = _project(tmp_path)
    save_startup_binding(tmp_path, StartupBinding(
        task="nightly", mode=STARTUP_MODE_CAPTURE, bound_hash="old",
    ))
    con = FakeConsole()
    state = _real_state(tmp_path)
    result = apply_startup_task(state, project, console=con, interactive=True)
    assert result.action == "prefill"
    assert "pipeline changed" in con.text
    assert state.pending_input == startup_run_command("nightly")


def test_switch_fires_once_per_root(startup_store, tmp_path, monkeypatch):
    from xlii.config import ProjectConfig
    from xlii.profile import code_profile
    from xlii.repl_cmds.switch import switch_to_code_project

    a = tmp_path / "a"
    b = tmp_path / "b"
    for root, name in ((a, "a"), (b, "b")):
        (root / ".xlii").mkdir(parents=True)
        (root / ".xlii" / "project.json").write_text(
            '{"name": "%s", "root": "%s", "collection_id": "", '
            '"created_at": "2026-01-01T00:00:00Z", "conversation_id": "c", '
            '"local_only": true}' % (name, root.resolve())
        )
        T.write_pipeline_toml(
            root / ".xlii", "nightly",
            'name = "nightly"\n[[step]]\nrun = "printf ok"\n',
        )
        save_startup_binding(root, StartupBinding(
            task="nightly",
            bound_hash=pipeline_file_hash(root / ".xlii", "nightly"),
        ))
    pa = ProjectConfig.load(a)
    pb = ProjectConfig.load(b)
    assert pa is not None and pb is not None

    agent = make_agent(a, cfg=make_cfg())
    agent.session.trust_tier = "safe"
    agent.history = [{"role": "system", "content": "s"}]
    state = REPLState(
        console=FakeConsole(), agent=agent, project=pa,
        cfg=agent.cfg, pool=agent.pool,
    )
    state.profile = code_profile(pa, seed_limit=20)
    state.command_scope = "code"
    state.shell_cwd = a.resolve()

    monkeypatch.setattr("xlii.commands.reload_project_commands", lambda xli_dir: 0)
    monkeypatch.setattr("xlii.tools.load_project_tools", lambda xli_dir: None)

    apply_startup_task(state, pa, console=FakeConsole(), interactive=True)
    assert state.pending_input == startup_run_command("nightly")
    state.pending_input = ""  # consumed at the prompt

    switch_to_code_project(state.as_context_dict(), pb)
    assert state.pending_input == startup_run_command("nightly")
    state.pending_input = ""

    switch_to_code_project(state.as_context_dict(), pa)
    assert state.pending_input == ""  # A already fired this session
    switch_to_code_project(state.as_context_dict(), pb)
    assert state.pending_input == ""  # B already fired


def test_suppressed_fire_retries_on_next_entry(startup_store, tmp_path):
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(task="nightly", bound_hash=h))
    state = _real_state(tmp_path)
    state.pending_input = "howto"
    apply_startup_task(state, project, console=FakeConsole(), interactive=True)
    assert tmp_path.resolve() not in getattr(state, "_startup_fired_roots", set())
    state.pending_input = ""
    result = apply_startup_task(state, project, console=FakeConsole(), interactive=True)
    assert result.action == "prefill"


def test_code_cli_exposes_no_startup():
    from xlii.cli import build_parser

    ns = build_parser().parse_args(["code", "--no-startup"])
    assert ns.no_startup is True
    ns2 = build_parser().parse_args(["code"])
    assert ns2.no_startup is False


def test_queue_pending_input_replace_and_append():
    from xlii.repl_state import queue_pending_input

    st = SimpleNamespace(pending_input="")
    queue_pending_input(st, "a.py")
    assert st.pending_input == "a.py"
    queue_pending_input(st, "b.py")
    assert st.pending_input == "a.py b.py"
    queue_pending_input(st, "/tasks run x", replace=True)
    assert st.pending_input == "/tasks run x"
    queue_pending_input(st, "  ")
    assert st.pending_input == "/tasks run x"
    queue_pending_input(None, "x")  # no-op


# --------------------------------------------------------------------------- #
# P2 — journal notes of binds / fires / skips
# --------------------------------------------------------------------------- #


def _recording_journal(project):
    from xlii.journal import ProjectJournal

    return ProjectJournal(project=project, code_on=True, batch_size=50)


def _journal_goals(journal) -> list[str]:
    if not journal.entries_dir.is_dir():
        return []
    goals = []
    for path in sorted(journal.entries_dir.glob("entry-*.md")):
        for line in path.read_text().splitlines():
            if line.startswith("**goal:** "):
                goals.append(line[len("**goal:** "):])
    return goals


def test_startup_journal_line_shape():
    assert startup_journal_line(
        "bind", "nightly", who="ada", mode="confirm",
    ) == "startup bind nightly by ada (confirm)"
    assert startup_journal_line(
        "fire", "nightly", who="ada", mode="auto",
    ) == "startup fire nightly by ada (auto)"
    assert startup_journal_line(
        "skip", "nightly", who="ada", reason="queued input",
    ) == "startup skip nightly by ada — queued input"
    assert startup_journal_line("mute", "", who="ada") == "startup mute by ada"


def test_bind_writes_journal_note(startup_store, tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    state = _real_state(tmp_path)
    state.journal = _recording_journal(project)
    assert bind_startup_task(project, "nightly", console=FakeConsole(), state=state) is True
    goals = _journal_goals(state.journal)
    assert len(goals) == 1
    assert "startup bind nightly" in goals[0]
    assert "(confirm)" in goals[0]
    assert "by " in goals[0]


def test_bind_auto_journal_note_uses_auto_mode(startup_store, tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    state = _real_state(tmp_path)
    state.journal = _recording_journal(project)
    assert bind_startup_task(
        project, "nightly", console=FakeConsole(),
        auto=True, elevated=True, tier="yolo", state=state,
    ) is True
    goals = _journal_goals(state.journal)
    assert len(goals) == 1
    assert "startup bind nightly" in goals[0]
    assert "(auto)" in goals[0]


def test_fire_writes_journal_note(startup_store, tmp_path):
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(
        task="nightly", mode=STARTUP_MODE_CAPTURE, bound_hash=h,
    ))
    state = _real_state(tmp_path)
    state.journal = _recording_journal(project)
    result = apply_startup_task(state, project, console=FakeConsole(), interactive=True)
    assert result.action == "prefill"
    goals = _journal_goals(state.journal)
    assert len(goals) == 1
    assert "startup fire nightly" in goals[0]
    assert "(confirm)" in goals[0]


def test_auto_fire_writes_journal_note(startup_store, tmp_path):
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(
        task="nightly", mode=STARTUP_MODE_AUTO, bound_hash=h, tier="safe",
    ))
    state = _real_state(tmp_path)
    state.journal = _recording_journal(project)
    result = apply_startup_task(state, project, console=FakeConsole(), interactive=True)
    assert result.action == "auto_run"
    goals = _journal_goals(state.journal)
    assert len(goals) == 1
    assert "startup fire nightly" in goals[0]
    assert "(auto)" in goals[0]


def test_clear_writes_journal_note(startup_store, tmp_path):
    project = _project(tmp_path)
    save_startup_binding(tmp_path, StartupBinding(task="nightly"))
    state = _real_state(tmp_path)
    state.journal = _recording_journal(project)
    clear_startup_binding(tmp_path, state=state)
    goals = _journal_goals(state.journal)
    assert len(goals) == 1
    assert "startup clear nightly" in goals[0]


def test_mute_writes_journal_note(startup_store, tmp_path):
    project = _project(tmp_path)
    save_startup_binding(tmp_path, StartupBinding(task="nightly"))
    state = _real_state(tmp_path)
    state.journal = _recording_journal(project)
    mute_startup(state)
    goals = _journal_goals(state.journal)
    assert len(goals) == 1
    assert "startup mute nightly" in goals[0]


def test_skip_queued_input_writes_journal_note(startup_store, tmp_path):
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(task="nightly", bound_hash=h))
    state = _real_state(tmp_path)
    state.journal = _recording_journal(project)
    state.pending_input = "queued"
    result = apply_startup_task(state, project, console=FakeConsole(), interactive=True)
    assert result.action == "none"
    goals = _journal_goals(state.journal)
    assert len(goals) == 1
    assert "startup skip nightly" in goals[0]
    assert "queued input" in goals[0]


def test_no_startup_skip_writes_journal_note(startup_store, tmp_path):
    project = _project(tmp_path)
    h = pipeline_file_hash(project.xli_dir, "nightly")
    save_startup_binding(tmp_path, StartupBinding(task="nightly", bound_hash=h))
    state = _real_state(tmp_path)
    state.journal = _recording_journal(project)
    result = apply_startup_task(
        state, project, console=FakeConsole(), interactive=True, no_startup=True,
    )
    assert result.action == "none"
    goals = _journal_goals(state.journal)
    assert len(goals) == 1
    assert "startup skip nightly" in goals[0]
    assert "--no-startup" in goals[0]


def test_journal_unavailable_does_not_block_bind(startup_store, tmp_path, monkeypatch):
    project = _project(tmp_path)
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    state = _real_state(tmp_path)

    class Boom:
        def is_recording(self):
            raise RuntimeError("journal down")

    state.journal = Boom()
    assert bind_startup_task(project, "nightly", console=FakeConsole(), state=state) is True
    assert load_startup_binding(tmp_path) is not None


def test_journal_off_is_a_noop_on_bind(startup_store, tmp_path, monkeypatch):
    from xlii.journal import ProjectJournal

    project = _project(tmp_path)
    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    state = _real_state(tmp_path)
    state.journal = ProjectJournal(project=project, code_on=False, batch_size=50)
    assert bind_startup_task(project, "nightly", console=FakeConsole(), state=state) is True
    assert load_startup_binding(tmp_path) is not None
    assert _journal_goals(state.journal) == []


def test_note_startup_journal_swallows_observe_failures():
    class Boom:
        def is_recording(self):
            return True

        def observe_turn(self, *a, **k):
            raise RuntimeError("disk full")

    note_startup_journal(SimpleNamespace(journal=Boom()), "startup bind nightly by ada (confirm)")

