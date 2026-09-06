"""The F10 Task Builder's pure model (xlii/tui/task_builder.py) — no terminal, no textual.

PipeDraft is the wizard's step list; these pin that every exit it compiles to (inline command ·
saved TOML · agent-draft command) round-trips through the real /tasks engine, so the builder can
never compose a pipe the runner won't accept."""

from __future__ import annotations

import pytest

from xlii import tasks as T
from xlii.tui.task_builder import PipeDraft, draft_command, intent_slug, quote_arg


# --- composing ----------------------------------------------------------------


def test_add_classifies_by_prefix():
    d = PipeDraft()
    shell = d.add("git diff --stat")
    agent = d.add("?summarize what changed")
    slash = d.add("/review --diff")
    assert shell.kind == T.KIND_SHELL
    assert agent.kind == T.KIND_AGENT
    assert slash.kind == T.KIND_SLASH
    assert [s.kind for s in d.steps] == [T.KIND_SHELL, T.KIND_AGENT, T.KIND_SLASH]


def test_add_rejects_empty_and_embedded_pipe():
    d = PipeDraft()
    with pytest.raises(T.TaskParseError):
        d.add("   ")
    with pytest.raises(T.TaskParseError):
        d.add("echo a |> echo b")   # one step at a time — the |> chain is the list itself
    assert d.steps == []


def test_remove_pop_move_respect_bounds():
    d = PipeDraft()
    d.add("one")
    d.add("two")
    d.add("three")
    assert d.move(0, 1) == 1                       # swap down
    assert [s.body for s in d.steps] == ["two", "one", "three"]
    assert d.move(0, -1) == 0                      # top can't move up — index unchanged
    assert d.move(9, 1) == 9                       # out of range is a no-op
    popped = d.pop(1)
    assert popped is not None and popped.body == "one"
    out_of_range = d.pop(9)
    assert out_of_range is None
    d.remove(0)
    d.remove(5)                                    # out of range is a no-op
    assert [s.body for s in d.steps] == ["three"]


def test_rows_are_numbered_with_kind_tags():
    d = PipeDraft()
    d.add("pytest -q")
    d.add("?which test failed")
    assert d.rows() == ["1 [shell] pytest -q", "2 [agent] ?which test failed"]


# --- the run exit: inline command --------------------------------------------


def test_inline_round_trips_through_parse_inline():
    d = PipeDraft()
    d.add("git diff --stat")
    d.add("?summarize what changed")
    d.add("/doc add notes {{prev}}")
    pipeline = T.parse_inline(d.inline())
    assert [s.kind for s in pipeline.steps] == [T.KIND_SHELL, T.KIND_AGENT, T.KIND_SLASH]
    assert pipeline.steps[1].body == "summarize what changed"


def test_command_quotes_and_flags():
    d = PipeDraft()
    d.add("echo hi")
    d.add("?uppercase this")
    assert d.command() == "/tasks run 'echo hi |> ?uppercase this'"
    assert d.command(background=True).endswith(" --background")


def test_quote_arg_prefers_single_falls_back_to_double():
    assert quote_arg("plain") == "'plain'"
    assert quote_arg("it's here") == '"it\'s here"'
    # both quote chars present: least-bad single-quote fallback (TOML is the lossless path)
    assert quote_arg("""both ' and \" here""").startswith("'")


def test_command_round_trips_through_the_repl_flag_parser():
    from xlii.repl_cmds.tasks import _parse_run_flags

    d = PipeDraft()
    d.add("git log --oneline -5")
    d.add('?pick the "big" one')                    # a double quote inside the pipe
    opts, target = _parse_run_flags(d.command(background=True).removeprefix("/tasks run "))
    assert opts["background"] is True
    assert T.parse_inline(target).steps[1].kind == T.KIND_AGENT


# --- the save exit: TOML ------------------------------------------------------


def test_toml_round_trips_through_load_pipeline(tmp_path):
    d = PipeDraft()
    d.add("git diff --stat")
    d.add("?summarize as 3 bullets")
    d.add("/doc add notes {{prev}}")
    T.write_pipeline_toml(tmp_path, "relnotes", d.toml("relnotes", description="diff → notes"))
    loaded = T.load_pipeline(tmp_path, "relnotes")
    assert loaded.name == "relnotes"
    assert loaded.description == "diff → notes"
    assert [s.kind for s in loaded.steps] == [T.KIND_SHELL, T.KIND_AGENT, T.KIND_SLASH]
    assert loaded.steps[1].body == "summarize as 3 bullets"


def test_toml_escapes_quotes_and_newlines(tmp_path):
    d = PipeDraft()
    d.add("""echo "double" and 'single'""")
    d.steps[0].body += "\nsecond line"              # a body an editor could produce
    T.write_pipeline_toml(tmp_path, "quoty", d.toml("quoty"))
    loaded = T.load_pipeline(tmp_path, "quoty")
    assert loaded.steps[0].body == """echo "double" and 'single'\nsecond line"""


# --- the draft exit: /tasks new --from ----------------------------------------


def test_intent_slug_is_short_and_file_safe():
    assert intent_slug("Diff since the last tag, summarize!") == "diff-since-the-last"
    assert intent_slug("") == "task"
    assert intent_slug("///???") == "task"


def test_draft_command_parses_back_to_name_and_description():
    from xlii.repl_cmds.tasks import _parse_new

    cmd = draft_command("diff since the last tag, summarize as bullets")
    assert cmd.startswith("/tasks new diff-since-the-last --from ")
    spec = _parse_new(cmd.removeprefix("/tasks new "))
    assert spec["name"] == "diff-since-the-last"
    assert spec["description"] == "diff since the last tag, summarize as bullets"


# --- the builder PANE (V2c — the minibuffer rule), headless -------------------


def _pane(**kw):
    from xlii.tui.task_builder import TaskBuilderPane

    return TaskBuilderPane(**kw)


def test_pane_enter_runs_the_primary_claim_action():
    from xlii.panes import CLAIM_INPUT

    pane = _pane()
    # no steps: the primary action is ADD (Enter claims the line for a new step)
    acts = pane.actions()
    assert acts[0].name == "add" and acts[0].outcome.kind == CLAIM_INPUT
    pane._add_step("git diff --stat")
    # with steps: the primary action is EDIT (Enter claims the selected step, seeded)
    acts = pane.actions()
    assert [a.name for a in acts] == ["edit", "add", "run", "run-bg", "save", "draft"]
    assert acts[0].outcome.kind == CLAIM_INPUT
    assert acts[0].outcome.claim.initial == "git diff --stat"


def test_pane_run_exits_are_prefill_review_before_run():
    from xlii.panes import PREFILL

    pane = _pane()
    pane._add_step("pytest -q")
    acts = {a.name: a for a in pane.actions()}
    assert acts["run"].outcome.kind == PREFILL
    assert acts["run"].outcome.text == "/tasks run 'pytest -q'"
    assert acts["run-bg"].outcome.text == "/tasks run 'pytest -q' --background"


def test_pane_edit_and_remove_and_save_flow_through_callbacks():
    changes, errors, saved = [], [], []
    pane = _pane(on_change=lambda: changes.append(True),
                 on_error=errors.append,
                 on_save=lambda draft, name: saved.append(name))
    pane._add_step("one")
    pane._add_step("two")
    assert pane.render().rows[1].selected               # the new step is the cursor

    pane._replace_step(1, "two v2")                     # the edit claim's submit
    assert [s.body for s in pane.draft.steps] == ["one", "two v2"]
    pane._replace_step(0, "")                           # empty → parse error, no clobber
    assert pane.draft.steps[0].body == "one" and errors

    assert pane.handle("back")                          # ⌫ removes the selected step
    assert [s.body for s in pane.draft.steps] == ["one"]
    pane._save_as("relnotes")                           # the save claim's submit
    assert saved == ["relnotes"]
    assert len(changes) >= 3


def test_pane_draft_exit_prefills_tasks_new_from():
    prefilled = []
    pane = _pane(on_prefill=prefilled.append)
    act = next(a for a in pane.actions() if a.name == "draft")
    act.outcome.claim.on_submit("diff since the last tag")
    assert prefilled == ["/tasks new diff-since-the-last --from 'diff since the last tag'"]


def test_pane_render_and_selection_track_the_draft():
    pane = _pane(saved=["nightly"])
    assert pane.render().empty and "nightly" in pane.render().title
    assert pane.selection().node is None
    pane._add_step("?summarize")
    rows = pane.render().rows
    assert rows[0].text.startswith("1 [agent]") and rows[0].selected
    assert pane.selection().node.address == "tasks://builder/1"
    assert pane.select_index(0) is True
    assert pane.select_index(5) is False
