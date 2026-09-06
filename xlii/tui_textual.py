"""Public entry points for the Textual TUI + a back-compat re-export shim.

The app shell (:class:`xlii.tui.app.XliiApp`) and its component seams were lifted
into the ``xlii/tui/`` package (the-fold Vector D extraction). This module keeps
the two launch entry points — ``launch`` (constructs the app, wires the confirm
modal / renderable sink / panel host, runs it) and ``run_tui_over_session``
(restores everything the app repoints on exit) — because call sites and tests
patch ``xlii.tui_textual.launch`` and import symbols from here; moving those would
break the monkeypatch/import surface for no gain.

Everything below the launch functions is a re-export: the symbols now live in the
component modules, and are surfaced under their old ``xlii.tui_textual`` names so
existing importers keep working.
"""

from __future__ import annotations

import os
from typing import Any, Callable

# Re-exports — the app shell + component seams, under their historical names.
from xlii.hints import BUILTIN_HINTS as _BUILTIN_HINTS
from xlii.tui.app import (
    ConfirmModal,
    XliiApp,
    _DOORWAY_LETTERS,
    _FrontEndCmd,
    _TUI_SLASH,
    _doorway_key_set,
    _menu_accel_key_map,
    _parse_modifier,
    _recall_suggestions,
)
from xlii.tui.input_surface import (
    _ChipRow,
    _InputActionButton,
    _InputCap,
    _PromptInput,
    _chip_row,
    _default_input_max_lines,
    _folder_tab,
)
from xlii.session_meter import context_window as _context_window
from xlii.session_meter import ktok as _ktok
from xlii.tui.status_strip import (
    _FKeyBar,
    _FKEY_HINTS,
    _fkey_spans,
)
from xlii.tui.transcript_console import _TranscriptConsole

# State hook names saved/restored around TUI launch so the session is left clean.
_TUI_STATE_HOOKS = ("_clipboard", "_run_interactive")

__all__ = [
    "launch",
    "run_tui_over_session",
    "XliiApp",
    "ConfirmModal",
    "_BUILTIN_HINTS",
    "_DOORWAY_LETTERS",
    "_FKEY_HINTS",
    "_FKeyBar",
    "_FrontEndCmd",
    "_ChipRow",
    "_InputActionButton",
    "_InputCap",
    "_PromptInput",
    "_chip_row",
    "_TUI_SLASH",
    "_TranscriptConsole",
    "_context_window",
    "_default_input_max_lines",
    "_doorway_key_set",
    "_fkey_spans",
    "_folder_tab",
    "_ktok",
    "_menu_accel_key_map",
    "_parse_modifier",
    "_recall_suggestions",
]


def launch(*, project_name: str, agent: Any, run_turn: Callable, state: Any) -> int:
    """Run the Textual app. Caller has already confirmed textual is importable
    and set XLII_SHELL_STYLE=styled.

    While the app runs, route the bash intent-gate's confirmation through a
    Textual modal: the gate's default `xlii.tools._confirm` is a blocking input()
    that can't read the keyboard under Textual, so a risky command would hang the
    worker thread forever. We swap the module global for the app's modal-backed
    confirm and restore it on exit so the inline REPL (after /tui) gets plain
    input() back.

    For the same reason, route inline image previews through the transcript: the
    stdout cascade in terminal_image shells a renderer out to stdout, which the
    compositor paints over. We install a renderable sink (app.write_block, which
    is thread-safe) so /imagine and /image latest emit a Rich renderable into the
    RichLog, and restore the previous sink on exit."""
    import xlii.tools as _tools
    import xlii.terminal_image as _termimg

    app = XliiApp(project_name=project_name, agent=agent, run_turn=run_turn, state=state)
    prev_confirm = _tools._confirm
    _tools._confirm = app._confirm_via_modal
    # Prime textual-image's protocol detection while the real terminal is still
    # queryable (it can't be probed once Textual owns the screen). True graphics
    # (sixel/kitty) → image widgets; otherwise inline previews use chafa symbols.
    try:
        _termimg.tui_graphics_available(refresh=True)
    except Exception:
        # A failed probe just means inline previews fall back to chafa symbols.
        pass
    prev_sink = _termimg.set_renderable_sink(app.write_block)
    # The TUI is a real mouth (tui-media-delivery P0): grant the session a local
    # outbox so send_file has a delivery channel and generate_image rides the
    # same gate the phone uses; drained after each turn into the transcript.
    # `_granted_outbox` gates the release — an enclosing inline REPL's grant
    # (the /tui path) must survive this surface's teardown.
    _granted_outbox = None
    try:
        from xlii.outbox import grant_local_outbox
        _granted_outbox = grant_local_outbox(state.agent.session)
    except Exception:
        pass  # fakes without .agent.session — the drain no-ops without a grant
    # Suspend the TUI around an external `$EDITOR` (the /edit facets shell out via
    # xlii.editor.open_for_edit). Without this the editor and Textual read the
    # same tty at once — the editor's escapes/keystrokes garble the input box.
    import xlii.editor as _editor
    prev_editor_launcher = _editor.set_editor_launcher(app._launch_editor_suspended)
    # Wire the running app as A2's preview/edit surface host, so /edithere and
    # tab previews open the in-app modal. Without this the host stays None and
    # /edithere falls through to its inline `$EDITOR` path — which, launched
    # inside Textual, floods the screen and steals the keyboard.
    from xlii.tui import preview as _preview
    prev_host = _preview.set_surface_host(_preview.AppSurfaceHost(app))
    # Vector P (J2): wire the running app as the panel host so /file-tab can dock
    # the split-screen views without importing the app — mirroring the surface
    # host above. Restored on exit so the inline REPL (after /terminal) has none.
    from xlii.tui import panels as _panels
    prev_panel_host = _panels.set_panel_host(_panels.AppPanelHost(app))
    # Kernel V2: register the VFS Dock surface as a panel view so `/file-tab vfs`
    # docks it through the seam above. Additive (last-wins); best-effort so a
    # surface import hiccup can never block launch.
    try:
        from xlii.tui.dock_surface import register_dock_view, register_transcript_view

        register_dock_view("vfs")
        register_transcript_view("transcript")
    except Exception:
        # Per the note above: a surface import hiccup must never block launch.
        pass
    # Vector P co-touch (J6): install Vector J's background-job listener so async
    # job progress repaints the profile bar. J publishes set_job_listener; we
    # place this one line. Guarded so the TUI runs even before Vector J merges.
    _jobs = None  # type: ignore
    job_listener_installed = False
    try:
        from xlii import jobs as _jobs  # type: ignore
    except Exception:
        # _jobs stays None, so the job listener below is skipped entirely.
        pass
    if _jobs is not None:
        try:
            _jobs.set_job_listener(lambda: app.call_from_thread(app._refresh_status))
            job_listener_installed = True
        except Exception:
            # Best-effort optional integration: if jobs listener wiring fails,
            # continue launching the TUI without background-job status updates.
            pass
    # Phase 6: Conversation chunk listener → throttled TranscriptPane refresh
    # (kernel never imports the TUI; this surface marshals to the main thread).
    conv_listener_installed = False
    try:
        from xlii import conversation as _conversation

        _conversation.set_conversation_listener(
            lambda: app.call_from_thread(app._refresh_transcript_dock)
        )
        conv_listener_installed = True
    except Exception:
        # conv_listener_installed stays False, so teardown skips the matching unset.
        pass
    # Plan listener → strip + plan:// pane repaint the moment a check/amend/
    # plan-write lands (plan-surface T1; same marshal-to-main-thread shape).
    plan_listener_installed = False
    try:
        from xlii import plan_ops as _plan_ops

        _plan_ops.set_plan_listener(
            lambda: app.call_from_thread(app._refresh_plan_surfaces)
        )
        plan_listener_installed = True
    except Exception:
        # Optional plan-surface integration; launch proceeds without live updates.
        pass
    # Ambient session (the 2d seam): install this live state so the stateless
    # doorway providers (mark:// marks, jobs:// jobs, later locker://) can reach
    # the running session's turn store / job registry when a chip resolves them
    # with no state in hand. Restored on exit so a bare resolve degrades to empty.
    from xlii import active_session as _active_session
    prev_session = _active_session.set_active_session(state)
    try:
        app.run()
    finally:
        _tools._confirm = prev_confirm
        if _granted_outbox is not None:
            try:
                from xlii.outbox import release_local_outbox
                release_local_outbox(state.agent.session)
            except Exception:
                # Teardown: a failed release must not mask why we are exiting.
                pass
        _termimg.set_renderable_sink(prev_sink)
        _editor.set_editor_launcher(prev_editor_launcher)
        _preview.set_surface_host(prev_host)
        _panels.set_panel_host(prev_panel_host)
        _active_session.set_active_session(prev_session)
        if _jobs is not None and job_listener_installed:
            try:
                _jobs.set_job_listener(None)
            except Exception:
                # The process is exiting; a stuck listener unset changes nothing.
                pass
        if conv_listener_installed:
            try:
                from xlii import conversation as _conversation
                _conversation.set_conversation_listener(None)
            except Exception:
                # Same teardown -- the process is exiting regardless.
                pass
        if plan_listener_installed:
            try:
                from xlii import plan_ops as _plan_ops
                _plan_ops.set_plan_listener(None)
            except Exception:
                # Best-effort teardown; exit must not fail on listener cleanup.
                pass
    return 0


def run_tui_over_session(state: Any, agent: Any, *, project_name: str) -> None:
    """Launch the TUI over a live session and restore everything the app repoints
    (both consoles, the TUI-only shell hooks, and XLII_SHELL_STYLE) on exit, so
    the inline REPL can resume cleanly when the user drops back via /terminal —
    or end the process on /exit. Shared by `/tui` (inline → TUI → inline) and the
    `--tui` launch path (TUI from the start → inline), so a `/terminal` drop-back
    behaves identically regardless of how the TUI was entered.

    `project_name` is the TUI title — the project name in code, the clean persona
    name in chat (not the `chat/<name>` state-dir key). Caller has already
    confirmed textual is importable. Whether the user dropped back vs fully quit
    is read off `state.quit_requested` after this returns."""
    _MISSING = object()
    prev_agent_console = agent.console
    prev_state_console = state.console
    prev_hooks = {name: getattr(state, name, _MISSING)
                  for name in _TUI_STATE_HOOKS}
    prev_style = os.environ.get("XLII_SHELL_STYLE")
    os.environ["XLII_SHELL_STYLE"] = "styled"  # the TUI is the styled view
    try:
        launch(project_name=project_name, agent=agent,
               run_turn=agent.run_turn, state=state)
    finally:
        agent.console = prev_agent_console
        state.console = prev_state_console
        for name, prev in prev_hooks.items():
            if prev is _MISSING:
                if hasattr(state, name):
                    delattr(state, name)
            else:
                setattr(state, name, prev)
        if prev_style is None:
            os.environ.pop("XLII_SHELL_STYLE", None)
        else:
            os.environ["XLII_SHELL_STYLE"] = prev_style

    # Graceful exit: the screen is gone and the real console is restored, so the
    # announced teardown (journal summary + shell-habit compile) renders as
    # progress on a normal terminal instead of grinding behind a blank screen.
    # Only on a full quit — a /terminal drop-back returns to inline silently and
    # that session runs the sequence on its own exit.
    if getattr(state, "quit_requested", False):
        from xlii.exit_sequence import run_graceful_exit
        run_graceful_exit(state, printer=state.console.print)
