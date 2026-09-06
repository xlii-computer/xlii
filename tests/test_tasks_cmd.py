"""Vector F — /tasks REPL command surface (dispatch, flags, subcommands)."""

from __future__ import annotations

from pathlib import Path

from helpers import FakeConsole, make_agent, make_cfg, script_iterations
from xlii import tasks as T
from xlii.commands import find_repl_command
from xlii.repl_cmds import register_all
from xlii.repl_cmds.tasks import _parse_run_flags, _strip_code_fence, _tasks_handler
from xlii.repl_state import REPLState

register_all()


def _state(tmp_path: Path, scope: str = "code") -> REPLState:
    agent = make_agent(tmp_path, cfg=make_cfg())
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    st = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                   cfg=agent.cfg, pool=agent.pool)
    st.command_scope = scope
    return st


def test_command_registers_in_both_repls():
    assert find_repl_command("/tasks", "code") is not None
    assert find_repl_command("/tasks", "chat") is not None


def test_parse_run_flags():
    opts, target = _parse_run_flags("--dry-run --yes --keep-going 'echo a |> ?b'")
    assert opts["dry_run"] and opts["yes"] and opts["keep_going"]
    assert target == "echo a |> ?b"

    opts, target = _parse_run_flags("--from seed.txt printf hi")
    assert opts["from_file"] == "seed.txt"
    assert target == "printf hi"

    opts, target = _parse_run_flags("plainname")
    assert target == "plainname" and not any(
        opts[k] for k in ("dry_run", "yes", "keep_going")
    )


def test_parse_run_flags_trailing_flag_and_quote_extraction():
    # Regression (the pacman /tasks run): `--keep-going` came AFTER the quoted pipe.
    # The trailing flag defeated quote-stripping, gluing a stray `'` onto step 1 —
    # a slash step `/browse --tree` then parsed as SHELL → "unterminated quoted
    # string" on resume. Flags must parse when trailing, and the wrapper quotes
    # must be extracted cleanly.
    rest = "'\n  /browse --tree\n|> ?analyze it\n|> /verify\n' --keep-going"
    opts, target = _parse_run_flags(rest)
    assert opts["keep_going"] is True            # trailing flag now parsed
    assert not target.startswith("'")            # wrapper quote stripped
    p = T.parse_inline(target)
    assert p.steps[0].kind == T.KIND_SLASH        # /browse --tree, not shell+stray-quote
    assert p.steps[0].body == "/browse --tree"
    assert p.steps[-1].kind == T.KIND_SLASH       # /verify, no absorbed flag
    assert p.steps[-1].body == "/verify"

    # Leading + quoted pipe + trailing flag all coexist.
    opts, target = _parse_run_flags("--yes 'echo hi |> ?b' --keep-going")
    assert opts["yes"] and opts["keep_going"]
    assert target == "echo hi |> ?b"


def test_run_inline_end_to_end(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks run --yes 'printf hi |> tr a-z A-Z'", st.as_context_dict())
    text = st.console.text
    assert "HI" in text
    assert "pipeline complete" in text


def test_dry_run_executes_nothing(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks run --dry-run 'echo a |> ?summarize |> /doc add x'", st.as_context_dict())
    text = st.console.text
    assert "dry run" in text
    assert "[shell]" in text and "[agent]" in text and "[slash]" in text
    # no run artifacts persisted by a dry run
    assert T.latest_run(st.project.xli_dir) is None


def test_run_rejects_unknown_slash_step(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks run --yes 'printf hi |> /nopecmd {{prev}}'", st.as_context_dict())
    assert "unknown slash command" in st.console.text


def test_bare_form_without_run_verb(tmp_path):
    st = _state(tmp_path)
    # `/tasks 'a |> b'` with no explicit `run` verb still pipes.
    _tasks_handler("/tasks --yes 'printf hi |> cat'", st.as_context_dict())
    # falls through to run since it contains the fat pipe
    assert "pipeline complete" in st.console.text or "result carry" in st.console.text


def test_list_and_new_and_show(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks list", st.as_context_dict())
    assert "saved pipelines" in st.console.text

    st.console.lines.clear()
    _tasks_handler("/tasks new demo", st.as_context_dict())
    assert "scaffolded" in st.console.text
    assert T.pipeline_path(st.project.xli_dir, "demo").exists()

    st.console.lines.clear()
    _tasks_handler("/tasks list", st.as_context_dict())
    assert "demo" in st.console.text

    st.console.lines.clear()
    _tasks_handler("/tasks show demo", st.as_context_dict())
    assert "[shell]" in st.console.text


def test_list_badges_bound_startup_task(tmp_path, monkeypatch):
    from xlii.session_boot import StartupBinding, save_startup_binding

    st = _state(tmp_path)
    T.scaffold_pipeline(st.project.xli_dir, "nightly")
    T.scaffold_pipeline(st.project.xli_dir, "deploy")
    store = tmp_path / "startup.json"
    monkeypatch.setattr("xlii.session_boot.STARTUP_BINDINGS_FILE", store)
    save_startup_binding(st.project.project_root, StartupBinding(task="nightly"))
    _tasks_handler("/tasks list", st.as_context_dict())
    text = st.console.text
    assert "nightly" in text and "startup" in text
    nightly_line = next(ln for ln in st.console.lines if "nightly" in ln)
    deploy_line = next(ln for ln in st.console.lines if "deploy" in ln)
    assert "startup" in nightly_line
    assert "startup" not in deploy_line


def test_run_saved_pipeline_by_name(tmp_path):
    st = _state(tmp_path)
    T.tasks_dir(st.project.xli_dir).mkdir(parents=True, exist_ok=True)
    T.pipeline_path(st.project.xli_dir, "greet").write_text(
        'name = "greet"\n[[step]]\nrun = "printf hello"\n'
    )
    _tasks_handler("/tasks run --yes greet", st.as_context_dict())
    assert "hello" in st.console.text
    assert "pipeline complete" in st.console.text


def test_status_and_cancel(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks status", st.as_context_dict())
    assert "no pipeline runs yet" in st.console.text

    st.console.lines.clear()
    _tasks_handler("/tasks run --yes 'printf done'", st.as_context_dict())
    st.console.lines.clear()
    _tasks_handler("/tasks status", st.as_context_dict())
    assert "done" in st.console.text


def test_unknown_subcommand(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks frobnicate", st.as_context_dict())
    assert "unknown /tasks subcommand" in st.console.text


def test_resume_when_nothing_to_resume(tmp_path):
    st = _state(tmp_path)
    _tasks_handler("/tasks resume", st.as_context_dict())
    assert "no interrupted pipeline" in st.console.text


def test_run_from_file_seeds_carry0(tmp_path):
    st = _state(tmp_path)
    seed = tmp_path / "seed.txt"
    seed.write_text("SEEDED")
    _tasks_handler(f"/tasks run --yes --from {seed} cat", st.as_context_dict())
    assert "SEEDED" in st.console.text


def test_strip_code_fence():
    assert _strip_code_fence("```toml\nname = 'x'\n```") == "name = 'x'"
    assert _strip_code_fence("no fence here") == "no fence here"


def test_new_from_description_drafts_and_opens_editor(tmp_path, monkeypatch):
    opened: list[Path] = []
    monkeypatch.setattr(
        "xlii.repl_cmds.tasks._open_editor",
        lambda console, path: opened.append(path),
    )
    agent = make_agent(tmp_path, cfg=make_cfg())
    script_iterations(agent, ('```toml\nname = "auto"\n[[step]]\nrun = "echo hi"\n```', None))
    (tmp_path / ".xlii").mkdir(parents=True, exist_ok=True)
    st = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                   cfg=agent.cfg, pool=agent.pool)
    st.command_scope = "code"

    _tasks_handler('/tasks new auto --from "echo hi then summarize"', st.as_context_dict())

    path = T.pipeline_path(st.project.xli_dir, "auto")
    assert path.exists()
    assert 'name = "auto"' in path.read_text()
    assert opened == [path]                      # opened for review
    # the drafted pipeline is loadable (valid TOML schema)
    p = T.load_pipeline(st.project.xli_dir, "auto")
    assert [s.kind for s in p.steps] == [T.KIND_SHELL]
