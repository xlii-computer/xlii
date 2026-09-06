"""Task arguments — P0 (generalized substitution seam) + P1 (declared params).

`{{prev}}` is arg #0 (the carry); declared `[params]` add named bindings that
resolve into `{{name}}` tokens. Values *bind*, never compute; shell steps keep
the `shlex.quote` injection defense the carry already had.
"""

from __future__ import annotations

import pytest

from helpers import FakeConsole, make_agent, make_cfg
from xlii import tasks as T
from xlii.repl_state import REPLState


def _state(tmp_path, *, agent=None):
    agent = agent or make_agent(tmp_path, cfg=make_cfg())
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    return REPLState(
        console=FakeConsole(), agent=agent, project=agent.project,
        cfg=agent.cfg, pool=agent.pool,
    )


def _write(xli, name, body):
    T.tasks_dir(xli).mkdir(parents=True, exist_ok=True)
    (T.tasks_dir(xli) / f"{name}.toml").write_text(body)


# --- P0: substitution seam --------------------------------------------------- #

def test_substitute_prev_backcompat():
    assert T.substitute_prev("echo {{prev}}", "a b", kind=T.KIND_SHELL) == "echo 'a b'"
    assert T.substitute_prev("echo {{prev:raw}}", "a b", kind=T.KIND_SHELL) == "echo a b"
    assert T.substitute_prev("say {{prev}}", "a b", kind=T.KIND_AGENT) == "say a b"


def test_substitute_multi_binding_unknown_untouched():
    out = T.substitute("{{a}}-{{b}}-{{c}}", {"a": "1", "b": "2"}, kind=T.KIND_AGENT)
    assert out == "1-2-{{c}}"


def test_substitute_single_pass_no_reinjection():
    # a value that looks like another token is not re-substituted
    out = T.substitute("{{a}} {{b}}", {"a": "{{b}}", "b": "x"}, kind=T.KIND_AGENT)
    assert out == "{{b}} x"


def test_substitute_shell_quotes_value():
    out = T.substitute("grep {{q}}", {"q": "a; rm -rf /"}, kind=T.KIND_SHELL)
    assert out == "grep 'a; rm -rf /'"


def test_references():
    assert T.references("x {{q}} y", "q")
    assert T.references("x {{q:raw}} y", "q")
    assert not T.references("x {{q}} y", "z")


# --- P1: TOML param parsing -------------------------------------------------- #

def test_toml_parses_params(tmp_path):
    xli = tmp_path / ".xlii"
    _write(xli, "t",
           'name="t"\n[params.q]\nrequired=true\nhelp="term"\n'
           '[params.n]\ndefault="src/"\nenum=["src/","tests/"]\n'
           '[[step]]\nrun="grep {{q}} {{n}}"\n')
    p = T.load_pipeline(xli, "t")
    by = {x.name: x for x in p.params}
    assert [x.name for x in p.params] == ["q", "n"]
    assert by["q"].required and by["q"].help == "term"
    assert by["n"].default == "src/" and by["n"].enum == ["src/", "tests/"]


def test_reserved_prev_param_rejected(tmp_path):
    xli = tmp_path / ".xlii"
    _write(xli, "t", 'name="t"\n[params.prev]\nrequired=true\n[[step]]\nrun="x"\n')
    with pytest.raises(T.TaskParseError):
        T.load_pipeline(xli, "t")


def test_bad_param_name_rejected(tmp_path):
    xli = tmp_path / ".xlii"
    _write(xli, "t", 'name="t"\n[params.BadName]\n[[step]]\nrun="x"\n')
    with pytest.raises(T.TaskParseError):
        T.load_pipeline(xli, "t")


# --- P1: binding ------------------------------------------------------------- #

def _params():
    return [T.Param("q", required=True), T.Param("scope", default="src/", enum=["src/", "tests/"])]


def test_bind_positional_named_default():
    assert T.bind_task_args(_params(), ["needle", "scope=tests/"]) == {"q": "needle", "scope": "tests/"}
    assert T.bind_task_args(_params(), ["needle"]) == {"q": "needle", "scope": "src/"}
    assert T.bind_task_args(_params(), ["--scope=tests/", "q=x"]) == {"q": "x", "scope": "tests/"}


def test_bind_missing_required_fails_closed():
    with pytest.raises(T.TaskParseError):
        T.bind_task_args(_params(), [])


def test_bind_enum_violation_fails_closed():
    with pytest.raises(T.TaskParseError):
        T.bind_task_args(_params(), ["x", "scope=nope"])


def test_bind_unknown_flag_fails_closed():
    with pytest.raises(T.TaskParseError):
        T.bind_task_args(_params(), ["--nope=1"])


def test_bind_extra_positional_fails_closed():
    with pytest.raises(T.TaskParseError):
        T.bind_task_args([T.Param("q")], ["a", "b"])


def test_bind_positional_value_with_equals():
    assert T.bind_task_args([T.Param("q")], ["a=b"]) == {"q": "a=b"}


# --- P1: run end-to-end + injection safety + resume -------------------------- #

def test_run_binds_args_into_shell(tmp_path):
    st = _state(tmp_path)
    xli = tmp_path / ".xlii"
    _write(xli, "echo", 'name="echo"\n[params.msg]\nrequired=true\n[[step]]\nrun="printf {{msg}}"\n')
    p = T.load_pipeline(xli, "echo")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False, args={"msg": "hi"})
    assert out.ok and out.carry == "hi"
    assert out.steps[0].resolved == "printf hi"


def test_run_arg_is_injection_safe(tmp_path):
    st = _state(tmp_path)
    xli = tmp_path / ".xlii"
    _write(xli, "e", 'name="e"\n[params.msg]\n[[step]]\nrun="printf {{msg}}"\n')
    p = T.load_pipeline(xli, "e")
    out = T.run_pipeline(p, st.as_context_dict(), confirm_shell=False, args={"msg": "x; echo PWNED"})
    # the metachars are a single quoted token — never a second command
    assert out.steps[0].resolved == "printf 'x; echo PWNED'"


def test_new_run_persists_args_and_resume_rebinds(tmp_path, monkeypatch):
    st = _state(tmp_path)
    xli = tmp_path / ".xlii"
    _write(xli, "e",
           'name="e"\n[params.msg]\nrequired=true\n'
           '[[step]]\nrun="printf step1"\n[[step]]\nrun="printf {{msg}}"\n')
    p = T.load_pipeline(xli, "e")
    run = T.new_run(p, args={"msg": "hello"})
    assert run.args == {"msg": "hello"}

    # block step 1 via the per-step confirm gate → checkpoint + stop
    monkeypatch.setattr("xlii.tools._confirm", lambda _p: "n")
    first = T.run_pipeline(p, st.as_context_dict(), confirm_shell=True, run=run,
                           xli_dir=xli, args={"msg": "hello"})
    assert not first.ok
    saved = T.latest_run(xli, statuses={"failed"})
    assert saved is not None and saved.args == {"msg": "hello"}  # persisted

    # resume WITHOUT re-passing args → must restore them from the saved run
    resumed = T.run_pipeline(saved.pipeline(), st.as_context_dict(), carry0=saved.carry,
                             start_index=saved.cursor, confirm_shell=False,
                             run=saved, xli_dir=xli)
    assert resumed.ok
    assert any(s.resolved == "printf hello" for s in resumed.steps)


def test_render_plan_shows_param_signature(tmp_path):
    xli = tmp_path / ".xlii"
    _write(xli, "t", 'name="t"\n[params.q]\nrequired=true\nhelp="term"\n[[step]]\nrun="grep {{q}}"\n')
    plan = "\n".join(T.render_plan(T.load_pipeline(xli, "t")))
    assert "params:" in plan
    assert "{{q}}" in plan and "required" in plan and "term" in plan


# --- P1: REPL resolver (saved-vs-inline + stock-runnable-by-name) ------------ #

def test_resolve_target_binds_saved_task_args(tmp_path):
    from xlii.repl_cmds.tasks import _resolve_target_and_args
    xli = tmp_path / ".xlii"
    _write(xli, "grep", 'name="grep"\n[params.q]\nrequired=true\n[[step]]\nrun="grep {{q}}"\n')
    pipe, args = _resolve_target_and_args({}, "grep needle", xli)
    assert pipe.name == "grep" and args == {"q": "needle"}


def test_resolve_target_stock_runnable_by_name(tmp_path):
    from xlii.repl_cmds.tasks import _resolve_target_and_args
    xli = tmp_path / ".xlii"
    xli.mkdir(parents=True)
    pipe, args = _resolve_target_and_args({}, "echo-hello", xli)
    assert pipe.name == "echo-hello" and args == {}  # stock resolved, not mis-parsed as shell


def test_resolve_target_inline_pipe_takes_no_args(tmp_path):
    from xlii.repl_cmds.tasks import _resolve_target_and_args
    xli = tmp_path / ".xlii"
    xli.mkdir(parents=True)
    pipe, args = _resolve_target_and_args({}, "printf a |> printf b", xli)
    assert len(pipe.steps) == 2 and args == {}


# --- run-flags trailing after a parameterized task's args (bug: eaten as arg) - #

def test_run_flags_trailing_after_task_args():
    from xlii.repl_cmds.tasks import _parse_run_flags
    opts, target = _parse_run_flags("diff-review main --dry-run")
    assert opts["dry_run"] and target == "diff-review main"
    opts, target = _parse_run_flags("mytask a b --keep-going --yes")
    assert opts["keep_going"] and opts["yes"] and target == "mytask a b"
    opts, target = _parse_run_flags("mytask a --from data.txt")
    assert opts["from_file"] == "data.txt" and target == "mytask a"


def test_run_flags_trailing_inside_inline_pipe_stay_in_last_step():
    from xlii.repl_cmds.tasks import _parse_run_flags
    opts, target = _parse_run_flags("echo hi |> /verify --dry-run")
    assert not opts["dry_run"]
    assert target == "echo hi |> /verify --dry-run"
    pipe = T.parse_inline(target)
    assert pipe.steps[-1].body == "/verify --dry-run"

    opts, target = _parse_run_flags("rm -rf /data |> /doc add summary --yes")
    assert not opts["yes"]
    assert target == "rm -rf /data |> /doc add summary --yes"
    pipe = T.parse_inline(target)
    assert pipe.steps[-1].body == "/doc add summary --yes"

    opts, target = _parse_run_flags("echo hi |> cat --from seed.txt")
    assert opts["from_file"] is None
    assert target == "echo hi |> cat --from seed.txt"
    pipe = T.parse_inline(target)
    assert pipe.steps[-1].body == "cat --from seed.txt"


def test_run_flags_leading_and_quoted_still_work():
    from xlii.repl_cmds.tasks import _parse_run_flags
    opts, target = _parse_run_flags("--dry-run diff-review main")
    assert opts["dry_run"] and target == "diff-review main"
    opts, target = _parse_run_flags("'a |> b' --dry-run")
    assert opts["dry_run"] and target == "a |> b"


def test_dry_run_parameterized_task_end_to_end(tmp_path):
    # the reported bug: `/tasks run <task> <arg> --dry-run` rejected --dry-run as an arg
    from xlii.repl_cmds.tasks import _parse_run_flags, _resolve_target_and_args
    xli = tmp_path / ".xlii"
    _write(xli, "dr", 'name="dr"\n[params.base]\ndefault="HEAD~1"\n[[step]]\nrun="git diff {{base}}"\n')
    opts, target = _parse_run_flags("dr main --dry-run")
    assert opts["dry_run"]
    pipe, args = _resolve_target_and_args({}, target, xli)
    assert pipe.name == "dr" and args == {"base": "main"}
