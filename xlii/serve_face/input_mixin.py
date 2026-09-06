"""Submit path: chat/code input, cancel, and the worker loop.

Mixin extracted from :mod:`xlii.serve_face.server`.
"""
from __future__ import annotations

import queue
import threading
from typing import Any

from xlii.serve_face.wire import _UNAVAILABLE_SLASH, classify_user_kind
from xlii.turn_events import AssistantAnswer, UserTurn
from xlii.ws_protocol import serialize_event

_ALLOWED_WHILE_AGENT_SLASH_TOKENS = frozenset({
    "btw",
    "jobs",
    "exit",
    "quit",
    "clear",
    "cls",
    "clear-screen",
})


class FaceInputMixin:
    """Submit path: chat/code input, cancel, and the worker loop."""

    # ------------------------------------------------------------ inputs

    def submit(self, text: str) -> bool:
        """Queue an input line.

        Hard-busy (worker mid slash/shell) refuses. An in-flight **agent** turn
        (bg-default) still accepts the allow-list; everything else refuses so
        the client can show the /btw steer hint. ``/exit``·``/quit`` always
        queue — OS close / title-bar exit must not look like a silent no-op."""
        t = (text or "").strip()
        if t in ("/exit", "/quit"):
            self._inputs.put(text)
            return True
        if self._busy.is_set() or not self._inputs.empty():
            return False
        if self._agent_running.is_set() and not self._allowed_while_agent(text):
            return False
        # Only clear cancel when starting fresh work, not when steering mid-turn.
        if not self._agent_running.is_set():
            self._turn_cancelled.clear()
        self._inputs.put(text)
        return True

    def cancel(self) -> None:
        """Halt the live turn, kill any in-flight shell, deny tool gates.

        Face copy says ``stop = deny`` while a confirm is open; without
        ``deny_all`` the gate kept blocking until CONFIRM_TIMEOUT_S even after
        cancel (the input bar felt frozen when users typed ``y``/hit stop).

        User shell (``grep -rn …``) runs on the hard-busy worker via
        :func:`xlii.shell_run.capture` — agent ``request_cancel`` alone does
        not touch it. :func:`~xlii.shell_run.kill_live_shell` kills that
        process group so Stop unblocks hung shell, not only agent turns.
        """
        self._turn_cancelled.set()
        # A desk_switch queued mid-turn must not apply after the user stops.
        self._pending_door_land = None
        self.confirm.deny_all()
        try:
            from xlii.shell_run import kill_live_shell

            if kill_live_shell():
                try:
                    self.send({
                        "type": "meta_message",
                        "level": "warn",
                        "text": "stop — killed live shell process group",
                    })
                except Exception:
                    # The kill already happened; only the notice is lost.
                    pass
        except Exception:
            # No shell_run module, or no live shell to kill — the agent and
            # oneshot cancels below are the rest of the stop.
            pass
        try:
            self.state.agent.request_cancel()
        except Exception:
            # No agent, or one that can't be cancelled — the oneshot path below
            # still runs.
            pass
        oneshot = self._oneshot_agent
        if oneshot is not None:
            try:
                oneshot.request_cancel()
            except Exception:
                # The oneshot agent is already finishing or gone.
                pass

    def _allowed_while_agent(self, text: str) -> bool:
        """Mirror TUI bg-default allow-list: shell, ?> , /btw, /jobs, /exit."""
        t = (text or "").strip()
        if not t:
            return False
        if t.startswith("!") or t.startswith("?>"):
            return True
        try:
            from xlii.desk import is_desk_nav

            if is_desk_nav(t):
                return True
        except Exception:
            # xlii.desk is optional here — fall through to the prefix checks
            # below.
            pass
        if t.startswith("?"):
            return False  # second agent ask — single-active
        if t.startswith("/"):
            token = t[1:].split(maxsplit=1)[0].lower() if len(t) > 1 else ""
            if token in _ALLOWED_WHILE_AGENT_SLASH_TOKENS:
                return True
            # Deterministic plugin invoke — same as /get fast path, not a turn.
            if token == "plugin" and " call " in f" {t} ":
                return True
            if token == "get":
                return True
        if self.posture == "code":
            try:
                from xlii.repl import _is_shell_primary
                return bool(_is_shell_primary(self.state))
            except Exception:
                return False
        return False  # chat bare line = another persona turn

    def _send_busy_state(self, *, hard: bool, agent: bool) -> None:
        """Face chrome: hard blocks submit; agent keeps heartbeat + free input."""
        self.send({"type": "busy_state", "hard": bool(hard), "agent": bool(agent)})

    def _turn_mutation_busy(self) -> bool:
        """True while session-mutating UI actions must wait for turn ownership."""
        return self._busy.is_set() or self._agent_running.is_set()

    def _turn_mutation_busy_message(self, action: str) -> str:
        if self._agent_running.is_set():
            return f"busy — agent turn running; {action} after the turn finishes"
        return f"busy — {action} after the turn finishes"

    def _finish_unit(self, ok: bool) -> None:
        """Chrome refresh + turn_done after a completed (non-deferred) unit."""
        # Door land:* queued mid-turn — apply now that _agent_running is clear.
        try:
            self._flush_deferred_door_land()
        except Exception:  # noqa: BLE001
            pass
        try:
            sink = getattr(getattr(self.state, "console", None), "file", None)
            if sink is not None and hasattr(sink, "flush"):
                sink.flush()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.drain_outbox_to_wire()
        except Exception:  # noqa: BLE001
            pass
        # Code turns consume once-files inside drive_turn; chat does it in
        # _oneshot. Re-project the chip so a used pin disappears.
        self._emit_focus_state(even_empty=False)
        pack_changed = False
        try:
            pack_changed = self.deck.sync_pack()
        except Exception:  # noqa: BLE001
            pack_changed = False
        self._apply_workbench_chrome()
        self.send(self.mode_state())
        self.send(self.chrome_state())
        self.deck.send_snapshot()
        if pack_changed:
            try:
                self.send(self.pane_catalog())
            except Exception:  # noqa: BLE001
                pass
        self.send({"type": "turn_done", "ok": ok, "exit_code": 0 if ok else 1})

    def worker(self) -> None:
        """Session worker: hard work is one-at-a-time; agent turns go bg (M2.2).

        Returns ``\"bg\"`` from run_* means an agent/persona thread owns
        completion (busy_state + final turn_done)."""
        while not self._shutdown.is_set():
            try:
                text = self._inputs.get(timeout=0.25)
            except queue.Empty:
                try:
                    from xlii.glass import live_chrome
                    from xlii.face_remote import reconcile_bridge

                    reconcile_bridge(self)
                    g, m, v = live_chrome()
                    prev = getattr(self, "_glass_sig", None)
                    sig = (g, m, v)
                    if sig != prev:
                        self._glass_sig = sig
                        self.send(self.chrome_state())
                except Exception:
                    # Idle-tick chrome sync: a failed reconcile or a dead
                    # socket is retried on the next poll.
                    pass
                continue
            if self._agent_running.is_set() and not self._allowed_while_agent(text):
                self.send({"type": "meta_message",
                           "text": "an agent turn is running — /btw <note> to steer, "
                                   "stop to halt, /jobs to watch; shell lines still run",
                           "level": "warn"})
                self.send({"type": "turn_done", "ok": True, "exit_code": 0})
                continue
            self._busy.set()
            ok = True
            deferred = False
            try:
                if self.posture == "chat":
                    result = self._run_chat_input(text)
                else:
                    result = self._run_code_input(text)
                if result == "bg":
                    deferred = True
                    ok = True
                else:
                    ok = bool(result)
            except Exception as e:  # noqa: BLE001 — surface, keep serving
                self.send({"type": "meta_message",
                           "text": f"{type(e).__name__}: {e}", "level": "error"})
                ok = False
            finally:
                self._busy.clear()
                if deferred:
                    # Free the prompt; agent thread still running.
                    self._send_busy_state(hard=False, agent=True)
                else:
                    self._finish_unit(ok)

    def _echo_input(self, text: str) -> None:
        """Paint what they typed. Slash/shell were silent — the transcript
        then looked like leftover output with no command."""
        from xlii.status import overlay_word

        kind = classify_user_kind(
            text, posture=self.posture, overlay=overlay_word(self.state),
        )
        self.send(serialize_event(UserTurn(text, kind=kind)))

    # ---------------------------------------------------- chat posture ([M])

    def _run_chat_input(self, text: str):
        stripped = text.lstrip()
        if stripped.startswith("/"):
            return self._run_chat_command_input(text)
        desk = False
        try:
            from xlii.desk import is_desk_nav

            desk = is_desk_nav(stripped)
        except Exception:
            desk = False
        if stripped.startswith("!") or stripped.startswith("?>") or desk:
            return self._run_chat_command_input(text)
        if self._agent_running.is_set():
            self.send({"type": "meta_message",
                       "text": "an agent turn is already running — stop to halt it",
                       "level": "warn"})
            return True

        import os

        from xlii.cmds.sessions.ask import PersonaProjectError, run_persona_oneshot
        from xlii.cmds.sessions.resolve import _lookup_persona, ensure_default_persona
        from xlii.persona import DEFAULT_PERSONA_ID, talk_persona_id
        from xlii.repl_cmds.mojo import build_mojo_ambient

        state = self.state
        persona_id = talk_persona_id(state=state, project=state.project, cfg=state.cfg)
        persona = _lookup_persona(persona_id)
        if persona is None:
            if persona_id == DEFAULT_PERSONA_ID:
                persona = ensure_default_persona()
            else:
                self.send({"type": "meta_message",
                           "text": f"persona {persona_id!r} not found "
                                   "(check the project's persona binding)",
                           "level": "error"})
                return False

        from xlii.bearings import face_surface

        surface = face_surface(self)
        ambient = build_mojo_ambient(state, text, surface=surface)
        attachments = self._chat_attachments()
        self._echo_input(text)
        self._begin_talk_turn(text)

        from xlii.project_paths import is_home_desk_project

        sitting = getattr(state, "project", None)
        hire = (
            "write" if sitting is not None and not is_home_desk_project(sitting)
            else "read"
        )

        def _hold(agent: Any) -> None:
            self._oneshot_agent = agent
            if sitting is not None:
                agent.lab_project = sitting
            agent.sitting = state
            try:
                agent.session.door_surface = surface
                agent.session.emit_door = self._emit_door
            except Exception:
                pass

        def _oneshot() -> bool:
            try:
                from xlii.chat_tiers import session_chat_tier

                reply = run_persona_oneshot(
                    persona, text, pool=state.pool, cfg=state.cfg,
                    console=state.console, ambient_context=ambient,
                    persist=True, drain=False, yolo=self.yolo,
                    attachments=attachments,
                    outbox_dir=getattr(state.agent.session, "outbox_dir", None),
                    on_agent=_hold,
                    cancelled=self._turn_cancelled.is_set,
                    desk_xli_dir=getattr(state.project, "xli_dir", None),
                    chat_tier=session_chat_tier(state),
                    hire=hire, surface=surface)
            except PersonaProjectError:
                self._talk_in_flight = None
                self.send({"type": "meta_message",
                           "text": f"couldn't open {persona.name}'s memory",
                           "level": "error"})
                return False
            finally:
                self._oneshot_agent = None
            self._finish_talk_turn(reply or "")
            self.send(serialize_event(AssistantAnswer(
                markdown=reply or "", streamed=True, mode="chat",
                mode_color="magenta")))
            self.drain_outbox_to_wire()
            try:
                consume = getattr(self.state, "consume_once_attachments", None)
                if callable(consume):
                    consume()
            except Exception:  # noqa: BLE001
                pass
            return True

        # XLII_FG_TURNS=1: old single-flight path (tests / escape hatch).
        if os.environ.get("XLII_FG_TURNS"):
            return _oneshot()
        self._agent_running.set()
        self._send_busy_state(hard=False, agent=True)

        def _bg() -> None:
            ok = True
            try:
                ok = _oneshot()
            except Exception as e:  # noqa: BLE001
                self.send({"type": "meta_message",
                           "text": f"{type(e).__name__}: {e}", "level": "error"})
                ok = False
            finally:
                self._agent_running.clear()
                self._send_busy_state(hard=False, agent=False)
                self._finish_unit(ok)

        threading.Thread(target=_bg, name="face-chat-turn", daemon=True).start()
        return "bg"

    def _try_face_plugin_call(self, text: str) -> bool:
        """Face invoke: `/plugin call` and `__plugin_call__:` work in [M] and [$].

        Chat's slash allowlist denies ``/plugin`` (subscribe/remove stay off that
        surface). The pane Run button is a read of a subscribed API — same as
        the ``plugin_call`` tool. Missing required params seed the input
        instead of firing an empty call.
        """
        from xlii.plugin_call import parse_call_line

        raw = (text or "").strip()
        try:
            parsed = parse_call_line(raw)
        except ValueError as e:
            if raw.startswith("/plugin call") or raw.startswith("__plugin_call__:"):
                self.send({"type": "meta_message", "level": "error", "text": str(e)})
                return True
            return False
        if parsed is None:
            return False
        from xlii.face_panes import _FaceInputSink

        _FaceInputSink(self)._seed_or_start_plugin_call(raw)
        return True

    def _try_face_plan_gateway(self, text: str) -> bool:
        """``/plan`` from [M] is a lab door — flip to [$], then run it there.

        Plan mode writes ``.xlii/plans/`` and investigates a repo. Talk can
        be a research desk, so chat never self-starts PlanController. The
        verb stays on the chat catalog as this gateway.
        """
        raw = (text or "").strip()
        token = raw.split(maxsplit=1)[0].lower() if raw else ""
        if token != "/plan":
            return False
        if not self._enter_lab():
            return True
        flags = {t.lower() for t in raw.split()[1:]}
        from_mojo = bool(flags & {"--from-mojo", "--from-talk", "--add-context"})
        if from_mojo:
            hint = "plan · lab ([$]) — carrying recent talk"
        else:
            hint = ("plan · lab ([$]) — cold. "
                    "/plan --from-mojo to add recent Mojo talk")
        self.send({"type": "meta_message", "level": "info", "text": hint})
        return bool(self._run_code_input(raw))

    def _try_face_clear_screen(self, text: str) -> bool:
        """``/clear`` · ``/cls`` · ``/clear-screen`` — wipe the glass, keep talk."""
        raw = (text or "").strip()
        token = raw.split(maxsplit=1)[0].lower() if raw else ""
        if token in ("/clear", "/cls", "/clear-screen"):
            self._on_clear_screen()
            return True
        try:
            from xlii.repl import _is_clear_command

            if _is_clear_command(raw):
                self._on_clear_screen()
                return True
        except Exception:
            # The repl helper is optional here — treat the line as ordinary
            # input.
            pass
        return False

    def _try_face_panel_command(self, text: str) -> bool:
        """Handle ``/panel …`` on the face without depending on the TUI host.

        Textual installs a panel host; if that import fails or races, ``/panel``
        used to only print a --tui nudge. The face always owns a Dock — open it.
        """
        raw = (text or "").strip()
        # /home → /panel home. Token is the first word: /home/path is not this verb.
        parts = raw.split(maxsplit=1)
        if parts and parts[0].lower() == "/home":
            raw = "/panel home" + (f" {parts[1]}" if len(parts) > 1 else "")
        if not (raw == "/panel" or raw.startswith("/panel ")):
            return False
        tokens = raw.split()[1:]
        want_off = False
        target = None
        for t in tokens:
            tl = t.lower()
            if tl in ("off", "close", "hide"):
                want_off = True
            elif tl in ("?", "help", "list", "--help", "-h"):
                self.send({
                    "type": "meta_message", "level": "info",
                    "text": "panel: home|projects|files|git|tasks|jobs|plan|"
                            "bookmarks|wiki|docs|locker|skills|sources|results|"
                            "artifacts|menu|gigwork · /panel off",
                })
                return True
            elif tl.startswith("-"):
                continue
            elif target is None:
                target = tl
        if want_off:
            self.deck.close_pane()
            self.send({"type": "meta_message", "level": "info",
                       "text": "panel closed"})
            return True
        # bare /panel → files (explorer), matching the slash command default
        word = target or "files"
        from xlii.face_panes import _SCHEME_TO_PANE, _VIEW_TO_PANE

        # Same maps as FacePanelHost / file_tab targets
        from xlii.repl_cmds.file_tab import _PANEL_TARGETS

        kind, scheme = _PANEL_TARGETS.get(word, ("", None))
        if kind == "files" or word in ("files", "file", "explorer", "vfs"):
            pid = "explorer"
        elif kind == "door" and scheme:
            pid = _SCHEME_TO_PANE.get(scheme) or scheme
        else:
            pid = _VIEW_TO_PANE.get(word) or _SCHEME_TO_PANE.get(word) or word
        self._prefer_home_pack()
        if self.deck.open_pane(pid):
            self.send({"type": "meta_message", "level": "info",
                       "text": f"panel · {pid}"})
        else:
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"couldn't open panel {word!r} "
                               f"(try /panel home or the Keep menu)"})
        return True

    def _run_chat_command_input(self, text: str) -> bool:
        """Run chat-safe slash commands in [M] instead of sending them to mojo.

        The Face's chat posture is a one-shot persona surface for bare text, but
        a slash-prefixed line is still command intent. Scope it like ``xlii
        chat`` so menu-prefilled commands either run through the chat allowlist
        or produce the dispatcher’s normal "not available" note.
        """
        from xlii.repl import process_repl_input
        from xlii.shell_run import styled_events

        self._sync_command_scope()
        if self._try_face_clear_screen(text):
            return True
        if self._try_face_plan_gateway(text):
            return True
        self._echo_input(text)
        if self._try_face_panel_command(text):
            return True
        if self._try_face_plugin_call(text):
            return True
        with styled_events():
            rewritten, handled = process_repl_input(self.state, text)
        if getattr(self.state, "pending_persona_switch", None):
            self.state.pending_persona_switch = None
            self.send({"type": "meta_message",
                       "text": "persona switching isn't available on this surface — "
                               "[M] talks to the default persona",
                       "level": "warn"})
        if text.strip() in ("/exit", "/quit"):
            self._request_session_end(reason=text.strip().lstrip("/"))
            return True
        if handled:
            return True
        if rewritten:
            # /get slow path: talk turn, not a lab flip.
            return self._run_chat_input(rewritten)
        # Unknown slash commands must not spend a persona turn or code-agent turn.
        self.send({"type": "meta_message",
                   "text": "slash commands in [M] are limited; flip to [$] for code commands",
                   "level": "warn"})
        return True

    # ---------------------------------------------------- code posture ([$])

    def _drive_code_agent(self, prompt: str) -> bool:
        """Run one code-posture agent turn (sync). Used by fg and bg paths."""
        from xlii.conversation import drive_turn
        from xlii.shell_run import styled_events
        from xlii.status import turn_record

        state = self.state
        outcome: list[bool] = [True]

        def _render(result: Any, _prompt: str) -> None:
            mode, color, role = turn_record(state)
            self.send(serialize_event(AssistantAnswer(
                markdown=getattr(result, "reply", "") or "", streamed=True,
                mode=mode, mode_color=color, role=role)))
            self.drain_outbox_to_wire()

        def _on_error(exc: Exception) -> None:
            outcome[0] = False
            self.send({"type": "meta_message",
                       "text": f"{type(exc).__name__}: {exc}", "level": "error"})

        with styled_events():
            drive_turn(state, prompt, state.agent.run_turn,
                       render=_render, on_error=_on_error)
        return outcome[0]

    def _run_code_input(self, text: str):
        import os

        from xlii.repl import process_repl_input
        from xlii.shell_run import styled_events

        state = self.state
        if self._try_face_clear_screen(text):
            return True
        self._echo_input(text)
        if text.strip() in _UNAVAILABLE_SLASH:
            self.send({"type": "meta_message",
                       "text": f"{text.strip()} is not available on this surface",
                       "level": "warn"})
            return True

        if self._try_face_panel_command(text):
            return True
        if self._try_face_plugin_call(text):
            return True

        with styled_events():
            rewritten, handled = process_repl_input(state, text)

        # A /chat switch marker can't be honored here (the face's [M] IS the
        # persona door) — consume it so it doesn't fire on a later surface.
        if getattr(state, "pending_persona_switch", None):
            state.pending_persona_switch = None
            self.send({"type": "meta_message",
                       "text": "persona chat isn't available on this surface — "
                               "[M] talks to the default persona",
                       "level": "warn"})

        if text.strip() in ("/exit", "/quit"):
            self._request_session_end(reason=text.strip().lstrip("/"))
            return True
        if handled:
            return True

        prompt = rewritten if rewritten is not None else text

        if os.environ.get("XLII_FG_TURNS"):
            return self._drive_code_agent(prompt)
        if self._agent_running.is_set():
            self.send({"type": "meta_message",
                       "text": "an agent turn is already running — /btw <note> to steer it",
                       "level": "warn"})
            return True

        self._agent_running.set()
        self._send_busy_state(hard=False, agent=True)

        def _bg() -> None:
            ok = True
            try:
                ok = self._drive_code_agent(prompt)
            except Exception as e:  # noqa: BLE001
                self.send({"type": "meta_message",
                           "text": f"{type(e).__name__}: {e}", "level": "error"})
                ok = False
            finally:
                self._agent_running.clear()
                self._send_busy_state(hard=False, agent=False)
                self._finish_unit(ok)

        threading.Thread(target=_bg, name="face-code-turn", daemon=True).start()
        return "bg"
