"""Integration: the F10 Task Builder PANE inside the real XliiApp (V2c).

Proves the minibuffer-rule conversion: F10 (input-focused delegation AND the app
binding) and Tools→Task builder… place a TaskBuilderPane into the Dock — no modal.
Steps compose through CLAIM_INPUT (the input line morphs); the run/draft exits seed
the ONE command line via PREFILL (review-before-run); save writes the .toml recipe.
Driven through Textual's run_test() pilot (no TTY); skipped without [tui]."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from xlii import tasks as T  # noqa: E402
from xlii.tui.dock_surface import DockSurface, register_dock_view  # noqa: E402
from xlii.tui.task_builder import TaskBuilderPane  # noqa: E402
from xlii.tui_textual import XliiApp, _PromptInput  # noqa: E402
from xlii.workbench import BUILTIN_WORKBENCHES  # noqa: E402


def _fake_agent():
    from xlii.agent import SessionState

    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
        howto_mode=False, history=[], model_override=None, session=SessionState(),
    )


def _app(tmp_path):
    xli = tmp_path / ".xlii"
    xli.mkdir(exist_ok=True)
    st = SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj", xli_dir=xli),
        agent=_fake_agent(),
        workbench=BUILTIN_WORKBENCHES["code"],
    )
    app = XliiApp(project_name="proj", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    return app, st


def _builder(app) -> TaskBuilderPane:
    surface = app.query_one(DockSurface)
    pane = surface.dock.pane()
    assert isinstance(pane, TaskBuilderPane)
    return pane


def _fire(app, action_name: str) -> None:
    """Dispatch one of the builder pane's actions through the real Dock path."""
    surface = app.query_one(DockSurface)
    pane = surface.dock.pane()
    action = next(a for a in pane.actions() if a.name == action_name)
    surface._dispatch_outcome(action.outcome, from_slot=surface.dock.focused)


async def _answer(pilot, app, text: str) -> None:
    """Type into the claimed input line and submit it."""
    from xlii.tui.input_surface import _PromptInput as _PI

    inp = app.query_one("#input", _PI)
    inp.text = text
    await pilot.press("enter")
    await pilot.pause()


# --- opening the builder ------------------------------------------------------


def test_f10_places_the_builder_pane_no_modal(tmp_path):
    register_dock_view("vfs")
    app, _st = _app(tmp_path)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            from textual.screen import ModalScreen

            await pilot.press("f10")           # input holds focus → the delegated fkey path
            await pilot.pause()
            _builder(app)
            assert not isinstance(app.screen, ModalScreen)   # the builder is a PANE

    asyncio.run(scenario())


def test_tools_menu_places_the_builder_pane(tmp_path):
    register_dock_view("vfs")
    app, _st = _app(tmp_path)
    ids = [i[0] for i in app._menu_items("Tools")]
    assert "tools:taskbuilder" in ids

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app._run_menu_action("tools:taskbuilder")
            await pilot.pause()
            _builder(app)

    asyncio.run(scenario())


# --- composing through the claimed input line ----------------------------------


def test_add_step_claims_the_input_line(tmp_path):
    register_dock_view("vfs")
    app, _st = _app(tmp_path)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_task_builder()
            await pilot.pause()
            await pilot.pause()
            pane = _builder(app)
            _fire(app, "add")
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            assert inp.claim_active                       # the line morphs — no popup
            await _answer(pilot, app, "git diff --stat")
            assert [s.body for s in pane.draft.steps] == ["git diff --stat"]
            assert not inp.claim_active                   # released back to the REPL
            rows = pane.render().rows
            assert len(rows) == 1 and rows[0].selected    # the new step is the cursor

    asyncio.run(scenario())


def test_enter_on_a_step_claims_it_for_editing(tmp_path):
    register_dock_view("vfs")
    app, _st = _app(tmp_path)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_task_builder()
            await pilot.pause()
            await pilot.pause()
            pane = _builder(app)
            pane._add_step("?summarize")
            primary = pane.actions()[0]
            assert primary.name == "edit"                 # Enter runs the edit claim
            _fire(app, "edit")
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            assert inp.claim_active and inp.text == "?summarize"   # seeded for editing
            await _answer(pilot, app, "?summarize the diff")
            assert [s.body for s in pane.draft.steps] == ["summarize the diff"]

    asyncio.run(scenario())


def test_backspace_removes_the_selected_step(tmp_path):
    register_dock_view("vfs")
    app, _st = _app(tmp_path)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_task_builder()
            await pilot.pause()
            await pilot.pause()
            pane = _builder(app)
            pane._add_step("one")
            pane._add_step("two")
            assert pane.handle("back")                    # ⌫ — composer delete idiom
            assert [s.body for s in pane.draft.steps] == ["one"]
            pane.handle("back")                           # last step out
            assert pane.draft.steps == []
            assert not pane.handle("back")                # empty → falls through to the surface

    asyncio.run(scenario())


def test_bad_step_flashes_the_error_and_keeps_the_draft(tmp_path):
    register_dock_view("vfs")
    app, _st = _app(tmp_path)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_task_builder()
            await pilot.pause()
            await pilot.pause()
            pane = _builder(app)
            _fire(app, "add")
            await pilot.pause()
            await _answer(pilot, app, "echo a |> echo b")      # one step at a time
            assert pane.draft.steps == []                 # rejected, draft untouched

    asyncio.run(scenario())


# --- the exits (nothing executes from the surface) ------------------------------


def test_run_seeds_the_compiled_command_for_review(tmp_path):
    register_dock_view("vfs")
    app, _st = _app(tmp_path)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_task_builder()
            await pilot.pause()
            await pilot.pause()
            pane = _builder(app)
            pane._add_step("git diff --stat")
            pane._add_step("?summarize what changed")
            _fire(app, "run")
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            assert inp.text == "/tasks run 'git diff --stat |> ?summarize what changed'"
            assert app.focused is inp                     # ready to review + Enter

    asyncio.run(scenario())


def test_run_bg_appends_the_background_flag(tmp_path):
    register_dock_view("vfs")
    app, _st = _app(tmp_path)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_task_builder()
            await pilot.pause()
            await pilot.pause()
            pane = _builder(app)
            pane._add_step("pytest -q")
            _fire(app, "run-bg")
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            assert inp.text == "/tasks run 'pytest -q' --background"

    asyncio.run(scenario())


def test_save_claims_a_name_and_writes_the_toml(tmp_path):
    register_dock_view("vfs")
    app, st = _app(tmp_path)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_task_builder()
            await pilot.pause()
            await pilot.pause()
            pane = _builder(app)
            pane._add_step("git diff --stat")
            pane._add_step("/doc add notes {{prev}}")
            _fire(app, "save")
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            assert inp.claim_active                       # the name ask owns the line
            await _answer(pilot, app, "relnotes")

            loaded = T.load_pipeline(st.project.xli_dir, "relnotes")
            assert [s.kind for s in loaded.steps] == [T.KIND_SHELL, T.KIND_SLASH]
            # the saved recipe is one Enter from its first run
            assert app.query_one("#input", _PromptInput).text == "/tasks run relnotes"

    asyncio.run(scenario())


def test_draft_claims_the_intent_and_seeds_tasks_new_from(tmp_path):
    register_dock_view("vfs")
    app, _st = _app(tmp_path)

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_task_builder()
            await pilot.pause()
            await pilot.pause()
            _builder(app)
            _fire(app, "draft")
            await pilot.pause()
            await _answer(pilot, app, "diff since the last tag, summarize as bullets")
            inp = app.query_one("#input", _PromptInput)
            assert inp.text == ("/tasks new diff-since-the-last --from "
                                "'diff since the last tag, summarize as bullets'")

    asyncio.run(scenario())


def test_save_refuses_to_clobber_an_existing_recipe(tmp_path):
    register_dock_view("vfs")
    app, st = _app(tmp_path)
    T.scaffold_pipeline(st.project.xli_dir, "relnotes")
    before = T.pipeline_path(st.project.xli_dir, "relnotes").read_text()

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            from xlii.tui.task_builder import PipeDraft

            draft = PipeDraft()
            draft.add("echo clobber")
            app._write_pipeline(draft, "relnotes")
            await pilot.pause()
            assert T.pipeline_path(st.project.xli_dir, "relnotes").read_text() == before

    asyncio.run(scenario())


def test_saved_names_show_in_the_builder_title(tmp_path):
    register_dock_view("vfs")
    app, st = _app(tmp_path)
    T.scaffold_pipeline(st.project.xli_dir, "relnotes")

    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.action_task_builder()
            await pilot.pause()
            await pilot.pause()
            pane = _builder(app)
            assert "relnotes" in pane.render().title

    asyncio.run(scenario())
