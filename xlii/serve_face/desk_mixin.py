"""Desk switch, Home, browser door, skin, and session-end.

Mixin extracted from :mod:`xlii.serve_face.server`.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any


class FaceDeskMixin:
    """Desk switch, Home, browser door, skin, and session-end."""

    def _bind_desk_hook(self) -> None:
        """``/project switch`` (and the new-folder system task) land the face pack."""
        try:
            self.state.on_desk_switched = self._on_desk_switched
            self.state.on_stream_reset = self._on_stream_reset
            self.state.on_clear_screen = self._on_clear_screen
        except Exception:  # noqa: BLE001
            pass

    def _on_stream_reset(self) -> None:
        """``/reset`` forgot the tape — glass must drop it, not wait for reload."""
        self.sync_stream()
        self.emit_open_peeks()

    def _on_clear_screen(self) -> None:
        """``/clear`` / ``/cls`` — pixels only; talk stays."""
        self.send({"type": "clear_transcript"})

    def _on_desk_switched(self, project: Any) -> None:
        from xlii.project_paths import is_home_desk_project

        if is_home_desk_project(project):
            self._landed_root = ""
            self._desk_announce = ""
            self._stream_live = ""
            return
        announce = getattr(self, "_desk_announce", "") or "switched"
        self._desk_announce = ""
        self._land_folder(project, announce=announce)

    def go_home(self) -> bool:
        """Leave the bound folder for the blank-slate desk (scratch = home).

        Does **not** flip talk/lab — Home is leaving a project, not a posture.
        """
        if self._turn_mutation_busy():
            self.send({"type": "meta_message", "level": "warn",
                       "text": self._turn_mutation_busy_message("go home")})
            return False
        try:
            from xlii.cmds.project.scratch import _ensure_home_desk
            from xlii.config import ProjectConfig
            from xlii.project_paths import is_home_desk_project, scratch_home_roam_cwd
            from xlii.repl_cmds.switch import switch_to_code_project
            from xlii.workbench import BUILTIN_WORKBENCHES
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"home unavailable: {type(e).__name__}: {e}"})
            return False

        try:
            root = _ensure_home_desk()
            project = ProjectConfig.load(root)
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"home failed: {type(e).__name__}: {e}"})
            return False
        if project is None:
            self.send({"type": "meta_message", "level": "error",
                       "text": "home desk missing after ensure"})
            return False

        current = getattr(self.state, "project", None)
        already = is_home_desk_project(current)
        if already:
            self.send({"type": "meta_message", "level": "info",
                       "text": "already home — blank slate"})
            return True

        try:
            from xlii.recent_desks import touch_desk

            touch_desk(current)
        except Exception:  # noqa: BLE001
            pass

        kept = self.posture
        ctx = self.state.as_context_dict()
        try:
            switch_to_code_project(ctx, project, reload_surface=True)
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"home failed: {type(e).__name__}: {e}"})
            return False

        try:
            self.state.scratch = True
            self.state.no_sync = True
        except Exception:  # noqa: BLE001
            pass
        roam = scratch_home_roam_cwd(project)
        if roam is not None:
            try:
                self.state.shell_cwd = roam
            except Exception:  # noqa: BLE001
                pass
        self.state.workbench = BUILTIN_WORKBENCHES.get("home")
        self._home_pack_settled = True
        self._wb_posture_applied = None
        if self._deck is not None:
            self._deck.sync_pack(force=True)
        # Posture stays — Home is not Talk.
        self._set_posture(kept if kept in ("chat", "code") else "chat")
        try:
            self.send(self.mode_state())
            self.send(self.chrome_state())
            self.deck.send_snapshot()
            self.send(self.pane_catalog())
            self.send(self.plugin_catalog())
            self.send(self.workbench_catalog())
        except Exception:  # noqa: BLE001
            pass
        self._landed_root = ""
        self._stream_live = ""
        self.sync_stream()
        self.emit_open_peeks()
        self.send({"type": "meta_message", "level": "info",
                   "text": "home — blank slate"})
        return True

    def open_research_tool(self, tool: str) -> bool:
        """Research-pack doors: kg / canvas (v1 → real store panes + teach).

        three-faces.md: tools on the pack, never a fourth face. v1 opens an
        existing pane that can hold research product; graph/whiteboard later.
        """
        t = (tool or "").strip().lower()
        if t in ("kg", "knowledge", "graph"):
            ok = self.deck.open_pane("wiki")
            self.send({
                "type": "meta_message", "level": "info",
                "text": "kg door → wiki (store facts & pages). "
                        "Graph edges / query later — still research tools, not a mode.",
            })
            # Bookmarks ride alongside as “nodes you already marked”.
            if ok:
                try:
                    self.deck.ensure_pane("bookmarks")
                except Exception:  # noqa: BLE001
                    pass
            return bool(ok)
        if t in ("canvas", "board", "whiteboard"):
            return bool(self.deck.open_pane("canvas"))
        self.send({
            "type": "meta_message", "level": "warn",
            "text": f"unknown research tool {tool!r} — kg | canvas | browser",
        })
        return False

    def open_terminal(self, run: str = "") -> bool:
        """Open a real terminal at the configured dest. ``run='mc'`` is Midnight Commander.

        The face is not a TTY — file managers stay in an emulator, not in Tauri.
        Dest is Options → Config → new terminal (this project / home / root / custom).
        """
        from xlii.desk import resolve_terminal_cwd
        from xlii.interactive import launch_in_external_terminal

        state = self.state
        cfg = getattr(state, "cfg", None)
        cwd = resolve_terminal_cwd(state, cfg)
        pref = str(getattr(cfg, "tui_terminal", "") or "")
        ok, msg = launch_in_external_terminal(cwd, run=run, preferred=pref)
        self.send({"type": "meta_message", "text": msg,
                   "level": "success" if ok else "warn"})
        return ok

    def set_terminal_cwd_path(self, path: str) -> bool:
        """Persist Tools → New terminal custom folder (from the config claim)."""
        from xlii.desk import apply_terminal_cwd_path

        cfg = getattr(self.state, "cfg", None)
        ok, msg = apply_terminal_cwd_path(cfg, path)
        self.send({"type": "meta_message", "text": msg,
                   "level": "success" if ok else "warn"})
        try:
            self.send(self.chrome_state())
        except Exception:
            # Best-effort chrome refresh — the setting is already persisted.
            pass
        try:
            self.deck.send_snapshot()
        except Exception:
            # Best-effort snapshot — the setting is already persisted.
            pass
        return ok

    def set_face_skin(self, name: str) -> bool:
        """Persist the face web skin (survives Tauri relaunch; localStorage does not)."""
        skin = (name or "").strip().lower()
        from xlii.skin_packs import is_known_skin

        if not is_known_skin(skin):
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"unknown skin {name!r}"})
            return False
        cfg = getattr(self.state, "cfg", None)
        if cfg is None:
            from xlii.config import GlobalConfig

            cfg = GlobalConfig.load()
            try:
                self.state.cfg = cfg
            except Exception:
                # Keep the local cfg even if state refuses the attribute.
                pass
        try:
            cfg.face_skin = skin
        except Exception:
            # Best-effort — save() below persists whatever landed.
            pass
        try:
            save = getattr(cfg, "save", None)
            if callable(save):
                save()
        except Exception as e:
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"skin not saved: {type(e).__name__}: {e}"})
            return False
        try:
            self.send(self.chrome_state())
        except Exception:
            # Best-effort chrome refresh — the skin is already saved.
            pass
        return True

    def _persist_face_pref(self, field: str, value: Any) -> bool:
        cfg = getattr(self.state, "cfg", None)
        if cfg is None:
            from xlii.config import GlobalConfig

            cfg = GlobalConfig.load()
            try:
                self.state.cfg = cfg
            except Exception:
                # The preference is on disk either way; a state object that
                # refuses the attribute just keeps the old cfg in memory.
                pass
        try:
            setattr(cfg, field, value)
        except Exception:
            return False
        try:
            save = getattr(cfg, "save", None)
            if callable(save):
                save()
        except Exception as e:
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"{field} not saved: {type(e).__name__}: {e}"})
            return False
        try:
            self.send(self.chrome_state())
        except Exception:
            # The preference is already persisted; the client re-reads chrome
            # on reconnect.
            pass
        return True

    def set_face_fkeys(self, visible: bool) -> bool:
        return self._persist_face_pref("face_fkeys", bool(visible))

    def set_face_bold(self, on: bool) -> bool:
        return self._persist_face_pref("face_bold", bool(on))

    def open_browser(self, url: str = "", *, headless: bool = False) -> bool:
        """Quick-strip browser door: **visible** Chromium (CDP) when available.

        Face default is *headed* (``headless=False``) so the strip button opens
        a real window. Agent-tool callers can still pass ``headless=True``.
        Prefers the process-local research browser (:mod:`xlii.agent_browser`)
        so the agent can later ``browser extract`` the same session. Falls
        back to the OS default browser (new window) if Chromium is missing.

        Empty *url* → forge ``origin`` when present, else project ``file://``
        root; bare open with no URL still spawns a blank agent session.
        """
        import webbrowser
        from urllib.parse import urlparse

        target = (url or "").strip()
        if not target:
            target = self._default_browser_url()

        # --- agent Chromium (path 3) ---
        try:
            from xlii.agent_browser import find_chromium, open_session
        except Exception:  # noqa: BLE001
            find_chromium = None  # type: ignore[assignment]
            open_session = None  # type: ignore[assignment]

        if find_chromium is not None and open_session is not None and find_chromium():
            try:
                snap = open_session(url=target or "", headless=headless)
            except Exception as e:  # noqa: BLE001
                self.send({"type": "meta_message", "level": "error",
                           "text": f"agent browser: {type(e).__name__}: {e}"})
                return False
            if snap.ok:
                shown = (snap.url or target or "about:blank")
                if len(shown) > 100:
                    shown = shown[:97] + "…"
                title = (snap.title or "").strip()
                bit = f" — {title}" if title else ""
                mode = "headless" if snap.headless else "window"
                self.send({
                    "type": "meta_message", "level": "info",
                    "text": (
                        f"browser → {shown}{bit} "
                        f"({mode}, pid {snap.pid}) · "
                        "agent tool: browser extract | close"
                    ),
                })
                return True
            # Agent path failed — report clearly (do NOT claim "no Chromium"
            # when the binary exists but CDP/profile lock failed).
            self.send({
                "type": "meta_message", "level": "warn",
                "text": f"agent browser failed: {snap.error or 'unknown error'}",
            })
            if not target:
                self.send({
                    "type": "meta_message", "level": "info",
                    "text": "browser — no URL to open in OS fallback "
                            "(Home has no forge remote). "
                            "Ask the agent: browser open url=https://… "
                            "or switch into a project first.",
                })
                return False
            self.send({"type": "meta_message", "level": "info",
                       "text": "trying OS browser…"})

        if not target:
            self.send({"type": "meta_message", "level": "info",
                       "text": "browser — install chromium (or set $XLII_CHROMIUM) "
                               "and retry, or pass a URL / switch into a project"})
            return False
        parsed = urlparse(target)
        if parsed.scheme not in ("http", "https", "file"):
            self.send({"type": "meta_message", "level": "warn",
                       "text": f"browser refused scheme {parsed.scheme!r}"})
            return False
        try:
            from xlii.desk import resolve_browser

            pref = resolve_browser(getattr(self.state, "cfg", None))
        except Exception:
            pref = None
        if pref:
            import shlex
            import subprocess

            try:
                subprocess.Popen(
                    [*shlex.split(pref), target],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                shown = target if len(target) < 120 else target[:117] + "…"
                self.send({"type": "meta_message", "level": "info",
                           "text": f"OS browser → {shown} ({pref})"})
                return True
            except Exception as e:  # noqa: BLE001
                self.send({"type": "meta_message", "level": "warn",
                           "text": f"configured browser failed ({pref}): {e}"})
        try:
            # new=1 → prefer a new window (product: strip door = visible window).
            ok = webbrowser.open(target, new=1)
        except Exception as e:  # noqa: BLE001
            self.send({"type": "meta_message", "level": "error",
                       "text": f"browser: {type(e).__name__}: {e}"})
            return False
        shown = target if len(target) < 120 else target[:117] + "…"
        self.send({"type": "meta_message",
                   "level": "info" if ok else "warn",
                   "text": f"OS browser → {shown}" if ok else
                           f"browser could not open {shown}"})
        return bool(ok)

    def _default_browser_url(self) -> str:
        """Best URL for the research browser door (forge → file://)."""
        import subprocess

        project = getattr(self.state, "project", None)
        root = getattr(project, "project_root", None) if project else None
        name = (getattr(project, "name", "") or "") if project else ""
        surface_scratch = (
            getattr(self.state, "scratch", False)
            or str(name).startswith("scratch/")
        )
        if not root:
            return ""
        root_p = Path(root).expanduser()
        if not surface_scratch:
            try:
                r = subprocess.run(
                    ["git", "-C", str(root_p), "remote", "get-url", "origin"],
                    capture_output=True, text=True, timeout=2,
                )
                remote = (r.stdout or "").strip()
                if remote.startswith("http://") or remote.startswith("https://"):
                    return remote.removesuffix(".git")
                if remote.startswith("git@"):
                    host_path = remote.split("@", 1)[-1]
                    host, _, path = host_path.partition(":")
                    path = path.removesuffix(".git")
                    if host and path:
                        return f"https://{host}/{path}"
            except Exception:
                # No git, no origin remote, or the 2s timeout expired — fall
                # through to the local file:// URI below.
                pass
            try:
                return root_p.resolve().as_uri()
            except Exception:
                return ""
        return ""

    def _request_session_end(self, reason: str = "exit") -> None:
        """Graceful teardown + tell the client to leave (close Tauri window).

        ``/exit`` / ``/quit`` used to only set ``_shutdown`` and print bye — the
        accept loop stopped, but the Tauri webview stayed open forever. Run the
        same exit sequence as the inline REPL (save / journal / habits / jobs),
        then emit ``session_end`` so the face host can ``window.close()``.
        """
        if getattr(self, "_session_end_sent", False):
            self._shutdown.set()
            return
        self._session_end_sent = True
        try:
            self.state.quit_requested = True
        except Exception:
            # A state stub without the flag still gets the cancel and teardown
            # below.
            pass
        # Halt an in-flight agent/tool gate so save/teardown is not blocked.
        try:
            self.cancel()
        except Exception:
            # Cancel is a courtesy to unblock teardown; failing it must not
            # stop the session from ending.
            pass

        def _print(msg: str) -> None:
            # Rich markup from exit_sequence → plain text for the wire.
            text = msg
            try:
                from rich.text import Text
                text = Text.from_markup(msg).plain
            except Exception:
                # rich missing or the markup is malformed — fall back to the
                # raw message set above.
                pass
            text = (text or "").strip()
            if text:
                self.send({"type": "meta_message", "text": text, "level": "info"})

        try:
            from xlii.exit_sequence import end_code_session, run_graceful_exit

            run_graceful_exit(
                self.state,
                printer=_print,
                on_exit=lambda: end_code_session(self.state, console=self.state.console),
            )
        except Exception as e:  # noqa: BLE001 — exit must still close the window
            self.send({"type": "meta_message",
                       "text": f"exit cleanup: {type(e).__name__}: {e}",
                       "level": "warn"})

        try:
            from xlii.face_receipt import note_project

            note_project(getattr(self.state, "project", None), reason=reason or "exit")
        except Exception:  # noqa: BLE001
            pass
        self.send({
            "type": "session_end",
            "reason": reason,
            "message": "session saved — bye",
        })
        self._shutdown.set()
        # Drop the live socket shortly after so a stuck client still tears down
        # the accept loop path; session_end is the normal close signal.
        def _drop() -> None:
            time.sleep(0.15)
            with self._client_lock:
                live, self._client = self._client, None
            if live is not None:
                try:
                    live.close()
                except OSError:
                    # Already closed, or half-open after the peer vanished.
                    pass

        threading.Thread(target=_drop, name="face-session-end", daemon=True).start()
