"""Flip-mode REPL dispatch: shell-primary `code` input model.

Covers the new dispatch contract in process_repl_input (proposals/done/flipmode.md):
bare input → live shell in a tracked cwd, `?` → AI, `!` → force project root
in shell-primary (talk-primary `!` uses the live desk and persists `cd`),
`/` (incl. rewrite markers) wins over the shell, and conversational modes /
the XLII_SHELL_PRIMARY toggle fall back to AI-first except desk nav.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from xlii.repl import (
    REPLState,
    process_repl_input,
    format_shell_cwd,
    shell_cwd_is_outside,
    _looks_like_prose,
    _note_shell_activity,
    _maybe_offer_reroot,
    _xlii_stateful_heads_up,
)
from tests.helpers import FakeConsole, make_agent, script_iterations
from tests.test_rail import _bare_agent


@pytest.fixture
def state(tmp_path):
    agent = _bare_agent()
    agent.history = [{"role": "system", "content": "s"}]
    xli = tmp_path / ".xlii"
    xli.mkdir()
    proj = SimpleNamespace(
        project_root=tmp_path, xli_dir=xli, local_only=True, name="proj"
    )
    st = REPLState(
        console=FakeConsole(),
        agent=agent,
        project=proj,
        cfg=SimpleNamespace(orchestrator_temp=lambda: 0.7),
        pool=[],
    )
    st.shell_cwd = tmp_path.resolve()
    return st


def _capture_shell(monkeypatch):
    """Replace subprocess.call so no real command runs; record (cmd, cwd)."""
    calls: list[tuple] = []

    def fake_call(cmd, **kw):
        calls.append((cmd, kw.get("cwd")))
        return 0

    monkeypatch.setattr("xlii.repl.subprocess.call", fake_call)
    return calls


# --------------------------------------------------------------------------- #
#  Bare input → live shell
# --------------------------------------------------------------------------- #

def test_bare_input_runs_as_shell_in_tracked_cwd(state, monkeypatch):
    calls = _capture_shell(monkeypatch)
    assert process_repl_input(state, "ls -la") == (None, True)
    assert calls == [("ls -la", str(state.shell_cwd))]


def test_prose_is_not_executed(state, monkeypatch):
    calls = _capture_shell(monkeypatch)
    out = process_repl_input(state, "frobnicate the parser module")
    assert out == (None, True)
    assert calls == []  # guarded — suggested `?` instead of running it


# --------------------------------------------------------------------------- #
#  cd tracking
# --------------------------------------------------------------------------- #

def test_cd_tracks_and_bare_cd_returns_to_root(state):
    (state.project.project_root / "sub").mkdir()
    root = state.project.project_root.resolve()

    assert process_repl_input(state, "cd sub") == (None, True)
    assert state.shell_cwd == root / "sub"

    process_repl_input(state, "cd ..")
    assert state.shell_cwd == root

    process_repl_input(state, "cd sub")
    process_repl_input(state, "cd")  # bare cd → project root
    assert state.shell_cwd == root


def test_cd_into_missing_dir_is_rejected(state):
    before = state.shell_cwd
    process_repl_input(state, "cd does_not_exist")
    assert state.shell_cwd == before


def test_cd_expands_a_unique_glob(state):
    (state.project.project_root / "grokbot-lab").mkdir()
    (state.project.project_root / "notes.txt").write_text("x")
    assert process_repl_input(state, "cd grokbot*") == (None, True)
    assert state.shell_cwd == (state.project.project_root / "grokbot-lab").resolve()


def test_cd_expands_leading_star_lab(state):
    """Muscle memory: `cd *lab` when one folder ends in lab."""
    (state.project.project_root / "iXaac-lab").mkdir()
    (state.project.project_root / "readme.md").write_text("x")
    assert process_repl_input(state, "cd *lab") == (None, True)
    assert state.shell_cwd == (state.project.project_root / "iXaac-lab").resolve()


def test_cd_glob_nested_and_question_mark(state):
    nested = state.project.project_root / "apps" / "grokbot"
    nested.mkdir(parents=True)
    process_repl_input(state, "cd apps/grokbo?")
    assert state.shell_cwd == nested.resolve()


def test_cd_glob_too_many_stays_put(state):
    (state.project.project_root / "grokbot-a").mkdir()
    (state.project.project_root / "grokbot-b").mkdir()
    before = state.shell_cwd
    process_repl_input(state, "cd grokbot*")
    assert state.shell_cwd == before
    assert "too many arguments" in state.console.text


def test_cd_glob_no_match_is_rejected(state):
    before = state.shell_cwd
    process_repl_input(state, "cd nope*")
    assert state.shell_cwd == before
    assert "not a directory" in state.console.text


def test_cd_literal_star_dir_beats_glob(state):
    """A directory actually named ``foo*`` is the target, not a glob."""
    weird = state.project.project_root / "foo*"
    weird.mkdir()
    (state.project.project_root / "foo-bar").mkdir()
    process_repl_input(state, "cd foo*")
    assert state.shell_cwd == weird.resolve()


def test_cd_outside_flags_divergence(state):
    outside = state.project.project_root.parent
    process_repl_input(state, f"cd {outside}")
    assert shell_cwd_is_outside(state)
    assert format_shell_cwd(state).startswith("(outside")


def test_home_desk_shell_roams_tilde(tmp_path, monkeypatch):
    """Home desk: shell at ~ is not 'outside'; bare cd returns to ~."""
    from types import SimpleNamespace

    from xlii.repl import format_shell_cwd, shell_cwd_is_outside, shell_roam_root
    from xlii.repl import process_repl_input

    home = tmp_path / "home"
    home.mkdir()
    desk = home / ".xlii" / "scratch" / "home"
    desk.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    proj = SimpleNamespace(name="scratch/home", project_root=desk)
    console = SimpleNamespace(print=lambda *a, **k: None)
    state = SimpleNamespace(
        project=proj, shell_cwd=home, console=console, agent=None,
        as_context_dict=lambda: {},
    )
    assert shell_roam_root(state) == home.resolve()
    assert shell_cwd_is_outside(state) is False
    assert format_shell_cwd(state) == "~"
    # move into a subdir of home
    sub = home / "Documents"
    sub.mkdir()
    state.shell_cwd = sub
    assert shell_cwd_is_outside(state) is False
    assert format_shell_cwd(state) == "~/Documents"
    # bare cd → home, not the config desk
    process_repl_input(state, "cd")
    assert state.shell_cwd == home.resolve()


def test_chained_cd_does_not_persist(state, monkeypatch):
    # `cd a && ls` runs as a subshell command; it must NOT move the tracked cwd.
    (state.project.project_root / "sub").mkdir()
    calls = _capture_shell(monkeypatch)
    before = state.shell_cwd
    process_repl_input(state, "cd sub && ls")
    assert state.shell_cwd == before
    assert calls and calls[0][0] == "cd sub && ls"


# --------------------------------------------------------------------------- #
#  `?` AI sigil
# --------------------------------------------------------------------------- #

def test_question_sends_remainder_to_ai(state):
    assert process_repl_input(state, "? explain the bug") == ("explain the bug", False)
    assert process_repl_input(state, "?explain") == ("explain", False)


def test_bare_question_prints_hint_no_turn(state, monkeypatch):
    calls = _capture_shell(monkeypatch)
    assert process_repl_input(state, "?") == (None, True)
    assert process_repl_input(state, "?   ") == (None, True)
    assert calls == []


# --------------------------------------------------------------------------- #
#  `!` forces project root
# --------------------------------------------------------------------------- #

def test_bang_runs_at_project_root_even_after_cd(state, monkeypatch):
    (state.project.project_root / "sub").mkdir()
    process_repl_input(state, "cd sub")
    calls = _capture_shell(monkeypatch)
    assert process_repl_input(state, "!ls") == (None, True)
    assert calls == [("ls", str(state.project.project_root))]


def test_talk_primary_bang_cd_persists_and_next_bang_uses_cwd(state, monkeypatch):
    """On [?], `!` is the run prefix — `!cd` must stick for the next `!ls`."""
    (state.project.project_root / "sub").mkdir()
    state.ask_primary = True
    assert process_repl_input(state, "!cd sub") == (None, True)
    assert state.shell_cwd == (state.project.project_root / "sub").resolve()
    calls = _capture_shell(monkeypatch)
    assert process_repl_input(state, "!ls") == (None, True)
    assert calls == [("ls", str(state.shell_cwd))]


def test_talk_primary_bare_cd_and_ls_are_desk(state, monkeypatch):
    dest = state.project.project_root / "Downloads"
    dest.mkdir()
    state.ask_primary = True
    assert process_repl_input(state, "cd Downloads") == (None, True)
    assert state.shell_cwd == dest.resolve()
    calls = _capture_shell(monkeypatch)
    assert process_repl_input(state, "ls") == (None, True)
    assert calls == [("ls", str(state.shell_cwd))]


def test_talk_primary_cd_prose_still_talks(state, monkeypatch):
    state.ask_primary = True
    before = state.shell_cwd
    calls = _capture_shell(monkeypatch)
    assert process_repl_input(state, "cd to the downloads folder") == (None, False)
    assert state.shell_cwd == before
    assert calls == []


# --------------------------------------------------------------------------- #
#  Precedence: `/` and rewrite markers beat the shell
# --------------------------------------------------------------------------- #

def test_rewrite_marker_becomes_agent_turn_not_shell(state, monkeypatch):
    def fake_dispatch(ui, ctx):
        ctx["_execute_rewritten"] = "Approved. Execute the plan above."
        return False

    monkeypatch.setattr("xlii.commands.dispatch_repl_command", fake_dispatch)
    calls = _capture_shell(monkeypatch)
    out = process_repl_input(state, "/execute")
    assert out == ("Approved. Execute the plan above.", False)
    assert calls == []  # the approval never leaked to the shell


def test_unknown_slash_falls_through_to_ai(state, monkeypatch):
    monkeypatch.setattr("xlii.commands.dispatch_repl_command", lambda ui, ctx: False)
    assert process_repl_input(state, "/nope") == (None, False)


# --------------------------------------------------------------------------- #
#  Conversational modes + toggle fall back to AI-first
# --------------------------------------------------------------------------- #

def test_plan_mode_is_talk_primary(state, monkeypatch):
    calls = _capture_shell(monkeypatch)
    state.plan_mode = True
    assert process_repl_input(state, "ls") == (None, False)
    assert calls == []


def test_plan_mode_dispatches_plan_control_commands(state, monkeypatch):
    """Regression: in plan mode the plan-control commands are actions, not
    docs queries. Bare `/execute` (and `/rail`, `/execute rail`) must reach
    their handlers — deferring them to the agent leaves you stuck in plan
    mode because plan_mode never clears."""
    dispatched: list[str] = []

    def fake_dispatch(ui, ctx):
        dispatched.append(ui)
        ctx["_execute_rewritten"] = "Approved. Execute the plan above."
        return False

    monkeypatch.setattr("xlii.commands.dispatch_repl_command", fake_dispatch)
    state.plan_mode = True
    for cmd in ("/execute", "/execute rail", "/rail", "/rail start"):
        dispatched.clear()
        out = process_repl_input(state, cmd)
        assert dispatched == [cmd], f"{cmd!r} was deferred instead of dispatched"
        assert out == ("Approved. Execute the plan above.", False)


def test_chat_persona_is_talk_primary(state, monkeypatch):
    calls = _capture_shell(monkeypatch)
    state.persona = object()
    assert process_repl_input(state, "tell me about x") == (None, False)
    assert calls == []


def test_howto_mode_defers_action_slash_to_agent(state, monkeypatch):
    """Mentioning /loop while asking must not start a loop."""
    dispatched: list[str] = []
    monkeypatch.setattr(
        "xlii.commands.dispatch_repl_command",
        lambda ui, ctx: dispatched.append(ui) or True,
    )
    state.howto_mode = True
    assert process_repl_input(state, "what is /loop") == (None, False)
    assert dispatched == []
    assert process_repl_input(state, "/loop how does it work") == (None, False)
    assert dispatched == []
    assert process_repl_input(state, "/loop how do i use the --judge flag in the run") == (None, False)
    assert dispatched == []
    assert process_repl_input(state, "/loop") == (None, False)
    assert dispatched == []


def test_howto_mode_still_dispatches_loop_meta_and_describe(state, monkeypatch):
    dispatched: list[str] = []
    monkeypatch.setattr(
        "xlii.commands.dispatch_repl_command",
        lambda ui, ctx: dispatched.append(ui) or True,
    )
    state.howto_mode = True
    assert process_repl_input(state, "/loop status") == (None, True)
    assert dispatched == ["/loop status"]
    dispatched.clear()
    assert process_repl_input(state, "/describe loop") == (None, True)
    assert dispatched == ["/describe loop"]


def test_shell_primary_still_runs_loop(state, monkeypatch):
    dispatched: list[str] = []
    monkeypatch.setattr(
        "xlii.commands.dispatch_repl_command",
        lambda ui, ctx: dispatched.append(ui) or True,
    )
    assert state.howto_mode is False
    assert process_repl_input(state, "/loop fix the tests") == (None, True)
    assert dispatched == ["/loop fix the tests"]


def test_env_toggle_disables_shell_primary(state, monkeypatch):
    monkeypatch.setenv("XLII_SHELL_PRIMARY", "0")
    calls = _capture_shell(monkeypatch)
    assert process_repl_input(state, "ls") == (None, False)
    assert calls == []


# --------------------------------------------------------------------------- #
#  Prefix / prose-guard units
# --------------------------------------------------------------------------- #

def test_format_shell_cwd_inside_project(state):
    assert format_shell_cwd(state) == "proj"
    (state.project.project_root / "src").mkdir()
    process_repl_input(state, "cd src")
    assert format_shell_cwd(state) == "proj/src"


# --------------------------------------------------------------------------- #
#  /cwd — quick return to the project root (and navigate)
# --------------------------------------------------------------------------- #

def test_slash_cwd_returns_to_project_root(state):
    from xlii.repl_cmds import register_all
    register_all()
    (state.project.project_root / "sub").mkdir()
    process_repl_input(state, "cd sub")
    assert state.shell_cwd == (state.project.project_root / "sub").resolve()
    out = process_repl_input(state, "/cwd")
    assert out == (None, True)
    assert state.shell_cwd == state.project.project_root.resolve()


def test_slash_cwd_with_path_navigates(state):
    from xlii.repl_cmds import register_all
    register_all()
    (state.project.project_root / "sub").mkdir()
    process_repl_input(state, "/cwd sub")
    assert state.shell_cwd == (state.project.project_root / "sub").resolve()


# --------------------------------------------------------------------------- #
#  Revived /consult (cross-vendor second opinion)
# --------------------------------------------------------------------------- #

def test_consult_parse():
    from xlii.repl_cmds.consult import _parse
    assert _parse("hello?") == (0, None, None, None, "hello?", None)
    assert _parse("--turns hi") == (1, None, None, None, "hi", None)
    assert _parse("--last 3 hi there") == (3, None, None, None, "hi there", None)
    assert _parse("--full deep q") == (-1, None, None, None, "deep q", None)
    assert _parse("--from-verify summarize")[1] == ("verify", None)
    assert _parse("--from-peer summarize")[1] == ("peer", None)
    assert _parse("--from /tmp/x.md q")[1] == ("file", "/tmp/x.md")
    assert _parse("--via cursor --model claude-sonnet q")[2:4] == ("cursor", "claude-sonnet")
    assert _parse("--from-verify --from-peer x")[5] is not None  # mutually exclusive
    _, _, _, _, _, err = _parse("--bogus x")
    assert err is not None


def test_consult_from_verify_pulls_report(state, monkeypatch):
    from xlii.repl_cmds import register_all
    import xlii.secondary_ai as sa
    register_all()
    (state.project.xli_dir / "verify-last.md").write_text("# verifier\n\nFAIL: foo.py:10 off-by-one")
    captured = {}

    def fake_query(messages, question, scope=None, profile=None, **kw):
        captured["messages"] = messages
        return sa.SecondaryResponse(text="fix the loop bound", model="m", provider="anthropic")

    monkeypatch.setattr(sa, "query_with_profile", fake_query)
    process_repl_input(state, "/consult --from-verify what should I fix?")
    assert captured["messages"], "expected a prepended context message"
    assert "FAIL: foo.py:10 off-by-one" in captured["messages"][0]["content"]
    assert "context from /verify" in captured["messages"][0]["content"]


def test_consult_from_verify_missing_is_graceful(state):
    from xlii.repl_cmds import register_all
    register_all()
    process_repl_input(state, "/consult --from-verify q")
    assert any("run /verify" in l for l in state.console.lines)


def test_consult_from_binary_file_does_not_crash(state, tmp_path, monkeypatch):
    import xlii.secondary_ai as sa
    from xlii.repl_cmds import register_all
    register_all()
    binf = tmp_path / "bin.dat"
    binf.write_bytes(b"\xff\xfe\x00\x01 not utf-8")
    monkeypatch.setattr(sa, "query_with_profile",
                        lambda messages, question, **kw: sa.SecondaryResponse(
                            text="ok", model="m", provider="anthropic"))
    # errors='replace' makes the read safe — must reach query, not crash
    process_repl_input(state, f"/consult --from {binf} summarize")
    assert any("consult ·" in l for l in state.console.lines)


def test_consult_status_line():
    from xlii.repl_cmds.consult import consult_status_line
    assert "not configured" in consult_status_line() or "harness/" in consult_status_line()


def test_consult_status_line_configured(monkeypatch):
    from types import SimpleNamespace
    import xlii.config as cfgmod
    from xlii.repl_cmds.consult import consult_status_line
    monkeypatch.setattr(
        cfgmod.GlobalConfig, "load",
        classmethod(lambda cls: SimpleNamespace(
            secondary_ai={"provider": "anthropic", "model": "claude-x", "api_key_env": "ANTHROPIC_API_KEY"})),
    )
    s = consult_status_line()
    assert "anthropic" in s and "claude-x" in s and "ANTHROPIC_API_KEY" in s


def test_consult_usage_without_question(state):
    from xlii.repl_cmds import register_all
    register_all()
    process_repl_input(state, "/consult")
    assert any("usage" in l.lower() for l in state.console.lines)


def test_consult_unconfigured_is_graceful(state):
    from xlii.repl_cmds import register_all
    register_all()
    process_repl_input(state, "/consult is this approach sound?")
    assert any("unavailable" in l.lower() for l in state.console.lines)


def test_consult_via_cursor(state, monkeypatch):
    from xlii.harness import cursor as harness_cursor
    from xlii.repl_cmds import register_all
    import json

    register_all()
    monkeypatch.setattr(harness_cursor, "resolve_cursor_cli", lambda: "/fake/cursor-agent")
    monkeypatch.setattr(
        harness_cursor.subprocess,
        "run",
        lambda cmd, **kw: type("P", (), {
            "stdout": json.dumps({"result": "harness opinion", "usage": {}}),
            "stderr": "",
            "returncode": 0,
        })(),
    )
    process_repl_input(state, "/consult --via cursor is this safe?")
    assert any("consult · cursor/" in l for l in state.console.lines)
    assert any("harness opinion" in l for l in state.console.lines)


def test_consult_happy_path(state, monkeypatch):
    from xlii.repl_cmds import register_all
    import xlii.secondary_ai as sa
    register_all()
    monkeypatch.setattr(sa, "query_with_profile",
                        lambda messages, question, **kw: sa.SecondaryResponse(
                            text="I'd reconsider the retry logic.", model="m",
                            provider="anthropic"))
    process_repl_input(state, "/consult --turns should I retry?")
    assert any("consult ·" in l for l in state.console.lines)
    assert any("reconsider the retry" in l for l in state.console.lines)


def test_consult_applies_model_override_and_provider_tier(state, monkeypatch):
    from types import SimpleNamespace
    import xlii.config as cfgmod
    import xlii.secondary_ai as sa
    from xlii.repl_cmds import register_all

    register_all()
    captured: dict = {}

    def fake_query(messages, question, *, profile=None, scope=None, **kw):
        captured["profile"] = profile
        return sa.SecondaryResponse(
            text="ok", model=(profile or {}).get("model") or "m",
            provider=(profile or {}).get("provider") or "")

    monkeypatch.setattr(sa, "query_with_profile", fake_query)
    monkeypatch.setattr(
        cfgmod.GlobalConfig,
        "load",
        classmethod(lambda cls: SimpleNamespace(
            secondary_ai={"provider": "xai", "model": "grok-4", "api_key_env": "XAI_API_KEY"})),
    )
    process_repl_input(state, "/consult --model grok-code-fast is this safe?")
    # --model override reaches the provider call
    assert captured["profile"]["model"] == "grok-code-fast"
    # xAI is same-vendor (xlii is xAI-powered), not cross_org
    assert any("consult · grok-code-fast · same_vendor" in l for l in state.console.lines)


def test_api_independence_tier_helper():
    from xlii.repl_cmds.consult import _api_independence_tier

    assert _api_independence_tier("xai", None) == "same_vendor"
    assert _api_independence_tier("anthropic", None) == "cross_org"
    assert _api_independence_tier("openai", {"tier": "custom"}) == "custom"


# --------------------------------------------------------------------------- #
#  Revived /peer + /verify (cold-context review subagents)
# --------------------------------------------------------------------------- #

def test_verify_no_prior_turn(state):
    from xlii.repl_cmds import register_all
    register_all()
    # fixture agent.history is system-only → no task to verify
    assert process_repl_input(state, "/verify") == (None, True)
    assert any("no prior turn" in l for l in state.console.lines)


def test_peer_not_a_git_repo(state):
    from xlii.repl_cmds import register_all
    register_all()
    # tmp_path isn't a git repo → genuine git error surfaced under /peer:, no crash
    process_repl_input(state, "/peer")
    assert any("/peer:" in l for l in state.console.lines)


def test_peer_single_commit_message(state):
    import subprocess
    from xlii.repl_cmds import register_all
    register_all()
    root = state.project.project_root
    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=root, check=True)
    (root / "f.txt").write_text("x")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    process_repl_input(state, "/peer")  # only one commit → no HEAD~1
    assert any("first commit" in l for l in state.console.lines)


def test_review_transient_error_does_not_quarantine(tmp_path, monkeypatch):
    from xlii.repl_cmds import review
    auth = {"n": 0}
    ag = make_agent(tmp_path)
    ag.history = [{"role": "system", "content": "s"}, {"role": "user", "content": "do X"}]
    ag.project.xli_dir.mkdir(parents=True, exist_ok=True)
    ag.pool = SimpleNamespace(
        acquire=lambda: SimpleNamespace(label="w"),
        report_success=lambda c: None,
        report_auth_failure=lambda c: auth.__setitem__("n", auth["n"] + 1),
    )
    monkeypatch.setattr(
        review, "_git",
        lambda cwd, args, timeout=10: ("diff x", None) if args[:2] == ["diff", "HEAD"] and "--name-only" not in args else ("x.py", None),
    )

    def boom(self, task, system_prompt_override=None):
        raise RuntimeError("connection reset by peer")  # transient, NOT auth-shaped

    monkeypatch.setattr("xlii.agent.WorkerAgent.run", boom)
    console = FakeConsole()
    review._verify_handler("/verify", {"console": console, "agent": ag, "project": ag.project, "state": None})
    assert auth["n"] == 0  # a transient failure must not quarantine a healthy key
    assert any("crashed" in l for l in console.lines)


def test_peer_bad_usage(state):
    from xlii.repl_cmds import register_all
    register_all()
    process_repl_input(state, "/peer --bogus arg here")
    assert any("usage" in l.lower() for l in state.console.lines)


def test_verify_spawns_reviewer_when_diff_present(tmp_path, monkeypatch):
    from xlii.repl_cmds import review
    from xlii.agent import CallStats

    ag = make_agent(tmp_path)
    ag.history = [{"role": "system", "content": "s"},
                  {"role": "user", "content": "add the login endpoint"}]

    def fake_git(cwd, args, timeout=10):
        if args[:2] == ["diff", "HEAD"] and "--name-only" not in args:
            return ("diff --git a/login.py b/login.py\n+def login(): ...", None)
        if args == ["diff", "HEAD", "--name-only"]:
            return ("login.py", None)
        return ("", None)

    monkeypatch.setattr(review, "_git", fake_git)
    monkeypatch.setattr(
        "xlii.agent.WorkerAgent.run",
        lambda self, task, system_prompt_override=None, max_iterations=None: ("PASS: adds a login endpoint", CallStats(model="m", iterations=1)),
    )

    ag.project.xli_dir.mkdir(parents=True, exist_ok=True)  # so the sink can be written
    console = FakeConsole()
    ctx = {"console": console, "agent": ag, "project": ag.project, "state": None}
    assert review._verify_handler("/verify", ctx) is True
    assert any("PASS: adds a login endpoint" in l for l in console.lines)
    # the report is saved for /consult --from-verify chaining
    saved = (ag.project.xli_dir / "verify-last.md").read_text()
    assert "PASS: adds a login endpoint" in saved


# --------------------------------------------------------------------------- #
#  Revived /mark + /marks + /recall (chat reference points)
# --------------------------------------------------------------------------- #

def _as_chat(state, tmp_path):
    """Turn the code-flavored fixture into a chat session with a turns dir."""
    turns = tmp_path / "turns"
    turns.mkdir()
    state.persona = SimpleNamespace(turns_dir=turns, name="p")
    return turns


def test_mark_marks_recall_roundtrip(state, tmp_path):
    from xlii.repl_cmds import register_all
    from xlii.transcript import write_turn
    register_all()
    turns = _as_chat(state, tmp_path)
    write_turn(turns, "what is the auth flow?", "it uses OMEMO device trust")

    assert process_repl_input(state, "/mark auth") == (None, True)
    assert any("marked last turn" in l for l in state.console.lines)

    process_repl_input(state, "/marks")
    assert any("auth" in l for l in state.console.lines)

    process_repl_input(state, "/recall auth")
    assert any(n == "point:auth" for n, _ in state.attached_docs)
    body = dict(state.attached_docs)["point:auth"]
    assert "OMEMO device trust" in body


def test_mark_without_turns_is_graceful(state, tmp_path):
    from xlii.repl_cmds import register_all
    register_all()
    _as_chat(state, tmp_path)  # no turns written
    process_repl_input(state, "/mark x")
    assert any("no turns to mark" in l for l in state.console.lines)


def test_recall_unknown_mark(state, tmp_path):
    from xlii.repl_cmds import register_all
    from xlii.transcript import write_turn
    register_all()
    turns = _as_chat(state, tmp_path)
    write_turn(turns, "hi", "hello")
    process_repl_input(state, "/recall nope")
    assert any("no mark named" in l for l in state.console.lines)
    assert not state.attached_docs


# --------------------------------------------------------------------------- #
#  Terminal title carries the cwd (frees the prompt line)
# --------------------------------------------------------------------------- #

def test_title_reflects_cwd(state):
    from xlii.repl import _title_for
    assert _title_for(state) == "xlii · proj"
    (state.project.project_root / "src").mkdir()
    process_repl_input(state, "cd src")
    assert _title_for(state) == "xlii · proj/src"


def test_title_disabled_by_env(monkeypatch):
    from xlii.repl import terminal_title_enabled
    monkeypatch.setenv("XLII_NO_TITLE", "1")
    assert terminal_title_enabled() is False


def test_set_terminal_title_no_tty_is_noop():
    from xlii.repl import set_terminal_title
    # pytest captures stdout (not a TTY) → must be a silent no-op, never raise
    set_terminal_title("anything")


# --------------------------------------------------------------------------- #
#  Shell integration — `xlii shell-init` + on-exit cwd hand-off
# --------------------------------------------------------------------------- #

def test_exit_cwd_written_when_navigated(state, tmp_path, monkeypatch):
    from xlii.repl import _write_exit_cwd
    (state.project.project_root / "sub").mkdir()
    state.shell_cwd = (state.project.project_root / "sub").resolve()
    out_file = tmp_path / "cwdfile"
    monkeypatch.setenv("XLII_CWD_FILE", str(out_file))
    _write_exit_cwd(state)
    assert out_file.read_text() == str(state.shell_cwd)


def test_exit_cwd_not_written_when_at_root(state, tmp_path, monkeypatch):
    from xlii.repl import _write_exit_cwd
    out_file = tmp_path / "cwdfile"
    monkeypatch.setenv("XLII_CWD_FILE", str(out_file))
    _write_exit_cwd(state)  # fixture shell_cwd == project_root → never moved
    assert not out_file.exists()


def test_exit_cwd_noop_without_env(state, tmp_path, monkeypatch):
    from xlii.repl import _write_exit_cwd
    monkeypatch.delenv("XLII_CWD_FILE", raising=False)
    (state.project.project_root / "sub").mkdir()
    state.shell_cwd = (state.project.project_root / "sub").resolve()
    _write_exit_cwd(state)  # no env var → opt-out by default, must not raise


def test_shell_init_bash_wrapper(capsys):
    from xlii.cmds.provision import cmd_shell_init
    cmd_shell_init(SimpleNamespace(shell="bash"))
    out = capsys.readouterr().out
    assert "xlii()" in out
    assert "XLII_CWD_FILE" in out
    assert "command xlii" in out  # avoids recursing into the wrapper


def test_shell_init_fish_wrapper(capsys):
    from xlii.cmds.provision import cmd_shell_init
    cmd_shell_init(SimpleNamespace(shell="fish"))
    out = capsys.readouterr().out
    assert "function xlii" in out
    assert "XLII_CWD_FILE" in out


# --------------------------------------------------------------------------- #
#  Phase 2 — cwd as signal: run_turn awareness injection
# --------------------------------------------------------------------------- #

def test_run_turn_injects_cwd_note_when_outside(tmp_path):
    ag = make_agent(tmp_path)
    ag.session.user_shell_cwd = Path("/var/log")
    script_iterations(ag, ("ok", None))  # one iteration, no tool calls
    ag.run_turn("what is eating disk here?")
    last_user = [m for m in ag.history if m["role"] == "user"][-1]["content"]
    assert "/var/log" in last_user
    assert "shell context" in last_user
    assert "what is eating disk here?" in last_user  # original message preserved
    assert ag.session.user_shell_cwd is None         # consumed one-shot


def test_run_turn_no_note_when_inside(tmp_path):
    ag = make_agent(tmp_path)
    assert ag.session.user_shell_cwd is None
    script_iterations(ag, ("ok", None))
    ag.run_turn("hello")
    last_user = [m for m in ag.history if m["role"] == "user"][-1]["content"]
    assert last_user == "hello"  # untouched


# --------------------------------------------------------------------------- #
#  Phase 3a — re-init-to-re-root suggestion (suggest-only, single-shot)
# --------------------------------------------------------------------------- #

def _outside_state(state, tmp_path, *, is_project=False):
    """Re-root the fixture so project_root and the shell cwd are sibling dirs
    under tmp_path (so the shell cwd is genuinely *outside* the project)."""
    proj = tmp_path / "proj"
    (proj / ".xlii").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    if is_project:
        (elsewhere / ".xlii").mkdir()
    state.project.project_root = proj
    state.shell_cwd = elsewhere.resolve()
    return elsewhere.resolve()


def test_reroot_offer_is_single_shot(state, tmp_path):
    _outside_state(state, tmp_path)
    _note_shell_activity(state, state.shell_cwd)
    _note_shell_activity(state, state.shell_cwd)
    _maybe_offer_reroot(state)
    assert any("xlii init" in l for l in state.console.lines)
    n = len(state.console.lines)
    _maybe_offer_reroot(state)  # no repeat for the same cwd
    assert len(state.console.lines) == n


def test_reroot_not_offered_without_activity(state, tmp_path):
    _outside_state(state, tmp_path)
    _maybe_offer_reroot(state)
    assert not any("xlii init" in l for l in state.console.lines)


def test_reroot_not_offered_inside_project(state):
    # at the project root → not outside → no offer regardless of activity
    _note_shell_activity(state, state.shell_cwd)
    _note_shell_activity(state, state.shell_cwd)
    _maybe_offer_reroot(state)
    assert not any("xlii init" in l for l in state.console.lines)


def test_reroot_skipped_if_dir_is_already_a_project(state, tmp_path):
    _outside_state(state, tmp_path, is_project=True)
    _note_shell_activity(state, state.shell_cwd)
    _note_shell_activity(state, state.shell_cwd)
    _maybe_offer_reroot(state)
    assert not any("xlii init" in l for l in state.console.lines)


# --------------------------------------------------------------------------- #
#  Phase 3b — `xlii …` session-stateful heads-up
# --------------------------------------------------------------------------- #

def test_xlii_models_set_heads_up(state):
    _xlii_stateful_heads_up(state, "xlii models set --orchestrator grok-4")
    assert any("restart" in l.lower() for l in state.console.lines)


def test_xlii_stateless_no_heads_up(state):
    _xlii_stateful_heads_up(state, "xlii status")
    assert state.console.lines == []


def test_non_xlii_no_heads_up(state):
    _xlii_stateful_heads_up(state, "ls -la")
    assert state.console.lines == []


def test_xlii_general_heads_up_on_stateful_commands(state):
    for line in ("xlii sync", "xlii setup --force", "xlii init", "xlii keys rotate"):
        state.console.lines.clear()
        _xlii_stateful_heads_up(state, line)
        assert any("heads-up" in l for l in state.console.lines), line


def test_xlii_readonly_and_list_stay_quiet(state):
    for line in ("xlii keys list", "xlii projects", "xlii doctor", "xlii models", "xlii"):
        state.console.lines.clear()
        _xlii_stateful_heads_up(state, line)
        assert state.console.lines == [], line


@pytest.mark.parametrize(
    "line,expected",
    [
        ("ls -la", False),          # known binary
        ("cat notes.txt", False),   # known binary
        ("echo hello there", False),  # shell builtin
        ("foo", False),             # single token (typo, not prose)
        ("rm stuff && echo done", False),  # shell operators
        ("frobnicate the parser module", True),  # multi-word, unknown first token
    ],
)
def test_looks_like_prose(line, expected):
    assert _looks_like_prose(line) is expected
