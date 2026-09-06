"""Contract tests for the Coding Rail (xlii/rail.py + its agent wiring).

Dependency-free — run directly:  ./venv/bin/python tests/test_rail.py
(also discoverable by pytest if it's installed).

These pin the behaviours that are easy to regress:
  * the 6-stage state machine and its transitions,
  * the tool-gating invariant (read-only stages ⟺ no write tools),
  * the fix that keeps the per-stage directive in the *system prompt*
    (current stage only) and OUT of persisted history.
"""

from xlii.agent import Agent
from xlii.rail import (
    LAST_STAGE,
    READ_ONLY_STAGES,
    RailController,
    RailStage,
)
from xlii.tools import dispatch_subagent_schema, plan_mode_schemas, tool_schemas

WRITE_TOOLS = {"write_file", "edit_file", "bash", "dispatch_subagent"}


def _schema_names(schemas):
    return {s["function"]["name"] for s in schemas}


def test_stage_walk_and_transitions():
    r = RailController()
    seen = []
    while True:
        seen.append(r.current_stage.value)
        if not r.advance():
            break
    assert seen == [0, 1, 2, 3, 4, 5]
    assert r.is_final_stage
    assert r.advance() is False  # no-op past the end

    assert r.back() and r.current_stage.value == 4
    r.reset()
    assert r.current_stage is RailStage.REQUIREMENTS_LOCK
    assert r.back() is False  # no-op before the start


def test_read_only_stages_are_the_thinking_stages():
    assert READ_ONLY_STAGES == {
        RailStage.REQUIREMENTS_LOCK,
        RailStage.ARCHITECTURE_PLAN,
        RailStage.EDGE_CASES,
        RailStage.PSEUDOCODE,
    }
    for stage in RailStage:
        r = RailController()
        r.current_stage = stage
        assert r.is_read_only_stage == (stage.value <= 3)


def test_tool_gating_matches_stage():
    """Stages 0-3 must expose zero write tools; 4-5 must expose them."""
    for stage in RailStage:
        r = RailController()
        r.current_stage = stage
        schemas = (
            plan_mode_schemas()
            if r.is_read_only_stage
            else tool_schemas() + [dispatch_subagent_schema()]
        )
        has_writers = bool(WRITE_TOOLS & _schema_names(schemas))
        assert has_writers == (not r.is_read_only_stage), stage


def test_directive_reflects_gate():
    r = RailController()
    assert "Writes are LOCKED" in r.get_system_directive()
    r.current_stage = RailStage.IMPLEMENTATION
    assert "Writes are UNLOCKED" in r.get_system_directive()
    assert f"0/{LAST_STAGE.value}" not in r.get_system_directive()


def _bare_agent(base="BASE", docs=None, rail=None):
    """An Agent shell with only the attrs _effective_system_prompt touches."""
    from xlii.agent import SessionState
    a = object.__new__(Agent)
    a.session = SessionState()
    a.base_system_prompt = base
    a.attached_docs = docs or []
    a.active_mode = None
    if rail is not None:
        a.rail = rail
    return a


def test_directive_lives_in_system_prompt_current_stage_only():
    a = _bare_agent(rail=RailController())

    sp0 = a._effective_system_prompt()
    assert "BASE" in sp0
    assert "STAGE 0" in sp0 and "Writes are LOCKED" in sp0

    # Advancing must not accumulate earlier stages' (contradictory) directives.
    a.rail.current_stage = RailStage.IMPLEMENTATION
    sp4 = a._effective_system_prompt()
    assert "STAGE 4" in sp4 and "Writes are UNLOCKED" in sp4
    assert "STAGE 0" not in sp4
    assert "Writes are LOCKED" not in sp4


def test_rail_off_returns_base_prompt_verbatim():
    a = _bare_agent(base="BASE PROMPT")
    assert a._effective_system_prompt() == "BASE PROMPT"


def test_docs_and_rail_compose():
    a = _bare_agent(docs=[("conv", "DOC BODY")], rail=RailController())
    sp = a._effective_system_prompt()
    assert "DOC BODY" in sp and "STAGE 0" in sp


def test_seed_flag_changes_directive_and_reset_clears_it():
    plain = RailController()
    seeded = RailController(seeded_from_plan=True)
    assert "APPROVED PLAN" not in plain.get_system_directive()
    assert "APPROVED PLAN" in seeded.get_system_directive()
    seeded.reset()
    assert seeded.seeded_from_plan is False
    assert "APPROVED PLAN" not in seeded.get_system_directive()


def test_plan_to_rail_handoff_clears_plan_mode():
    """/plan → /rail must seed the rail and leave plan mode off everywhere.

    Agent and REPLState now share one SessionState, so a clobber between the
    two views is structurally impossible — this asserts both views agree."""
    from types import SimpleNamespace
    from rich.console import Console
    from xlii.commands import _INDEX
    from xlii.repl import REPLState
    from xlii.repl_cmds import register_all
    register_all()  # built-in slash commands are registered explicitly, not on import

    agent = _bare_agent()
    agent.history = [{"role": "user", "content": "build X"}]
    state = REPLState(console=Console(), agent=agent,
                      project=SimpleNamespace(project_root="/tmp"),
                      cfg=SimpleNamespace(), pool=[])

    # enter plan mode
    _INDEX["plan"][0].handler("/plan", state.as_context_dict())
    assert state.plan_mode is True
    assert agent.plan_mode is True               # same storage, same answer

    # approve via /rail
    ctx = state.as_context_dict()
    ret = _INDEX["rail"][0].handler("/rail", ctx)
    assert ret is False                          # runs the seeded stage-0 turn
    assert ctx.get("_rail_rewritten")            # carries the task forward
    assert agent.rail is not None and agent.rail.seeded_from_plan

    # plan mode must be off in BOTH views (they are the same object now)
    assert agent.plan_mode is False
    assert state.plan_mode is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
