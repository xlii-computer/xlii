"""Submit path: slash/shell/agent turn orchestration.

Mixin extracted from :mod:`xlii.tui.app` (grades plan Phase 5).
"""
from __future__ import annotations

import contextlib
import os
import time
from typing import Any

from textual import work

from xlii.tui import blocks
from xlii.tui.blocks import turn_footer
from xlii.tui.events import MetaMessage, UserTurn
from xlii.tui.input_surface import (
    _PromptInput,
)
from xlii.tui.transcript import TuiAnswer


import sys as _sys
_app = _sys.modules["xlii.tui.app"]
_TUI_SLASH = _app._TUI_SLASH

class AppTurnMixin:
    """Submit path: slash/shell/agent turn orchestration."""

    def on_button_pressed(self, event: Any) -> None:
        """The `stop` button beside the input (bg-default P0): while a turn runs
        it is a real Stop — cooperative cancel on the in-flight agent turn,
        honored at tool boundaries. Idle, it stays inert (dimmed) chrome."""
        if getattr(event.button, "id", None) != "input-action":
            return
        event.stop()
        if not (self._busy or self._agent_job_active()):
            return
        # A turn blocked on a question (bash gate, failure-nudge run/edit/
        # dismiss) never reaches a tool boundary, so request_cancel alone can't
        # touch it — resolve any open confirm as deny first. Stop then works
        # whether the turn is thinking or asking.
        self.cancel_pending_confirms()
        req = getattr(self._agent, "request_cancel", None)
        if not callable(req):
            self._transcript.write(blocks.meta_block(MetaMessage(
                "stop is not available for this session", "warn")))
            return
        req()  # also kills live shell_run.capture process group
        try:
            from xlii.shell_run import live_shell_running

            shell_note = (
                "live shell process group killed"
                if not live_shell_running()
                else "shell kill requested"
            )
        except Exception:
            shell_note = "shell kill requested if one was running"
        self._transcript.write(blocks.meta_block(MetaMessage(
            f"stop requested — agent halts at next tool boundary; {shell_note}",
            "warn")))

    def _submit_prompt(self, raw: str) -> None:
        text = raw.strip()
        # While the popup is open, Enter accepts the highlighted command rather
        # than submitting a partial token (which would mis-route) — unless the
        # user already typed it in full, or typed a front-end command.
        if (self._popup_open and self._popup_matches
                and text not in _TUI_SLASH and text != "/"):
            if self._popup_kind == "palette":
                self._palette_run_selected()
                return
            if self._popup_kind == "at":
                self._at_accept()
                return
            if self._popup_kind == "recall":
                # Enter submits once the input is the fully-formed `/recall <cand>`;
                # otherwise it accepts the highlighted candidate into the input.
                cand = self._popup_matches[self._popup_index][0]
                if text != f"/recall {cand}":
                    self._popup_accept()
                    return
            else:
                cmd = self._popup_matches[self._popup_index]
                # A fully-typed command OR alias (e.g. /ws for /workspace) submits;
                # a genuine partial token is accepted into the input so the user can
                # add args, then Enter. A lone "/" falls through (no auto-commit).
                if not cmd.matches(text[1:]):
                    self._popup_accept()
                    return
        self._popup_hide()

        inp = self.query_one("#input", _PromptInput)
        # Large pastes land as ``(pasted N lines · #id)`` placeholders in the
        # buffer; expand for the agent, keep the short form for transcript/history
        # so a traceback paste can't wipe the visible session.
        display_text = text
        full_text = inp.expand_pastes(text)
        inp.text = ""
        inp._sync_height()
        if not display_text:
            return
        self._history_add(display_text)  # record every real submission (parity w/ inline)
        if display_text in ("/exit", "/quit"):
            # A2: mark a full quit before tearing down. _tui_handler / the inline
            # loop see this and end the process instead of dropping back a layer.
            if self._state is not None:
                self._state.quit_requested = True
            self.exit()
            return
        if display_text in ("/terminal", "/inline"):
            # A1: the named inverse of /tui — leave the full-screen view and
            # return to the inline REPL. _tui_handler resumes the inline loop on
            # launch()'s return (the current post-TUI behavior, now invokable).
            # Under the web driver (textual-serve — the browser face) there is
            # no terminal beneath the app: the wire speaks the Textual protocol,
            # not a PTY, so "dropping back" would end the whole browser session.
            if "web_driver" in os.environ.get("TEXTUAL_DRIVER", ""):
                self._transcript.write(blocks.meta_block(MetaMessage(
                    "no inline terminal in the browser face — this tab is the "
                    "TUI; /exit ends the session", "warn")))
                return
            self.exit()
            return
        if display_text.split()[0] in ("/clear", "/cls", "/clear-screen"):
            self._clear_transcript()
            return
        if self._busy:
            self._transcript.write(blocks.meta_block(
                MetaMessage("busy — wait for the current turn to finish", "warn")))
            return
        # bg-default P1: an agent turn runs as a tracked job and the input stays
        # free — but only for work that can't fight it: shell lines, ?> post-
        # processing, /btw steering, /jobs. A second agent turn (single-active)
        # or a state-mutating slash waits.
        if self._agent_job_active() and not self._allowed_while_agent(display_text):
            self._transcript.write(blocks.meta_block(MetaMessage(
                "an agent turn is running — /btw <note> to steer it, stop to halt, "
                "/jobs to watch; shell lines still run", "warn")))
            return
        # Echo the user's line so the transcript reads as a coherent turn frame.
        # Shell is echoed by its own block (`$ cmd`); questions and slash
        # commands are echoed here (the handler output doesn't repeat the input).
        # Use display_text (placeholders) so huge pastes don't flood the log.
        if display_text.startswith("?>"):
            self._transcript.write(blocks.user_block(UserTurn(display_text)))
        elif display_text.startswith("?"):
            q = display_text[1:].strip()
            self._transcript.write(blocks.user_block(UserTurn(q)))
            self._set_question(q)  # pin it so it stays in view as the answer scrolls
        elif display_text.startswith("/"):
            self._transcript.write(blocks.user_block(UserTurn(display_text)))
            from xlii.commands import find_repl_command

            scope = getattr(self._state, "command_scope", None) or "code"
            active_role = getattr(self._state, "active_role", None)
            cmd = find_repl_command(display_text, repl=scope, active_role=active_role)
            if cmd and cmd.conversational:
                self._set_question(display_text)
        elif not display_text.startswith("!") and (
                getattr(self._state, "harness_foreground", None) or not self._bare_is_shell()):
            # bare input in a conversational mode (/plan, chat persona) OR a
            # foreground harness mode (/cursor on) is a turn, not shell — echo +
            # pin it like a ? question so the call-response frame survives (shell,
            # by contrast, echoes its own `$ cmd`).
            self._transcript.write(blocks.user_block(UserTurn(display_text)))
            self._set_question(display_text)
        self._turn_started = time.monotonic()
        self._busy = True
        self._process(full_text)

    @work(thread=True)
    def _process(self, text: str) -> None:
        try:
            if text.startswith("!"):
                raw = text.startswith("!!")   # `!!` forces the real terminal
                cmd = text.lstrip("!").strip()
                if cmd:
                    self._run_shell(cmd, source="user_bang", raw=raw)
            elif text.startswith("?>"):
                inst = text[2:].strip()
                if not inst:
                    self.write_block(blocks.meta_block(MetaMessage(
                        "usage: ?> <instruction>  (analyze last shell output)", "info")))
                else:
                    from xlii.shell_toolkit import post_process_flow

                    post_process_flow(self._state, inst)
            elif text.startswith("?"):
                q = text[1:].strip()
                if q:
                    self._start_agent_job(q)
            elif text.startswith("/"):
                self._slash(text)
            elif self._drive_foreground(text):
                pass  # a harness mode (e.g. /cursor on) drove the live session
            elif self._bare_is_shell():
                # shell-primary: bare input runs as a shell command
                self._run_shell(text, source="user_shell")
            else:
                # [M] ask-primary: desk nav still moves the live cwd so `/sh`
                # sees the directory they just cd'd into. Plan/howto stay talk.
                from xlii.desk import is_desk_nav

                if getattr(self._state, "ask_primary", False) and is_desk_nav(text):
                    self._run_shell(text, source="user_shell")
                else:
                    # conversational mode (/plan, or a chat persona): bare input is
                    # an agent turn — same rule the inline REPL uses.
                    self._start_agent_job(text)
        finally:
            self._busy = False
            if getattr(self._state, "quit_requested", False):
                self._on_main(self.exit)
                return
            # mode/cwd/attachments may have changed (slash toggles, /cwd, /ref)
            self._on_main(self._refresh_status)
            # A turn may have changed the working tree (/git stage·commit, a !git, an AI edit) — if
            # the Git doorway is the visible pane, re-list it so the change shows immediately.
            self._on_main(self._refresh_git_view)

    def _agent_job_active(self) -> bool:
        """Whether a background agent turn (bg-default P1) is in flight."""
        jid = getattr(self, "_agent_job_id", None)
        if not jid:
            return False
        try:
            from xlii.jobs import get_registry
            reg = get_registry(self._state)
            job = reg.get(jid) if reg is not None else None
            return bool(job is not None and job.active)
        except Exception:
            return False

    def _allowed_while_agent(self, text: str) -> bool:
        """What may run while an agent turn is in flight: shell lines (`!`, and
        bare in shell-primary), `?>` post-processing, and the steering/watching
        slashes. Everything else — a second agent turn, state-mutating slashes —
        waits (single-active contract; /exit·/quit·/clear bypass upstream)."""
        if text.startswith("!") or text.startswith("?>"):
            return True
        if text.startswith("?"):
            return False                     # a second agent turn — single-active
        if text.startswith("/"):
            token = text[1:].split(maxsplit=1)[0].lower() if len(text) > 1 else ""
            return token in ("btw", "jobs")
        return self._bare_is_shell()         # bare shell line ok; bare ask is a turn

    def _start_agent_job(self, q: str) -> None:
        """bg-default P1: run the agent turn on its own worker, tracked as a
        `turn` job (visible in /jobs + the status fleet segment) — the input
        stays free. XLII_FG_TURNS=1 is the escape hatch back to the old
        foreground behavior. Execution stays on a Textual worker (NOT the job
        pool) so trust gates keep prompting and tests can await it."""
        import os
        if os.environ.get("XLII_FG_TURNS"):
            self._agent_turn(q)
            return
        if self._agent_job_active():
            self.write_block(blocks.meta_block(MetaMessage(
                "an agent turn is already running — /btw <note> to steer it", "warn")))
            return
        jid = None
        try:
            from xlii.jobs import KIND_TURN, get_registry
            reg = get_registry(self._state)
            if reg is not None:
                # notify=False: the answer lands in the transcript itself — a
                # second "✓ job done" line would be noise.
                jid = reg.adopt(KIND_TURN, q[:48], notify=False)
        except Exception:
            jid = None
        if jid is None:
            self._agent_turn(q)              # no registry (fakes) — old sync path
            return
        self._agent_job_id = jid
        self._run_agent_job(q, jid)

    @work(thread=True)
    def _run_agent_job(self, q: str, jid: str) -> None:
        from xlii.jobs import get_registry
        reg = get_registry(self._state)
        if reg is not None:
            reg.begin(jid)
        error = None
        try:
            self._agent_turn(q)              # drive_turn renders its own errors
        except Exception as e:               # infra failure outside the spine
            error = f"{type(e).__name__}: {e}"
        finally:
            if reg is not None:
                reg.finish(jid, error=error)
            self._agent_job_id = None
            self._on_main(self._refresh_status)
            self._on_main(self._refresh_git_view)

    def _bare_is_shell(self) -> bool:
        """Whether bare (un-prefixed) input runs as a live shell command. Mirrors
        the inline REPL's _is_shell_primary so /plan and chat personas flip bare
        input to a conversational agent turn in the TUI exactly as they do inline.
        Defaults to shell-primary if the state can't be read."""
        try:
            from xlii.repl import _is_shell_primary
            return _is_shell_primary(self._state)
        except Exception:
            return True

    def _drive_foreground(self, text: str) -> bool:
        """Route bare input to the foreground harness session (e.g. after
        ``/cursor on``), mirroring the inline REPL's ``drive_foreground_session``
        hook (repl.py:690). The TUI reimplements bare-input routing, so without
        this the check is skipped and a typed line goes to shell/xlii instead of
        the live session — forcing the ``/cursor`` prefix and losing coherence.
        Returns True when a harness mode was active and took the turn (so it rides
        the live ACP session, not a one-shot)."""
        if not getattr(self._state, "harness_foreground", None):
            return False
        try:
            from xlii.harness.session import drive_foreground_session

            return bool(drive_foreground_session(self._state, text))
        except Exception:
            return False

    # -- actions ----------------------------------------------------------

    def _run_shell(self, cmd: str, *, source: str, raw: bool = False) -> None:
        from xlii.tui.shell import run_shell_captured

        # A pure `clear`/`cls` must reset the transcript itself — a captured
        # subprocess can only emit escape codes into the RichLog, which clears
        # nothing and leaves the next line at the old vertical offset (it just
        # scrolls). Treat it like /clear. Applies to bare and `!` alike, since
        # the TUI only ever captures. (_on_main: we're on a worker thread.)
        from xlii.repl import _is_clear_command
        if _is_clear_command(cmd):
            self._on_main(self._clear_transcript)
            return

        # A pure leading `cd` mutates the tracked cwd — a subprocess can never
        # move its parent, so (exactly like the inline REPL's _handle_live_shell)
        # resolve it here and update state.shell_cwd instead of running it.
        # Bare shell always navigates. Talk-primary `!cd` also persists — `!`
        # is the run prefix there, not a one-off. Shell-primary `!` stays a
        # project-root one-shot.
        from xlii.repl import _change_dir, _is_cd_command, _is_shell_primary, _looks_like_prose
        persist_cd = source == "user_shell" or not _is_shell_primary(self._state)
        if persist_cd and _is_cd_command(cmd):
            _change_dir(self._state, cmd)
            return
        if source == "user_shell":
            # Muscle-memory guard, mirrored from the inline REPL's bare-shell
            # path (repl.py): prose typed at the shell-primary prompt must not
            # execute — it fails, the failure nudge then spends a model call
            # "fixing" a non-command, and the nudge's confirm holds _busy until
            # answered. `!` (user_bang) stays the explicit bypass.
            if _looks_like_prose(cmd):
                self.write_block(blocks.meta_block(MetaMessage(
                    "that looks like a task, not a command — try /sh <task> "
                    "for a command, or ?<text> for the AI", "warn")))
                return

        cwd = getattr(self._state, "shell_cwd", None) or self._state.project.project_root

        # `!!` OR a known full-screen program → hand over the real terminal.
        # Capture can't host an ncurses app, and Textual itself owns the screen,
        # so we suspend the app for the duration (mc/vim/htop just work).
        from xlii.interactive import is_interactive, needs_password_tty
        if raw or is_interactive(cmd) or needs_password_tty(cmd):
            self._run_fullscreen(cmd, cwd)
            return

        try:
            ev = run_shell_captured(cmd, cwd, source=source)
        except OSError as e:
            self.write_block(blocks.meta_block(MetaMessage(f"shell error: {e}", "error")))
            return
        self.write_block(blocks.shell_block(ev, max_lines=40))
        from xlii.shell_toolkit import offer_failure_nudge, record_last_shell

        record_last_shell(self._state, ev)
        # Vector F: feed the shell-suggestion compile window. Off-loop (this runs
        # on the shell worker — the right place for the opt-in distiller's model
        # call, off the UI/teardown thread), redacts before buffering, fires a
        # compile every ~35 commands. The distiller is OFF unless
        # XLII_SHELL_SUGGEST_AI is set (deterministic, zero spend by default).
        # cd/clear/full-screen hand-offs early-return above and never reach here,
        # which is correct (they aren't habitual suggestions).
        from xlii import shell_suggest
        shell_suggest.record_shell_command(
            cmd, distiller=shell_suggest.build_shell_distiller(self._state)
        )
        if ev.returncode != 0:
            offer_failure_nudge(self._state, ev)
        # Self-curating hint: a captured command that turned out to be full-screen.
        from xlii.tui.shell import looks_interactive
        if looks_interactive(ev.stdout, ev.stderr):
            from xlii.interactive import program_token
            tok = program_token(cmd)
            add = f" — or add it: /interactive add {tok}" if tok else ""
            self.write_block(blocks.meta_block(MetaMessage(
                f"that looked like a full-screen program; run it with !!{add}", "warn")))

    def _run_fullscreen(self, cmd: str, cwd) -> None:
        """Hand the real terminal to a full-screen program: suspend the Textual
        app (drop the alt-screen, restore a normal terminal), run the command
        with inherited stdio, then resume. Bridged to the main thread because
        suspend() drives the terminal; defensive if the driver can't suspend
        (headless / old textual) — falls back to a clear message."""
        import subprocess
        import threading

        result: dict = {}
        done = threading.Event()

        def _do() -> None:
            try:
                with self.suspend():
                    result["rc"] = subprocess.call(cmd, shell=True, cwd=str(cwd))
            except Exception as e:   # SuspendNotSupported, AttributeError, OSError…
                result["err"] = e
            finally:
                done.set()

        try:
            self.call_from_thread(_do)      # we're on a worker thread (_process)
            done.wait()
        except RuntimeError:
            _do()                            # already on the main thread — run directly

        if "err" in result:
            self.write_block(blocks.meta_block(MetaMessage(
                f"could not hand over the terminal ({result['err']}) — run full-screen "
                "programs from the inline REPL (xlii code) instead", "error")))
            return
        rc = result.get("rc", 0)
        tail = "" if rc == 0 else f" (exit {rc})"
        self.write_block(blocks.meta_block(MetaMessage(f"$ {cmd}  · full-screen{tail}", "info")))

    def _launch_editor_suspended(self, cmd: list[str]) -> int:
        """Run an external ``$EDITOR`` with the TUI SUSPENDED so it doesn't fight
        the app for the terminal — the raw-mode collision that otherwise garbles
        the input box with the editor's escapes/keystrokes. Installed as
        :func:`xlii.editor.set_editor_launcher` while the TUI runs; called on the
        command worker thread and bridged to the main thread (suspend() drives
        the tty). Returns the editor's exit code, or 127 if the hand-over failed
        (headless / a driver that can't suspend)."""
        import subprocess
        import threading

        result: dict = {}
        done = threading.Event()

        def _do() -> None:
            try:
                with self.suspend():
                    result["rc"] = subprocess.call(cmd)
            except Exception as e:   # SuspendNotSupported, OSError, FileNotFoundError…
                result["err"] = e
            finally:
                done.set()

        try:
            self.call_from_thread(_do)      # we're on a worker thread (_process)
            done.wait()
        except RuntimeError:
            _do()                            # already on the main thread — run directly

        if "err" in result:
            if isinstance(result["err"], FileNotFoundError):
                raise result["err"]          # open_for_edit maps this to the sentinel
            return 127                        # couldn't hand over the terminal cleanly
        return result.get("rc", 0)

    def _slash(self, text: str) -> None:
        """Route a `/command` through the same registry/dispatch the inline REPL
        uses (`process_repl_input`), so behavior — handled commands, the
        rewrite-marker commands, unknown-slash-falls-through-to-agent — matches
        exactly. Handlers print via state.console (now the transcript)."""
        token = text[1:].split(maxsplit=1)[0] if len(text) > 1 else ""
        if not token:
            # Bare "/" — nothing to route. (The shared dispatch also indexes the
            # first token unguarded, so don't hand it an empty one.)
            self.write_block(blocks.meta_block(MetaMessage("type a command after /", "info")))
            return
        # /tui would call launch() again and nest a second App inside this event
        # loop — the user is already in the TUI, so intercept it here.
        if token == "tui":
            self.write_block(blocks.meta_block(MetaMessage("already in the TUI", "info")))
            return

        from xlii.repl import process_repl_input

        rewritten, should_continue = process_repl_input(self._state, text)
        # A command that streamed with end="" (e.g. /cursor · /delegate) may end
        # with chunks still buffered in the transcript console — flush them now
        # so the tail of the answer isn't stranded until the next print.
        with contextlib.suppress(AttributeError):
            self._console.flush_stream()
        if should_continue:
            # A command may queue text for the next prompt (e.g. /browse --reference).
            # The bare REPL consumes state.pending_input as the prompt default; here
            # we mirror that by populating the input buffer. Consume it once.
            queued = getattr(self._state, "pending_input", "") or ""
            if queued:
                self._state.pending_input = ""
                self._set_input(self.query_one("#input", _PromptInput), queued)
            return  # a handled command; its output already went to the transcript
        # Unknown slash, or a rewrite-marker (/execute, /get, /rail …): run as an
        # agent turn, with the rewritten text when one was produced.
        self._start_agent_job(rewritten if rewritten is not None else text)

    def _begin_activity(self) -> None:
        """Open the turn's tool-activity fold (main thread). Best-effort: a mount
        hiccup or torn-down transcript must never fail the turn."""
        with contextlib.suppress(Exception):
            self._transcript.begin_activity()

    def _seal_activity(self) -> None:
        """Freeze the current batch's drawer summary but keep it expanded (main
        thread). Best-effort; the turn-end collapse folds it."""
        with contextlib.suppress(Exception):
            self._transcript.seal_activity()

    def _end_activity(self) -> None:
        """Collapse ALL the turn's batch drawers in one pass (main thread).
        Idempotent + best-effort — safe from the render slice and the backstop."""
        with contextlib.suppress(Exception):
            self._transcript.end_activity()

    def begin_tool_group(self) -> None:
        """Thread-safe: the agent (on its worker thread, via the transcript
        console) brackets each tool *batch* with begin/end so each step's blocks
        fold into their OWN drawer — reasoning between batches stays loose."""
        self._on_main(self._begin_activity)

    def end_tool_group(self) -> None:
        """Thread-safe end of a batch: SEAL the drawer (freeze its summary) but
        leave it expanded. The actual collapse is deferred to one turn-end pass
        (see _render_turn_result / _agent_turn) so folding never shrinks content
        mid-turn — that repeated reflow was the screen flash."""
        self._on_main(self._seal_activity)

    def _render_turn_result(self, result: Any, q: str) -> None:
        """Render-ONLY delivery for the kernel spine: answer block, footer,
        meter. Persistence/sync/hooks/journal/conversation-complete all live in
        :func:`xlii.conversation.drive_turn` — rendering here twice-persists
        nothing and owns nothing."""
        # The tool sequence for this turn is done: collapse the homework into its
        # summary drawer BEFORE the answer/footer/receipt land as permanent blocks.
        self._on_main(self._end_activity)
        # Media-out drain (tui-media-delivery P0): deliver what the turn queued
        # via send_file — images render inline between the folded drawers and
        # the answer block (the phone's image-then-text order).
        try:
            from xlii.outbox import drain_outbox
            drain_outbox(self._state.agent.session, self._console)
        except Exception:
            # Queued files surface on the next drain; a failure here must not swallow the turn result.
            pass
        text, stats = result.reply, result.stats
        # P3 instrumentation: one-shot warm-vs-cold line after episode resume.
        baseline = getattr(self._state, "_episode_cache_baseline", None)
        if baseline is not None:
            try:
                from xlii.episode import format_cache_delta_line
                line = format_cache_delta_line(baseline, stats)
                if line:
                    self.write_block(blocks.meta_block(MetaMessage(line, "info")))
            except Exception:
                # The cache-delta line is diagnostic -- the turn renders fine without it.
                pass
            try:
                self._state._episode_cache_baseline = None
            except Exception:
                # A state stub without the attribute has no baseline to clear.
                pass
        if text:
            model = getattr(getattr(stats, "orch", None), "model", None)
            try:
                from xlii.tui.status import turn_record
                mode, mode_color, role_txt = turn_record(self._state)
            except Exception:
                mode, mode_color, role_txt = "", "", ""
            self.write_block(TuiAnswer(markdown=text, model=model,
                                       mode=mode, mode_color=mode_color, role=role_txt))
        try:
            self.write_block(turn_footer(stats))
        except Exception:
            # Best-effort footer rendering: do not fail the turn on UI/footer errors.
            pass
        self._on_main(self._apply_meter, stats)
        # Plan-surface T1: the strip re-reads the working plan at every turn
        # end (mode flips and /plan save|continue don't ride the listener).
        self._on_main(self._refresh_plan_surfaces)

    def _agent_turn(self, q: str) -> None:
        """A normal agent turn now rides THE kernel spine (convergence Phase 3):
        :func:`xlii.conversation.drive_turn` owns lifecycle + persistence + loop
        continuation; this surface supplies only rendering and error display.
        Loop continuations and policy follow-ups ride the same spine
        (Phase 5b), so this render slice serves every turn shape."""
        from xlii.conversation import drive_turn

        def _on_error(e: Exception) -> None:
            self.write_block(
                blocks.error_box(
                    f"turn failed: {type(e).__name__}: {e}\n"
                    "Try again. If this keeps happening, check your model/tool configuration "
                    "and network access, then run /status or /help for troubleshooting."
                )
            )

        # Tool activity folds per BATCH now, not per turn: the agent brackets
        # each model step's tool calls via the transcript console's
        # begin/end_tool_group, so each step gets its own collapsible drawer and
        # the reasoning between steps stays loose. This finally is just the
        # backstop — collapse any batch left open by an error mid-step.
        try:
            drive_turn(
                self._state, q, self._run_turn,
                render=self._render_turn_result,
                on_error=_on_error,
            )
        finally:
            self._on_main(self._end_activity)
            # Backstop drain: an errored turn never reached the render path —
            # surface its queued files now instead of leaking them into the
            # next turn. Idempotent (drained files moved to delivered/).
            try:
                from xlii.outbox import drain_outbox
                drain_outbox(self._state.agent.session, self._console)
            except Exception:
                # Same drain as the render path: undelivered files simply ride the next turn.
                pass
