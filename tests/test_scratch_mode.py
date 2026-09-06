"""Vector S (interaction-III) — scratch mode: project-less · no-sync · free-traversal.

Scratch is an ephemeral, unbound, never-sync session surfaced as its own mode.
These pin the vector's seams:

  * status readers (xlii.tui.status): the `scratch` lead, the `scratch · no-sync`
    marker (reads the never-sync flag), the frame-mode tone, placeholder_key.
  * the input hint (xlii.hints): a built-in `scratch` mode contract.
  * the `/scratch` REPL command: enter/exit, forcing no-sync and restoring the
    prior setting on the way out.
  * REPLState: the `no_sync`/`scratch` fields + the process-local handoff a
    spawned `xlii scratch` session uses to start in the mode.
  * `xlii scratch` CLI routing (cmds/project.py): bare → from home, `here` →
    local .xlii, NAME → home-store, all never-sync.

Pure functions over fakes — no textual, no prompt_toolkit, no network.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import xlii.cmds.project as project_mod
import xlii.cmds.project.scratch as scratch_mod
import xlii.repl_state as repl_state
from xlii import hints
from xlii.commands import dispatch_repl_command
from xlii.repl_cmds import register_all
from xlii.repl_state import REPLState
from xlii.tui import status

from tests.helpers import FakeConsole
from tests.test_rail import _bare_agent

register_all()  # built-in slash commands are registered explicitly, not on import


# --------------------------------------------------------------------------- #
#  status readers — the mode lead, the never-sync marker, the frame tone
# --------------------------------------------------------------------------- #

def _st(**kw):
    base = dict(
        shell_cwd=None,
        project=SimpleNamespace(name="proj", project_root=None),
        plan_mode=False, persona=None, yolo=False,
        agent=SimpleNamespace(active_mode=None, rail=None),
        attached_refs=[], attached_docs=[],
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_mode_scratch():
    assert status.mode(_st(scratch=True)) == ("SCRATCH", "SCRATCH")


def test_mode_scratch_overlaid_by_howto():
    # /howto is an overlay above the base scratch surface — the active intent wins.
    assert status.mode(_st(scratch=True, howto_mode=True)) == ("HOWTO", "HOWTO")


def test_profile_bar_scratch_leads_with_no_sync():
    # scratch leads; trust chip is always present; never-sync marker rides after.
    assert status.profile_bar(_st(scratch=True, no_sync=True)).plain == \
        "scratch · safe · no-sync · jrnl○  ·  proj"


def test_profile_bar_no_sync_marker_on_plain_code():
    # The marker reads the never-sync flag, so a `--no-sync` project also shows it.
    assert status.profile_bar(_st(no_sync=True)).plain == "code · safe · no-sync · jrnl○  ·  proj"


def test_profile_bar_lean_when_syncing():
    # default (no_sync=False) → no marker; trust chip still present (Track G0).
    assert status.profile_bar(_st()).plain == "code · safe · jrnl○  ·  proj"


def test_frame_mode_scratch_is_tan_hex():
    label, color = status.frame_mode(_st(scratch=True))
    assert label == "scratch"
    assert color == "#d19a66"
    # the frame feeds the bare token straight into the Textual border, so it must
    # parse as a Textual color (same contract /howto's hex blue satisfies).
    from textual.color import Color
    assert Color.parse(color)


def test_frame_mode_scratch_token_matches_mode_rich_palette():
    _, color = status.frame_mode(_st(scratch=True))
    assert status._MODE_RICH["SCRATCH"].endswith(color)


def test_placeholder_key_scratch():
    assert status.placeholder_key(_st(scratch=True)) == "scratch"


def test_placeholder_key_howto_overrides_scratch():
    assert status.placeholder_key(_st(scratch=True, howto_mode=True)) == "howto"


def test_profile_bar_renders_without_jobs_module():
    # The J4 co-touch line is guarded — with xlii.jobs unmerged the bar still
    # renders cleanly (no job segment appended).
    assert "fleet" not in status.profile_bar(_st(scratch=True, no_sync=True)).plain


# --------------------------------------------------------------------------- #
#  input hint (seam #4) — scratch is a built-in mode contract
# --------------------------------------------------------------------------- #

def _hst(**kw):
    base = dict(
        agent=SimpleNamespace(active_mode=None, rail=None),
        project=SimpleNamespace(name="proj", project_root=None),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_scratch_is_a_builtin_hint():
    assert "scratch" in hints.BUILTIN_HINTS
    assert hints.mode_hint("scratch") == hints.BUILTIN_HINTS["scratch"]


def test_resolve_hint_scratch_mode():
    assert hints.resolve_hint(_hst(scratch=True)) == hints.BUILTIN_HINTS["scratch"]


# --------------------------------------------------------------------------- #
#  /scratch command — enter/exit; scratch is inherently never-sync
# --------------------------------------------------------------------------- #

def _make_state(tmp_path):
    agent = _bare_agent()
    agent.history = [{"role": "system", "content": "s"}]
    xli = tmp_path / ".xlii"
    xli.mkdir(exist_ok=True)
    return REPLState(
        console=FakeConsole(),
        agent=agent,
        project=SimpleNamespace(
            project_root=tmp_path, xli_dir=xli, local_only=True, name="proj"
        ),
        cfg=SimpleNamespace(orchestrator_temp=lambda: 0.7),
        pool=[],
    )


def test_scratch_command_enters_and_exits(tmp_path):
    st = _make_state(tmp_path)
    assert st.scratch is False and st.no_sync is False

    dispatch_repl_command("/scratch", st.as_context_dict())
    assert st.scratch is True
    assert st.no_sync is True  # scratch is inherently never-sync (locked Q3)

    dispatch_repl_command("/scratch off", st.as_context_dict())
    assert st.scratch is False
    assert st.no_sync is False


def test_scratch_off_restores_prior_no_sync(tmp_path):
    # A --no-sync project that toggled scratch must not start syncing on exit.
    st = _make_state(tmp_path)
    st.no_sync = True

    dispatch_repl_command("/scratch", st.as_context_dict())
    assert st.scratch is True and st.no_sync is True

    dispatch_repl_command("/scratch off", st.as_context_dict())
    assert st.scratch is False
    assert st.no_sync is True  # restored, not blindly cleared


def test_scratch_status_reports(tmp_path):
    st = _make_state(tmp_path)
    dispatch_repl_command("/scratch", st.as_context_dict())
    st.console.lines.clear()
    dispatch_repl_command("/scratch status", st.as_context_dict())
    assert any("scratch mode" in ln and "ON" in ln for ln in st.console.lines)


# --------------------------------------------------------------------------- #
#  REPLState fields + the spawned-session handoff
# --------------------------------------------------------------------------- #

def test_replstate_no_sync_scratch_default_false(tmp_path):
    st = _make_state(tmp_path)
    assert st.no_sync is False
    assert st.scratch is False


def test_pending_scratch_handoff_consumed_once(tmp_path):
    repl_state._PENDING_SCRATCH = True
    try:
        st1 = _make_state(tmp_path)
        assert st1.scratch is True  # the spawned session starts in scratch mode
        st2 = _make_state(tmp_path)
        assert st2.scratch is False  # one-shot — never leaks to the next session
    finally:
        repl_state._PENDING_SCRATCH = False


# --------------------------------------------------------------------------- #
#  `xlii scratch` CLI routing — bare (home) · here (local) · NAME (home-store)
# --------------------------------------------------------------------------- #

def _scratch_args(name=None, no_chat=False, yolo=False, force=False, tui=False):
    return argparse.Namespace(name=name, no_chat=no_chat, yolo=yolo, force=force, tui=tui)


def _capture_cmd_code(monkeypatch):
    """Capture the typed B4 seam (the convention that replaced the fabricated
    cmd_code Namespace): the kwargs scratch passes to
    ``session_boot.build_code_session`` and the ``tui`` flag reaching the code
    façade's ``run_code_session``."""
    captured = {}
    import xlii.session_boot as session_boot
    from xlii.cmds.sessions import code as code_mod

    def fake_build(root, **kwargs):
        captured["root"] = root
        captured["kwargs"] = kwargs
        return session_boot.BootOutcome(
            "ok", session=SimpleNamespace(preview_state_dir=None)
        )

    def fake_run(cs, *, tui=False):
        captured["tui"] = tui
        return 0

    monkeypatch.setattr(session_boot, "build_code_session", fake_build)
    monkeypatch.setattr(code_mod, "run_code_session", fake_run)
    return captured


def test_cmd_scratch_bare_launches_ephemeral_from_home(monkeypatch):
    captured = _capture_cmd_code(monkeypatch)
    rc = project_mod.cmd_scratch(_scratch_args(name=None))
    assert rc == 0
    assert captured["root"] == Path(str(Path.home()))
    assert captured["kwargs"]["preview"] is True  # ephemeral: no .xlii under ~, temp state dir
    assert captured["kwargs"]["launch"] is False
    assert captured["kwargs"]["no_sync"] is True
    assert captured["kwargs"]["scratch"] is True  # the session is seeded into scratch mode
    assert repl_state._PENDING_SCRATCH is False  # no stale handoff after launch


def test_cmd_scratch_here_inits_local_and_launches_no_sync(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scratch_mod.ProjectConfig, "load", lambda root: None)
    init_calls = []

    def fake_init(clients, root, *, name=None, local_only=False, **kw):
        init_calls.append({"root": Path(root), "name": name, "local_only": local_only})
        return SimpleNamespace(name=name, project_root=Path(root), local_only=local_only)

    monkeypatch.setattr(scratch_mod, "init_project", fake_init)
    captured = _capture_cmd_code(monkeypatch)

    rc = project_mod.cmd_scratch(_scratch_args(name="here"))
    assert rc == 0
    assert init_calls and init_calls[0]["local_only"] is True
    assert init_calls[0]["name"].startswith("scratch/")
    assert init_calls[0]["root"] == tmp_path.resolve()
    assert captured["root"] == tmp_path.resolve()
    assert captured["kwargs"]["preview"] is False
    assert captured["kwargs"]["launch"] is True
    assert captured["kwargs"]["no_sync"] is True
    assert captured["kwargs"]["scratch"] is True


def test_cmd_scratch_here_refuses_synced_project(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        scratch_mod.ProjectConfig, "load",
        lambda root: SimpleNamespace(name="real", local_only=False),
    )
    called = {"init": False, "code": False}
    monkeypatch.setattr(
        scratch_mod, "init_project",
        lambda *a, **k: called.__setitem__("init", True),
    )
    import xlii.session_boot as session_boot
    monkeypatch.setattr(
        session_boot, "build_code_session", lambda *a, **k: called.__setitem__("code", True)
    )

    rc = project_mod.cmd_scratch(_scratch_args(name="here"))
    assert rc == 1  # refused — never shadow a real synced project
    assert called["init"] is False
    assert called["code"] is False


def test_cmd_scratch_named_no_chat_creates_without_launch(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(scratch_mod.ProjectConfig, "load", lambda root: None)
    init_calls = []

    def fake_init(clients, root, *, name=None, local_only=False, snapshot=False, **kw):
        init_calls.append({"name": name, "local_only": local_only, "root": Path(root)})
        return SimpleNamespace(name=name, project_root=Path(root), local_only=local_only)

    monkeypatch.setattr(scratch_mod, "init_project", fake_init)
    called = {"code": False}
    import xlii.session_boot as session_boot
    monkeypatch.setattr(
        session_boot, "build_code_session", lambda *a, **k: called.__setitem__("code", True)
    )

    rc = project_mod.cmd_scratch(_scratch_args(name="foo", no_chat=True))
    assert rc == 0
    assert called["code"] is False  # --no-chat: created, not entered
    assert init_calls and init_calls[0]["name"] == "scratch/foo"
    assert init_calls[0]["local_only"] is True
    assert (tmp_path / ".xlii" / "scratch" / "foo").is_dir()


def test_cmd_scratch_named_force_warns_before_reinit(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        scratch_mod.ProjectConfig, "load",
        lambda root: SimpleNamespace(name="scratch/foo", local_only=True),
    )
    monkeypatch.setattr(
        scratch_mod, "init_project",
        lambda *a, **k: SimpleNamespace(name="scratch/foo", project_root=Path.home(), local_only=True),
    )
    printed = []
    monkeypatch.setattr(scratch_mod.console, "print", lambda msg: printed.append(str(msg)))

    rc = project_mod.cmd_scratch(_scratch_args(name="foo", no_chat=True, force=True))
    assert rc == 0
    assert any("--force set:" in line for line in printed)


def test_cmd_scratch_tui_flag_reaches_cmd_code(monkeypatch):
    """`xlii scratch --tui` parity with `xlii code --tui`: the flag must flow
    through to the code run tail instead of being hardcoded False."""
    captured = _capture_cmd_code(monkeypatch)
    rc = project_mod.cmd_scratch(_scratch_args(name=None, tui=True))
    assert rc == 0
    assert captured["tui"] is True


def test_cmd_scratch_defaults_to_inline_repl(monkeypatch):
    captured = _capture_cmd_code(monkeypatch)
    rc = project_mod.cmd_scratch(_scratch_args(name=None))
    assert rc == 0
    assert captured["tui"] is False


def test_scratch_parser_accepts_tui_flag():
    """The CLI actually registers --tui for scratch (all three forms take it)."""
    from xlii.cli import build_parser
    args = build_parser().parse_args(["scratch", "--tui"])
    assert args.tui is True
    args = build_parser().parse_args(["scratch", "here", "--tui"])
    assert args.tui is True and args.name == "here"
