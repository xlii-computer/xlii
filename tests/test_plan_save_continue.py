"""Saved plans: /plan save, /plan continue, the continue-or-fresh ask on re-entry,
and the one-shot refresher (folded into the next turn, NOT a per-turn /doc)."""

from __future__ import annotations

from types import SimpleNamespace

import xlii.tools as tools
from xlii.commands import dispatch_repl_command
from xlii.repl_cmds import register_all
from tests.helpers import FakeConsole, make_agent, script_iterations

register_all()


def _ctx(tmp_path, history=None):
    agent = make_agent(
        tmp_path,
        history=history if history is not None
        else [{"role": "assistant", "content": "1. step one\n2. step two"}],
    )
    proj = SimpleNamespace(project_root=tmp_path, xli_dir=tmp_path / ".xlii")
    proj.xli_dir.mkdir(parents=True, exist_ok=True)
    con = FakeConsole()
    return {"agent": agent, "console": con, "project": proj,
            "state": SimpleNamespace(loop=None)}, con, agent


def _user_msgs(agent):
    return [h["content"] for h in agent.history
            if h.get("role") == "user" and isinstance(h.get("content"), str)]


# --- save -------------------------------------------------------------------

def test_save_writes_last_assistant_plan(tmp_path):
    ctx, con, _ = _ctx(tmp_path)
    dispatch_repl_command("/plan save myplan", ctx)
    f = tmp_path / ".xlii" / "plans" / "myplan.md"
    assert f.exists() and "step one" in f.read_text()
    assert "saved plan 'myplan'" in con.text


def test_save_defaults_and_slugifies(tmp_path):
    ctx, _, _ = _ctx(tmp_path)
    dispatch_repl_command("/plan save", ctx)
    assert (tmp_path / ".xlii" / "plans" / "plan.md").exists()
    ctx2, _, _ = _ctx(tmp_path)
    dispatch_repl_command("/plan save my cool plan", ctx2)   # only first token
    assert (tmp_path / ".xlii" / "plans" / "my.md").exists()


def test_save_nothing_to_save(tmp_path):
    ctx, con, _ = _ctx(tmp_path, history=[{"role": "user", "content": "hi"}])
    dispatch_repl_command("/plan save", ctx)
    assert not (tmp_path / ".xlii" / "plans").exists()
    assert "nothing to save" in con.text


# --- P1: save = christening (promote current.md; say which source) ----------

def test_save_fresh_current_says_promoted_with_age(tmp_path):
    ctx, con, _ = _ctx(tmp_path)
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("# THE PLAN")
    dispatch_repl_command("/plan save myname", ctx)
    assert (plans / "myname.md").read_text() == "# THE PLAN\n"
    assert "promoted current.md" in con.text and "→ myname.md" in con.text
    # Cluster E: promotion shows the working file's age (here: freshly written)
    assert "updated just now" in con.text


def test_save_old_current_shows_its_age(tmp_path):
    import os
    ctx, con, _ = _ctx(tmp_path)
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    f = plans / "current.md"
    f.write_text("# ANCIENT PLAN")
    os.utime(f, (1_000, 1_000))
    dispatch_repl_command("/plan save relic", ctx)
    assert (plans / "relic.md").read_text() == "# ANCIENT PLAN\n"
    assert "promoted current.md" in con.text
    assert "updated" in con.text and "d ago" in con.text  # age surfaced


def test_save_fallback_notes_chat_text(tmp_path):
    # no current.md → legacy chat-text save, said out loud
    ctx, con, _ = _ctx(tmp_path)
    dispatch_repl_command("/plan save myplan", ctx)
    assert "saved plan 'myplan'" in con.text
    assert "saved from chat text" in con.text


def test_save_current_is_refused(tmp_path):
    ctx, con, _ = _ctx(tmp_path)
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("WORKING")
    dispatch_repl_command("/plan save current", ctx)
    assert "not a christening target" in con.text
    assert (plans / "current.md").read_text() == "WORKING"  # untouched


def test_save_strips_md_extension(tmp_path):
    """P3 review Fix 6a: typing the extension means the name without it —
    '/plan save spec.md' creates spec.md, never spec.md.md."""
    ctx, _, _ = _ctx(tmp_path)
    dispatch_repl_command("/plan save spec.md", ctx)
    plans = tmp_path / ".xlii" / "plans"
    assert (plans / "spec.md").exists()
    assert not (plans / "spec.md.md").exists()


def test_save_current_refused_in_any_casing(tmp_path):
    """Cluster B: the guard casefolds — Current/CURRENT/current.MD can't sneak
    a working-file impostor into the saved-plans namespace."""
    for casing in ("Current", "CURRENT", "current.MD", "Current.md"):
        ctx, con, _ = _ctx(tmp_path)
        plans = tmp_path / ".xlii" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        (plans / "current.md").write_text("WORKING")
        dispatch_repl_command(f"/plan save {casing}", ctx)
        assert "not a christening target" in con.text, casing
        listed = [p.name for p in plans.glob("*.md")]
        assert listed == ["current.md"], (casing, listed)


def test_list_saved_plans_excludes_current_any_casing(tmp_path):
    from tests.helpers import make_project
    from xlii.repl_cmds.mode import _list_saved_plans

    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "Current.md").write_text("impostor")
    (plans / "real.md").write_text("christened")
    names = [n for n, _p, _m in _list_saved_plans({"project": make_project(tmp_path)})]
    assert names == ["real"]


# --- P1: bare /plan continue prefers the working file ------------------------

def test_bare_continue_prefers_current_md(tmp_path):
    import os
    ctx, con, agent = _ctx(tmp_path)
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    f = plans / "current.md"
    f.write_text("# LIVING PLAN\n")
    os.utime(f, (1_000, 1_000))  # even stale: asking to continue IS the intent
    dispatch_repl_command("/plan continue", ctx)
    assert agent.session.pending_plan_refresher == "# LIVING PLAN\n"
    assert agent.plan_mode is True
    # Cluster E: the resume message surfaces the working file's age
    assert "continuing plan 'current'" in con.text
    assert "updated" in con.text and "d ago" in con.text


def test_bare_continue_without_current_uses_newest_named(tmp_path):
    # regression pin: no working file → the existing newest-named behavior
    ctx, _, _ = _ctx(tmp_path)
    dispatch_repl_command("/plan save myplan", ctx)
    ctx2, _, agent2 = _ctx(tmp_path)
    dispatch_repl_command("/plan continue", ctx2)
    assert "step one" in (agent2.session.pending_plan_refresher or "")


def test_bare_continue_skips_blank_current_for_named(tmp_path):
    """Cluster D: an empty/whitespace current.md must not shadow real named
    plans (its 'folded into your next message' promise would silently no-op)."""
    ctx, _, _ = _ctx(tmp_path)
    dispatch_repl_command("/plan save myplan", ctx)
    ctx2, _, agent2 = _ctx(tmp_path)
    (tmp_path / ".xlii" / "plans" / "current.md").write_text("   \n\n")
    dispatch_repl_command("/plan continue", ctx2)
    assert "step one" in (agent2.session.pending_plan_refresher or "")


def test_bare_continue_blank_current_and_no_named_says_so(tmp_path):
    ctx, con, agent = _ctx(tmp_path)
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("")
    dispatch_repl_command("/plan continue", ctx)
    assert agent.session.pending_plan_refresher is None
    assert "no saved plans yet" in con.text


def test_named_continue_current_resolves_to_working_file(tmp_path):
    """The bare form announces the plan as 'current' — the explicit form of
    that name must not be a dead end (review finding: taught-name refusal)."""
    ctx, _, agent = _ctx(tmp_path)
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("# LIVING PLAN\n")
    dispatch_repl_command("/plan continue current", ctx)
    assert agent.session.pending_plan_refresher == "# LIVING PLAN\n"
    assert agent.plan_mode is True


def test_named_continue_current_without_working_file_teaches(tmp_path):
    ctx, con, agent = _ctx(tmp_path)
    dispatch_repl_command("/plan save myplan", ctx)  # named plans exist
    con2 = con  # same ctx; working file absent
    dispatch_repl_command("/plan continue current", ctx)
    assert "no working plan" in con2.text
    assert agent.session.pending_plan_refresher is None


def _recording_confirm(monkeypatch):
    """Cluster F: a RAISING _confirm is swallowed by dispatch's crash handler
    (the old tests passed even when the offer prompted) — record calls instead
    and let the test assert the list stayed empty."""
    calls: list[str] = []
    monkeypatch.setattr(tools, "_confirm", lambda p: calls.append(p) or "n")
    return calls


def test_entry_offer_ignores_current_md(tmp_path, monkeypatch):
    # regression pin: the passive entry offer stays named-only (D3)
    calls = _recording_confirm(monkeypatch)
    ctx, con, agent = _ctx(tmp_path)
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("WORKING")
    dispatch_repl_command("/plan", ctx)  # must not prompt
    assert calls == []
    assert "crashed" not in con.text
    assert agent.session.pending_plan_refresher is None
    assert "plan mode ON" in con.text


# --- continue / entry ask: queue a one-shot refresher -----------------------

def test_continue_queues_oneshot_refresher(tmp_path):
    ctx, _, _ = _ctx(tmp_path)
    dispatch_repl_command("/plan save myplan", ctx)
    ctx2, _, agent2 = _ctx(tmp_path)
    dispatch_repl_command("/plan continue myplan", ctx2)
    assert "step one" in (agent2.session.pending_plan_refresher or "")
    assert agent2.plan_mode is True


def test_entry_ask_yes_queues_refresher(tmp_path, monkeypatch):
    ctx, _, _ = _ctx(tmp_path)
    dispatch_repl_command("/plan save myplan", ctx)
    monkeypatch.setattr(tools, "_confirm", lambda prompt: "y")
    ctx2, con2, agent2 = _ctx(tmp_path)
    dispatch_repl_command("/plan", ctx2)
    assert "step one" in (agent2.session.pending_plan_refresher or "")
    assert "continuing plan 'myplan'" in con2.text


def test_entry_ask_no_starts_fresh(tmp_path, monkeypatch):
    ctx, _, _ = _ctx(tmp_path)
    dispatch_repl_command("/plan save myplan", ctx)
    monkeypatch.setattr(tools, "_confirm", lambda prompt: "n")
    ctx2, con2, agent2 = _ctx(tmp_path)
    dispatch_repl_command("/plan", ctx2)
    assert agent2.session.pending_plan_refresher is None
    assert "starting fresh" in con2.text
    assert agent2.plan_mode is True


def test_entry_no_saved_plan_does_not_prompt(tmp_path, monkeypatch):
    calls = _recording_confirm(monkeypatch)
    ctx, con, agent = _ctx(tmp_path)
    dispatch_repl_command("/plan", ctx)
    assert calls == []
    assert "crashed" not in con.text
    assert agent.session.pending_plan_refresher is None
    assert "plan mode ON" in con.text


def test_entry_does_not_reprompt_when_already_resumed(tmp_path, monkeypatch):
    ctx, _, _ = _ctx(tmp_path)
    dispatch_repl_command("/plan save myplan", ctx)
    calls = _recording_confirm(monkeypatch)
    ctx2, con2, agent2 = _ctx(tmp_path)
    agent2.session.pending_plan_refresher = "already queued"   # resumed this turn
    dispatch_repl_command("/plan", ctx2)                       # must not prompt
    assert calls == []
    assert "crashed" not in con2.text


# --- run_turn folds the refresher in ONCE, then clears ----------------------

def test_run_turn_folds_refresher_once_then_clears(tmp_path):
    agent = make_agent(tmp_path)
    agent.session.pending_plan_refresher = "SAVED-PLAN-BODY"
    script_iterations(agent, ("ok", None))
    agent.run_turn("continue please")
    last_user = _user_msgs(agent)[-1]
    assert "SAVED-PLAN-BODY" in last_user          # folded into this turn
    assert "continue please" in last_user          # alongside the real message
    assert agent.session.pending_plan_refresher is None   # consumed

    script_iterations(agent, ("ok2", None))
    agent.run_turn("next step")
    assert "SAVED-PLAN-BODY" not in _user_msgs(agent)[-1]  # one-shot, not re-inlined
