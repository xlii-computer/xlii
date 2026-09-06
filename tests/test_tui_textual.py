"""Headless tests for the Textual front-end (tui-layer T6, MVP).

Driven through Textual's run_test() pilot harness (no real TTY). Skipped wholly
when textual isn't installed — it's an optional [tui] dependency. The app takes
its agent / run_turn / state injected, so these use fakes (no API calls).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

pytest.importorskip("textual")

from xlii.tui_textual import XliiApp  # noqa: E402


def _fake_agent(console=None):
    """A turn-path agent stub with a real SessionState — what `self._state.agent`
    must look like (the TUI reads `.session` via _sync_loop_session and `.history`).
    Mirrors the real Agent's attrs the TUI/handlers touch."""
    from xlii.agent import SessionState
    return SimpleNamespace(
        console=console, rail=None, debug=None, plan_mode=False,
        active_mode=None,
        howto_mode=False, history=[], model_override=None,
        session=SessionState(),
    )


def _state(tmp_path):
    return SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj"),
        agent=_fake_agent(),
    )


def _routed_state(tmp_path):
    """A fuller fake REPLState that supports the slash path (as_context_dict +
    the attributes process_repl_input / handlers / end-of-turn sync read)."""
    st = SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj",
                                local_only=True, xli_dir=tmp_path),
        agent=_fake_agent(),
        cfg=None, pool=None, persona=None, yolo=False, no_sync=False, console=None,
        attached_refs=[], attached_docs=[],
        attach_ref=lambda *a, **k: None, attach_doc=lambda *a, **k: None,
    )
    st.as_context_dict = lambda: {
        "console": st.console, "agent": st.agent, "project": st.project,
        "cfg": st.cfg, "pool": st.pool, "state": st,
        "persona": st.persona, "yolo": st.yolo,
    }
    st.save = lambda: None
    return st


def _fake_stats(context_tokens=0, cached_tokens=0):
    return SimpleNamespace(
        orch=SimpleNamespace(model="grok-build-0.1", iterations=1, total_tokens=10, cost_usd=None),
        tool_calls=0,
        workers=SimpleNamespace(total_tokens=0, cost_usd=None),
        workers_dispatched=0,
        total_cost=None,
        context_tokens=context_tokens,
        cached_tokens=cached_tokens,
    )


def _log_text(app) -> str:
    from xlii.tui.transcript import TranscriptLog
    return app.query_one("#log", TranscriptLog).plain_text()


async def _submit(app, pilot, text):
    from xlii.tui_textual import _PromptInput
    app.query_one("#input", _PromptInput).text = text
    await pilot.pause()
    await pilot.press("enter")
    await app.workers.wait_for_complete()
    await pilot.pause()


def _run(coro_fn):
    asyncio.run(coro_fn())


def test_launch_editor_runs_command_with_app_suspended(tmp_path, monkeypatch):
    """The /edit terminal-fight fix: the external editor runs the command through
    the app's suspend context, so Textual isn't reading the tty at the same time
    (the raw-mode collision that garbled the input box)."""
    import contextlib

    calls: list = []
    suspended: list = []
    monkeypatch.setattr("subprocess.call", lambda cmd, **k: calls.append(cmd) or 0)

    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()

            @contextlib.contextmanager
            def _susp():
                suspended.append("in")
                yield
                suspended.append("out")

            monkeypatch.setattr(app, "suspend", _susp)   # headless driver can't really suspend
            rc = app._launch_editor_suspended(["nano", "x.txt"])
            assert rc == 0
            assert calls == [["nano", "x.txt"]]      # the editor command ran…
            assert suspended == ["in", "out"]        # …inside the suspend context
    _run(body)


def test_splash_shown_on_mount(tmp_path, monkeypatch):
    # Isolate from any real/seeded ~/.config/xlii/splash.nfo (a non-existent config dir) so we assert
    # the shipped default splash.nfo — the auto-loading fallback rendered verbatim.
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path / "cfg"))

    async def body():
        app = XliiApp(project_name="my-proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            text = _log_text(app)
            assert "/help" in text
            assert "/howto" in text
            assert "the answer, at the command line" in text
            assert "╚███╔╝" in text  # XLII slant-X, from the bundled default nfo
    _run(body)


def test_shell_command_renders_block(tmp_path):
    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await _submit(app, pilot, "echo hello")
            text = _log_text(app)
            assert "shell" in text        # ShellBlock header (exit code may wrap off-width)
            assert "$ echo hello" in text  # the command line
            assert "hello" in text        # captured output
    _run(body)


def test_bang_command_renders_block(tmp_path):
    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await _submit(app, pilot, "!echo via-bang")
            assert "via-bang" in _log_text(app)
    _run(body)


def test_cd_updates_tracked_cwd(tmp_path):
    # Regression: `cd` in the TUI ran as a captured subprocess, which can't move
    # the parent — so the tracked shell_cwd never changed and every later command
    # (and the #status strip) kept showing the old directory. `cd` must mutate
    # state.shell_cwd like the inline REPL does.
    from pathlib import Path
    sub = tmp_path / "sub"
    sub.mkdir()

    async def body():
        st = _state(tmp_path)
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "cd sub")
            assert Path(st.shell_cwd).resolve() == sub.resolve()       # moved down
            await _submit(app, pilot, "cd ..")
            assert Path(st.shell_cwd).resolve() == tmp_path.resolve()  # and back up
    _run(body)


def test_bare_clear_resets_transcript(tmp_path):
    # Regression: a bare `clear` ran as a captured subprocess whose escape codes
    # only scrolled the RichLog — the screen looked blank but new output kept the
    # old vertical offset. `clear` must actually reset the transcript, like /clear.
    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await _submit(app, pilot, "echo hello")
            assert "hello" in _log_text(app)
            await _submit(app, pilot, "clear")
            text = _log_text(app)
            assert "hello" not in text     # transcript emptied, not just scrolled
            assert "$ clear" not in text   # no ShellBlock rendered for it
    _run(body)


def test_bang_clear_resets_transcript(tmp_path):
    # `!clear` only ever captures in the TUI, so it's broken the same way — it
    # must reset the transcript too (the inline `!!clear` raw escape is separate).
    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await _submit(app, pilot, "echo hello")
            assert "hello" in _log_text(app)
            await _submit(app, pilot, "!clear")
            assert "hello" not in _log_text(app)
    _run(body)


def test_up_down_walk_command_history(tmp_path):
    async def body():
        from xlii.tui_textual import _PromptInput
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await _submit(app, pilot, "echo one")
            await _submit(app, pilot, "echo two")
            inp = app.query_one("#input", _PromptInput)
            await pilot.press("up")
            await pilot.pause()
            assert inp.text == "echo two"      # newest first
            await pilot.press("up")
            await pilot.pause()
            assert inp.text == "echo one"      # older
            await pilot.press("down")
            await pilot.pause()
            assert inp.text == "echo two"      # back toward newer
            await pilot.press("down")
            await pilot.pause()
            assert inp.text == ""              # past newest → the empty draft
    _run(body)


def test_recalled_collapsed_paste_resubmits_full_body(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_PASTE_COLLAPSE_LINES", "3")
    seen = []

    def fake_run_turn(q):
        seen.append(q)
        return ("ok", set(), _fake_stats())

    async def body():
        from xlii.tui_textual import _PromptInput

        app = XliiApp(project_name="proj", agent=None,
                      run_turn=fake_run_turn, state=_state(tmp_path))
        async with app.run_test() as pilot:
            inp = app.query_one("#input", _PromptInput)
            body = "alpha\nbeta\ngamma\ndelta"
            placeholder = inp._paste_store.maybe_collapse_insert(body)

            await _submit(app, pilot, f"? summarize {placeholder}")
            await pilot.press("up")
            await pilot.pause()
            assert inp.text == f"? summarize {placeholder}"
            await pilot.press("enter")
            await app.workers.wait_for_complete()

    _run(body)
    assert seen == [
        "summarize alpha\nbeta\ngamma\ndelta",
        "summarize alpha\nbeta\ngamma\ndelta",
    ]


def test_transcript_mounts_image_widget_for_imageref(tmp_path, monkeypatch):
    # B: an ImageRef written into the transcript mounts the textual-image widget
    # (not a Static), and records the [image: …] marker in the plain transcript.
    # The renderable sink (write_block) carries the ImageRef from maybe_preview,
    # so /imagine and /image latest get inline graphics in /tui. We stub the
    # widget class so the test exercises OUR routing without depending on
    # textual-image's renderer working headlessly (no real graphics protocol).
    import base64
    import textual_image.widget as tiw
    from textual.widgets import Static
    from xlii.terminal_image import ImageRef
    from xlii.tui.transcript import TranscriptLog

    class _FakeImage(Static):
        def __init__(self, image, **kw):
            super().__init__(f"IMG:{image}", **kw)

    monkeypatch.setattr(tiw, "Image", _FakeImage)

    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    img_path = tmp_path / "shot.png"
    img_path.write_bytes(tiny_png)

    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            app.write_block(ImageRef(img_path, 40))
            await pilot.pause()
            log = app.query_one("#log", TranscriptLog)
            assert len(log.query(_FakeImage)) == 1   # mounted the image widget
            assert f"[image: {img_path.name}]" in log.plain_text()
    _run(body)


def test_history_persists_to_shared_repl_history_file(tmp_path):
    # The TUI writes the same .xlii/repl_history (prompt_toolkit FileHistory
    # format) the inline REPL reads, so the two views share one history.
    async def body():
        st = _routed_state(tmp_path)  # has project.xli_dir = tmp_path
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "echo persisted")
    _run(body)
    from prompt_toolkit.history import FileHistory
    strings = list(FileHistory(str(tmp_path / "repl_history")).load_history_strings())
    assert "echo persisted" in strings


def test_history_recall_of_slash_command_keeps_popup_closed(tmp_path):
    # Recalling a `/command` from history must NOT pop the completion menu —
    # otherwise the next up/down would drive the popup, not history.
    async def body():
        from xlii.tui_textual import _PromptInput
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            app._hist = ["echo a", "/help"]      # seed (don't execute a real slash)
            app._hist_pos = len(app._hist)
            inp = app.query_one("#input", _PromptInput)
            await pilot.press("up")
            await pilot.pause()
            assert inp.text == "/help"
            assert not app._popup_open            # suppressed — no menu hijack
            await pilot.press("up")
            await pilot.pause()
            assert inp.text == "echo a"          # history nav continues
    _run(body)


def test_agent_turn_renders_answer_and_footer(tmp_path):
    captured = {}

    def fake_run_turn(q):
        captured["q"] = q
        return ("The fix was a case-sensitive compare.", set(), _fake_stats())

    async def body():
        agent = SimpleNamespace(console=None)
        app = XliiApp(project_name="proj", agent=agent,
                      run_turn=fake_run_turn, state=_state(tmp_path))
        async with app.run_test() as pilot:
            await _submit(app, pilot, "?why did it fail")
            text = _log_text(app)
            assert captured["q"] == "why did it fail"
            assert "you" in text                # question echoed as a UserBlock
            assert "why did it fail" in text
            assert "The fix was a case-sensitive compare." in text
            assert "┤ code ├" in text            # tense-chrome turn-record chip
            assert "grok-build-0.1" in text     # footer model (bar/frame home is separate)
            # the app routed the agent's console into the transcript
            assert agent.console is app._console
    _run(body)


def test_agent_turn_failure_shows_error_box(tmp_path):
    def boom(q):
        raise RuntimeError("401 Unauthorized")

    async def body():
        app = XliiApp(project_name="proj", agent=SimpleNamespace(console=None),
                      run_turn=boom, state=_state(tmp_path))
        async with app.run_test() as pilot:
            await _submit(app, pilot, "?do it")
            text = _log_text(app)
            assert "error" in text and "401 Unauthorized" in text
    _run(body)


def test_locker_files_ride_the_tui_turn_and_once_is_consumed(tmp_path, monkeypatch):
    # U4: the --tui front-end folds enabled locker files into the turn (the inline
    # REPL already did). A real REPLState provides the locker methods; stub the
    # end-of-turn sync so no Collection/network is touched.
    import xlii.cmds.sessions as S
    monkeypatch.setattr(S, "_end_of_turn_sync", lambda *a, **k: None)
    from xlii.repl import REPLState
    from tests.helpers import FakeConsole, make_agent

    root = tmp_path / "proj"
    (root / ".xlii").mkdir(parents=True)
    agent = make_agent(root)
    state = REPLState(console=FakeConsole(), agent=agent, project=agent.project,
                      cfg=agent.cfg, pool=agent.pool)
    durable = root / "keep.png"
    durable.write_bytes(b"a")
    oneshot = root / "flash.png"
    oneshot.write_bytes(b"b")
    state.attach_file(durable)
    state.attach_file(oneshot, once=True)

    seen = {}

    def fake_run_turn(q, attachments=None):
        seen["attachments"] = list(attachments or [])
        return ("ok", set(), _fake_stats())

    async def body():
        app = XliiApp(project_name="proj", agent=agent, run_turn=fake_run_turn, state=state)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "?describe these")
    _run(body)

    assert len(seen["attachments"]) == 2          # both enabled files rode the turn
    by = {e["name"]: e for e in state.attached_files}
    assert by["flash.png"]["enabled"] is False    # --once consumed after the turn
    assert by["keep.png"]["enabled"] is True       # durable stays live


def test_bare_input_in_harness_mode_drives_foreground_session(tmp_path, monkeypatch):
    # Break 1 regression: the TUI reimplements bare-input routing and previously
    # skipped the harness_foreground check — so after `/cursor on` a typed line went
    # to shell/xlii instead of the live session (forcing the `/cursor` prefix and
    # losing coherence). Bare input in mode must route through drive_foreground_session.
    import xlii.harness.session as hs

    seen = {}
    monkeypatch.setattr(
        hs, "drive_foreground_session",
        lambda state, text: (seen.__setitem__("text", text), True)[1],
    )

    st = _routed_state(tmp_path)
    st.harness_foreground = "cursor"  # as if `/cursor on` is active

    async def body():
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q, attachments=None: ("", set(), _fake_stats()), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "make a change")
    _run(body)

    assert seen.get("text") == "make a change"  # drove the session, not shell/agent


def test_bare_input_without_mode_does_not_drive_session(tmp_path, monkeypatch):
    # The guard is inert outside a harness mode — bare input falls through to the
    # normal shell/agent routing, never to a session.
    import xlii.harness.session as hs

    seen = {}
    monkeypatch.setattr(
        hs, "drive_foreground_session",
        lambda state, text: (seen.__setitem__("hit", True), True)[1],
    )
    st = _routed_state(tmp_path)  # no harness_foreground attribute set

    async def body():
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q, attachments=None: ("", set(), _fake_stats()), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "echo hi")
    _run(body)

    assert "hit" not in seen  # foreground router never consulted


def test_clear_empties_the_transcript(tmp_path):
    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await _submit(app, pilot, "echo something")
            assert "something" in _log_text(app)
            await _submit(app, pilot, "/clear")
            assert _log_text(app).strip() == ""
    _run(body)


def test_exit_stops_the_app(tmp_path):
    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            from xlii.tui_textual import _PromptInput
            app.query_one("#input", _PromptInput).text = "/exit"
            await pilot.press("enter")
            await pilot.pause()
        assert app.is_running is False
    _run(body)


def test_frame_fits_and_has_no_footer(tmp_path):
    async def body():
        from textual.widgets import Footer, OptionList, Static
        from xlii.tui_textual import _PromptInput

        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            screen_h = app.size.height
            # the whole input (including its border) is on-screen at the bottom
            assert inp.region.y >= 0
            assert inp.region.bottom <= screen_h, (inp.region, screen_h)
            # the Footer is gone — its `^p palette` hint duplicated the header's
            # command-palette icon, and was the widget that clipped at the edge
            assert len(app.query(Footer)) == 0
            # the pinned-question, heartbeat, and completions bars exist but start hidden
            assert not app.query_one("#question", Static).display
            assert not app.query_one("#heartbeat", Static).display
            assert not app.query_one("#completions", OptionList).display
            sc = app.query_one("#shortcode-popup", OptionList)
            assert not sc.display
            assert sc.region.height == 0
    _run(body)


def test_question_pins_above_transcript_on_ask(tmp_path):
    async def body():
        from textual.widgets import Static
        app = XliiApp(project_name="proj", agent=SimpleNamespace(console=None),
                      run_turn=lambda q: ("ok", set(), _fake_stats()), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await _submit(app, pilot, "?why did it fail")
            bar = app.query_one("#question", Static)
            assert bar.display                                  # shown after an ask
            assert "why did it fail" in str(bar.render())       # holds the question
            # /clear retires the pinned question along with the transcript
            await _submit(app, pilot, "/clear")
            assert not app.query_one("#question", Static).display
    _run(body)


def test_conversational_slash_pins_delegate_cursor(tmp_path, monkeypatch):
    from xlii.repl_cmds import register_all

    register_all()
    monkeypatch.setattr(
        "xlii.repl_cmds.delegate.run_delegate_command",
        lambda line, ctx, **kw: True,
    )

    async def body():
        from textual.widgets import Static
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "/delegate cursor do the thing")
            bar = app.query_one("#question", Static)
            assert bar.display
            assert "/delegate cursor do the thing" in str(bar.render())
    _run(body)


def test_conversational_slash_pins_claude_and_grok_build(tmp_path, monkeypatch):
    from xlii.repl_cmds import register_all

    register_all()
    monkeypatch.setattr(
        "xlii.repl_cmds.delegate.run_delegate_command",
        lambda line, ctx, **kw: True,
    )

    async def body():
        from textual.widgets import Static
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "/claude summarize this repo")
            bar = app.query_one("#question", Static)
            assert bar.display
            assert "/claude summarize this repo" in str(bar.render())
            await _submit(app, pilot, "/grok-build plan the migration")
            assert "/grok-build plan the migration" in str(bar.render())
    _run(body)


def test_non_conversational_slash_does_not_pin(tmp_path):
    from xlii.repl_cmds import register_all

    register_all()

    async def body():
        from textual.widgets import Static
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "?prior question")
            prior = str(app.query_one("#question", Static).render())
            await _submit(app, pilot, "/help")
            bar = app.query_one("#question", Static)
            assert "prior question" in str(bar.render())
            assert "/help" not in str(bar.render())
            assert prior == str(bar.render())
    _run(body)


def test_context_window_table_matches_published_caps():
    # Baked fallback: longest prefix. grok-4.6/4.5 are 500K; older grok-4.x is 1M.
    from xlii.session_meter import reset_model_window_cache
    from xlii.tui_textual import _context_window, _ktok
    reset_model_window_cache()
    assert _context_window("grok-4-0709") == 1_000_000
    assert _context_window("grok-4.3") == 1_000_000
    assert _context_window("grok-4.6") == 500_000
    assert _context_window("grok-4-fast-reasoning") == 1_000_000
    assert _context_window("grok-code-fast-1") == 256_000
    assert _context_window("grok-build-0.1") == 256_000
    assert _context_window("grok-3-fast") == 131_072
    assert _context_window("grok-2-latest") == 131_072
    assert _context_window("claude-opus-4-8") is None   # unknown → no /cap
    # M-scale formatting for the 1M windows
    assert _ktok(1_000_000) == "1M"
    assert _ktok(1_500_000) == "1.5M"
    assert _ktok(256_000) == "256K"
    assert _ktok(102_400) == "102K"


def test_context_meter_shows_in_profile_bar(tmp_path):
    async def body():
        from textual.widgets import Static
        # last-call prompt size 102_400, 80% of it cache-served
        stats = _fake_stats(context_tokens=102_400, cached_tokens=81_920)
        app = XliiApp(project_name="proj", agent=SimpleNamespace(console=None),
                      run_turn=lambda q: ("ok", set(), stats), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await _submit(app, pilot, "?status")
            bar = app.query_one("#status", Static).render().plain   # the profile bar (RP3)
            assert "102K" in bar                  # used (102_400 → 102K)
            assert "/ 256K" in bar                # grok-build-0.1 window cap
            assert "80% cached" in bar            # cache-served fraction
    _run(body)


def test_plan_mode_routes_bare_input_to_agent(tmp_path):
    # Regression: in the TUI, /plan (and chat personas) must flip bare input to a
    # conversational agent turn — not run it as a shell command. Mirrors the
    # inline REPL's _is_shell_primary. Bug: bare input always went to the shell,
    # so plan/chat needed an explicit `?` to talk to the model.
    captured = {}

    def fake_run_turn(q):
        captured["q"] = q
        return ("Here's the plan.", set(), _fake_stats())

    async def body():
        from textual.widgets import Static
        st = SimpleNamespace(
            shell_cwd=tmp_path,
            project=SimpleNamespace(project_root=tmp_path, name="proj"),
            persona=None, plan_mode=True, agent=_fake_agent(),
        )
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=fake_run_turn, state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "design the auth flow")
            text = _log_text(app)
            assert captured.get("q") == "design the auth flow"  # reached the agent, not shell
            assert "design the auth flow" in text               # echoed as a user line
            assert "Here's the plan." in text                   # assistant answer rendered
            assert app.query_one("#question", Static).display    # pinned like a ? turn
    _run(body)


def test_slash_help_routes_to_registry(tmp_path):
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "/help")
            text = _log_text(app)
            assert "/help" in text                    # echoed as a user line
            assert "Code REPL slash commands" in text  # the handler's heading
            assert "SHELL" in text                     # a generated help section
            assert st.console is app._console          # state console → transcript
    _run(body)


def test_status_strip_shows_mode_and_cwd(tmp_path):
    async def body():
        from textual.widgets import Static
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            plain = app.query_one("#status", Static).render().plain
            assert "code" in plain and "proj" in plain   # RP3 profile bar: mode · id
    _run(body)


def test_tui_input_shows_mode_placeholder_hint(tmp_path):
    """The input placeholder carries the bare-input contract from hints.py."""
    from xlii import hints
    from xlii.tui_textual import _PromptInput

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._refresh_status()
            assert app.query_one("#input", _PromptInput).placeholder == hints.BUILTIN_HINTS["code"]
    _run(body)


def test_input_action_button_is_framed_and_compact(tmp_path):
    """A compact action button sits to the right of the framed input."""
    async def body():
        from textual.color import Color
        from textual.containers import Horizontal
        from xlii.tui.input_surface import _INPUT_ACTION_WIDTH
        from xlii.tui_textual import _InputActionButton

        st = _state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            action = app.query_one("#input-action", _InputActionButton)
            box = app.query_one("#input-box", Horizontal)
            app._refresh_status()
            await pilot.pause()
            assert action.region.height == box.region.height
            assert action.region.width == _INPUT_ACTION_WIDTH
            assert action.region.width < box.region.width
            assert box.styles.border_left == action.styles.border_left == (
                "round", Color.parse("green")
            )
    _run(body)


def test_input_shell_ask_prefix(tmp_path):
    """Shell-primary surfaces show $ ; conversational surfaces show M ."""
    from textual.widgets import Static
    from xlii.tui_textual import _PromptInput

    async def body():
        st = _state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._refresh_status()
            await pilot.pause()
            prefix = app.query_one("#input-prefix", Static)
            assert str(prefix.render()) == "$ "
            from xlii.mode_controller import PlanController
            st.plan_mode = True
            st.agent.active_mode = PlanController()
            app._refresh_status()
            await pilot.pause()
            assert str(prefix.render()) == "M "
            assert app.query_one("#input", _PromptInput).placeholder == (
                "describe the goal · plan writes → .xlii/plans/ · /execute · /cancel"
            )
    _run(body)


def test_input_frame_tab_and_colors_by_mode(tmp_path):
    """The input wears a uniform per-mode round border on all four edges."""
    async def body():
        from textual.color import Color
        from textual.containers import Horizontal
        from xlii.tui_textual import _ChipRow

        st = _state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            box = app.query_one("#input-box", Horizontal)
            chips = app.query_one("#input-chips", _ChipRow)
            assert chips.display  # row always reserved for breathing room
            assert chips.region.height == 1

            assert box.styles.border_left == ("round", Color.parse("green"))
            assert box.styles.border_top == ("round", Color.parse("green"))
            from xlii.mode_controller import PlanController
            st.agent.active_mode = PlanController()
            app._refresh_status()
            await pilot.pause()
            assert box.styles.border_left == ("round", Color.parse("yellow"))
            assert box.styles.border_top == ("round", Color.parse("yellow"))
    _run(body)


def test_chip_row_renderable_structure():
    """_chip_row renders right-aligned symbol-prefixed chips with click spans."""
    from xlii.tui_textual import _chip_row

    text, spans = _chip_row(40, [("role:ada", "role", "ada")], "green")
    rows = text.plain.split("\n")
    assert len(rows) == 1
    assert rows[0].endswith("◎ role:ada")
    assert len(rows[0]) == 40
    assert "┤" not in rows[0] and "╭" not in rows[0]
    assert len(spans) == 1
    assert spans[0][2] == "role"

    text2, spans2 = _chip_row(
        60,
        [("skills 2", "door", "skills"), ("foo.py", "file", "foo.py")],
        "green",
    )
    assert "⚡ skills 2" in text2.plain
    assert "📎 foo.py" in text2.plain
    assert len(spans2) == 2
    assert spans2[0][2] == "door"
    assert spans2[1][2] == "file"


def test_input_frame_tabs_show_mode_role_and_doorways(tmp_path):
    """The chip row renders an equipped role and a content-type doorway for each attached kind."""
    async def body():
        from xlii.tui_textual import _ChipRow
        st = _state(tmp_path)
        st.active_role = "ada"
        st.persona = None
        st.attached_docs = [("conventions", "d"), ("point:thesis", "p")]
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test(size=(120, 24)) as pilot:
            await pilot.pause()
            app._refresh_status()
            await pilot.pause()
            chips = app.query_one("#input-chips", _ChipRow)
            assert chips.display
            r = chips.render()
            plain = getattr(r, "plain", str(r))
            assert "role:ada" in plain
            assert "docs" in plain and "bookmarks" in plain
    _run(body)


def test_agent_tag_last_agent_or_harness_indicator(tmp_path):
    """tense-chrome: finished turns chip the answer frame with mode (not model)."""
    async def body():
        from xlii.tui.transcript import AnswerTranscript
        st = _state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            log = app.query_one("#log")
            assert not any(isinstance(w, AnswerTranscript) for w in log.children)

            from xlii.conversation import TurnResult
            app._render_turn_result(
                TurnResult(reply="an answer", dirty=set(), stats=_fake_stats()), "q")
            await pilot.pause()
            answers = [w for w in log.children if isinstance(w, AnswerTranscript)]
            assert len(answers) == 1
            ans = answers[0]
            assert ans.border_title == "┤ code ├"
            assert "grok-build-0.1" not in (ans.border_title or "")
    _run(body)


def test_input_frame_scroll_mode_wins_over_mode_color(tmp_path):
    """Coexistence wrinkle: the input border carries a transient `-scroll-mode`
    class (content overflowed). While it's active the scroll tint wins; the mode
    color leads again once it clears."""
    async def body():
        from textual.color import Color
        from textual.containers import Horizontal
        from xlii.tui.theme import THEME
        from xlii.tui_textual import _PromptInput

        st = _state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            box = app.query_one("#input-box", Horizontal)
            inp = app.query_one("#input", _PromptInput)
            assert box.styles.border_left == ("round", Color.parse("green"))  # mode color
            inp.add_class("-scroll-mode")
            box.add_class("-scroll-mode")
            app._paint_input_frame()
            await pilot.pause()
            assert box.styles.border_left == ("round", Color.parse(THEME.tui_input_scroll_border))
            inp.remove_class("-scroll-mode")
            box.remove_class("-scroll-mode")
            app._paint_input_frame()
            await pilot.pause()
            assert box.styles.border_left == ("round", Color.parse("green"))  # mode color back
    _run(body)


def test_input_placeholder_shows_equipped_role_hint(tmp_path):
    """Role hints from hints.py paint as the input placeholder when equipped."""
    async def body():
        from xlii.tui_textual import _PromptInput

        roles = tmp_path / ".xlii" / "roles"
        roles.mkdir(parents=True)
        (roles / "shipper.md").write_text(
            "---\nhint: shipper · cut the release · /execute\n---\nYou ship.\n",
            encoding="utf-8",
        )
        st = _state(tmp_path)
        st.active_role = "shipper"
        st.persona = None
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._refresh_status()
            assert app.query_one("#input", _PromptInput).placeholder == (
                "shipper · cut the release · /execute"
            )
    _run(body)


def test_status_strip_reflects_mode_change(tmp_path):
    async def body():
        from textual.widgets import Static
        st = _state(tmp_path)
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "code" in app.query_one("#status", Static).render().plain
            # mode flip (in real use: /plan -> agent.set_mode(PlanController())).
            # The status strip reads the agent's unified `active_mode` slot, so set
            # it the way the real machinery does rather than poking the bare flag.
            from xlii.mode_controller import PlanController
            st.agent.active_mode = PlanController()
            app._refresh_status()
            await pilot.pause()
            assert "plan" in app.query_one("#status", Static).render().plain   # affordance flag
    _run(body)


def test_status_strip_reflects_identity_switch(tmp_path):
    # The bar follows a live identity change (in real use: /chat --id bob).
    async def body():
        from textual.widgets import Static
        st = _state(tmp_path)
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "code" in app.query_one("#status", Static).render().plain
            st.persona = SimpleNamespace(name="bob")
            app._refresh_status()
            await pilot.pause()
            bar = app.query_one("#status", Static).render().plain
            assert "chat" in bar and "bob" in bar
    _run(body)


def test_ref_output_reaches_transcript(tmp_path):
    # Regression: /ref (and /unref /doc /lib) printed to the module-global
    # console, which is invisible under the Textual screen. Must use ctx console.
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "/ref")
            assert "nothing recalled this session" in _log_text(app)
    _run(body)


def test_cost_output_reaches_transcript(tmp_path):
    # Regression: /cost (_print_pricing) printed to the module-global console.
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        st = _routed_state(tmp_path)
        st.cfg = SimpleNamespace(
            pricing={"grok-x": {"input_per_million": 1.0, "output_per_million": 2.0}},
            get_model_for_role=lambda role: "grok-x",
        )
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "/cost")
            text = _log_text(app)
            assert "pricing" in text and "grok-x" in text
    _run(body)


def test_slash_tui_is_intercepted_not_relaunched(tmp_path):
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "/tui")
            assert "already in the TUI" in _log_text(app)
    _run(body)


def test_unknown_slash_is_rejected_not_run(tmp_path):
    """A misspelled command must be rejected (with a suggestion), NOT sent to the
    agent — a typo must never spend a turn or let the model start editing files."""
    from xlii.repl_cmds import register_all
    register_all()
    captured = {}

    def fake_run_turn(q):
        captured["q"] = q
        return ("ran it", set(), _fake_stats())

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=fake_run_turn, state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "/definitelynotacommand")
            assert "q" not in captured            # the agent never ran
            assert "unknown command" in _log_text(app)
    _run(body)


def test_slash_popup_filters_code_commands(tmp_path):
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            app._update_popup("/st")
            await pilot.pause()
            assert app._popup_open
            names = [c.name for c in app._popup_matches]
            assert "status" in names
            assert all(n.startswith("st") for n in names)
            # a completed token (trailing space → args) dismisses the popup
            app._update_popup("/status ")
            await pilot.pause()
            assert not app._popup_open
    _run(body)


def test_slash_popup_frame_matches_input_box_edges(tmp_path):
    """#completions left/right edges align with #input-box (not full-bleed)."""
    from textual.containers import Horizontal
    from textual.widgets import OptionList
    from xlii.repl_cmds import register_all
    from xlii.tui.app import _tui_app_css

    css = _tui_app_css()
    assert "-canvas-dark" in css
    assert "#log.-canvas-light" in css
    assert "#completions" in css
    assert "margin: 0 1" in css  # default gutter matches #input-row
    # Frame outline only — no solid panel fill behind the option rows.
    assert "background: transparent" in css
    assert "background: $panel" not in css.split("#completions")[1].split("#input-chips")[0]

    register_all()

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test(size=(100, 30)) as pilot:
            app._update_popup("/")
            await pilot.pause()
            await pilot.pause()  # after_refresh align
            assert app._popup_open
            ol = app.query_one("#completions", OptionList)
            box = app.query_one("#input-box", Horizontal)
            assert ol.display
            assert ol.region.width > 0 and box.region.width > 0
            # Same outer vertical strip as the typed frame (±1 for layout jitter).
            assert abs(ol.region.x - box.region.x) <= 1, (ol.region, box.region)
            assert abs(ol.region.right - box.region.right) <= 1, (ol.region, box.region)
    _run(body)


def test_popup_tab_completes_the_command(tmp_path):
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        from xlii.tui_textual import _PromptInput
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            inp = app.query_one("#input", _PromptInput)
            inp.text = "/temp"          # setting text fires TextArea.Changed
            await pilot.pause()
            assert app._popup_open        # → the popup wired up via on_text_area_changed
            await pilot.press("tab")
            await pilot.pause()
            assert inp.text == "/temp "  # accepted, ready for an argument
            assert not app._popup_open
    _run(body)


def test_popup_navigation_and_escape(tmp_path):
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            app._update_popup("/c")       # several code commands start with "c"
            await pilot.pause()
            assert app._popup_open and len(app._popup_matches) >= 2
            assert app._popup_index == 0
            await pilot.press("down")
            await pilot.pause()
            assert app._popup_index == 1
            await pilot.press("up")
            await pilot.pause()
            assert app._popup_index == 0
            await pilot.press("escape")
            await pilot.pause()
            assert not app._popup_open
    _run(body)


def test_popup_lists_front_end_commands(tmp_path):
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            app._update_popup("/cl")          # /clear AND /clear-attachments
            await pilot.pause()
            names = [c.name for c in app._popup_matches]
            assert "clear" in names           # the front-end command self-suggests
            assert "clear-attachments" in names
            assert names.index("clear") < names.index("clear-attachments")
            app._update_popup("/ex")
            await pilot.pause()
            assert "exit" in [c.name for c in app._popup_matches]
    _run(body)


def test_full_alias_submits_instead_of_being_swallowed(tmp_path):
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        from xlii.tui_textual import _PromptInput
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            inp = app.query_one("#input", _PromptInput)
            inp.text = "/ws"                 # full alias for /workspace
            await pilot.pause()
            assert app._popup_open
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            # submitted (input cleared), NOT rewritten to "/workspace "
            assert inp.text == ""
    _run(body)


def test_bare_slash_does_not_autocommit_first_command(tmp_path):
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        from xlii.tui_textual import _PromptInput
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            inp = app.query_one("#input", _PromptInput)
            inp.text = "/"
            await pilot.pause()
            assert app._popup_open
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert inp.text != "/attachments "   # did not auto-fill the first one
            assert inp.text == ""
            assert "type a command after /" in _log_text(app)
    _run(body)


def test_tui_honors_quit_requested_from_handled_slash(tmp_path):
    from xlii.commands import REPLCommand, register_repl_command, unregister_repl_command

    def _quit_from_command(line, ctx):
        ctx["state"].quit_requested = True
        return True

    register_repl_command(REPLCommand(name="_tuiquit", handler=_quit_from_command, repls=["code"]))
    try:
        async def body():
            st = _routed_state(tmp_path)
            app = XliiApp(project_name="proj", agent=st.agent,
                          run_turn=lambda q: ("", set(), None), state=st)
            async with app.run_test() as pilot:
                await _submit(app, pilot, "/_tuiquit")
            assert st.quit_requested is True
            assert app.is_running is False
        _run(body)
    finally:
        unregister_repl_command("_tuiquit")


def test_exit_persists_session_state(tmp_path):
    saved = {"n": 0}

    async def body():
        st = _routed_state(tmp_path)
        st.save = lambda: saved.__setitem__("n", saved["n"] + 1)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            from xlii.tui_textual import _PromptInput
            app.query_one("#input", _PromptInput).text = "/exit"
            await pilot.press("enter")
            await pilot.pause()
        assert saved["n"] >= 1   # on_unmount flushed state on the way out
    _run(body)


def test_inline_dropback_refused_under_web_driver(tmp_path, monkeypatch):
    """textual-serve runs the app under the web driver — the wire speaks the
    Textual protocol, not a PTY, so there is no terminal beneath the browser
    tab. /inline·/terminal must refuse with a meta note instead of exiting
    (which reads as 'basically exit' in the browser face)."""
    monkeypatch.setenv("TEXTUAL_DRIVER", "textual.drivers.web_driver:WebDriver")

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            from xlii.tui_textual import _PromptInput
            app.query_one("#input", _PromptInput).text = "/inline"
            await pilot.press("enter")
            await pilot.pause()
            assert app.is_running               # the app did NOT exit
        assert getattr(st, "quit_requested", False) is False
    _run(body)


def test_inline_dropback_still_exits_on_a_real_terminal(tmp_path, monkeypatch):
    monkeypatch.delenv("TEXTUAL_DRIVER", raising=False)

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            from xlii.tui_textual import _PromptInput
            app.query_one("#input", _PromptInput).text = "/terminal"
            await pilot.press("enter")
            await pilot.pause()
        # The app exited (drop-back) WITHOUT the full-quit disposition — the
        # launcher reads this as "inline" and resumes the inline loop.
        assert getattr(st, "quit_requested", False) is False
    _run(body)


def test_exit_flushes_buffered_journal_entries(tmp_path):
    """JRN-1 parity: a `xlii code --tui` session must flush buffered journal
    entries on exit — the "or on session exit" half of the batched summarizer.
    The flush moved OUT of the silent on_unmount into the announced
    run_graceful_exit (which run_tui_over_session runs after app.run() returns,
    on the restored terminal). This drives /exit, then runs that same post-app
    step the launcher does, and asserts the pending entry was drained."""

    class _FakeJournal:
        def __init__(self):
            self.buffer = ["pending-entry"]   # one entry, below the batch threshold
            self.flushed = 0

        def flush(self):
            self.flushed += 1
            self.buffer = []

    async def body():
        st = _routed_state(tmp_path)
        st.journal = _FakeJournal()
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            from xlii.tui_textual import _PromptInput
            app.query_one("#input", _PromptInput).text = "/exit"
            await pilot.press("enter")
            await pilot.pause()
        assert st.quit_requested is True     # /exit set the full-quit disposition
        # The launcher's post-app step (run_tui_over_session), on a full quit:
        from xlii.exit_sequence import run_graceful_exit
        run_graceful_exit(st, printer=lambda *_a: None)
        assert st.journal.flushed >= 1       # the announced sequence flushed it
        assert st.journal.buffer == []       # the pending entry was drained, not lost
    _run(body)


def test_exit_without_journal_is_safe(tmp_path):
    """A chat --tui session carries no journal (state.journal is None); teardown
    must not choke on its absence."""
    async def body():
        st = _routed_state(tmp_path)
        st.journal = None
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            from xlii.tui_textual import _PromptInput
            app.query_one("#input", _PromptInput).text = "/exit"
            await pilot.press("enter")
            await pilot.pause()
        assert app.is_running is False   # clean teardown despite no journal
    _run(body)


def test_enter_submits_single_line_input(tmp_path):
    async def body():
        from xlii.tui_textual import _PromptInput
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            inp = app.query_one("#input", _PromptInput)
            inp.text = "echo hello"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert inp.text == ""
            assert "hello" in _log_text(app)
    _run(body)


def test_shift_enter_inserts_newline_without_submitting(tmp_path):
    async def body():
        from xlii.tui_textual import _PromptInput
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            inp = app.query_one("#input", _PromptInput)
            inp.text = "line one"
            inp.cursor_location = (0, len(inp.text))
            await pilot.press("shift+enter")
            await pilot.pause()
            assert inp.text == "line one\n"
            assert not _log_text(app).strip().endswith("line one")
    _run(body)


def test_agent_turn_drives_loop_continuation(tmp_path):
    """After a build turn, the TUI must advance the loop like the inline REPL."""
    async def body():
        from unittest.mock import patch

        st = _routed_state(tmp_path)
        app = XliiApp(
            project_name="proj",
            agent=st.agent,
            run_turn=lambda q: (f"done:{q}", set(), _fake_stats()),
            state=st,
        )
        with patch("xlii.repl._drive_loop_continuation") as drive:
            async with app.run_test() as pilot:
                await _submit(app, pilot, "? build phase")
            drive.assert_called_once()
            args = drive.call_args[0]
            kwargs = drive.call_args[1]
            assert args[0] is st
            assert args[1] is app._run_turn
            # Phase 5b: continuations ride the spine — the surface hands its
            # render-only slice through, not a legacy full-delivery callback.
            assert kwargs["render"] == app._render_turn_result
    _run(body)


def test_input_grows_with_lines_and_scrolls_at_cap(tmp_path, monkeypatch):
    from xlii.tui_textual import _PromptInput

    async def body():
        monkeypatch.setenv("XLII_TUI_INPUT_MAX_LINES", "3")
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.pause()
            inp = app.query_one("#input", _PromptInput)
            assert inp._max_visible_lines == 3
            inp.text = "one"
            await pilot.pause()
            assert inp.region.height == 1          # 1 wrapped content line
            assert not inp.has_class("-scroll-mode")
            inp.text = "one\ntwo\nthree"
            await pilot.pause()
            assert inp.region.height == 3          # 3 lines, no border rows here
            inp.text = "one\ntwo\nthree\nfour"
            await pilot.pause()
            assert inp.region.height == 3          # capped at max visible lines
            assert inp.has_class("-scroll-mode")
            assert inp.show_vertical_scrollbar
    _run(body)


def test_default_input_max_lines_env(monkeypatch):
    from xlii.tui_textual import _default_input_max_lines

    monkeypatch.delenv("XLII_TUI_INPUT_MAX_LINES", raising=False)
    assert _default_input_max_lines() == 5
    monkeypatch.setenv("XLII_TUI_INPUT_MAX_LINES", "8")
    assert _default_input_max_lines() == 8
    monkeypatch.setenv("XLII_TUI_INPUT_MAX_LINES", "0")
    assert _default_input_max_lines() == 1
    monkeypatch.setenv("XLII_TUI_INPUT_MAX_LINES", "garbage")
    assert _default_input_max_lines() == 5


def test_agent_turn_fires_project_hooks(tmp_path, monkeypatch):
    from xlii.repl_cmds import register_all
    register_all()
    events = []
    monkeypatch.setattr("xlii.hooks.run_hooks",
                        lambda xli_dir, event, data, **kw: events.append(event))

    async def body():
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("ok", set(), _fake_stats()), state=st)
        async with app.run_test() as pilot:
            await _submit(app, pilot, "?do a thing")
            assert "pre-turn" in events
            assert "post-turn" in events
    _run(body)


# --- intent-gate confirmation modal (the TUI's answer to the blocking input()
#     hang: a risky bash command under Textual must prompt, not deadlock) ------

def test_confirm_modal_resolves_yes_and_no(tmp_path):
    """The modal dismisses 'y' on `y` and 'n' on `n` — the primitive the
    worker-thread confirm is built on."""
    from xlii.tui_textual import ConfirmModal

    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        results = []
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ConfirmModal("approve? [y/N]"), lambda r: results.append(r))
            await pilot.pause()
            await pilot.press("y")
            await pilot.pause()
            app.push_screen(ConfirmModal("approve? [y/N]"), lambda r: results.append(r))
            await pilot.pause()
            await pilot.press("n")
            await pilot.pause()
        assert results == ["y", "n"]
    _run(body)


def test_confirm_modal_copy_option(tmp_path):
    """`c` resolves to 'c' only when the prompt advertises [c]; otherwise it is
    ignored (must not act as an accidental deny)."""
    from xlii.tui_textual import ConfirmModal

    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        results = []
        async with app.run_test() as pilot:
            await pilot.pause()
            # prompt offers copy → `c` resolves to "c"
            app.push_screen(
                ConfirmModal("[y] run here · [c] copy for your terminal · [N] cancel"),
                lambda r: results.append(r),
            )
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            # prompt WITHOUT copy → `c` is ignored, modal stays up
            app.push_screen(ConfirmModal("approve? [y/N]"), lambda r: results.append(r))
            await pilot.pause()
            await pilot.press("c")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmModal)   # still open — c did nothing
            await pilot.press("n")
            await pilot.pause()
        assert results == ["c", "n"]
    _run(body)


def test_confirm_modal_renders_command_and_has_size(tmp_path):
    """Regression: the dialog must show the prompt/command and be laid out with
    real width — the first cut collapsed to an empty orange box (width: auto),
    so the user couldn't tell it was asking to approve a command."""
    from textual.widgets import Static
    from xlii.tui_textual import ConfirmModal

    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause()
            cmd = "pip install pytest -q; mkdir -p tests; ls tests/"
            app.push_screen(
                ConfirmModal(f"approve network command?\n  {cmd}\n[y/N] "),
                lambda r: None,
            )
            await pilot.pause()
            dialog = app.screen.query_one("#confirm-dialog")
            body_w = app.screen.query_one("#confirm-body", Static)
            assert cmd in str(body_w.render())             # the command is shown
            assert dialog.size.width > 20                  # not a collapsed box
            assert dialog.size.height >= 3
            await pilot.press("n")
    _run(body)


def test_confirm_via_modal_unblocks_worker_thread(tmp_path):
    """The real fix: a worker thread calling _confirm_via_modal blocks on the
    modal (no busy-spin, no input() deadlock) and gets 'y' once the user answers."""
    import threading
    from xlii.tui_textual import ConfirmModal

    out = {}

    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            t = threading.Thread(
                target=lambda: out.__setitem__("v", app._confirm_via_modal("approve? [y/N]")),
                daemon=True,
            )
            t.start()
            for _ in range(50):           # let the modal mount on the UI thread
                await pilot.pause()
                if isinstance(app.screen, ConfirmModal):
                    break
            assert isinstance(app.screen, ConfirmModal)
            assert t.is_alive()           # worker is genuinely blocked on the answer
            await pilot.press("y")
            for _ in range(50):           # answer propagates → thread returns
                await pilot.pause()
                if not t.is_alive():
                    break
            t.join(timeout=2)
        assert out.get("v") == "y"
    _run(body)


def test_confirm_via_modal_denies_on_ui_thread(tmp_path):
    """Belt-and-suspenders: if ever called on the UI thread it denies rather than
    deadlocking (it can't block the loop to await its own modal)."""
    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._confirm_via_modal("approve? [y/N]") == "n"
    _run(body)


def test_launch_installs_and_restores_confirm(tmp_path, monkeypatch):
    """launch() swaps xlii.tools._confirm for the modal-backed confirm while the
    app runs, and restores the original (plain input()) on exit."""
    import xlii.tools as tools
    import xlii.tui_textual as tt

    original = tools._confirm
    captured = {}

    def fake_run(self):
        captured["during"] = tools._confirm

    monkeypatch.setattr(tt.XliiApp, "run", fake_run)
    tt.launch(project_name="p", agent=None,
              run_turn=lambda q: ("", set(), None), state=_state(tmp_path))

    assert tools._confirm is original            # restored on exit
    assert captured["during"] is not original    # the override was live during run
    assert callable(captured["during"])


def test_palette_opens_via_ctrl_k_from_input(tmp_path):
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        from xlii.tui_textual import _PromptInput
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            inp = app.query_one("#input", _PromptInput)
            inp.text = "hello world"
            inp.focus()
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert app._palette_active
            assert app._popup_open
            assert inp.text == ""
            inp.text = "stat"
            await pilot.pause()
            assert any(m.name == "status" for m in app._popup_matches)
            await pilot.press("escape")
            await pilot.pause()
            assert not app._palette_active
    _run(body)


def test_palette_opens_and_filters(tmp_path):
    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        from xlii.tui_textual import _PromptInput
        st = _routed_state(tmp_path)
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            app.action_open_palette()
            await pilot.pause()
            assert app._palette_active
            assert app._popup_open
            inp = app.query_one("#input", _PromptInput)
            inp.text = "stat"
            await pilot.pause()
            names = [m.name for m in app._popup_matches]
            assert "status" in names
            await pilot.press("escape")
            await pilot.pause()
            assert not app._palette_active
    _run(body)


def test_at_popup_pins_project_file(tmp_path):
    from xlii.repl_cmds import register_all
    register_all()
    (tmp_path / "README.md").write_text("hello", encoding="utf-8")

    async def body():
        from xlii.tui_textual import _PromptInput
        st = _routed_state(tmp_path)
        st.attached_files = []
        st.agent.session.attached_files = st.attached_files

        def _attach(path, once=False):
            entry = {
                "name": "README.md",
                "kind": "text",
                "path": str((tmp_path / "README.md").resolve()),
                "enabled": True,
            }
            st.attached_files.append(entry)
            return entry

        st.attach_file = _attach

        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            inp = app.query_one("#input", _PromptInput)
            inp.text = "@README"
            await pilot.pause()
            assert app._popup_open
            assert app._popup_kind == "at"
            await pilot.press("enter")
            await pilot.pause()
            assert st.attached_files
            assert "@README" not in inp.text
    _run(body)


# --------------------------------------------------------------------------- #
#  `stop` input-action button = Stop (bg-default P0)
# --------------------------------------------------------------------------- #

def test_stop_button_is_labelled_stop(tmp_path):
    async def body():
        from xlii.tui.input_surface import _INPUT_ACTION_WIDTH, _InputActionButton
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            btn = app.query_one("#input-action", _InputActionButton)
            assert "stop" in str(btn.label).lower()
            # wide enough for the word + its round border
            assert _INPUT_ACTION_WIDTH >= len("stop") + 2
            assert btn.region.width >= len("stop") + 2
    _run(body)


def _press_action_button(app):
    """Drive the handler directly (deterministic; no click geometry)."""
    ev = SimpleNamespace(button=SimpleNamespace(id="input-action"),
                         stop=lambda: None)
    app.on_button_pressed(ev)


def test_stop_button_requests_cancel_while_busy(tmp_path):
    async def body():
        st = _state(tmp_path)
        called = {"n": 0}
        agent = st.agent
        agent.request_cancel = lambda: called.__setitem__("n", called["n"] + 1)
        app = XliiApp(project_name="proj", agent=agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            _press_action_button(app)
            await pilot.pause()
            assert called["n"] == 1
            assert "stop requested" in _log_text(app)
    _run(body)


def test_stop_button_is_inert_when_idle(tmp_path):
    async def body():
        st = _state(tmp_path)
        called = {"n": 0}
        agent = st.agent
        agent.request_cancel = lambda: called.__setitem__("n", called["n"] + 1)
        app = XliiApp(project_name="proj", agent=agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            before = _log_text(app)
            _press_action_button(app)
            await pilot.pause()
            assert called["n"] == 0
            assert _log_text(app) == before  # no meta line — nothing to stop
    _run(body)


def test_stop_button_dismisses_pending_confirm(tmp_path):
    """A turn blocked on a confirm (bash gate, failure nudge) has no tool
    boundary for request_cancel to reach — stop must resolve the open modal as
    deny, unblocking the worker AND clearing the screen."""
    import threading
    from xlii.tui_textual import ConfirmModal

    out = {}

    async def body():
        st = _state(tmp_path)
        called = {"n": 0}
        agent = st.agent
        agent.request_cancel = lambda: called.__setitem__("n", called["n"] + 1)
        app = XliiApp(project_name="proj", agent=agent,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            t = threading.Thread(
                target=lambda: out.__setitem__("v", app._confirm_via_modal("run fix? [y/N]")),
                daemon=True,
            )
            t.start()
            for _ in range(50):           # let the modal mount on the UI thread
                await pilot.pause()
                if isinstance(app.screen, ConfirmModal):
                    break
            assert isinstance(app.screen, ConfirmModal)
            app._busy = True              # the blocked shell/agent turn
            _press_action_button(app)
            for _ in range(50):           # deny propagates → thread returns
                await pilot.pause()
                if not t.is_alive():
                    break
            t.join(timeout=2)
            assert not t.is_alive()
            assert not isinstance(app.screen, ConfirmModal)  # modal gone, not lingering
            assert called["n"] == 1       # cancel still reaches the agent too
        assert out.get("v") == "n"        # an unanswered gate must never approve
    _run(body)


def test_cancel_pending_confirms_sets_stranded_waiter_events(tmp_path):
    """Double-modal strand hardening (PR #189 fast-follow): after dismiss,
    every pending confirm Event is set so a waiter Textual failed to pop
    cannot hang forever. Deny-by-default — set only unblocks."""
    import threading

    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            stranded = threading.Event()
            app._pending_confirms.add(stranded)
            # No modal mounted for this waiter — the dismiss loop alone would
            # leave it blocked; the direct ev.set() pass must release it.
            app.cancel_pending_confirms()
            assert stranded.is_set()
    _run(body)


def test_heartbeat_says_waiting_while_confirm_pending(tmp_path):
    """An open confirm must not masquerade as agent work: the heartbeat reads
    'waiting for your answer', and flips back to 'working' once answered."""
    import threading
    import time as _time

    async def body():
        from textual.widgets import Static
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._busy = True
            app._turn_started = _time.monotonic()
            ev = threading.Event()
            app._pending_confirms.add(ev)
            app._tick_heartbeat()
            hb = app.query_one("#heartbeat", Static)
            assert "waiting for your answer" in str(hb.render())
            app._pending_confirms.discard(ev)
            app._tick_heartbeat()
            assert "working" in str(hb.render())
    _run(body)


def test_bare_prose_guard_blocks_user_shell(tmp_path, monkeypatch):
    """Inline-REPL parity (repl._looks_like_prose): prose typed at the
    shell-primary prompt must not execute — no subprocess, no failure nudge
    spending a model call on a non-command, just the guard message."""
    import xlii.tui.shell as tui_shell

    ran: list = []
    monkeypatch.setattr(tui_shell, "run_shell_captured",
                        lambda *a, **k: ran.append(a))

    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._run_shell("that purple strip IS the scrollbar?", source="user_shell")
            await pilot.pause()
            assert ran == []                              # never reached the shell
            assert "looks like a task" in _log_text(app)  # same nudge as inline
    _run(body)


def test_bang_prose_bypasses_guard(tmp_path, monkeypatch):
    """`!` (user_bang) stays the explicit escape — the same prose still runs."""
    from pathlib import Path as _Path

    import xlii.shell_suggest as shell_suggest
    import xlii.tui.shell as tui_shell
    from xlii.tui.events import ShellRan

    ran: list = []

    def fake_run(cmd, cwd, source="user_shell"):
        ran.append(cmd)
        return ShellRan(command=cmd, cwd=_Path(cwd), stdout="", stderr="",
                        returncode=0, duration_s=0.0, source=source)

    monkeypatch.setattr(tui_shell, "run_shell_captured", fake_run)
    monkeypatch.setattr(shell_suggest, "record_shell_command",
                        lambda *a, **k: False)  # keep the test off the real config dir

    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._run_shell("that purple strip IS the scrollbar?", source="user_bang")
            await pilot.pause()
            assert ran == ["that purple strip IS the scrollbar?"]
            assert "looks like a task" not in _log_text(app)
    _run(body)


def test_question_bar_caps_height_and_scrolls(tmp_path):
    # pinned-query-bar-style P0/P1: round-framed wrapper capped at 3 text rows
    # (+2 border); a long paste scrolls inside the bar instead of eating the
    # screen; a short ask stays small; empty hides both widgets.
    async def body():
        from textual.widgets import Static
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            wrap = app.query_one("#question-scroll")
            bar = app.query_one("#question", Static)
            assert not wrap.display and not bar.display

            app._set_question("why " * 200)      # long paste
            await pilot.pause()
            assert wrap.display and bar.display
            assert wrap.region.height <= 5, wrap.region       # 3 text rows + frame
            assert wrap.max_scroll_y > 0                      # remainder scrollable
            assert wrap.border_title == "you"

            app._set_question("short ask")
            await pilot.pause()
            assert wrap.region.height <= 3, wrap.region       # 1 text row + frame

            app._set_question("")
            await pilot.pause()
            assert not wrap.display and not bar.display
    _run(body)


def test_mode_prefix_click_flips_ask_primary(tmp_path):
    # flipmode-visible-repl-shell-toggle v1: the $/M glyph is a live button —
    # click flips bare-line routing (state.ask_primary), the glyph follows.
    async def body():
        from textual.widgets import Static
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=_state(tmp_path))
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            prefix = app.query_one("#input-prefix", Static)
            assert "$" in str(prefix.render())            # shell-first default
            assert app._bare_is_shell() is True

            await pilot.click("#input-prefix")
            await pilot.pause()
            assert getattr(app._state, "ask_primary", False) is True
            assert app._bare_is_shell() is False          # bare input now asks
            assert "M" in str(prefix.render())

            await pilot.click("#input-prefix")
            await pilot.pause()
            assert app._state.ask_primary is False
            assert app._bare_is_shell() is True
            assert "$" in str(prefix.render())
    _run(body)


def test_ask_primary_flag_routes_bare_input():
    # The seam the button flips — shared with the inline REPL's router.
    from types import SimpleNamespace

    from xlii.repl import _is_shell_primary
    st = SimpleNamespace(persona=None, plan_mode=False, howto_mode=False,
                         ask_primary=False)
    assert _is_shell_primary(st) is True
    st.ask_primary = True
    assert _is_shell_primary(st) is False


def test_bg_default_agent_turn_keeps_input_free(tmp_path):
    # bg-default P1: an agent turn runs as a tracked `turn` job; the input
    # stays free for shell lines; a second agent turn is refused with the
    # steering hint (single-active); the job resolves DONE.
    import threading

    async def body():
        gate = threading.Event()
        turns: list[str] = []

        def slow_run_turn(q, **kw):
            turns.append(q)
            gate.wait(timeout=10)
            return ("slow answer", set(), _fake_stats())

        st = _routed_state(tmp_path)
        st.job_registry = None                      # a real registry slot → lazy create
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=slow_run_turn, state=st)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            from xlii.tui_textual import _PromptInput
            inp = app.query_one("#input", _PromptInput)

            inp.text = "?do the long thing"
            await pilot.pause()
            await pilot.press("enter")
            # let the submit worker route + spawn the turn job
            for _ in range(50):
                await pilot.pause()
                if app._agent_job_active():
                    break
            assert app._agent_job_active()
            assert not app._busy                     # the wall is DOWN

            from xlii.jobs import get_registry
            reg = get_registry(st)
            jobs = reg.active_jobs()
            assert any(j.kind == "turn" for j in jobs)

            # A second agent turn is refused with the steering hint...
            inp.text = "?another one"
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "an agent turn is running" in _log_text(app)
            assert len(turns) == 1                   # never reached run_turn

            # ...but a shell line still runs while the agent works.
            inp.text = "!echo input-still-free"
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(100):                     # poll: shell lands while turn runs
                await pilot.pause()
                if "input-still-free" in _log_text(app):
                    break
            assert "input-still-free" in _log_text(app)
            assert app._agent_job_active()           # turn STILL in flight — true concurrency

            gate.set()                               # release the turn
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "slow answer" in _log_text(app)
            job = reg.get(next(j.job_id for j in reg.jobs() if j.kind == "turn"))
            assert job.status == "done"
            assert not app._agent_job_active()
    _run(body)


def test_btw_steers_while_agent_turn_runs(tmp_path):
    # bg-default P2: /btw passes the while-agent gate and queues steering that
    # the running turn will drain at its next tool boundary.
    import threading

    from xlii.repl_cmds import register_all
    register_all()

    async def body():
        gate = threading.Event()

        def slow_run_turn(q, **kw):
            gate.wait(timeout=10)
            return ("ok", set(), _fake_stats())

        st = _routed_state(tmp_path)
        st.job_registry = None
        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=slow_run_turn, state=st)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            from xlii.tui_textual import _PromptInput
            inp = app.query_one("#input", _PromptInput)

            inp.text = "?long task"
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause()
                if app._agent_job_active():
                    break
            assert app._agent_job_active()

            inp.text = "/btw actually use pathlib"
            await pilot.pause()
            await pilot.press("enter")
            for _ in range(100):
                await pilot.pause()
                if st.agent.session.btw_inbox:
                    break
            assert st.agent.session.btw_inbox == ["actually use pathlib"]
            assert "queued" in _log_text(app)

            gate.set()
            await app.workers.wait_for_complete()
    _run(body)


def test_main_input_and_pane_enqueue_both_land_in_conversation(tmp_path, monkeypatch):
    """Wave 1.5 smoke: one session exercises main ``?`` input AND a pane
    ``ENQUEUE_TURN``; both turns land in the live Conversation exactly once.
    Pins cross-view consistency before any transcript-fold work."""
    import xlii.cmds.sessions as S
    from xlii.conversation import Conversation
    from xlii.panes import ENQUEUE_TURN, Outcome
    from xlii.tui.dock_surface import AppTurnSink

    monkeypatch.setattr(S, "_end_of_turn_sync", lambda *a, **k: None)

    xli = tmp_path / ".xlii"
    (xli / "turns").mkdir(parents=True)
    (tmp_path / "note.txt").write_text("pane context body\n")

    seen: list[str] = []

    def fake_run_turn(q, attachments=None):
        seen.append(q)
        return (f"reply:{q[:40]}", set(), _fake_stats())

    async def body():
        st = _routed_state(tmp_path)
        st.project = SimpleNamespace(
            project_root=tmp_path, name="proj", local_only=True, xli_dir=xli,
        )
        st.console = SimpleNamespace(print=lambda *a, **k: None)
        st.agent = _fake_agent()
        st.agent.history = [{"role": "system", "content": "sys"}]
        st.conversation = Conversation(turns_dir=xli / "turns")
        st.job_registry = None
        # Pane ENQUEUE_TURN submits bare prose through `_submit_prompt`; under
        # shell-primary that would run as a shell line. Flip to ask-primary so
        # both the main `?` turn and the pane enqueue share the agent path in
        # one session (the cross-view consistency this smoke pins).
        st.ask_primary = True

        app = XliiApp(project_name="proj", agent=st.agent,
                      run_turn=fake_run_turn, state=st)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            assert st.conversation is app._conversation

            # 1) Main input agent turn
            await _submit(app, pilot, "?main smoke question")
            for _ in range(100):
                await pilot.pause()
                if not app._agent_job_active() and len(st.conversation.turns) >= 1:
                    break
            users = [t.user for t in st.conversation.turns]
            assert users.count("main smoke question") == 1

            # 2) Pane ENQUEUE_TURN through the same AppTurnSink / _submit_prompt
            sink = AppTurnSink(app)
            from xlii.panes.dock import Dock
            dock = Dock()
            dock.set_turn_sink(sink)
            dock.dispatch(
                Outcome(ENQUEUE_TURN, f"file://{tmp_path}/note.txt",
                        text="Summarize this file."),
            )
            await app.workers.wait_for_complete()
            for _ in range(100):
                await pilot.pause()
                if not app._agent_job_active() and len(st.conversation.turns) >= 2:
                    break

            users = [t.user for t in st.conversation.turns]
            assert users.count("main smoke question") == 1
            pane_hits = [u for u in users if u.startswith("Summarize this file.")]
            assert len(pane_hits) == 1
            assert "pane context body" in pane_hits[0]
            assert len(seen) == 2
    _run(body)


def test_mount_drains_pending_input_into_the_line(tmp_path):
    """Session boot may queue a PREFILL (startup-task capture): the TUI seeds it
    into the input line at mount — editable, never auto-submitted, consumed once."""
    st = _state(tmp_path)
    st.pending_input = "/tasks run nightly"

    async def body():
        app = XliiApp(project_name="proj", agent=None,
                      run_turn=lambda q: ("", set(), None), state=st)
        async with app.run_test() as pilot:
            await pilot.pause()
            from xlii.tui_textual import _PromptInput

            assert app.query_one("#input", _PromptInput).text == "/tasks run nightly"
            assert st.pending_input == ""
    _run(body)
