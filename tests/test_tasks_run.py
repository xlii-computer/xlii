"""Vector F — /tasks sequential runner: carry threading, step types, gate."""

from __future__ import annotations

from pathlib import Path

from helpers import FakeConsole, make_agent, make_cfg, script_iterations
from xlii import tasks as T
from xlii.commands import REPLCommand, register_repl_command
from xlii.repl_state import REPLState


def _state(tmp_path: Path, *, agent=None, scope: str = "code") -> REPLState:
    agent = agent or make_agent(tmp_path, cfg=make_cfg())
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    st = REPLState(
        console=FakeConsole(),
        agent=agent,
        project=agent.project,
        cfg=agent.cfg,
        pool=agent.pool,
    )
    st.command_scope = scope
    return st


def test_shell_only_carry_threads(tmp_path):
    st = _state(tmp_path)
    p = T.parse_inline("printf hi |> tr a-z A-Z")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.ok and not out.had_errors
    assert out.carry == "HI"


def test_shell_prev_is_quoted_against_injection(tmp_path):
    st = _state(tmp_path)
    # If {{prev}} were spliced raw, the `;` would start a new command. Quoted, the
    # whole carry is one argument echo prints verbatim.
    p = T.parse_inline("printf 'a; touch PWNED' |> echo {{prev}}")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.carry.strip() == "a; touch PWNED"
    assert not (tmp_path / "PWNED").exists()


def test_shell_to_agent_round_trip_and_fence(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(agent, ("UPPERCASED", None))
    st = _state(tmp_path, agent=agent)
    p = T.parse_inline("printf hi |> ?uppercase this")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.carry == "UPPERCASED"
    # The carry was injected under the fence for the agent turn.
    user_msgs = [h for h in agent.history if h.get("role") == "user"]
    assert any("--- piped input ---" in m["content"] and "hi" in m["content"] for m in user_msgs)


def test_agent_explicit_prev_token(tmp_path):
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(agent, ("done", None))
    st = _state(tmp_path, agent=agent)
    p = T.parse_inline("printf hi |> ?repeat {{prev}} now")
    T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    user_msgs = [h for h in agent.history if h.get("role") == "user"]
    # Explicit token interpolates inline; no fence is appended.
    assert any("repeat hi now" in m["content"] for m in user_msgs)
    assert all("--- piped input ---" not in m["content"] for m in user_msgs)


def test_agent_carry_falls_back_to_history_when_streamed(tmp_path):
    # run_turn returns "" when content streamed live; carry must read history.
    agent = make_agent(tmp_path, cfg=make_cfg())

    def fake(*a, **k):
        from types import SimpleNamespace

        return (SimpleNamespace(content="STREAMED", tool_calls=None), None, True)
        # streamed=True → run_turn returns "", forcing history fallback.

    agent._stream_orchestrator_iteration = fake
    st = _state(tmp_path, agent=agent)
    p = T.parse_inline("?go")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.carry == "STREAMED"


def test_slash_step_output_is_captured_as_carry(tmp_path):
    register_repl_command(REPLCommand(
        name="taskcap1",
        handler=lambda line, ctx: (ctx["console"].print("CAP:" + line.split(maxsplit=1)[1]) or True),
        description="t", repls=["code", "chat"],
    ))
    st = _state(tmp_path)
    p = T.parse_inline("printf hello |> /taskcap1 {{prev}}")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.steps[-1].carry.strip() == "CAP:hello"


def test_unknown_slash_step_is_rejected_before_running(tmp_path):
    st = _state(tmp_path)
    p = T.parse_inline("printf hi |> /definitelynotacommand {{prev}}")
    errs = T.validate_pipeline(p, st.as_context_dict())
    assert errs and "unknown slash command" in errs[0]


def test_error_stops_pipe(tmp_path):
    st = _state(tmp_path)
    p = T.parse_inline("false |> printf after")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert not out.ok
    assert out.failed_index == 0
    assert len(out.steps) == 1  # second step never ran


def test_keep_going_carries_past_failure(tmp_path):
    st = _state(tmp_path)
    p = T.parse_inline("false |> printf after")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False, keep_going=True)
    assert out.ok            # nothing fatally stopped it
    assert out.had_errors    # but a step did fail
    assert out.carry == "after"


def test_continue_on_error_per_step(tmp_path):
    st = _state(tmp_path)
    p = T.Pipeline(name="x", steps=[
        T.Step(kind=T.KIND_SHELL, body="false", continue_on_error=True),
        T.Step(kind=T.KIND_SHELL, body="printf ok"),
    ])
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.had_errors and out.ok
    assert out.carry == "ok"


def test_carry_truncated_on_inject(tmp_path):
    st = _state(tmp_path)
    big = "X" * 500
    p = T.parse_inline(f"printf {big} |> cat")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False, carry_max_chars=80)
    # `cat` echoes the (truncated) stdin it received, proving truncate-on-inject.
    assert "chars elided" in out.carry
    assert len(out.carry) < 500


# --------------------------------------------------------------------------- #
#  security gate
# --------------------------------------------------------------------------- #

def test_normal_shell_gate_prompts_unless_yes(tmp_path, monkeypatch):
    prompts: list[str] = []
    monkeypatch.setattr("xlii.tools._confirm", lambda *a, **k: prompts.append("asked") or "y")
    st = _state(tmp_path)
    p = T.parse_inline("printf hi")
    T.run_pipeline(p, st.as_context_dict(), confirm_shell=True, yes=False)
    assert prompts == ["asked"]

    prompts.clear()
    T.run_pipeline(p, st.as_context_dict(), confirm_shell=True, yes=True)
    assert prompts == []  # --yes skips the ordinary gate


def test_untrusted_carry_gate_fires_even_under_yes(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.tools._confirm", lambda *a, **k: "n")  # decline downstream
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(agent, ("rm -rf /tmp/whatever", None))
    st = _state(tmp_path, agent=agent)
    # shell (trusted) → agent (taints) → shell (untrusted: must confirm even w/ yes)
    p = T.parse_inline("printf hi |> ?suggest a command |> echo {{prev}}")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=True, yes=True)
    assert not out.ok
    assert out.steps[-1].blocked
    # the gate text (printed to the session console) names the untrusted reason
    assert "untrusted" in st.console.text


def test_untrusted_gate_disabled_by_confirm_shell_false(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.tools._confirm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("prompted")))
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(agent, ("payload", None))
    st = _state(tmp_path, agent=agent)
    p = T.parse_inline("printf hi |> ?suggest |> echo {{prev}}")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False, yes=False)
    assert out.ok  # no prompt at all → ran clean


# --- pipeline shell steps feed the last-shell capture seam (the /shum lag) -----------


def test_shell_step_feeds_the_capture_seam(tmp_path):
    """Regression: `ls -al |> /shum` summarized the PREVIOUS interactive command.
    Pipeline shell steps ran via bare subprocess and never recorded into the
    last_shell seam, so /shum·/sh --transform·/replay·?> all lagged by one command."""
    from xlii.shell_toolkit import last_output_capture, last_shell_capture, record_last_shell
    from xlii.tui.events import ShellRan

    st = _state(tmp_path)
    # A stale interactive capture from "one command ago" (the user's earlier `ip a`).
    record_last_shell(st, ShellRan(command="ip a", cwd=tmp_path,
                                   stdout="IFACE STUFF", stderr="", returncode=0))
    p = T.parse_inline("printf hi |> tr a-z A-Z")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.ok
    ev = last_shell_capture(st)
    assert ev is not None and ev.command == "tr a-z A-Z"   # the LAST pipeline shell step
    assert ev.stdout == "HI"                                # not the stale "IFACE STUFF"
    cap = last_output_capture(st)                           # the /replay mirror moved too
    assert cap is not None and cap.source == "shell" and "HI" in cap.text


def test_slash_step_after_shell_sees_that_shells_capture(tmp_path):
    """The /shum shape exactly: a slash step with no {{prev}} that reads the
    last-shell seam must see the shell step that JUST ran in this pipeline."""
    from xlii.shell_toolkit import last_shell_capture, record_last_shell
    from xlii.tui.events import ShellRan

    seen: list[str] = []

    def _probe(line, ctx):
        ev = last_shell_capture(ctx["state"])
        seen.append(ev.command if ev else "(none)")
        ctx["console"].print(ev.stdout if ev else "")
        return True

    register_repl_command(REPLCommand(name="taskshum", handler=_probe,
                                      description="t", repls=["code", "chat"]))
    st = _state(tmp_path)
    record_last_shell(st, ShellRan(command="ip a", cwd=tmp_path,
                                   stdout="OLD", stderr="", returncode=0))
    p = T.parse_inline("printf fresh |> /taskshum")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False)
    assert out.ok
    assert seen == ["printf fresh"]                 # the pipeline's own step, not `ip a`
    assert out.steps[-1].carry.strip() == "fresh"   # and its stdout rode through
