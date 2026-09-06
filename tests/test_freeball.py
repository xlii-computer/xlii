"""Freeball — the trusted-run tier above /yolo (the-fold Vector C).

Covers the whole surface the vector owns:
  * the three /yolo forms (plain · --freeball toggle · --freeball <task> one-shot);
  * the one-shot restore in run_turn's finally (reverts after the turn, even on
    error) — driven through a real Agent with a scripted model;
  * the policy seam (tool_context.shell_command_needs_confirm) opening under the tier;
  * non-persistence (a fresh session always starts safe);
  * loud status (the filled-red FREEBALL pill wins over plain yolo);
  * the rails that NEVER drop under freeball — /budget, the /admin capability gate,
    and the sync delete-guard.

Hermetic: no network. Session json is pinned to tmp_path.

Run directly:  ./venv/bin/python -m pytest tests/test_freeball.py
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tests.helpers import FakeConsole, make_agent, script_iterations
from xlii.repl_cmds.session import h_approve, h_safe, h_yolo
from xlii.session_state import TIER_FREEBALL, TIER_SAFE, TIER_YOLO, TrustState
from xlii.shellgate import NETWORK
from xlii.tool_context import (
    INTENT_MODIFIES_SYSTEM,
    INTENT_NETWORK,
    INTENT_READ_ONLY,
    shell_command_needs_confirm,
)


def _handler_agent(**over):
    """Minimal agent stand-in for the /yolo·/safe·/approve handlers (mirrors the
    SimpleNamespace style in test_auto_approve). yolo/freeball are plain attrs so
    the handler's Agent-property writes land somewhere assertable, and `session`
    carries the one-shot ticket."""
    base = dict(yolo=False, freeball=False, auto_approve=set())
    base.update(over)
    return SimpleNamespace(session=SimpleNamespace(freeball_restore=None), **base)


# --------------------------------------------------------------------------- #
#  The three /yolo forms
# --------------------------------------------------------------------------- #

def test_plain_yolo_sets_yolo_not_freeball():
    agent = _handler_agent()
    ctx = {"agent": agent, "console": FakeConsole()}
    assert h_yolo("/yolo", ctx) is True
    assert agent.yolo is True
    assert agent.freeball is False
    assert "_freeball_rewritten" not in ctx  # no turn to run


def test_freeball_toggle_sets_both_no_ticket():
    agent = _handler_agent()
    ctx = {"agent": agent, "console": FakeConsole()}
    assert h_yolo("/yolo --freeball", ctx) is True
    assert agent.yolo is True                       # composite superset of yolo
    assert agent.freeball is True
    assert agent.session.freeball_restore is None   # toggle: persists until /safe
    assert "_freeball_rewritten" not in ctx         # toggle runs no turn itself


def test_freeball_oneshot_arms_restore_and_rewrites():
    agent = _handler_agent()  # prior tier: safe (yolo False, freeball False)
    ctx = {"agent": agent, "console": FakeConsole()}
    assert h_yolo("/yolo --freeball fix the flaky test", ctx) is True
    assert agent.yolo is True
    assert agent.freeball is True
    assert agent.session.freeball_restore == TIER_SAFE        # prior tier stashed
    assert ctx["_freeball_rewritten"] == "fix the flaky test"  # <task> becomes a turn


def test_freeball_oneshot_restores_to_prior_yolo_not_safe():
    # A one-shot fired from an already-yolo session must revert to plain yolo,
    # not all the way to safe.
    agent = _handler_agent(yolo=True)
    ctx = {"agent": agent, "console": FakeConsole()}
    h_yolo("/yolo --freeball ship it", ctx)
    assert agent.session.freeball_restore == TIER_YOLO


def test_safe_stands_down_freeball_and_ticket():
    agent = _handler_agent(yolo=True, freeball=True, auto_approve={NETWORK})
    agent.session.freeball_restore = TIER_SAFE
    ctx = {"agent": agent, "console": FakeConsole()}
    assert h_safe("/safe", ctx) is True
    assert agent.yolo is False
    assert agent.freeball is False
    assert agent.session.freeball_restore is None   # pending one-shot cannot resurrect
    assert agent.auto_approve == set()


def test_approve_display_names_freeball():
    agent = _handler_agent(yolo=True, freeball=True)
    console = FakeConsole()
    h_approve("/approve", {"agent": agent, "console": console})
    assert "freeball is ON" in console.text


def test_oneshot_routes_task_as_turn_end_to_end(tmp_path):
    # The full wiring: `/yolo --freeball <task>` dispatches h_yolo (sets the tier,
    # arms the ticket, plants the rewrite marker); process_repl_input pops the
    # marker and returns <task> to be run as an agent turn (both surfaces share it).
    from xlii.repl import process_repl_input
    from xlii.repl_cmds import register_all
    from xlii.repl_state import REPLState

    register_all()
    agent = make_agent(tmp_path)
    xli = tmp_path / ".xlii"
    xli.mkdir(exist_ok=True)
    proj = SimpleNamespace(
        project_root=tmp_path, xli_dir=xli, local_only=True, name="proj"
    )
    st = REPLState(
        console=FakeConsole(), agent=agent, project=proj,
        cfg=SimpleNamespace(), pool=[],
    )
    st.shell_cwd = tmp_path.resolve()

    out = process_repl_input(st, "/yolo --freeball fix the flaky test")
    assert out == ("fix the flaky test", False)          # <task> becomes a turn
    assert agent.session.freeball is True                # tier live for that turn
    assert agent.session.yolo is True
    assert agent.session.freeball_restore == TIER_SAFE   # armed to revert after


# --------------------------------------------------------------------------- #
#  Policy seam — tool_context.shell_command_needs_confirm
# --------------------------------------------------------------------------- #

def test_policy_seam_freeball_tier_opens_gate():
    # freeball ⇒ yolo is structural now: TrustState derives yolo from the one
    # tier slot, so the gate (which receives yolo) opens under freeball with no
    # hand-wired coupling — the incoherent yolo=False/freeball=True state the
    # old boolean pair allowed cannot be represented.
    trust = TrustState(tier=TIER_FREEBALL)
    assert trust.yolo is True and trust.freeball is True
    assert shell_command_needs_confirm(
        INTENT_MODIFIES_SYSTEM, yolo=trust.yolo, auto_approve=set()
    ) is True
    assert shell_command_needs_confirm(
        INTENT_NETWORK, yolo=trust.yolo, auto_approve=set()
    ) is False


def test_trust_state_six_tier_setter_transitions():
    """All six setter transitions on the single tier slot (PR #188 fast-follow)."""
    t = TrustState()
    assert t.tier == TIER_SAFE

    # 1. safe → yolo=True → YOLO
    t.yolo = True
    assert t.tier == TIER_YOLO and t.yolo and not t.freeball

    # 2. yolo → yolo=False → SAFE
    t.yolo = False
    assert t.tier == TIER_SAFE and not t.yolo and not t.freeball

    # 3. safe → freeball=True → FREEBALL (implies yolo)
    t.freeball = True
    assert t.tier == TIER_FREEBALL and t.yolo and t.freeball

    # 4. freeball → freeball=False → YOLO (not SAFE)
    t.freeball = False
    assert t.tier == TIER_YOLO and t.yolo and not t.freeball

    # 5. freeball → yolo=False stands freeball down to SAFE
    t.freeball = True
    t.yolo = False
    assert t.tier == TIER_SAFE and not t.yolo and not t.freeball

    # 6. freeball → yolo=True never downgrades a live freeball
    t.freeball = True
    t.yolo = True
    assert t.tier == TIER_FREEBALL and t.yolo and t.freeball


def test_policy_seam_composite_rides_yolo():
    # Yolo (derived from freeball too) still skips *network*; system stays gated.
    assert shell_command_needs_confirm(
        INTENT_NETWORK, yolo=True, auto_approve=set()
    ) is False
    assert shell_command_needs_confirm(
        INTENT_MODIFIES_SYSTEM, yolo=True, auto_approve=set()
    ) is True


def test_policy_seam_safe_still_gates():
    # Neither tier active → modifies-system / network still confirm; read-only doesn't.
    assert shell_command_needs_confirm(
        INTENT_MODIFIES_SYSTEM, yolo=False, auto_approve=set()
    ) is True
    assert shell_command_needs_confirm(
        INTENT_NETWORK, yolo=False, auto_approve=set()
    ) is True
    assert shell_command_needs_confirm(
        INTENT_READ_ONLY, yolo=False, auto_approve=set()
    ) is False


# --------------------------------------------------------------------------- #
#  One-shot restore — driven through a real run_turn (scripted model, no network)
# --------------------------------------------------------------------------- #

def _arm_oneshot(agent, prior):
    """Put the agent in the state the one-shot handler leaves before the turn."""
    agent.session.freeball_restore = prior
    agent.session.yolo = True
    agent.session.freeball = True


def test_oneshot_reverts_after_turn(tmp_path):
    agent = make_agent(tmp_path)
    _arm_oneshot(agent, prior=TIER_SAFE)
    script_iterations(agent, ("done", None))           # terminate, no tools
    agent.run_turn("the proven task")
    assert agent.session.yolo is False
    assert agent.session.freeball is False
    assert agent.session.freeball_restore is None


def test_oneshot_reverts_to_prior_yolo(tmp_path):
    agent = make_agent(tmp_path)
    _arm_oneshot(agent, prior=TIER_YOLO)
    script_iterations(agent, ("done", None))
    agent.run_turn("go")
    assert agent.session.yolo is True                  # back to plain yolo, not safe
    assert agent.session.freeball is False


def test_oneshot_restore_fires_even_on_turn_error(tmp_path):
    # The finally is load-bearing: an interrupted / failed turn must NOT leave the
    # gates stuck down — that is the dangerous failure mode.
    agent = make_agent(tmp_path)
    _arm_oneshot(agent, prior=TIER_SAFE)

    def boom(*a, **k):
        raise RuntimeError("turn blew up")

    agent._stream_orchestrator_iteration = boom
    with pytest.raises(RuntimeError):
        agent.run_turn("go")
    assert agent.session.yolo is False
    assert agent.session.freeball is False
    assert agent.session.freeball_restore is None


def test_normal_turn_leaves_a_live_toggle_alone(tmp_path):
    # Session TOGGLE (no ticket): a normal turn must NOT revert freeball.
    agent = make_agent(tmp_path)
    agent.session.yolo = True
    agent.session.freeball = True
    agent.session.freeball_restore = None
    script_iterations(agent, ("done", None))
    agent.run_turn("keep going")
    assert agent.session.yolo is True
    assert agent.session.freeball is True


# --------------------------------------------------------------------------- #
#  Non-persistence — a fresh session always starts safe
# --------------------------------------------------------------------------- #

def _repl_state(tmp_path, session):
    from xlii.repl_state import REPLState

    (tmp_path / ".xlii").mkdir(exist_ok=True)
    project = SimpleNamespace(
        xli_dir=tmp_path / ".xlii", name="t", project_root=tmp_path
    )
    return REPLState(
        console=FakeConsole(),
        agent=SimpleNamespace(session=session),
        project=project,
        cfg=SimpleNamespace(),
        pool=SimpleNamespace(),
    )


def test_freeball_not_written_to_session_json(tmp_path):
    session = SimpleNamespace(
        auto_approve=set(), attached_refs=[], attached_docs=[], attached_files=[],
        yolo=True, freeball=True, freeball_restore=TIER_SAFE,
        next_turn_temp_override=None,
    )
    state = _repl_state(tmp_path, session)
    state.save()
    data = json.loads((tmp_path / ".xlii" / "session.json").read_text())
    assert "freeball" not in data
    assert "freeball_restore" not in data


def test_load_never_resurrects_freeball(tmp_path):
    # Even a session.json that (somehow) carries freeball keys must not turn the
    # tier back on — load() ignores them, so a reopened session starts safe.
    (tmp_path / ".xlii").mkdir()
    (tmp_path / ".xlii" / "session.json").write_text(json.dumps({
        "version": 2, "current_workspace": "main", "workspaces": {},
        "yolo": True, "freeball": True, "freeball_restore": [False, False],
    }))
    session = SimpleNamespace(
        auto_approve=set(), attached_refs=[], attached_docs=[], attached_files=[],
        yolo=False, freeball=False, freeball_restore=None,
        next_turn_temp_override=None,
    )
    state = _repl_state(tmp_path, session)
    state.load()
    assert session.freeball is False
    assert session.freeball_restore is None


# --------------------------------------------------------------------------- #
#  Loud status — the filled-red FREEBALL pill
# --------------------------------------------------------------------------- #

def test_status_affordance_flag_is_loud_freeball():
    from xlii.tui.status import _MODE_RICH, _affordance_flag

    # freeball sets yolo too; the flag must show the LOUDER freeball pill.
    state = SimpleNamespace(freeball=True, yolo=True, agent=None)
    label, style = _affordance_flag(state)
    assert label == "FREEBALL"
    assert style == _MODE_RICH["FREEBALL"]
    assert "red" in style  # unmissable


def test_status_mode_freeball_leads_over_yolo():
    from xlii.tui.status import mode as status_mode

    state = SimpleNamespace(
        freeball=True, yolo=True, agent=None, persona=None,
        scratch=False, howto_mode=False, image_mode=False,
    )
    assert status_mode(state) == ("FREEBALL", "FREEBALL")


def test_status_plain_yolo_unchanged():
    from xlii.tui.status import _affordance_flag

    state = SimpleNamespace(freeball=False, yolo=True, agent=None)
    label, _style = _affordance_flag(state)
    assert label == "yolo"


# --------------------------------------------------------------------------- #
#  Rails that NEVER drop under freeball
# --------------------------------------------------------------------------- #

def test_budget_rail_still_warns_under_freeball():
    from xlii.session_meter import budget_warning

    session = SimpleNamespace(
        budget_usd=1.0, session_cost=5.0, yolo=True, freeball=True
    )
    warn = budget_warning(session)
    assert warn is not None and "budget" in warn  # the one brake left still fires


def test_admin_capability_gate_holds_under_freeball():
    from xlii.commands import session_is_elevated

    # A freeball-on session is NOT elevated — freeball never touches state.elevated.
    fb = SimpleNamespace(freeball=True, yolo=True)  # no `elevated` attr
    assert session_is_elevated({"state": fb}) is False
    assert session_is_elevated({"state": fb, "yolo": True}) is False


def test_admin_gated_command_still_refused_under_freeball():
    from xlii.commands import (
        REPLCommand,
        dispatch_repl_command,
        register_repl_command,
        unregister_repl_command,
    )

    calls: list[int] = []
    register_repl_command(REPLCommand(
        name="__fb_gated",
        handler=lambda line, ctx: (calls.append(1), True)[1],
        capability="admin",
        repls=["code"],
    ))
    try:
        console = FakeConsole()
        ctx = {
            "command_scope": "code", "console": console,
            "state": SimpleNamespace(freeball=True, yolo=True),  # freeball, not elevated
        }
        handled = dispatch_repl_command("/__fb_gated", ctx)
        assert handled is True          # consumed, never falls through to the model
        assert calls == []              # but the gated handler did NOT run
        assert "/admin unlock" in console.text
    finally:
        unregister_repl_command("__fb_gated")


def test_sync_delete_guard_holds_without_a_confirmer(tmp_path, monkeypatch):
    # The sync delete-guard is a data-loss rail with its OWN mechanism (threshold
    # + a human confirm_deletes callback); it never receives the session, so
    # freeball cannot reach it. Under an unattended/gates-down run no confirmer is
    # surfaced, so an above-threshold mass delete is SKIPPED, not auto-approved —
    # freeball can never turn remote destruction into a silent yes.
    from xlii import sync as sync_mod
    from xlii.config import GlobalConfig, ProjectConfig

    d = tmp_path / ".xlii"
    d.mkdir()
    (d / "project.json").write_text(json.dumps({
        "name": "t", "root": str(tmp_path.resolve()), "collection_id": "c1",
        "created_at": "2026-01-01", "conversation_id": "x", "local_only": False,
    }))
    project = ProjectConfig.load(tmp_path)
    (tmp_path / "keep.py").write_text("x")
    remote = {f"f{i}.py": {"file_id": f"id-{i}", "sha256": "0" * 64, "name": f"f{i}.py"}
              for i in range(15)}
    monkeypatch.setattr(sync_mod, "fetch_collection_state", lambda c, cid: remote)
    monkeypatch.setattr(sync_mod, "_do_upload", lambda *a, **k: "fake-id")
    deleted_ids: list[str] = []
    monkeypatch.setattr(sync_mod, "_do_delete", lambda c, cid, fid: deleted_ids.append(fid))

    stats = sync_mod.sync_project(None, project, GlobalConfig())  # no confirmer
    assert stats.deleted == 0
    assert deleted_ids == []
    assert any("delete-guard" in e for e in stats.errors)
