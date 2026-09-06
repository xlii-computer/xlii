"""CLAIM_INPUT — the TUI fulfilment: the input line morphs for one ask (godzilla-mothra V0c).

The minibuffer rule end-to-end in the real app: claim → the line relabels and seeds →
Enter answers the ask (callback) → release restores the stashed REPL draft + prompt; Esc
ALWAYS releases; a second claim is rejected (single-tenant). Driven through Textual's
run_test() pilot (no TTY); skipped without [tui]. Harness mirrors test_dock_surface_app.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from xlii.panes import CLAIM_INPUT, InputClaim, Outcome  # noqa: E402
from xlii.tui.input_surface import _PromptInput  # noqa: E402
from xlii.tui_textual import XliiApp  # noqa: E402


def _fake_agent():
    from xlii.agent import SessionState

    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
        howto_mode=False, history=[], model_override=None, session=SessionState(),
    )


def _app(tmp_path):
    st = SimpleNamespace(shell_cwd=tmp_path, project=SimpleNamespace(project_root=tmp_path, name="proj"),
                         agent=_fake_agent())
    app = XliiApp(project_name="proj", agent=st.agent, run_turn=lambda q: ("", set(), None), state=st)
    return app, st


def _ask(submits, cancels=None, prompt="task name:", initial=""):
    return InputClaim(
        prompt=prompt,
        on_submit=submits.append,
        initial=initial,
        on_cancel=None if cancels is None else (lambda: cancels.append(True)),
    )


# --- the happy path: claim → relabel → submit → callback → release ------------


def test_claim_morphs_submit_answers_and_releases(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            box = app.query_one("#input-box")
            # an in-progress REPL draft the claim must stash and restore
            inp.text = "half-typed draft"
            inp.cursor_location = (0, 4)
            await pilot.pause()

            submits, cancels = [], []
            assert inp.claim_input(_ask(submits, cancels, initial="old")) is True
            await pilot.pause()
            assert inp.claim_active
            assert inp.text == "old"  # the ask's initial value, not the draft
            assert box.border_title == "task name:"  # the prompt relabel
            assert app.focused is inp

            await pilot.press("v", "2", "enter")
            await pilot.pause()
            assert submits == ["oldv2"]
            assert cancels == []  # answered asks never cancel
            # released: draft + cursor + frame title all restored
            assert not inp.claim_active
            assert inp.text == "half-typed draft"
            assert inp.cursor_location == (0, 4)
            assert box.border_title is None

    asyncio.run(body())


def test_commander_hotkeys_are_inert_during_a_claim(tmp_path):
    """C7 regression: while a claim owns the input line, the commander surface
    (F-row, ctrl+b tab-focus, doorway keys) must NOT fire — they'd steal focus
    and strand the ask where Esc can no longer reach it."""
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            fkeys, tabs = [], []
            app._handle_fkey = lambda key: fkeys.append(key)
            app.action_focus_tabs = lambda: tabs.append(True)

            assert inp.claim_input(_ask([], prompt="new step:")) is True
            await pilot.pause()
            assert inp.claim_active

            await pilot.press("f2")      # would open home:// and steal focus
            await pilot.press("ctrl+b")  # would focus the tab bar
            await pilot.pause()

            assert fkeys == [] and tabs == []   # both swallowed, no action fired
            assert inp.claim_active             # ask not stranded
            assert app.focused is inp

            await pilot.press("escape")         # Esc still the one way out
            await pilot.pause()
            assert not inp.claim_active

    asyncio.run(body())


def test_submitted_value_is_stripped(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            submits = []
            inp.claim_input(_ask(submits, initial="  padded  "))
            await pilot.press("enter")
            await pilot.pause()
            assert submits == ["padded"]

    asyncio.run(body())


# --- Esc: the release contract -------------------------------------------------


def test_escape_always_releases_and_cancels(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            box = app.query_one("#input-box")
            inp.text = "draft"
            await pilot.pause()

            submits, cancels = [], []
            inp.claim_input(_ask(submits, cancels, initial="typed-then-abandoned"))
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert cancels == [True]  # the ask ended without an answer
            assert submits == []
            assert not inp.claim_active
            assert inp.text == "draft"  # back in the normal REPL input
            assert box.border_title is None

    asyncio.run(body())


# --- the double-claim rule: single-tenant, rejected never queued ----------------


def test_second_claim_is_rejected_first_stays_intact(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            box = app.query_one("#input-box")

            first, second, second_cancels = [], [], []
            assert inp.claim_input(_ask(first, prompt="first:", initial="one")) is True
            await pilot.pause()
            # rejected — and the widget invokes NO callbacks on refusal (the
            # dispatcher owns refusal notification, proven in the dock test below)
            assert inp.claim_input(_ask(second, second_cancels, prompt="second:")) is False
            assert inp.text == "one" and box.border_title == "first:"
            assert second == [] and second_cancels == []

            await pilot.press("enter")
            await pilot.pause()
            assert first == ["one"]  # Enter answered the FIRST (surviving) ask
            assert second == []

    asyncio.run(body())


# --- Enter variants --------------------------------------------------------------


def test_empty_enter_is_a_noop_ask_stays(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            submits, cancels = [], []
            inp.claim_input(_ask(submits, cancels))
            await pilot.press("enter")  # an empty line is not an answer
            await pilot.pause()
            assert inp.claim_active and submits == [] and cancels == []
            await pilot.press("a", "enter")
            await pilot.pause()
            assert submits == ["a"] and not inp.claim_active

    asyncio.run(body())


def test_ctrl_enter_submits_multiline_claim(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            submits = []
            inp.claim_input(_ask(submits, initial="line1"))
            await pilot.press("shift+enter", "x")  # newline: plain Enter now edits, not submits
            await pilot.press("enter")
            await pilot.pause()
            assert inp.claim_active and submits == []
            await pilot.press("ctrl+enter")  # Ctrl+Enter always answers the ask
            await pilot.pause()
            assert submits == ["line1\nx\n"] or submits == ["line1\nx"]
            assert not inp.claim_active

    asyncio.run(body())


# --- the minibuffer rule holds against the REPL's own chrome --------------------


def test_no_popup_opens_over_a_claimed_line(tmp_path):
    """Typing '/' in a claimed line must NOT open the slash popup (the claimed line
    is a panel's input, not the REPL), and Enter answers the ask with the raw text."""
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            submits = []
            inp.claim_input(_ask(submits))
            await pilot.press("slash", "q", "u")
            await pilot.pause()
            assert not app._popup_open  # no popup over an ask — the minibuffer rule
            await pilot.press("enter")
            await pilot.pause()
            assert submits == ["/qu"]

    asyncio.run(body())


def test_chained_claim_keeps_popups_shut_and_draft_riding(tmp_path):
    """A claim made from inside on_submit (the chained-ask path) works, keeps the
    REPL draft riding through to the final release, and its slash-y initial value
    opens no popup despite the release+seed double Changed."""
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            box = app.query_one("#input-box")
            inp.text = "the draft"
            await pilot.pause()

            first, second = [], []

            def _chain(answer):
                first.append(answer)
                assert inp.claim_input(_ask(second, prompt="second:", initial="/qu")) is True

            inp.claim_input(InputClaim(prompt="first:", on_submit=_chain, initial="one"))
            await pilot.press("enter")
            await pilot.pause()
            assert first == ["one"]
            assert inp.claim_active and inp.text == "/qu"  # the chained ask took the line
            assert box.border_title == "second:"
            assert not app._popup_open  # seeded "/qu" opened nothing

            await pilot.press("i", "t", "enter")
            await pilot.pause()
            assert second == ["/quit"]
            assert not inp.claim_active
            assert inp.text == "the draft"  # the draft rode through BOTH asks
            assert box.border_title is None

    asyncio.run(body())


def test_history_keys_never_walk_into_a_claim(tmp_path):
    """Up/Down while claimed neither replace the answer with REPL history nor leak
    the claim's text into the app's history draft."""
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            app._hist = ["/quit"]  # a real history entry Up would normally recall
            app._hist_pos = 1
            app._hist_draft = ""
            submits = []
            inp.claim_input(_ask(submits, initial="answer"))
            await pilot.press("up", "ctrl+up", "down", "ctrl+down")
            await pilot.pause()
            assert inp.text == "answer"  # history never reached the claimed line
            assert app._hist_draft == ""  # and the claim text never leaked out
            await pilot.press("enter")
            await pilot.pause()
            assert submits == ["answer"]

    asyncio.run(body())


def test_palette_and_tool_drawer_stay_shut_while_claimed(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            submits = []
            inp.claim_input(_ask(submits, initial="typed-answer"))
            await pilot.press("ctrl+k", "ctrl+t")
            await pilot.pause()
            assert not app._palette_active  # ctrl+k swallowed — no palette wipe
            assert inp.text == "typed-answer"  # the in-progress answer survived
            assert inp.claim_active

    asyncio.run(body())


def test_multiline_claim_up_still_moves_the_cursor(tmp_path):
    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            inp.claim_input(_ask([], initial="a\nb"))
            await pilot.pause()
            assert inp.cursor_location == (1, 1)
            await pilot.press("up")  # cursor movement, not history
            await pilot.pause()
            assert inp.cursor_location[0] == 0
            assert inp.text == "a\nb"

    asyncio.run(body())


# --- end-to-end through the Dock in the live app --------------------------------


def test_pane_outcome_claims_the_live_input_line(tmp_path):
    """The whole Stage-2 shape, wired today: a CLAIM_INPUT outcome dispatched on the
    docked surface's Dock reaches AppInputSink.claim → the real input line morphs;
    the user answers; a second ask mid-claim is refused THROUGH the Dock (on_cancel)."""
    from xlii.tui.dock_surface import DockSurface, register_dock_view

    register_dock_view("vfs")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app._show_panel_view("right", "vfs") is True
            await pilot.pause()
            dock = app.query_one("#panel").query_one(DockSurface).dock
            inp = app.query_one("#input", _PromptInput)
            box = app.query_one("#input-box")

            submits, cancels = [], []
            out = Outcome(CLAIM_INPUT, "tasks://old", claim=_ask(submits, cancels, prompt="rename to:"))
            assert dock.dispatch(out, from_slot=dock.focused) is None
            await pilot.pause()
            assert inp.claim_active
            assert box.border_title == "rename to:"
            assert app.focused is inp  # the claim pulled focus to the input line

            # a second ask while claimed: refused via the Dock → its on_cancel fires
            s2, c2 = [], []
            dock.dispatch(Outcome(CLAIM_INPUT, claim=_ask(s2, c2)), from_slot=dock.focused)
            assert c2 == [True] and s2 == []
            assert box.border_title == "rename to:"  # first ask untouched

            await pilot.press("n", "e", "w", "enter")
            await pilot.pause()
            assert submits == ["new"]
            assert cancels == []
            assert not inp.claim_active and box.border_title is None

    asyncio.run(body())


def test_prefill_mid_claim_releases_the_ask_first(tmp_path):
    """Single-tenancy cuts both ways: a PREFILL dispatched while a claim is active
    releases the ask exactly as Esc would (on_cancel fires, prompt restored) and
    then seeds the command — it never silently becomes the ask's answer."""
    from xlii.panes import PREFILL
    from xlii.tui.dock_surface import DockSurface, register_dock_view

    register_dock_view("vfs")

    async def body():
        app, _st = _app(tmp_path)
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app._show_panel_view("right", "vfs")
            await pilot.pause()
            dock = app.query_one("#panel").query_one(DockSurface).dock
            inp = app.query_one("#input", _PromptInput)
            box = app.query_one("#input-box")

            submits, cancels = [], []
            dock.dispatch(Outcome(CLAIM_INPUT, claim=_ask(submits, cancels)), from_slot=dock.focused)
            await pilot.pause()
            assert inp.claim_active

            dock.dispatch(Outcome(PREFILL, text="/tasks run nightly"), from_slot=dock.focused)
            await pilot.pause()
            assert cancels == [True]  # the ask was released, and the asker told
            assert submits == []
            assert not inp.claim_active
            assert inp.text == "/tasks run nightly"  # the prefill landed normally
            assert box.border_title is None

    asyncio.run(body())
