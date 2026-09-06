"""plan-write-domain P0 — path-scoped write profiles (proposals/plan-write-domain.md).

Three layers under test, mirroring the enforcement points:
  * handler: write_path_refusal via t_write_file / t_edit_file (resolved-path
    containment, deny-wins, symlink escape, not-yet-existing subdirs);
  * controller: PlanController's write_scope + widened palette, and the uniform
    "not the planner => plans/ denied" posture of every other mode;
  * agent: run_turn derives the profile from active_mode each turn (nothing
    stored), and dispatch refuses tools outside the advertised palette;
  * persistence: plan-last.md / /plan save prefer the fresh working file
    (plans/current.md) over the chat summary.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from xlii import tools
from xlii.commands import dispatch_repl_command
from xlii.debug_mode import DebugController
from xlii.mode_controller import DiscoveryController, OpsController, PlanController
from xlii.rail import RailController, RailStage
from xlii.repl import REPLState
from xlii.repl_cmds import register_all
from xlii.tools import plan_write_schemas

from tests.helpers import (
    FakeConsole,
    make_agent,
    make_project,
    make_tool_ctx,
    script_iterations,
)

register_all()


def _plans_root(root) -> Path:
    return (Path(root) / ".xlii" / "plans").resolve()


def _plan_ctx(root):
    """A ToolContext wearing the plan-mode write profile."""
    ctx = make_tool_ctx(root)
    ctx.write_allow = (str(_plans_root(root)),)
    return ctx


def _exec_ctx(root):
    """A ToolContext wearing the execute-mode write profile."""
    ctx = make_tool_ctx(root)
    ctx.write_deny = (str(_plans_root(root)),)
    return ctx


# --------------------------------------------------------------------------- #
#  handler level — write_path_refusal through t_write_file / t_edit_file
# --------------------------------------------------------------------------- #

def test_plan_profile_allows_write_inside_plans(tmp_path):
    r = tools.t_write_file(_plan_ctx(tmp_path),
                           {"path": ".xlii/plans/current.md", "content": "# plan"})
    assert not r.is_error
    assert (tmp_path / ".xlii/plans/current.md").read_text() == "# plan"


def test_plan_profile_refuses_repo_write(tmp_path):
    r = tools.t_write_file(_plan_ctx(tmp_path), {"path": "src/app.py", "content": "x"})
    assert r.is_error and "plan mode writes are limited to .xlii/plans/" in r.content
    assert not (tmp_path / "src/app.py").exists()


def test_plan_profile_edit_file_same_domain(tmp_path):
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("goal: old")
    (tmp_path / "repo.py").write_text("code = 1")

    ctx = _plan_ctx(tmp_path)
    r = tools.t_edit_file(ctx, {"path": ".xlii/plans/current.md",
                                "old_string": "old", "new_string": "new"})
    assert not r.is_error
    assert (plans / "current.md").read_text() == "goal: new"

    r = tools.t_edit_file(ctx, {"path": "repo.py",
                                "old_string": "1", "new_string": "2"})
    assert r.is_error and "plan mode writes are limited" in r.content
    assert (tmp_path / "repo.py").read_text() == "code = 1"


def test_execute_profile_refuses_plans_allows_repo(tmp_path):
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("the spec")

    ctx = _exec_ctx(tmp_path)
    r = tools.t_write_file(ctx, {"path": ".xlii/plans/current.md", "content": "rewritten"})
    assert r.is_error and "planner's domain" in r.content
    assert (plans / "current.md").read_text() == "the spec"  # spec drift is dead

    r = tools.t_edit_file(ctx, {"path": ".xlii/plans/current.md",
                                "old_string": "spec", "new_string": "hack"})
    assert r.is_error and "planner's domain" in r.content

    r = tools.t_write_file(ctx, {"path": "src/impl.py", "content": "code"})
    assert not r.is_error
    assert (tmp_path / "src/impl.py").read_text() == "code"


def test_deny_wins_over_allow(tmp_path):
    ctx = make_tool_ctx(tmp_path)
    plans = str(_plans_root(tmp_path))
    ctx.write_allow = (plans,)
    ctx.write_deny = (plans,)
    r = tools.t_write_file(ctx, {"path": ".xlii/plans/current.md", "content": "x"})
    assert r.is_error and "planner's domain" in r.content


def test_symlink_inside_plans_cannot_reach_repo(tmp_path):
    """Containment is on RESOLVED paths — a symlink under plans/ pointing at a
    repo file resolves outside plans/ and is refused."""
    target = tmp_path / "secret.py"
    target.write_text("untouched")
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    os.symlink(target, plans / "link.md")

    r = tools.t_write_file(_plan_ctx(tmp_path),
                           {"path": ".xlii/plans/link.md", "content": "pwned"})
    assert r.is_error and "plan mode writes are limited" in r.content
    assert target.read_text() == "untouched"


def test_new_file_in_not_yet_existing_plans_subdir_allowed(tmp_path):
    # plans/ (and the subdir) don't exist yet — non-strict resolution must
    # still recognise the target as inside the plans domain.
    assert not (tmp_path / ".xlii").exists()
    r = tools.t_write_file(_plan_ctx(tmp_path),
                           {"path": ".xlii/plans/sub/notes.md", "content": "n"})
    assert not r.is_error
    assert (tmp_path / ".xlii/plans/sub/notes.md").read_text() == "n"


def test_default_context_is_unrestricted(tmp_path):
    # No profile set (workers / legacy call sites): behavior unchanged.
    r = tools.t_write_file(make_tool_ctx(tmp_path), {"path": "a.txt", "content": "x"})
    assert not r.is_error


def test_allow_root_itself_is_not_a_write_target(tmp_path):
    """Writing the allow root would create a FILE named plans and brick every
    later write into the domain (review Fix 3): allow means STRICTLY inside."""
    r = tools.t_write_file(_plan_ctx(tmp_path), {"path": ".xlii/plans", "content": "x"})
    assert r.is_error and "plan mode writes are limited" in r.content
    assert not (tmp_path / ".xlii/plans").exists()
    # deny keeps refusing on equality too — the root is never a write target
    r = tools.t_write_file(_exec_ctx(tmp_path), {"path": ".xlii/plans", "content": "x"})
    assert r.is_error and "planner's domain" in r.content


def test_prefix_sibling_dirs_are_outside_the_domain(tmp_path):
    """Containment is per path segment, never string prefix: `.xlii/plans-notes`
    and `.xlii/plansfoo.md` share the prefix but are NOT the plans domain."""
    r = tools.t_write_file(_plan_ctx(tmp_path),
                           {"path": ".xlii/plans-notes/x.md", "content": "x"})
    assert r.is_error and "plan mode writes are limited" in r.content
    assert not (tmp_path / ".xlii/plans-notes").exists()
    # the execute deny must not leak onto prefix siblings either
    r = tools.t_write_file(_exec_ctx(tmp_path),
                           {"path": ".xlii/plansfoo.md", "content": "x"})
    assert not r.is_error
    assert (tmp_path / ".xlii/plansfoo.md").read_text() == "x"


def test_preview_override_write_target_escape(tmp_path):
    """xli_dir outside project_root (state_dir_override / symlinked .xlii): an
    absolute write STRICTLY inside an allow root is resolved via the write-target
    escape; without an allow domain the project-escape error stays fatal."""
    root = tmp_path / "repo"
    root.mkdir()
    state_dir = tmp_path / "preview-state"
    plans = (state_dir / "plans").resolve()

    ctx = make_tool_ctx(root)
    ctx.project.xli_dir = state_dir
    ctx.write_allow = (str(plans),)
    r = tools.t_write_file(ctx, {"path": str(plans / "current.md"), "content": "# plan"})
    assert not r.is_error
    assert (plans / "current.md").read_text() == "# plan"
    assert ctx.dirty_paths == set()  # out-of-root plan writes never queue for sync

    # execute profile: no allow domain — the real plans dir stays unreachable
    ctx2 = make_tool_ctx(root)
    ctx2.project.xli_dir = state_dir
    ctx2.write_deny = (str(plans),)
    with pytest.raises(ValueError):
        tools.t_write_file(ctx2, {"path": str(plans / "current.md"), "content": "x"})
    assert (plans / "current.md").read_text() == "# plan"


# --------------------------------------------------------------------------- #
#  controller level — write_scope + palettes
# --------------------------------------------------------------------------- #

def test_plan_controller_write_scope(tmp_path):
    xli = tmp_path / ".xlii"
    allow, deny = PlanController().write_scope(xli)
    assert allow == (str((xli / "plans").resolve()),)
    assert deny == ()


def test_plan_controller_schemas_add_gated_writers():
    plan = PlanController()
    assert plan.tool_schemas() == plan_write_schemas()
    names = {s["function"]["name"] for s in plan.tool_schemas()}
    assert {"write_file", "edit_file"} <= names
    assert "bash" not in names and "dispatch_subagent" not in names


def test_non_plan_modes_deny_the_plans_dir(tmp_path):
    """The uniform rule (review Fix 2): not the planner ⇒ plans/ denied — rail
    write stages, debug fix, ops included. Read-only palettes still advertise
    no writers; the deny is belt-and-braces there."""
    xli = tmp_path / ".xlii"
    plans_deny = (None, (str((xli / "plans").resolve()),))
    for mode in (DiscoveryController(), OpsController(), RailController(),
                 DebugController()):
        assert mode.write_scope(xli) == plans_deny, mode
        names = {s["function"]["name"] for s in mode.tool_schemas()}
        assert "write_file" not in names and "edit_file" not in names, mode
    # no xli_dir to resolve against ⇒ the unrestricted default
    assert RailController().write_scope(None) == (None, ())


# --------------------------------------------------------------------------- #
#  agent level — the profile is DERIVED from active_mode inside run_turn
# --------------------------------------------------------------------------- #

def _tool_results(agent) -> list[str]:
    return [h["content"] for h in agent.history if h.get("role") == "tool"]


def test_plan_turn_writes_plan_refuses_repo(tmp_path):
    agent = make_agent(tmp_path)
    agent.set_mode(PlanController())
    script_iterations(
        agent,
        ("planning", [
            ("write_file", {"path": ".xlii/plans/current.md", "content": "# the plan"}),
            ("write_file", {"path": "hack.py", "content": "oops"}),
        ]),
        ("plan written", None),
    )
    agent.run_turn("plan it")
    assert (tmp_path / ".xlii/plans/current.md").read_text() == "# the plan"
    assert not (tmp_path / "hack.py").exists()
    assert any("plan mode writes are limited" in t for t in _tool_results(agent))


def test_execute_turn_writes_repo_refuses_plans(tmp_path):
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("the spec")
    agent = make_agent(tmp_path)
    agent.set_mode(None)
    script_iterations(
        agent,
        ("building", [
            ("write_file", {"path": ".xlii/plans/current.md", "content": "rewrite"}),
            ("write_file", {"path": "impl.py", "content": "code"}),
        ]),
        ("done", None),
    )
    agent.run_turn("go")
    assert (tmp_path / "impl.py").read_text() == "code"
    assert (plans / "current.md").read_text() == "the spec"
    assert any("planner's domain" in t for t in _tool_results(agent))


def test_unadvertised_tool_refused_at_dispatch(tmp_path):
    """A hallucinated call to a tool that was never advertised this turn (bash /
    dispatch_subagent in plan mode) is refused at dispatch, not executed."""
    agent = make_agent(tmp_path)
    agent.set_mode(PlanController())
    script_iterations(
        agent,
        ("trying", [
            ("bash", {"command": "touch pwned", "intent": "read-only"}),
            ("dispatch_subagent", {"task": "go around the gate"}),
        ]),
        ("done", None),
    )
    agent.run_turn("go")
    assert not (tmp_path / "pwned").exists()
    refusals = [t for t in _tool_results(agent) if "advertised palette" in t]
    assert len(refusals) == 2
    assert "refused, not run" in refusals[0]


def test_execute_command_flips_the_write_profile(tmp_path):
    """/plan → plans/ writable, repo locked; /execute → the exact inverse —
    asserted through real run_turns, since the profile is derived, not stored."""
    agent = make_agent(tmp_path)
    with open(os.devnull, "w") as devnull:
        state = REPLState(
            console=Console(file=devnull),
            agent=agent,
            project=agent.project,
            cfg=type("C", (), {"orchestrator_temp": lambda: 0.7})(),
            pool=[],
        )
        dispatch_repl_command("/plan", state.as_context_dict())
        assert isinstance(agent.active_mode, PlanController)

        script_iterations(
            agent,
            ("planning", [("write_file", {"path": ".xlii/plans/current.md",
                                          "content": "1. do thing"})]),
            ("1. do thing", None),
        )
        agent.run_turn("plan it")
        assert (tmp_path / ".xlii/plans/current.md").exists()

        dispatch_repl_command("/execute", state.as_context_dict())
        assert agent.active_mode is None and state.plan_mode is False
        # /execute persisted the plan FILE (fresh current.md), not the chat text
        assert (tmp_path / ".xlii" / "plan-last.md").read_text() == "1. do thing\n"

        script_iterations(
            agent,
            ("building", [
                ("write_file", {"path": "impl.py", "content": "code"}),
                ("write_file", {"path": ".xlii/plans/current.md", "content": "drift"}),
            ]),
            ("done", None),
        )
        agent.run_turn("Approved. Execute the plan above using all available tools.")
        assert (tmp_path / "impl.py").read_text() == "code"
        assert (tmp_path / ".xlii/plans/current.md").read_text() == "1. do thing"
        assert any("planner's domain" in t for t in _tool_results(agent))


def test_rail_write_stage_cannot_edit_the_spec(tmp_path):
    """/execute rail is the flagship implementer path — its write stages get
    the repo but never the plans/ spec (review Fix 2)."""
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("the spec")
    agent = make_agent(tmp_path)
    rail = RailController(seeded_from_plan=True)
    rail.current_stage = RailStage.IMPLEMENTATION
    agent.set_mode(rail)
    script_iterations(
        agent,
        ("implementing", [
            ("write_file", {"path": ".xlii/plans/current.md", "content": "drift"}),
            ("write_file", {"path": "impl.py", "content": "code"}),
        ]),
        ("done", None),
    )
    agent.run_turn("go")
    assert (tmp_path / "impl.py").read_text() == "code"
    assert (plans / "current.md").read_text() == "the spec"
    assert any("planner's domain" in t for t in _tool_results(agent))


class _LegacyMode:
    """A ModeController fake predating write_scope — pins the defensive getattr
    in run_turn (review Fix 7: the suite passed with it replaced by a direct
    call, so a 'simplifying' refactor would ship an AttributeError)."""

    suppresses_claim_check = True

    def tool_schemas(self, project_xli_dir=None):
        from xlii.tools import tool_schemas

        return tool_schemas()

    def get_system_directive(self):
        return "LEGACY MODE"

    def status_tag(self):
        return None

    def advance(self):
        return False

    def back(self):
        return False

    def on_enter(self, agent):
        pass

    def on_exit(self, agent):
        pass


def test_legacy_controller_without_write_scope_is_unrestricted(tmp_path):
    agent = make_agent(tmp_path)
    agent.active_mode = _LegacyMode()  # raw assignment: a pre-protocol fake
    script_iterations(
        agent,
        ("working", [("write_file", {"path": "x.txt", "content": "ok"})]),
        ("done", None),
    )
    text, _dirty, _stats = agent.run_turn("go")
    assert text == "done"
    assert (tmp_path / "x.txt").read_text() == "ok"  # unrestricted profile


def test_preview_plan_turn_reaches_real_domain_and_is_told_where(tmp_path):
    """state_dir_override end-to-end (review Fix 1): the system prompt names the
    REAL writable domain, absolute writes into it land, repo writes still
    refuse."""
    root = tmp_path / "repo"
    root.mkdir()
    state_dir = tmp_path / "preview-state"
    agent = make_agent(root)
    agent.project.xli_dir = state_dir  # what state_dir_override does
    agent.set_mode(PlanController())
    plans_root = (state_dir / "plans").resolve()
    script_iterations(
        agent,
        ("planning", [
            ("write_file", {"path": str(plans_root / "current.md"),
                            "content": "# plan"}),
            ("write_file", {"path": "repo.py", "content": "no"}),
        ]),
        ("done", None),
    )
    agent.run_turn("plan it")
    assert (plans_root / "current.md").read_text() == "# plan"
    assert not (root / "repo.py").exists()
    sys_prompt = agent.history[0]["content"]
    assert "Writable domain this turn:" in sys_prompt
    assert str(plans_root) in sys_prompt


# --------------------------------------------------------------------------- #
#  persistence — the plan FILE, not the chat summary, is what gets saved
# --------------------------------------------------------------------------- #

def _mode_ctx(tmp_path, last_text="the chat summary"):
    """A minimal repl-command ctx for the mode.py persistence helpers."""
    agent = SimpleNamespace(
        history=[{"role": "assistant", "content": last_text}],
        active_mode=None,
    )
    return {"agent": agent, "project": make_project(tmp_path),
            "console": FakeConsole()}


def test_save_plan_last_prefers_fresh_current_md(tmp_path):
    from xlii.repl_cmds.mode import _save_plan_last

    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("THE PLAN")
    _save_plan_last(_mode_ctx(tmp_path), started_at=None)
    assert (tmp_path / ".xlii" / "plan-last.md").read_text() == "THE PLAN\n"


def test_save_plan_last_falls_back_without_current_md(tmp_path):
    from xlii.repl_cmds.mode import _save_plan_last

    (tmp_path / ".xlii").mkdir()
    _save_plan_last(_mode_ctx(tmp_path), started_at=time.time())
    assert (tmp_path / ".xlii" / "plan-last.md").read_text() == "the chat summary\n"


def test_save_plan_last_ignores_stale_current_md(tmp_path):
    """A current.md older than the live plan session is a leftover, not this
    plan — fall back to the chat text."""
    from xlii.repl_cmds.mode import _save_plan_last

    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    f = plans / "current.md"
    f.write_text("OLD LEFTOVER PLAN")
    os.utime(f, (1_000, 1_000))  # long before this plan session began
    _save_plan_last(_mode_ctx(tmp_path), started_at=time.time())
    assert (tmp_path / ".xlii" / "plan-last.md").read_text() == "the chat summary\n"


def test_plan_save_promotes_current_md(tmp_path):
    """/plan save christens the working file when it's fresh — the first sliver
    of P1's promote flow (review Fix 4)."""
    from xlii.repl_cmds.mode import _plan_save

    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("THE PLAN")
    _plan_save(_mode_ctx(tmp_path), ["myplan"])
    assert (plans / "myplan.md").read_text() == "THE PLAN\n"


def test_list_saved_plans_excludes_current(tmp_path):
    """current.md is the working file, not a christened save — it must not
    resurface via /plan list or the on-entry continue offer (review Fix 5)."""
    from xlii.repl_cmds.mode import _list_saved_plans

    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    (plans / "current.md").write_text("working")
    (plans / "other.md").write_text("christened")
    names = [n for n, _p, _m in _list_saved_plans({"project": make_project(tmp_path)})]
    assert names == ["other"]


# --------------------------------------------------------------------------- #
#  P1 living plan — approval announces its source; the fallback warns VISIBLY
# --------------------------------------------------------------------------- #

def _approval_ctx(tmp_path):
    """A dispatchable ctx around a real make_agent (the h_plan/h_execute path)."""
    agent = make_agent(tmp_path)
    Path(agent.project.xli_dir).mkdir(parents=True, exist_ok=True)
    con = FakeConsole()
    ctx = {"agent": agent, "console": con, "project": agent.project,
           "state": SimpleNamespace(loop=None)}
    return ctx, con, agent


# The exact rewritten text h_execute plants — the stash's turn-binding key.
_EXEC_REWRITE = "Approved. Execute the plan above using all available tools."


def test_execute_announces_plan_file_source(tmp_path):
    """Fresh current.md at approval: plan-last.md gets the FILE, the console
    names the source, no warning, and the stash pairs plan-file with the
    rewritten approval text (the turn binding)."""
    ctx, con, agent = _approval_ctx(tmp_path)
    dispatch_repl_command("/plan", ctx)                  # started_at = now
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    f = plans / "current.md"
    f.write_text("# THE PLAN")      # written after entry
    t = agent.active_mode.started_at + 60   # fs mtime ticks coarser than time.time() — pin past the fence
    os.utime(f, (t, t))
    agent.history.append({"role": "assistant", "content": "just a summary"})
    dispatch_repl_command("/execute", ctx)
    assert (tmp_path / ".xlii" / "plan-last.md").read_text() == "# THE PLAN\n"
    assert "from plans/current.md" in con.text
    assert "no reconciled" not in con.text
    assert agent.session.pending_plan_source == ("plan-file", _EXEC_REWRITE)


def test_execute_fallback_warns_visibly(tmp_path):
    """No current.md at approval: the chat snapshot still lands (legacy), but
    the mcTesty moment is SURFACED — a visible warning, and the stash says so."""
    ctx, con, agent = _approval_ctx(tmp_path)
    dispatch_repl_command("/plan", ctx)
    agent.history.append({"role": "assistant", "content": "1. chat plan"})
    dispatch_repl_command("/execute", ctx)
    assert (tmp_path / ".xlii" / "plan-last.md").read_text() == "1. chat plan\n"
    assert "no reconciled plans/current.md" in con.text
    assert "snapshotted the last chat message" in con.text
    # The VISIBLE property is load-bearing (D1b) — pin the styling channel so a
    # demotion to [dim] fails: the warning line carries the repo's warning
    # markers, ⚠ inside a [yellow] span.
    warn_line = next(ln for ln in con.lines if "no reconciled plans/current.md" in ln)
    assert "⚠" in warn_line and "[yellow]" in warn_line
    assert agent.session.pending_plan_source == ("chat-snapshot", _EXEC_REWRITE)


def test_execute_stale_current_falls_back_with_warning(tmp_path):
    """A current.md older than this plan session is a leftover — approval falls
    back to chat text AND warns."""
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    f = plans / "current.md"
    f.write_text("OLD LEFTOVER")
    os.utime(f, (1_000, 1_000))
    ctx, con, agent = _approval_ctx(tmp_path)
    dispatch_repl_command("/plan", ctx)                  # started_at >> 1000
    agent.history.append({"role": "assistant", "content": "fresh chat plan"})
    dispatch_repl_command("/execute", ctx)
    assert (tmp_path / ".xlii" / "plan-last.md").read_text() == "fresh chat plan\n"
    assert "no reconciled plans/current.md" in con.text
    assert agent.session.pending_plan_source == ("chat-snapshot", _EXEC_REWRITE)


def test_execute_rail_also_announces_source(tmp_path):
    """/execute rail approves through _seed_rail_from_plan — same honesty."""
    ctx, con, agent = _approval_ctx(tmp_path)
    dispatch_repl_command("/plan", ctx)
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    f = plans / "current.md"
    f.write_text("# RAIL PLAN")
    t = agent.active_mode.started_at + 60   # fs mtime ticks coarser than time.time() — pin past the fence
    os.utime(f, (t, t))
    agent.history.append({"role": "assistant", "content": "summary"})
    dispatch_repl_command("/execute rail", ctx)
    assert (tmp_path / ".xlii" / "plan-last.md").read_text() == "# RAIL PLAN\n"
    assert "from plans/current.md" in con.text
    source, expected = agent.session.pending_plan_source
    assert source == "plan-file"
    assert "Coding Rail" in expected            # bound to the rail rewrite text
    assert ctx["_rail_rewritten"] == expected   # the SAME string that gets planted


def _receipt_state(tmp_path, agent):
    return SimpleNamespace(console=FakeConsole(), project=agent.project,
                           agent=agent, cfg=None)


def test_receipt_stamps_only_the_bound_approval_turn(tmp_path):
    """D1c: the receipt carries plan_source exactly once, and ONLY on the turn
    that opens with the stashed rewritten text; ordinary receipts omit the key
    entirely (ledger shape unchanged)."""
    from xlii.turn_receipt import build_receipt

    agent = make_agent(tmp_path)
    agent.history.append({"role": "user", "content": _EXEC_REWRITE})
    agent.history.append({"role": "assistant", "content": "done"})
    agent.session.pending_plan_source = ("chat-snapshot", _EXEC_REWRITE)
    st = _receipt_state(tmp_path, agent)
    r = build_receipt(st, _EXEC_REWRITE, "done", set(), SimpleNamespace(tool_calls=0))
    assert r.plan_source == "chat-snapshot"
    assert r.to_dict()["plan_source"] == "chat-snapshot"
    assert agent.session.pending_plan_source is None      # consumed
    r2 = build_receipt(st, "go", "done", set(), SimpleNamespace(tool_calls=0))
    assert r2.plan_source is None
    assert "plan_source" not in r2.to_dict()


def test_receipt_discards_stash_when_approval_turn_never_ran(tmp_path):
    """The HIGH from review: an aborted approval (Ctrl-C / error / TUI busy-gate
    drop) leaves the stash — the next UNRELATED turn must consume-and-DISCARD,
    never stamp its receipt with the approval source."""
    from xlii.turn_receipt import build_receipt

    agent = make_agent(tmp_path)
    agent.session.pending_plan_source = ("plan-file", _EXEC_REWRITE)
    # ...the approval turn never runs; a later unrelated turn completes:
    agent.history.append({"role": "user", "content": "what does foo.py do?"})
    agent.history.append({"role": "assistant", "content": "it parses foo."})
    st = _receipt_state(tmp_path, agent)
    r = build_receipt(st, "what does foo.py do?", "it parses foo.", set(),
                      SimpleNamespace(tool_calls=0))
    assert r.plan_source is None
    assert "plan_source" not in r.to_dict()
    assert agent.session.pending_plan_source is None      # cleared regardless


def test_receipt_matches_prefixed_opening(tmp_path):
    """run_turn may PREFIX the rewritten text (folded /plan continue refresher,
    live-shell note) — containment, not equality, is the binding."""
    from xlii.turn_receipt import build_receipt

    agent = make_agent(tmp_path)
    agent.history.append({
        "role": "user",
        "content": f"[Resuming a plan I saved earlier]\n\nstuff\n\n{_EXEC_REWRITE}",
    })
    agent.history.append({"role": "assistant", "content": "done"})
    agent.session.pending_plan_source = ("plan-file", _EXEC_REWRITE)
    st = _receipt_state(tmp_path, agent)
    r = build_receipt(st, "x", "done", set(), SimpleNamespace(tool_calls=0))
    assert r.plan_source == "plan-file"


def test_reset_clears_plan_source_stash(tmp_path):
    ctx, _con, agent = _approval_ctx(tmp_path)
    agent.session.pending_plan_source = ("plan-file", _EXEC_REWRITE)
    dispatch_repl_command("/reset", ctx)
    assert agent.session.pending_plan_source is None


def test_saveless_approval_clears_stale_stash(tmp_path):
    """The stash setter always overwrites: an approval that saved nothing must
    CLEAR a stale pair, not leave the old wrong value in place."""
    ctx, _con, agent = _approval_ctx(tmp_path)
    agent.session.pending_plan_source = ("plan-file", "OLD BINDING")
    dispatch_repl_command("/plan", ctx)
    # no current.md, no assistant text → _save_plan_last returns None
    dispatch_repl_command("/execute", ctx)
    assert agent.session.pending_plan_source is None


def test_bare_continue_then_execute_honors_resumed_plan(tmp_path):
    """Cluster C: bare /plan continue backdates the freshness fence to the
    file's mtime — the user resumed precisely to approve, so an immediate
    /execute must persist the FILE, not fence it as a stale leftover."""
    plans = tmp_path / ".xlii" / "plans"
    plans.mkdir(parents=True)
    f = plans / "current.md"
    f.write_text("# RESUMED PLAN")
    os.utime(f, (1_000, 1_000))                          # long before this session
    ctx, con, agent = _approval_ctx(tmp_path)
    agent.history.append({"role": "assistant", "content": "old chat noise"})
    dispatch_repl_command("/plan continue", ctx)         # explicit user intent
    assert agent.active_mode.started_at <= 1_000         # fence backdated
    dispatch_repl_command("/execute", ctx)
    assert (tmp_path / ".xlii" / "plan-last.md").read_text() == "# RESUMED PLAN\n"
    assert "from plans/current.md" in con.text
    assert "no reconciled" not in con.text
    assert agent.session.pending_plan_source == ("plan-file", _EXEC_REWRITE)
