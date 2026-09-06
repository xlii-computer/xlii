"""TasksPane ``new`` — chained CLAIM_INPUT compose flow (panel-native builder)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii.panes import CLAIM_INPUT, PREFILL, RETARGET_SLOT
from xlii.tui.task_builder import (
    ClaimPrefillBridge,
    PipeDraft,
    TaskComposeFlow,
    new_task_action,
    save_prefill_command,
)


def test_save_prefill_command_is_the_persist_verb_with_the_name():
    draft = PipeDraft()
    draft.add("printf hi")
    assert save_prefill_command("nightly", draft) == "/tasks new nightly --from 'printf hi'"


def test_save_prefill_command_multi_step_carries_the_pipe():
    draft = PipeDraft()
    draft.add("printf hi")
    draft.add("?summarize")
    assert save_prefill_command("rel", draft) == (
        "/tasks new rel --from 'printf hi |> ?summarize'"
    )


def test_compose_flow_chains_name_then_steps_and_prefills():
    pending: list = []
    prefilled: list[str] = []

    def claim(ic):
        pending.append(ic)
        return True

    flow = TaskComposeFlow(claim=claim, prefill=prefilled.append)
    action = flow.start_action()
    assert action.outcome.kind == CLAIM_INPUT
    action.outcome.claim.on_submit("nightly")
    pending[0].on_submit("printf hi")
    pending[1].on_submit("")
    assert prefilled == ["/tasks new nightly --from 'printf hi'"]


def test_compose_invalid_name_reasks_with_visible_note():
    pending: list = []
    prefilled: list[str] = []
    errors: list[str] = []

    flow = TaskComposeFlow(
        claim=lambda c: pending.append(c) or True,
        prefill=prefilled.append,
        on_error=errors.append,
    )
    action = flow.start_action()
    action.outcome.claim.on_submit("bad name")   # space → invalid
    assert errors == ["task names are letters/digits/._- only"]
    assert len(pending) == 1                     # re-asked, flow not dead
    assert pending[0].prompt.startswith("task names are letters/digits/._- only — ")
    pending[0].on_submit("good-name")            # recovery continues the chain
    pending[1].on_submit("printf hi")
    pending[2].on_submit(".")
    assert prefilled == ["/tasks new good-name --from 'printf hi'"]


def test_compose_esc_cancels_cleanly():
    pending: list = []
    flow = TaskComposeFlow(claim=lambda c: pending.append(c) or True, prefill=lambda _t: None)
    action = flow.start_action()
    action.outcome.claim.on_cancel()
    action.outcome.claim.on_submit("nightly")
    pending[-1].on_cancel()
    assert flow._name == ""


def test_tasks_pane_new_action_present(tmp_path, monkeypatch):
    from xlii import active_session
    from xlii.panes.tasks import TasksPane

    xli = tmp_path / ".xlii"
    xli.mkdir()
    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(project=SimpleNamespace(xli_dir=xli), attached_docs=[]),
    )
    acts = TasksPane("tasks://").actions()
    assert acts[0].name == "new"
    assert acts[0].outcome.kind == RETARGET_SLOT
    assert acts[0].outcome.address == "taskmake://"


def test_tasks_pane_empty_still_offers_new(monkeypatch):
    from xlii import active_session
    from xlii.panes.tasks import TasksPane

    monkeypatch.setattr(active_session, "_ACTIVE", None)
    acts = TasksPane("tasks://").actions()
    names = [a.name for a in acts]
    assert names[0] == "new"
    assert "binds" in names


def test_new_task_action_uses_bridge():
    prefilled: list[str] = []
    pending: list = []
    ClaimPrefillBridge.bind(
        claim=lambda c: pending.append(c) or True,
        prefill=prefilled.append,
    )
    action = new_task_action()
    action.outcome.claim.on_submit("rel")
    pending[0].on_submit("echo hi")
    pending[1].on_submit("")
    assert prefilled == ["/tasks new rel --from 'echo hi'"]


def test_tasks_pane_new_chains_in_tui(tmp_path, monkeypatch):
    pytest.importorskip("textual")
    import asyncio

    from xlii.panes.tasks import TasksPane
    from xlii.tui.dock_surface import DockSurface, register_dock_view
    from xlii.tui.input_surface import _PromptInput
    from xlii.tui.task_builder import ClaimPrefillBridge
    from xlii.tui_textual import XliiApp

    def _fake_agent():
        from xlii.agent import SessionState
        return SimpleNamespace(
            console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
            howto_mode=False, history=[], model_override=None, session=SessionState(),
        )

    xli = tmp_path / ".xlii"
    xli.mkdir()
    st = SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj", xli_dir=xli),
        agent=_fake_agent(),
    )
    from xlii import active_session

    monkeypatch.setattr(
        active_session, "_ACTIVE",
        SimpleNamespace(project=SimpleNamespace(xli_dir=xli), attached_docs=[]),
    )
    app = XliiApp(project_name="proj", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    register_dock_view("vfs")

    async def body():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.action_doorway("t")
            await pilot.pause()
            surface = app.query_one("#panel").query_one(DockSurface)
            sink = surface.dock._input_sink
            assert sink is not None
            ClaimPrefillBridge.bind(claim=sink.claim, prefill=sink.prefill)
            pane = TasksPane("tasks://")
            surface.dock.place(surface.dock.focused, pane, focus=True)
            new_act = next(a for a in pane.actions() if a.name == "new")
            surface._dispatch_outcome(new_act.outcome, from_slot=surface.dock.focused)
            await pilot.pause()
            mounted = surface.dock.slots[surface.dock.focused]
            from xlii.panes.task_make import TaskMakePane

            assert isinstance(mounted, TaskMakePane)
            leaves = [r for r in mounted.render().rows if r.kind == "leaf"]
            si = next(n for n, r in enumerate(leaves) if r.address.endswith("/scaffold"))
            mounted.select_index(si)
            seed = next(a for a in mounted.actions() if a.name == "seed")
            assert seed.outcome.kind == PREFILL
            surface._dispatch_outcome(seed.outcome, from_slot=surface.dock.focused)
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            assert inp.text.startswith("/tasks new ")

    asyncio.run(body())
