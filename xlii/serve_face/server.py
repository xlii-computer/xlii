"""FaceServer shell — construction, wire send, client I/O.

Large method groups live in mixins composed here (same pattern as
``xlii.tui.app`` + ``app_*_mixin``):

* :mod:`xlii.serve_face.jobs_mixin` — jobs board / task-maker save
* :mod:`xlii.serve_face.catalogs_mixin` — chrome + catalogs
* :mod:`xlii.serve_face.plugins_mixin` — plugin catalog / call / maker
* :mod:`xlii.serve_face.focus_mixin` — outbox, uploads, feed focus
* :mod:`xlii.serve_face.projects_mixin` — create / join / drop / land
* :mod:`xlii.serve_face.streams_mixin` — stream catalog + sync
* :mod:`xlii.serve_face.desk_mixin` — Home, browser, skin, session-end
* :mod:`xlii.serve_face.input_mixin` — submit / cancel / worker

This module keeps ``__init__``, posture/deck, ``send``, and the client
reader. Public entry ``serve_face`` lives in :mod:`xlii.serve_face.http`.
"""
from __future__ import annotations

import json
import logging
import queue
import re
import secrets
import socket
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger("xlii.serve_face")

from xlii.serve_face.catalogs_mixin import FaceCatalogsMixin
from xlii.serve_face.desk_mixin import FaceDeskMixin
from xlii.serve_face.focus_mixin import FaceFocusMixin
from xlii.serve_face.input_mixin import FaceInputMixin
from xlii.serve_face.jobs_mixin import FaceJobsMixin
from xlii.serve_face.plugins_mixin import FacePluginsMixin
from xlii.serve_face.projects_mixin import FaceProjectsMixin
from xlii.serve_face.streams_mixin import FaceStreamsMixin
from xlii.serve_face.wire import _ConfirmBridge
from xlii.ws_server import _parse_client_message, _read_frame, _send_text


class FaceServer(FaceJobsMixin, FaceCatalogsMixin, FacePluginsMixin,
                 FaceFocusMixin, FaceProjectsMixin, FaceStreamsMixin,
                 FaceDeskMixin, FaceInputMixin):
    """One live session, one (current) client, one worker running inputs."""

    def __init__(self, *, boot, yolo: bool = False,
                 assets_dir: Optional[Path] = None) -> None:
        self.session = boot            # CodeSession from build_code_session
        self.state = boot.state
        self.yolo = yolo
        self.assets_dir = assets_dir
        self.posture = "chat"          # mojo-first (the locked surface model)
        self._set_posture("chat")
        self.pending_uploads: list[str] = []
        self._inputs: "queue.Queue[str]" = queue.Queue()
        self._busy = threading.Event()
        # bg-default M2.2: agent/persona turn running off the worker — input
        # stays free for the allow-list (/btw, /jobs, shell). Distinct from
        # ``_busy`` (hard: slash/shell/plugin on the worker).
        self._agent_running = threading.Event()
        self._send_lock = threading.Lock()
        self._client_lock = threading.Lock()
        self._client: Optional[socket.socket] = None
        self._shutdown = threading.Event()
        self.confirm = _ConfirmBridge(self.send)
        self._oneshot_agent: Any = None  # live transient [M] agent, for cancel
        self._turn_cancelled = threading.Event()
        self._session_end_sent = False  # /exit → session_end once
        self._home_pack_settled = False  # one-shot chat→home migrate only
        self._deck: Any = None  # face_panes.FaceDeck, built lazily (B1)
        self._wb_posture_applied: Optional[str] = None  # B2: posture follows type
        # Feed-view / F4 focus: next-turn references (not a rewind).
        self._focus: list[dict[str, Any]] = []
        try:
            from xlii.agent_browser import on_change

            on_change(self._on_browser_change)
        except Exception:  # noqa: BLE001 — HUD chip is optional
            pass
        self._landed_root = ""
        self._desk_announce = ""
        self._open_streams: list[dict[str, str]] = []
        self._stream_live = ""
        self._bind_plugin_form_hook()
        self._bind_desk_hook()
        self._bind_job_listener()
        self.view_posture = "desk"  # desk | phone — glass WS is phone
        self.glass_grant_mode = "full"  # full | preview
        self._client_glass = False
        # True from attach until hello is written — suppress chrome_state so a
        # brand-new conn never sees HUD before the handshake hello.
        self._hello_pending = False
        # Boot ``--view phone`` (serve --public backends): phone UI without
        # tailnet-glass sitting gate. Distinct from leftover view_posture of
        # another live glass client — see ``_handle_connection`` provenance.
        self._boot_view_phone = False
        self._wire_session_id = f"face-{secrets.token_hex(6)}"
        self._had_client = False
        self._talk_tape: deque[dict[str, str]] = deque(maxlen=80)
        self._talk_in_flight: dict[str, str] | None = None
        self._disk_resume_announced = False
        # Door land:* while a turn holds _agent_running/_busy — apply after
        # the turn clears (see _flush_deferred_door_land). None = none queued.
        self._pending_door_land: Optional[str] = None

    def _sync_command_scope(self) -> None:
        """The menu and slash dispatcher scope to the live face posture."""
        try:
            self.state.command_scope = self.posture
        except Exception:  # noqa: BLE001 — chrome scope must not block boot
            pass

    def _phone_glass(self) -> bool:
        return bool(
            getattr(self, "_client_glass", False)
            or getattr(self, "view_posture", "desk") == "phone"
        )

    def _apply_door_land(self, name: str) -> None:
        """Perform one land via join_project / go_home (server owns the switch)."""
        try:
            if not name or name in {"home", "scratch/home"}:
                self.go_home()
            else:
                # join_project → _land_folder; home desk routes to go_home
                # so Home keeps scratch/no_sync.
                self.join_project(name)
        except Exception:
            # Land is best-effort; tool result already reported the switch.
            pass

    def _flush_deferred_door_land(self) -> None:
        """Apply a land queued while _agent_running/_busy held the turn."""
        if self._pending_door_land is None:
            return
        if self._turn_cancelled.is_set():
            # User stopped the turn — do not apply a switch they aborted.
            self._pending_door_land = None
            return
        name = self._pending_door_land
        self._pending_door_land = None
        if self._turn_mutation_busy():
            # Still owned — keep the queue (last land wins on re-queue).
            self._pending_door_land = name
            return
        self._apply_door_land(name)

    def _emit_door(self, payload: dict) -> None:
        """Mojo-keeper K6: pane:* now; land:* deferred while the turn is busy.

        Server owns land (app.js must not also join_project/go_home). Door
        tools fire mid-oneshot with ``_agent_running`` set, so join_project /
        go_home would refuse via ``_turn_mutation_busy`` — queue the target
        and flush after the turn clears.
        """
        data = dict(payload or {})
        data["type"] = "door"
        try:
            self.send(data)
        except Exception:
            # Door wire emit is best-effort; local pane/land still runs below.
            pass
        action = str(data.get("action") or "")
        if action.startswith("pane:"):
            pid = action.split(":", 1)[1].strip()
            if pid:
                try:
                    self.deck.open_pane(pid)
                except Exception:
                    # Local pane open is best-effort for door tools.
                    pass
        elif action.startswith("land:"):
            name = action.split(":", 1)[1].strip()
            if self._turn_mutation_busy():
                self._pending_door_land = name
            else:
                self._apply_door_land(name)

    def _set_posture(self, posture: str) -> None:
        # Phone glass is elevated Mojo (D3): talk-primary, lab hire allowed,
        # never the [$] island — even after a project land that would be code.
        if self._phone_glass():
            posture = "chat"
        self.posture = posture
        try:
            # [M] is ask-first; [$] is the live desk. `!` / `cd` / `/sh` share
            # shell_cwd only when this flag matches the flip the user sees.
            self.state.ask_primary = posture == "chat"
        except Exception:  # noqa: BLE001
            pass
        self._sync_command_scope()
        if getattr(self, "_client", None) is not None:
            try:
                self.send(self.command_catalog())
            except Exception:
                # Catalog refresh is advisory; the client re-requests it on
                # reconnect.
                pass

    def _enter_lab(self) -> bool:
        """Flip [M] → [$] if the glass will take lab. True when already in lab."""
        if self.posture == "code":
            return True
        try:
            from xlii.glass import face_may_accept

            ok, reason = face_may_accept("[$]")
        except Exception:
            self.send({"type": "error",
                       "message": "face gate unavailable — try again"})
            return False
        if not ok:
            self.send({"type": "error", "message": reason})
            try:
                self.send(self.chrome_state())
            except Exception:
                # The refusal message above already went out; chrome is cosmetic.
                pass
            return False
        self._set_posture("code")
        try:
            self.send(self.mode_state())
            self.send(self.chrome_state())
            if getattr(self, "_deck", None) is not None:
                self._deck.send_snapshot()
        except Exception:
            # The posture already flipped; these sends are only the client's
            # view of it.
            pass
        return True

    def _prefer_home_pack(self) -> None:
        """One-shot: migrate default chat pack → switch-first home pack on desks.

        Must **not** re-run after the user (or switch) picks another workbench —
        that stomped Workbench menu / ``/workbench`` back to home every chrome
        refresh (``chrome_state`` / ``_finish_unit``).
        """
        if getattr(self, "_home_pack_settled", False):
            return
        st = self.state
        name = getattr(getattr(st, "project", None), "name", "") or ""
        is_home = bool(getattr(st, "scratch", False)) or str(name).startswith("scratch/")
        if not is_home:
            self._home_pack_settled = True
            return
        wb = getattr(st, "workbench", None)
        wb_name = getattr(wb, "name", "") or ""
        # Only upgrade bare/legacy defaults — never clobber code (or an explicit pack).
        if wb_name in ("", "chat", "general"):
            try:
                from xlii.workbench import BUILTIN_WORKBENCHES

                home = BUILTIN_WORKBENCHES.get("home")
                if home is not None:
                    st.workbench = home
            except Exception:  # noqa: BLE001
                pass
        self._home_pack_settled = True

    def _apply_workbench_chrome(self) -> None:
        """Workbench switch refreshes chrome (quick-launch strip), not identity.

        three-faces.md: `/workbench` is a quick-pack verb — do **not** flip
        posture here. Manual ``[M]``/``[$]`` stays user-owned.
        """
        self._prefer_home_pack()
        wb = getattr(self.state, "workbench", None)
        name = getattr(wb, "name", None)
        if name is None or name == self._wb_posture_applied:
            return
        self._wb_posture_applied = name
        try:
            self.deck.sync_pack()
            self.send(self.chrome_state())
        except Exception:  # noqa: BLE001 — chrome refresh is best-effort only
            pass

    @property
    def deck(self) -> Any:
        """The face's pane deck (B1). None until first use; empty-workbench
        decks are a no-op inside FaceDeck (the chat type = today's face)."""
        if self._deck is None:
            from xlii.face_panes import FaceDeck

            self._deck = FaceDeck(self)
            self._install_canvas_hook()
        return self._deck

    def _install_canvas_hook(self) -> None:
        """A written artifact opens on the canvas (talk | the work)."""
        def _hook(path: Any) -> None:
            try:
                self.deck.reveal_canvas(str(path))
            except Exception:
                # The artifact is already written; revealing it is a convenience.
                pass
        try:
            self.state.on_artifact_written = _hook
        except Exception:
            # A state object that refuses the attribute simply gets no canvas
            # hook.
            pass

    # ------------------------------------------------------------------ wire

    def send(self, obj: dict[str, Any]) -> None:
        """Serialize to the CURRENT client; drop silently when none/gone (the
        session outlives any one connection — a reconnect gets fresh state)."""
        kind = obj.get("type") if isinstance(obj, dict) else None
        # attach_glass phone_open_dual / agent_browser on_change can emit
        # chrome_state after _client is set but before hello — drop those so
        # the first frame on a brand-new conn stays the handshake.
        if kind == "chrome_state" and getattr(self, "_hello_pending", False):
            return
        with self._client_lock:
            conn = self._client
        if conn is None:
            return
        try:
            with self._send_lock:
                _send_text(conn, json.dumps(obj, separators=(",", ":")))
            if kind == "hello":
                self._hello_pending = False
        except OSError:
            # The peer went away mid-write; the reader thread is what detaches
            # the client, so there is nothing to do here.
            pass

    def _on_browser_change(self) -> None:
        """HUD chip follows the process-wide Chromium (face door or agent tool)."""
        # Skip while hello is pending — chrome_state() → live_chrome mutates
        # occupancy under the lock and can race mouth release on desk attach.
        if getattr(self, "_hello_pending", False):
            return
        try:
            self.send(self.chrome_state())
        except Exception:  # noqa: BLE001
            pass

    def _send_feed_view(self, obj: dict[str, Any]) -> None:
        """Emit a feed card and make sure the stream is visible to receive it."""
        try:
            reveal = getattr(self.deck, "_reveal_stream", None)
            if callable(reveal):
                reveal()
        except Exception:  # noqa: BLE001 — feed delivery must not depend on layout repair
            pass
        self.send(obj)

    # ------------------------------------------------------------ client I/O

    def _clear_glass_client_state(self) -> bool:
        """Reset phone-glass flags; return whether mouth should be released.

        Restores boot ``--view phone`` UI posture when that flag is set — local
        phone faces must keep cutting panes without ``_client_glass`` / sitting.
        """
        was_glass = bool(getattr(self, "_client_glass", False))
        self._client_glass = False
        self.view_posture = (
            "phone" if getattr(self, "_boot_view_phone", False) else "desk"
        )
        self.glass_grant_mode = "full"
        return was_glass

    def _release_glass_mouth_if(self, was_glass: bool) -> None:
        if not was_glass:
            return
        try:
            from xlii.occupancy_store import load_live, mutate
            from xlii.serve_face.glass import release_glass_mouth

            release_glass_mouth()
            # Fail-closed: if release was a no-op or a race re-took me@,
            # force the mouth back to desk for non-glass attach/detach.
            occ = load_live()
            if getattr(occ, "mouth", "") == "me":
                mutate(lambda o: o.take_mouth("desk"))
        except Exception:
            _log.exception("glass mouth release failed")

    def attach_client(self, conn: socket.socket) -> bool:
        """Make ``conn`` the live client. Refuse while a turn is in flight
        (the old client is mid-conversation); otherwise the newcomer wins and
        the idle old connection is closed.

        A non-glass attach always clears phone-glass state. Detach of a
        displaced phone can miss cleanup because ``self._client`` is already
        the desk — so desk attach must reset flags and release the mouth here.
        """
        with self._client_lock:
            if self._client is not None and (
                    self._busy.is_set() or self._agent_running.is_set()):
                return False
            old, self._client = self._client, conn
            self._hello_pending = True
            # Desk (or any non-glass) attach never inherits phone glass state.
            was_glass = self._clear_glass_client_state()
        self._release_glass_mouth_if(was_glass)
        if old is not None:
            try:
                old.close()
            except OSError:
                # The displaced client's socket is already dead.
                pass
        return True

    def attach_glass_client(self, conn: socket.socket, *, grant: Any = None) -> bool:
        """Attach a tailnet-glass WS: phone posture, take the mouth."""
        if not self.attach_client(conn):
            return False
        try:
            self._client_glass = True
            self.view_posture = "phone"
            mode = str(getattr(grant, "mode", "") or "full").strip().lower()
            self.glass_grant_mode = mode if mode in ("full", "preview") else "full"
            try:
                from xlii.serve_face.glass import phone_open_dual

                phone_open_dual(self)
            except Exception:
                _log.exception("glass dual-pane open failed")
            self._set_posture("chat")
            # Claim mouth LAST — posture/dual open can race occupancy writers
            # (e.g. stale agent_browser on_change → chrome_state → mutate_bundle).
            from xlii.occupancy_store import load_live
            from xlii.serve_face.glass import take_glass_mouth

            take_glass_mouth()
            occ = load_live()
            if getattr(occ, "mouth", "") != "me":
                take_glass_mouth()
                occ = load_live()
            if getattr(occ, "mouth", "") != "me":
                raise RuntimeError(
                    "glass mouth take did not stick "
                    f"(mouth={getattr(occ, 'mouth', None)!r} "
                    f"via={getattr(occ, 'via', None)!r})"
                )
            return True
        except Exception:
            # Fail closed: roll back partial attach so desk posture and
            # occupancy are not left in phone-glass state without a reader.
            _log.exception("glass attach failed — rolling back client")
            self.detach_client(conn)
            try:
                conn.close()
            except OSError:
                pass
            return False

    def detach_client(self, conn: socket.socket) -> None:
        with self._client_lock:
            if self._client is conn:
                self._client = None
                was_glass = self._clear_glass_client_state()
                was_live = True
            else:
                # Displaced conn (e.g. old phone after desk attach) — glass
                # state already cleared by the newcomer.
                was_glass = False
                was_live = False
        if was_live:
            self.confirm.deny_all()
        self._release_glass_mouth_if(was_glass)

    def _glass_sitting_lock(self) -> None:
        """Thumb lock = sitting lock (tears the tailnet listener down)."""
        try:
            from xlii.repl_cmds.remote_lab import apply_remote_control

            apply_remote_control("lock", now=time.time())
        except Exception as e:  # noqa: BLE001
            self.send({"type": "error", "message": str(e)})
            return
        self.send({"type": "meta_message", "level": "info",
                   "text": "sitting locked — glass dies with the port"})
        self.send(self.chrome_state())

    def _glass_mark(self, name: str = "") -> None:
        """Tag the last turn. Auto-name when the thumb bar sends a bare mark."""
        label = (name or "").strip() or time.strftime("phone-%Y%m%d-%H%M%S")
        if re.search(r"\(window:\s*\d+\)\s*$", label):
            self.send({"type": "error",
                       "message": "mark name can't end with a '(window: N)' suffix"})
            return
        try:
            from xlii.repl_cmds.chat import _active_turns_dir
            from xlii.transcript import mark_last_turn

            turns_dir = _active_turns_dir(self.state)
            if turns_dir is None:
                self.send({"type": "error", "message": "mark failed: no active turn store"})
                return
            if not mark_last_turn(turns_dir, label):
                self.send({"type": "meta_message", "level": "warn",
                           "text": "no turns to mark yet — say something first"})
                return
        except Exception as e:  # noqa: BLE001
            self.send({"type": "error", "message": f"mark failed: {e}"})
            return
        try:
            from xlii.occupancy_store import mutate

            mutate(lambda o: o.record_remote_lab_agent(now=time.time()))
        except Exception:
            pass
        self.send({"type": "meta_message", "level": "success",
                   "text": f"marked last turn as {label}"})
        try:
            self.deck.send_snapshot()
        except Exception:
            pass

    def _glass_input(self, text: str, *, mode: str | None = None) -> None:
        """Elevated-Mojo path: talk always; /mark and sitting-lock as verbs."""
        t = (text or "").strip()
        scope = mode if mode is not None else getattr(self, "glass_grant_mode", "full")
        preview = str(scope or "full").strip().lower() == "preview"
        if t in ("[$]", "/$",) or t.startswith("[$]"):
            _log.warning("glass refuse: $ from phone input")
            self.send({"type": "error",
                       "message": "phone glass is talk-only — no $ lab"})
            self.send({"type": "turn_done", "ok": False, "exit_code": 1})
            return
        if t in ("/remote-control lock", "/remote-control lock"):
            self._glass_sitting_lock()
            return
        if t == "/mark" or t.startswith("/mark "):
            rest = t[5:].strip() if t.startswith("/mark") else ""
            self._glass_mark(rest)
            return
        if preview:
            self.send({"type": "error",
                       "message": "preview grant — read only (marks still work)"})
            self.send({"type": "turn_done", "ok": False, "exit_code": 1})
            return
        if self._agent_running.is_set() or self._busy.is_set():
            self.send({"type": "error",
                       "message": "agent working — wait for the reply"})
            return
        # Client setBusy(true) on send and waits for turn_done. Run off the
        # WS reader so pings/lock still land while Mojo thinks.
        self._agent_running.set()
        self._send_busy_state(hard=False, agent=True)

        def _bg() -> None:
            ok = True
            try:
                from xlii.face_remote import ingest_face_turn

                reply = ingest_face_turn(self, t, claim_mouth=False)
                if isinstance(reply, str) and reply.startswith("[face]"):
                    self.send({"type": "error", "message": reply})
                    ok = False
            except Exception as e:  # noqa: BLE001
                self.send({"type": "error", "message": str(e)})
                ok = False
            finally:
                self._agent_running.clear()
                try:
                    self._send_busy_state(hard=False, agent=False)
                except Exception:
                    pass
                try:
                    self._flush_deferred_door_land()
                except Exception:
                    pass
                self.send({"type": "turn_done", "ok": ok,
                           "exit_code": 0 if ok else 1})

        threading.Thread(target=_bg, name="glass-mojo-turn", daemon=True).start()

    def reader(
        self,
        conn: socket.socket,
        *,
        conn_glass: bool = False,
        grant_mode: str = "full",
    ) -> None:
        """Per-connection reader — control messages work even mid-turn."""
        mode = str(grant_mode or "full").strip().lower()
        if mode not in ("full", "preview"):
            mode = "full"
        while not self._shutdown.is_set():
            with self._client_lock:
                if self._client is not conn:
                    break
            # G1 mid-WS gate: REAL tailnet-glass (grant-backed ``_client_glass``)
            # dies with the sitting. Local ``--view phone`` never sets this flag.
            if conn_glass:
                try:
                    from xlii.face_remote import sitting_allows_remote

                    if not sitting_allows_remote():
                        break
                except Exception:
                    break
            try:
                frame = _read_frame(conn)
            except OSError:
                break
            if frame is None:
                break
            opcode, payload = frame
            if opcode == 0x8:
                break
            if opcode != 0x1:
                continue
            try:
                msg = _parse_client_message(payload.decode(errors="replace"))
            except ValueError as e:
                self.send({"type": "error", "message": str(e)})
                continue
            with self._client_lock:
                if self._client is not conn:
                    break
            kind = msg.get("type")
            # Glass privilege cut: grant-backed phone WS is fail-closed —
            # only an explicit allowlist of verbs may dispatch (D2/D3/preview).
            if conn_glass:
                from xlii.serve_face.glass import glass_verb_allowed

                ok, reason = glass_verb_allowed(str(kind or ""), mode=str(mode))
                if not ok:
                    _log.warning("glass refuse: verb %s (%s)", kind, mode)
                    self.send({"type": "error", "message": reason})
                    continue
            if kind == "ping":
                self.send({"type": "pong"})
            elif kind == "confirm":
                self.confirm.resolve(str(msg.get("id") or ""),
                                     bool(msg.get("approve")))
            elif kind == "cancel":
                self.cancel()
            elif kind == "set_posture":
                posture = str(msg.get("posture") or "")
                if posture not in ("chat", "code"):
                    self.send({"type": "error",
                               "message": "posture must be chat or code"})
                    continue
                # Every refusal re-sends mode_state: the face paints the flip
                # chip optimistically on click, so a silent refuse would leave
                # them showing a posture the server never took.
                if conn_glass and posture == "code":
                    _log.warning("glass refuse: $ flip from phone session")
                    self.send({"type": "error",
                               "message": "phone glass is talk-only — no $ lab"})
                    self.send(self.mode_state())
                    continue
                if self._busy.is_set() or self._agent_running.is_set():
                    self.send({"type": "error",
                               "message": "busy — flip after the turn finishes"})
                    self.send(self.mode_state())
                    continue
                if posture == "code":
                    try:
                        from xlii.glass import face_may_accept

                        ok, reason = face_may_accept("[$]")
                        if not ok:
                            self.send({"type": "error", "message": reason})
                            self.send(self.mode_state())
                            self.send(self.chrome_state())
                            continue
                    except Exception:
                        self.send({"type": "error",
                                   "message": "face gate unavailable — try again"})
                        self.send(self.mode_state())
                        self.send(self.chrome_state())
                        continue
                self._set_posture(posture)
                self.send(self.mode_state())
                self.send(self.chrome_state())
                self.deck.send_snapshot()
            elif kind == "exit_overlay":
                # Flip chip while howto/ops/… is on — same as /off, chrome path.
                if self._turn_mutation_busy():
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "leave this mode")})
                    continue
                self._exit_overlay()
                self.send(self.mode_state())
                self.send(self.chrome_state())
            elif kind == "upload":
                if self._turn_mutation_busy():
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "upload")})
                    continue
                self.handle_upload(msg)
            elif kind == "pane_action":
                # Pane outcomes can mutate session and Dock state; serialize them with turns.
                if self._turn_mutation_busy():
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "use panes")})
                    continue
                self.deck.handle(msg)
            elif kind == "plugin_call":
                # Face Plugins menu (M2.1): same runner as /plugin call; works
                # in chat and code (no slash grammar required). Agent-running
                # is fine — this is a fetch, not a desk mutation.
                if self._busy.is_set():
                    self.send({"type": "error",
                               "message": "busy — try the plugin action again after this turn"})
                    continue
                plugin_id = str(msg.get("plugin") or "").strip()
                action_id = str(msg.get("action") or "").strip()
                raw_params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
                params = {str(k): v for k, v in raw_params.items()}
                if not plugin_id or not action_id:
                    self.send({"type": "error",
                               "message": "plugin_call requires plugin and action"})
                    continue
                if not self._start_plugin_call(plugin_id, action_id, params):
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "run plugins")})
            elif kind == "go_home":
                # Xlii → Home: leave the bound folder, keep talk/lab.
                if self._turn_mutation_busy():
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "go home")})
                    continue
                self.go_home()
            elif kind == "join_project":
                # Home switch door / projects pane — live switch, no chat slash.
                # Wire kind stays join_project (compat); product verb is switch.
                if self._turn_mutation_busy():
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "switch project")})
                    continue
                self.join_project(str(msg.get("name") or msg.get("project") or ""))
            elif kind == "fabric_sync_projects":
                if self._turn_mutation_busy():
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "sync fabric projects")})
                    continue
                self._run_off_reader(self.sync_fabric_projects,
                                     name="face-fabric-sync")
            elif kind == "fabric_new":
                if self._turn_mutation_busy():
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "create fabric project")})
                    continue
                node = str(msg.get("node") or "")
                name = str(msg.get("name") or msg.get("project") or "")
                self._run_off_reader(
                    lambda: self.create_fabric_project(node, name),
                    name="face-fabric-new",
                )
            elif kind == "swap_slots":
                if not self.deck.swap_slots():
                    self.send({"type": "meta_message", "level": "warn",
                               "text": "need two slots to swap"})
            elif kind == "set_slot":
                # Dual desk: assign stream / stream:<id> / a pane / empty to a|b.
                slot = str(msg.get("slot") or "b").strip()
                view = str(msg.get("view") if "view" in msg else msg.get("pane") or "")
                if view and view != "stream" and not view.startswith("stream:"):
                    self._prefer_home_pack()
                if self.deck.set_slot(slot, view):
                    pass
                elif view:
                    a, b, _focus = self.deck.slot_tuple()
                    other = b if str(slot).strip().lower() != "b" else a
                    mirrored = (
                        view == other
                        or (view == "stream" and str(other).startswith("stream:"))
                        or (str(view).startswith("stream:") and other == "stream")
                    )
                    if mirrored:
                        self.send({"type": "meta_message", "level": "warn",
                                   "text": "that view is already in the other slot"})
            elif kind == "focus_slot":
                # Click a slot. A peeked project tape becomes the live stream.
                slot = str(msg.get("slot") or "a").strip()
                self.deck.focus_slot(slot)
            elif kind == "open_pane":
                # Control-plane: open/close side dock without the turn queue.
                # Empty pane = close (``/panel off`` from the client).
                pid = str(msg.get("pane") or msg.get("id") or "").strip()
                if not pid:
                    self.deck.close_pane()
                    self.send({"type": "meta_message", "level": "info",
                               "text": "panel closed"})
                    continue
                self._prefer_home_pack()
                select = str(msg.get("select") or msg.get("address") or "").strip()
                if pid == "canvas" and select:
                    self.deck.reveal_canvas(select)
                    self.send({"type": "meta_message", "level": "info",
                               "text": f"canvas · {Path(select).name}"})
                    continue
                if self.deck.open_pane(pid):
                    self.send({"type": "meta_message", "level": "info",
                               "text": f"panel · {pid}"})
                else:
                    self.send({"type": "meta_message", "level": "warn",
                               "text": f"no pane {pid!r} — try /panel home"})
            elif kind == "set_workbench":
                # Menubar Workbench pack switch (same as /workbench <name>).
                if self._turn_mutation_busy():
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "switch workbench")})
                    continue
                self.set_workbench(str(msg.get("name") or msg.get("workbench") or ""))
            elif kind == "create_project":
                if self._turn_mutation_busy():
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "create project")})
                    continue
                self.create_project(
                    str(msg.get("path") or "").strip(),
                    kind=str(msg.get("kind") or "").strip(),
                )
            elif kind == "create_in_project":
                if self._turn_mutation_busy():
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "create")})
                    continue
                self.create_in_project(
                    str(msg.get("kind") or "file"),
                    str(msg.get("name") or "").strip(),
                )
            elif kind == "save_screenshot":
                self.save_screenshot(
                    str(msg.get("b64") or ""),
                    name=str(msg.get("name") or ""),
                    kind=str(msg.get("kind") or ""),
                )
            elif kind == "open_terminal":
                self.open_terminal(str(msg.get("run") or ""))
            elif kind == "jobs_open":
                self.jobs_open(str(msg.get("id") or msg.get("job") or ""))
            elif kind == "jobs_clear":
                self.jobs_clear()
            elif kind == "task_write":
                spec = msg.get("spec") if isinstance(msg.get("spec"), dict) else {}
                self.write_task(spec, run=bool(msg.get("run")))
            elif kind == "plugin_write":
                spec = msg.get("spec") if isinstance(msg.get("spec"), dict) else {}
                self.write_plugin(spec)
            elif kind == "set_terminal_cwd_path":
                self.set_terminal_cwd_path(str(msg.get("path") or msg.get("text") or ""))
            elif kind == "set_face_skin":
                self.set_face_skin(str(msg.get("skin") or msg.get("name") or ""))
            elif kind == "set_face_fkeys":
                vis = msg.get("visible")
                if vis is None:
                    vis = msg.get("on")
                self.set_face_fkeys(bool(vis))
            elif kind == "set_face_bold":
                on = msg.get("on")
                if on is None:
                    on = msg.get("bold")
                self.set_face_bold(bool(on))
            elif kind == "open_browser":
                # Quick-strip browser door — visible window by default (headed).
                # Opt-in headless only when the client sends headless=true.
                self.open_browser(
                    str(msg.get("url") or ""),
                    headless=bool(msg.get("headless", False)),
                )
            elif kind == "focus":
                # Feed-view / F4: pin an address for the next turn (not a rewind).
                self.handle_focus(msg)
            elif kind == "research_tool":
                # KG / canvas doors — interim real panes, not a fourth face.
                self.open_research_tool(str(msg.get("tool") or ""))
            elif kind == "plugin_subscribe":
                # Toggle subscription from the Plugins menu (mirrors panel).
                if self._turn_mutation_busy():
                    self.send({"type": "error",
                               "message": self._turn_mutation_busy_message(
                                   "try again")})
                    continue
                pid = str(msg.get("plugin") or "").strip()
                if not pid:
                    self.send({"type": "error", "message": "plugin_subscribe requires plugin"})
                    continue
                try:
                    from xlii.plugin import toggle_subscription
                    outcome, message = toggle_subscription(self.state, pid)
                    level = "warn" if outcome == "gated" else (
                        "error" if outcome == "no-project" else "success")
                    self.send({"type": "meta_message", "text": message, "level": level})
                    self.send(self.plugin_catalog())
                except Exception as e:  # noqa: BLE001
                    self.send({"type": "meta_message",
                               "text": f"{type(e).__name__}: {e}", "level": "error"})
            elif kind == "lock":
                try:
                    from xlii.glass import lock_face

                    lock_face()
                except Exception as e:  # noqa: BLE001
                    self.send({"type": "error", "message": str(e)})
                    continue
                self.send(self.chrome_state())
            elif kind == "unlock":
                if conn_glass:
                    _log.warning("glass refuse: unlock from phone session")
                    self.send({"type": "error",
                               "message": "the phone can lock, never unlock"})
                    continue
                code = str(msg.get("code") or "").strip()
                try:
                    from xlii.glass import unlock_face

                    ok, reason = unlock_face(code=code)
                except Exception as e:  # noqa: BLE001
                    self.send({"type": "error", "message": str(e)})
                    continue
                if not ok:
                    self.send({"type": "error", "message": f"unlock refused ({reason})"})
                self.send(self.chrome_state())
            elif kind == "remote_lock":
                # Public internet webcode must not lock the operator sitting.
                if not conn_glass:
                    _log.warning("refuse: remote_lock from non-glass client")
                    self.send({"type": "error",
                               "message": "remote_lock is glass-only"})
                    continue
                self._glass_sitting_lock()
            elif kind == "mark_last":
                self._glass_mark(str(msg.get("name") or ""))
            elif kind in ("input", "turn"):
                text = str(msg.get("text") or msg.get("prompt") or "").strip()
                if not text:
                    self.send({"type": "error", "message": f"{kind} requires text"})
                    continue
                # Exit must not wait behind hard-busy / agent gates — OS close
                # and title-bar ✕ send /exit and expect session_end promptly.
                if text in ("/exit", "/quit"):
                    self._request_session_end(reason=text.lstrip("/"))
                    continue
                if conn_glass:
                    self._glass_input(text, mode=mode)
                    continue
                try:
                    from xlii.glass import face_may_accept

                    ok, reason = face_may_accept(text)
                    if not ok:
                        self.send({"type": "error", "message": reason})
                        self.send(self.chrome_state())
                        continue
                except Exception:
                    self.send({"type": "error",
                               "message": "face gate unavailable — try again"})
                    self.send(self.chrome_state())
                    continue
                if not self.submit(text):
                    if self._agent_running.is_set():
                        busy_msg = ("agent working — /btw <note> to steer, "
                                    "stop to halt, /jobs to watch")
                    else:
                        busy_msg = "busy — one input at a time"
                    self.send({"type": "error", "message": busy_msg})
            else:
                self.send({"type": "error",
                           "message": f"unknown client message type: {kind}"})
        self.detach_client(conn)
        try:
            conn.close()
        except OSError:
            # Already closed by the peer or by the detach above.
            pass
