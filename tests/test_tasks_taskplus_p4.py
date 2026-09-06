"""Task+ T+P4 — dry-run path preview (enumerate the routes through a branching pipe)."""

from __future__ import annotations

from xlii import tasks as T


def _verdict_pipe():
    return T.Pipeline(name="g", steps=[
        T.Step(T.KIND_SHELL, "git status", id="status"),
        T.Step(T.KIND_AGENT, "classify", id="classify"),
        T.Step(T.KIND_AGENT, "clean", id="clean"),
        T.Step(T.KIND_AGENT, "dirty", id="dirty"),
    ], edges=[T.Edge("classify", "clean", "clean"), T.Edge("classify", "dirty", "dirty")])


def test_linear_pipe_has_single_path_and_no_preview_section():
    p = T.parse_inline("printf a |> printf b |> ?sum")
    assert T.enumerate_paths(p) == ["1 → 2 → 3"]
    # a linear pipe is not "branching", so render_plan omits the paths section
    assert not T.pipeline_has_branching(p)
    assert "paths" not in "\n".join(T.render_plan(p))


def test_shell_rc_two_paths():
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_SHELL, "pytest", id="tests", on_failure="triage"),
        T.Step(T.KIND_SHELL, "git log -1", id="head"),
        T.Step(T.KIND_AGENT, "summarize", id="triage"),
    ])
    paths = T.enumerate_paths(p)
    assert "tests ─ok→ head → triage" in paths
    assert "tests ─fail→ triage" in paths
    assert len(paths) == 2


def test_verdict_arms_are_terminal_no_fallthrough():
    paths = T.enumerate_paths(_verdict_pipe())
    assert "status → classify ─branch=clean→ clean" in paths
    assert "status → classify ─branch=dirty→ dirty" in paths
    # the clean arm must NOT fall through to the dirty sibling
    assert not any("clean → dirty" in r for r in paths)
    assert len(paths) == 2


def test_nested_verdict_arm_reroutes():
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_AGENT, "a", id="a"),
        T.Step(T.KIND_AGENT, "b", id="b"),
        T.Step(T.KIND_AGENT, "x", id="x"),
        T.Step(T.KIND_AGENT, "y", id="y"),
        T.Step(T.KIND_AGENT, "z", id="z"),
    ], edges=[
        T.Edge("a", "one", "b"), T.Edge("a", "two", "z"),
        T.Edge("b", "hi", "x"), T.Edge("b", "lo", "y"),
    ])
    paths = T.enumerate_paths(p)
    assert "a ─branch=one→ b ─branch=hi→ x" in paths
    assert "a ─branch=one→ b ─branch=lo→ y" in paths
    assert "a ─branch=two→ z" in paths
    assert len(paths) == 3


def test_split_shows_parallel_hop():
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_SHELL, "printf prep", id="prep"),
        T.Step(T.KIND_SHELL, "", id="gate", split=["lint", "type", "test"], join="triage", policy="all"),
        T.Step(T.KIND_SHELL, "lint", id="lint"),
        T.Step(T.KIND_SHELL, "type", id="type"),
        T.Step(T.KIND_SHELL, "test", id="test"),
        T.Step(T.KIND_AGENT, "triage", id="triage"),
    ])
    paths = T.enumerate_paths(p)
    assert paths == ["prep → gate ─split{lint‖type‖test}·all→ triage"]


def test_shell_fail_with_no_handler_stops():
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_SHELL, "check", id="check", on_success="done"),
        T.Step(T.KIND_SHELL, "mid", id="mid"),
        T.Step(T.KIND_AGENT, "done", id="done"),
    ])
    paths = T.enumerate_paths(p)
    assert "check ─ok→ done" in paths
    assert "check ─fail→ ⊘" in paths


def test_cycle_is_cut_with_loop_marker():
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_SHELL, "a", id="a", on_failure="b"),
        T.Step(T.KIND_SHELL, "b", id="b", on_failure="a"),
    ])
    paths = T.enumerate_paths(p)
    assert any(r.endswith("a ↻") for r in paths)  # the back-edge is cut, not infinite


def test_truncation_cap_appends_ellipsis():
    paths = T.enumerate_paths(_verdict_pipe(), max_paths=1)
    assert paths[-1] == "…"
    assert len([r for r in paths if r != "…"]) == 1


def test_render_plan_includes_paths_for_branching():
    plan = "\n".join(T.render_plan(_verdict_pipe()))
    assert "paths (2):" in plan
    assert "branch=clean" in plan
    assert "branch=dirty" in plan


# --- fidelity fixes from the adversarial pass ------------------------------- #

def test_routed_shell_arm_follows_on_failure():
    # a verdict arm that is a gated shell stops on success but follows on_failure
    # on failure (mirrors the runner's stop_after_current, which only wins on ok).
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_AGENT, "triage", id="triage"),
        T.Step(T.KIND_SHELL, "patch", id="patch", on_failure="rollback"),
        T.Step(T.KIND_SHELL, "rollback", id="rollback"),
        T.Step(T.KIND_SHELL, "done", id="done"),
    ], edges=[T.Edge("triage", "fix", "patch"), T.Edge("triage", "skip", "done")])
    paths = T.enumerate_paths(p)
    assert "triage ─branch=fix→ patch ─ok→ ⊘" in paths          # success stops
    assert "triage ─branch=fix→ patch ─fail→ rollback → done" in paths  # failure routes
    assert "triage ─branch=skip→ done" in paths


def test_gated_shell_continue_on_error_fail_continues():
    # a gated shell with continue_on_error and no on_failure continues to the next
    # step on failure — not a dead-end ⊘.
    p = T.Pipeline(name="g", steps=[
        T.Step(T.KIND_SHELL, "probe", id="a", on_success="c", continue_on_error=True),
        T.Step(T.KIND_SHELL, "fallback", id="b"),
        T.Step(T.KIND_SHELL, "done", id="c"),
    ])
    paths = T.enumerate_paths(p)
    assert "a ─ok→ c" in paths
    assert "a ─fail→ b → c" in paths
    assert not any("─fail→ ⊘" in r for r in paths)


def test_deep_chain_does_not_recursion_error():
    steps = [T.Step(T.KIND_SHELL, f"s{i}", id=f"s{i}") for i in range(400)]
    steps[0] = T.Step(T.KIND_SHELL, "s0", id="s0", on_failure="s399")  # make it branching
    paths = T.enumerate_paths(T.Pipeline(name="deep", steps=steps))  # must not raise
    assert paths  # produced something (bounded by the depth guard)
