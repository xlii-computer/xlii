"""Stock-task showcase — a bundled example of each Task+ shape.

`commit-msg` / `changelog` are linear; `git-triage` demonstrates agent-verdict
routing (T+P2 `[[edge]]` + `TASK+` trailer); `tests-guard` demonstrates a
shell-rc `on_failure` gate (T+P1). These load-and-validate here (parse-level,
runner-agnostic) so `/tasks list` has real discoverable examples.
"""

from __future__ import annotations

from pathlib import Path

from xlii import tasks as T

NEW_STOCK = {
    "commit-msg", "changelog", "git-triage", "tests-guard",
    "notes-from-diff", "map-and-ask", "pre-pr", "shell-agent-slash",
}


def _xli(tmp_path: Path) -> Path:
    d = tmp_path / ".xlii"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_showcase_tasks_present_and_valid(tmp_path):
    names = {p.stem for p in T.stock_tasks_dir().glob("*.toml")}
    assert NEW_STOCK <= names
    xli = _xli(tmp_path)
    for name in NEW_STOCK:
        p = T.load_pipeline(xli, name)  # load_pipeline validates via step_index_maps
        assert p.name == name
        assert p.steps


def test_commit_msg_is_linear(tmp_path):
    p = T.load_pipeline(_xli(tmp_path), "commit-msg")
    assert not T.pipeline_has_branching(p)
    assert p.steps[0].kind == T.KIND_SHELL
    assert p.steps[-1].kind == T.KIND_AGENT


def test_git_triage_routes_by_agent_verdict(tmp_path):
    p = T.load_pipeline(_xli(tmp_path), "git-triage")
    assert T.pipeline_has_branching(p)
    assert {e.branch for e in p.edges} == {"clean", "dirty"}
    id_map = T.step_index_maps(p)[0]
    assert {"status", "classify", "clean", "dirty"} <= set(id_map)
    plan = "\n".join(T.render_plan(p))
    assert "verdict edges [classify]:" in plan
    assert "clean → clean" in plan
    assert "dirty → dirty" in plan


def test_tests_guard_uses_shell_rc_gate(tmp_path):
    p = T.load_pipeline(_xli(tmp_path), "tests-guard")
    assert T.pipeline_has_branching(p)
    tests_step = next(s for s in p.steps if s.id == "tests")
    assert tests_step.on_failure == "triage"
    assert "on failure → triage" in "\n".join(T.render_plan(p))


def test_showcase_tasks_list_as_stock(tmp_path):
    entries = dict(T.list_pipeline_entries(_xli(tmp_path)))
    for name in NEW_STOCK:
        assert entries.get(name) == "stock"


def test_git_triage_routes_clean_arm_end_to_end(tmp_path):
    """The verdict actually routes: a 'clean' trailer runs the clean arm and the
    dirty arm never fires (keep_going lets the status shell step ride even outside
    a git repo, so this exercises routing, not the shell)."""
    from helpers import FakeConsole, make_agent, make_cfg, script_iterations
    from xlii.repl_state import REPLState

    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(
        agent,
        ('working tree is clean\nTASK+ {"branch": "clean"}', None),  # classify
        ("try the next feature", None),                              # clean arm
    )
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    st = REPLState(
        console=FakeConsole(), agent=agent, project=agent.project,
        cfg=agent.cfg, pool=agent.pool,
    )
    p = T.load_pipeline(tmp_path / ".xlii", "git-triage")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False, keep_going=True)
    resolved = [s.resolved for s in out.steps]
    assert any("Suggest one worthwhile next task" in r for r in resolved)  # clean arm ran
    assert not any("group them into logical commits" in r for r in resolved)  # dirty did NOT
