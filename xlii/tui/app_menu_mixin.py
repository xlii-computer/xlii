"""Commander menus, doorways, F-keys, New/Remove, task builder.

Mixin extracted from :mod:`xlii.tui.app` (grades plan Phase 5).
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Callable, Optional


from textual import work

from xlii.tui.input_surface import (
    _PromptInput,
)
from xlii.tui.transcript import TranscriptLog


import sys as _sys
_app = _sys.modules["xlii.tui.app"]
ConfirmModal = _app.ConfirmModal
PromptModal = _app.PromptModal
_parse_modifier = _app._parse_modifier
_doorway_key_set = _app._doorway_key_set
_menu_accel_key_map = _app._menu_accel_key_map

class AppMenuMixin:
    """Commander menus, doorways, F-keys, New/Remove, task builder."""

    def _resolve_hotkey_modifier(self) -> str:
        """The configured doorway-hotkey modifier (default 'alt'), read defensively off cfg."""
        cfg = getattr(self._state, "cfg", None) if self._state is not None else None
        return str(getattr(cfg, "tui_hotkey_modifier", None) or "alt")

    def set_hotkey_modifier(self, raw: str, *, persist: bool = True) -> str:
        """Rebuild the doorway hotkeys for modifier ``raw`` (the future Options-menu panel calls
        this). Optionally persist to config. Returns the normalized modifier actually applied."""
        self._doorway_keys = _doorway_key_set(raw)
        self._menu_accel_keys = _menu_accel_key_map(raw)  # menu mnemonics ride the same modifier
        applied = "+".join(_parse_modifier(raw))
        if persist:
            cfg = getattr(self._state, "cfg", None) if self._state is not None else None
            if cfg is not None and hasattr(cfg, "tui_hotkey_modifier"):
                try:
                    cfg.tui_hotkey_modifier = applied
                    if hasattr(cfg, "save"):
                        cfg.save()
                except Exception as exc:
                    print(f"failed to persist hotkey modifier {applied!r}: {exc}", file=_sys.stderr)
        return applied

    def _apply_theme(self, name: str) -> str:
        """Apply a Textual app theme by name and persist it — the single sink the Options → Theme…
        panel, its click verb (``PanelActions.apply_theme``), and the ``/theme`` command all route
        through. An unknown name is ignored with a warning toast. Returns the theme applied ('' if
        none). Switching ``self.theme`` repaints the whole TUI."""
        name = (name or "").strip()
        if not name or name not in self.available_themes:
            try:
                self.notify(f"unknown theme: {name!r}", severity="warning", timeout=3)
            except Exception:
                # Notification is best-effort; ignore UI notify failures and keep behavior unchanged.
                pass
            return ""
        self.theme = name
        cfg = getattr(self._state, "cfg", None) if self._state is not None else None
        if cfg is not None and hasattr(cfg, "tui_theme"):
            try:
                cfg.tui_theme = name
                if hasattr(cfg, "save"):
                    cfg.save()
            except Exception as exc:
                try:
                    self.notify(f"theme applied but not saved: {exc}", severity="warning", timeout=3)
                except Exception:
                    # Best-effort UI toast; failure to show this warning must not affect theme application.
                    pass
        try:
            self.notify(f"theme → {name}", timeout=2)
        except Exception:
            # Best-effort UI toast; applying the theme should still succeed if notifications fail.
            pass
        return name

    def _apply_canvas(self, mode: str | None = None, *, persist: bool = True, notify: bool = True) -> str:
        """Set transcript paper polarity (Track C) — Screen + ``#log`` CSS classes.

        Trim theme is unchanged; if the saved theme polarity no longer matches, re-resolve
        a compatible theme. Returns the normalized mode applied (``dark`` or ``light``)."""
        from xlii.tui.canvas import normalize_canvas, resolve_theme_for_canvas

        cfg = getattr(self._state, "cfg", None) if self._state is not None else None
        applied = normalize_canvas(
            mode if mode is not None else (getattr(cfg, "tui_canvas", None) if cfg else None)
        )
        cls = f"-canvas-{applied}"
        for m in ("dark", "light"):
            try:
                self.remove_class(f"-canvas-{m}")
            except Exception:
                # The class may not be set in the first place; only the add below has to land.
                pass
        try:
            self.add_class(cls)
        except Exception:
            # Unmounted or mid-teardown -- the canvas class is reapplied on the next mount.
            pass
        try:
            log = self.query_one("#log")
            for m in ("dark", "light"):
                log.remove_class(f"-canvas-{m}")
            log.add_class(cls)
        except Exception:
            # No #log widget in this layout; the app-level class set above already carries the canvas.
            pass
        if cfg is not None and hasattr(cfg, "tui_canvas"):
            try:
                cfg.tui_canvas = applied
                if persist and hasattr(cfg, "save"):
                    cfg.save()
            except Exception as exc:
                try:
                    self.notify(f"canvas applied but not saved: {exc}", severity="warning", timeout=3)
                except Exception:
                    # Already reporting a save failure -- a failed toast must not mask it.
                    pass
        # Re-resolve trim theme when polarity may have drifted.
        if cfg is not None:
            resolved = resolve_theme_for_canvas(
                getattr(cfg, "tui_theme", ""),
                applied,
                self.available_themes,
            )
            if resolved and resolved != getattr(self, "theme", ""):
                try:
                    self.theme = resolved
                    if hasattr(cfg, "tui_theme"):
                        cfg.tui_theme = resolved
                        if persist and hasattr(cfg, "save"):
                            cfg.save()
                except Exception:
                    # An unknown theme name or an unwritable config leaves the previous theme in place.
                    pass
        try:
            if notify:
                self.notify(f"canvas → {applied}", timeout=2)
        except Exception:
            # The canvas is already applied; this toast is only confirmation.
            pass
        return applied

    def _do_screenshot(self, dest: Optional[str] = None) -> Optional[str]:
        """Save an SVG screenshot of the TUI (Options → Save screenshot). ``dest`` overrides the
        path; otherwise it lands on the Desktop (falling back to $HOME) as
        ``xlii-<project>-<timestamp>.svg``. Textual's ``save_screenshot`` always writes SVG. Returns
        the saved path, or None on failure (surfaced as an error toast)."""
        try:
            if dest:
                p = Path(dest).expanduser()
                saved = self.save_screenshot(filename=p.name, path=str(p.parent))
            else:
                base = Path.home() / "Desktop"
                if not base.is_dir():
                    base = Path.home()
                fname = f"xlii-{self._project_name}-{time.strftime('%Y%m%d-%H%M%S')}.svg"
                saved = self.save_screenshot(filename=fname, path=str(base))
        except Exception as exc:
            try:
                self.notify(f"screenshot failed: {exc}", severity="error", timeout=4)
            except Exception:
                # Best-effort UI notification only; preserve original failure path if notify fails.
                pass
            return None
        try:
            self.notify(f"screenshot → {saved}", timeout=4)
        except Exception:
            # Best-effort UI toast; screenshot save should still succeed if notifications fail.
            pass
        return saved

    def action_fkey_open(self, scheme: str) -> None:
        """A content-driving F-key: open ``scheme://`` in Pane 2 (same seam as the chip doorways)."""
        self._open_in_dock_view(f"{scheme}://")

    def _current_selection_address(self) -> Optional[str]:
        """The address of the item highlighted in the Dock's focused pane, or None."""
        from xlii.tui.dock_surface import DockSurface

        try:
            dock = self.query_one(DockSurface).dock
        except Exception:
            return None
        pane = dock.pane(dock.focused)
        node = getattr(pane.selection(), "node", None) if pane is not None else None
        return getattr(node, "address", None) if node is not None else None

    def action_copy_selection(self) -> None:
        """Copy the file panel's current selection (its address) into the command line, so the
        thing you just highlighted in Pane 2 can be referenced in your next prompt/command. No-op when
        no Dock is docked or nothing is selected. (Helper, still tested; not bound to a key since the
        V2b F-row remap — F5 now runs action_copy_export.)"""
        address = self._current_selection_address()
        if address:
            self._append_to_input(address)

    def action_copy_mode(self) -> None:
        """Ctrl+R — enter the transcript's mouseless block-range copy-mode
        (Vector E Round-2). No-op unless the selection-aware transcript is
        mounted (it always is; the getattr guard keeps a plain TranscriptLog
        from raising)."""
        try:
            log = self.query_one("#log", TranscriptLog)
        except Exception:
            return
        enter = getattr(log, "enter_copy_mode", None)
        if callable(enter):
            enter()

    def action_help(self) -> None:
        """F1 — help: seed ``/howto`` (ask xlii how to use itself)."""
        self._prefill_input("/howto ")

    def _show_about(self) -> None:
        """Help → About — credits + today's tagline, into the transcript."""
        from xlii.about import about_plain, about_renderable

        try:
            self.query_one("#log").write(about_renderable())
            return
        except Exception:
            # Transcript write is preferred; fall through to notify on any log error.
            pass
        try:
            self.notify(about_plain(), timeout=8)
        except Exception:
            # Best-effort UI toast; About content is non-critical if notify fails.
            pass

    def action_view_selection(self) -> None:
        """F3 — view the panel's current selection (run its view action → morph the pane to show it).
        Works from the command line too (reads the Dock's focused slot, not keyboard focus)."""
        from xlii.tui.dock_surface import DockSurface

        try:
            surface = self.query_one(DockSurface)
        except Exception:
            try:
                self.notify("open a panel (a doorway / Alt-F) to view", timeout=3)
            except Exception:
                # Best-effort UX hint only: if notifications are unavailable here,
                # silently continue and keep this action a no-op.
                pass
            return
        if surface._run_named_action("view"):
            surface.repaint()

    def action_edit_selection(self) -> None:
        """F4 — edit the panel's current selection (Midnight Commander).
        Seeds ``/edit`` with the right flag; ``/edit`` owns the actual editing."""
        address = self._current_selection_address()
        arg = self._edit_arg_for(address) if address else ""
        self._prefill_input(f"/edit {arg}" if arg else "/edit ")

    def _edit_arg_for(self, address: str) -> str:
        """The ``/edit`` flag for a selection's scheme — ``--doc``/``--id``/``--file`` (a skill edits
        its SKILL.md by path). Empty for a scheme ``/edit`` doesn't know."""
        from pathlib import Path

        from xlii.addressing import Address

        a = Address.parse(address)
        if a.scheme == "docs":
            return f"--doc {a.key}"
        if a.scheme == "persona":
            return f"--id {a.key}"
        if a.scheme == "file":
            return f"--file {a.target}"
        if a.scheme == "skills":
            from xlii.skills import load_skills

            root = getattr(getattr(self._state, "project", None), "project_root", None)
            sk = load_skills(Path(str(root)) if root else None).get(a.key)
            if sk is not None and getattr(sk, "path", None):
                return f"--file {sk.path}"
        return ""

    def _append_to_input(self, text: str) -> None:
        """Append ``text`` to the command line (space-separated), cursor to the end, and focus it."""
        try:
            inp = self.query_one("#input", _PromptInput)
        except Exception:
            return
        cur = inp.text
        inp.text = cur + ((" " if cur and not cur.endswith(" ") else "") + text)
        lines = inp.text.split("\n")
        inp.cursor_location = (len(lines) - 1, len(lines[-1]))
        inp._sync_height()
        inp.focus()

    def _prefill_input(self, text: str) -> None:
        """Replace the command line with ``text`` and put the cursor at the end, focused — a menu
        item that seeds a slash command the user completes (``/tasks run '…``, ``/get …``)."""
        try:
            inp = self.query_one("#input", _PromptInput)
        except Exception:
            return
        inp.text = text
        lines = inp.text.split("\n")
        inp.cursor_location = (len(lines) - 1, len(lines[-1]))
        inp._sync_height()
        inp.focus()

    def action_doorway(self, letter: str) -> None:
        """Alt-<letter>: toggle a content-type doorway in Pane 2. If that doorway is already showing,
        close the panel; otherwise open (or switch to) it and hand keyboard focus to the pane so the
        a/v/d action keys work immediately. Mirrors clicking the chip, plus the toggle + focus."""
        if letter == "f":
            if self._panel_open and (self._current_dock_scheme() in ("file", "project", "conv")):
                self.hide_panel()
            else:
                from xlii.tui.dock_surface import file_dock_root_address

                self._open_in_dock_view(file_dock_root_address(self._state))
                self._focus_dock()
            return
        scheme = self._DOORWAY_SCHEMES.get(letter)
        if scheme is None:
            return
        if self._panel_open and self._current_dock_scheme() == scheme:
            self.hide_panel()
        else:
            self._open_in_dock_view(f"{scheme}://")
            self._focus_dock()

    def _current_dock_scheme(self) -> Optional[str]:
        """The scheme of the Dock surface's working-slot pane (for the doorway toggle), or None when
        no Dock is docked."""
        try:
            from xlii.tui.dock_surface import DockSurface

            surface = self.query_one(DockSurface)
        except Exception:
            return None
        dock = surface.dock
        pane = dock.pane(dock.slot_ids[-1])
        return pane.address.scheme if pane is not None else None

    def _refresh_git_view(self) -> None:
        """Re-list the Git doorway if it's the visible Pane-2 view, so a stage/commit/discard (or any
        turn that touched the working tree) reflects at once. A no-op when Git isn't showing — cheap
        (one ``git status``), scoped, and it keeps the pure-projection GitPane in sync with the repo."""
        try:
            if self._current_dock_scheme() == "git":
                self._open_in_dock_view("git://")
        except Exception:
            # Best-effort UI refresh: ignore transient dock/query errors to avoid interrupting flow.
            return

    def _open_plan_items(self, name: str = "") -> None:
        """Open the working plan's item view in Pane 2 — the plan strip's click
        target (plan-surface T1); bare falls back to the plan list."""
        self._open_in_dock_view(f"plan://{name}" if name else "plan://")
        self._focus_dock()

    def _refresh_plan_surfaces(self) -> None:
        """Repaint the plan strip, and re-list a visible plan:// pane, after a
        plan mutation — the kernel plan listener's target and the end-of-turn
        hook (plan-surface T1). Best-effort like every UI refresh."""
        try:
            from xlii.tui.plan_strip import _PlanStrip

            self.query_one("#plan-strip", _PlanStrip).update_from(self._state)
        except Exception:
            # Best-effort strip refresh; the pane pass below still runs.
            pass
        try:
            from xlii.tui.dock_surface import DockSurface

            surface = self.query_one(DockSurface)
            dock = surface.dock
            pane = dock.pane(dock.slot_ids[-1])
            if pane is not None and pane.address.scheme == "plan":
                # Re-open the SAME address (root list or a named plan's items),
                # so a check/amend reflects at once — the git-view idiom.
                self._open_in_dock_view(str(pane.address))
        except Exception:
            return

    def _refresh_transcript_dock(self) -> None:
        """Re-project a docked TranscriptPane when Conversation streams chunks
        (Phase 6). Cheap: pane refresh uses the mtime parse cache; no-op when
        the transcript view isn't showing."""
        try:
            from xlii.tui.dock_surface import DockSurface
            from xlii.panes.transcript import TranscriptPane

            surface = self.query_one(DockSurface)
            dock = surface.dock
            repainted = False
            for sid in dock.slot_ids:
                pane = dock.pane(sid)
                if isinstance(pane, TranscriptPane):
                    # Refresh EVERY docked transcript — returning after the first
                    # left a second (split) transcript stale on each stream chunk.
                    pane.refresh()
                    repainted = True
            if repainted:
                surface.repaint()
        except Exception:
            return

    def _focus_dock(self) -> None:
        """Hand keyboard focus to the Dock surface after it mounts (call_after_refresh bridges the
        async panel mount), so the a/v/d + arrow keys drive the pane."""
        def _f() -> None:
            try:
                from xlii.tui.dock_surface import DockSurface

                self.query_one(DockSurface).focus()
            except Exception:
                # Best-effort only: focus can fail transiently during async mount/refresh cycles.
                pass

        self.call_after_refresh(_f)

    def _handle_fkey(self, key: str) -> None:
        """Dispatch a function key delegated from the focused input.

        F-keys are the V2b commander verbs unless a bind owns that key.
        """
        try:
            from xlii.binds import fkey_bind, load_binds

            hit = fkey_bind(load_binds(self._xli_dir()), key)
        except Exception:
            hit = None
        if hit is not None:
            self._run_bind(hit.task)
            return
        if key == "f1":
            self.action_help()
        elif key == "f2":
            self.action_home_panel()
        elif key == "f3":
            self.action_view_selection()
        elif key == "f4":
            self.action_edit_selection()
        elif key == "f5":
            self.action_copy_export()
        elif key == "f6":
            self.action_detach_all()
        elif key == "f7":
            self.action_new()
        elif key == "f8":
            self.action_remove()
        elif key == "f9":
            self.action_jobs_panel()
        elif key == "f10":
            self.action_task_builder()

    def _run_quick_launch_action(self, action: str) -> None:
        """Run a face/TUI shared quick-launch action string (pane:/seed:/…)."""
        if not action:
            return
        if action == "cmd:help":
            self.action_help()
            return
        if action.startswith("pane:"):
            scheme = action[5:].strip()
            if scheme:
                self._open_in_dock_view(f"{scheme}://")
            return
        if action.startswith("seed:"):
            self._prefill_input(action[5:])
            return
        if action == "attach:files":
            # Review-before-run: seed attach path; no blind file dialog in TUI.
            self._prefill_input("/ref ")
            return
        if action == "menu:plugins":
            try:
                self._show_panel_view(self._preferred_panel_side(), "plugins")
            except Exception:
                self._prefill_input("/plugin ")
            return

    def _refresh_fkey_bar(self) -> None:
        """Repaint the F-key row — commander labels, overlayed by binds."""
        try:
            from xlii.binds import fkey_hints, load_binds
            from xlii.tui.status_strip import _FKeyBar

            hints = fkey_hints(load_binds(self._xli_dir()))
            self.query_one(_FKeyBar).reset_commander(hints)
        except Exception:
            # Best-effort chrome: a missing bar must not break the session.
            pass

    # --- the V2b F-row actions ------------------------------------------------

    def action_home_panel(self) -> None:
        """F2 — the ``home://`` launcher in Pane 2 (toggle, like a content doorway)."""
        if self._panel_open and self._current_dock_scheme() == "home":
            self.hide_panel()
        else:
            self._open_in_dock_view("home://")
            self._focus_dock()

    def action_jobs_panel(self) -> None:
        """F9 — the live jobs list in Pane 2."""
        self._open_in_dock_view("jobs://")

    def action_attach_selection(self) -> None:
        """F4 — attach the focused panel item so it rides the next turn's context (the
        pane's own ``attach`` action — skills, docs, marks — dispatched through the Dock's
        session sink, the same path as the footer button / the ``a`` pane key)."""
        from xlii.tui.dock_surface import DockSurface

        try:
            surface = self.query_one(DockSurface)
        except Exception:
            try:
                self.notify("open a panel and select an attachable item (skill / doc / mark)",
                            timeout=3)
            except Exception:
                # Best-effort UX hint only.
                pass
            return
        if surface._run_named_action("attach"):
            surface.repaint()
        else:
            try:
                self.notify("the focused pane item isn't attachable", timeout=3)
            except Exception:
                # Best-effort UX hint only.
                pass

    def action_detach_all(self) -> None:
        """F6 — detach EVERY session attachment from the turn (docs, persona refs,
        bookmarks, recalled points) through the same kernel paths ``/detach`` uses —
        one summary toast instead of N command echoes."""
        from xlii.repl_cmds.attach import _detach_named
        from xlii.repl_cmds.knowledge import _get_attachment_owner

        owner = _get_attachment_owner(self._state)
        names = [n for n, _c in (getattr(owner, "attached_docs", []) or [])]
        names += [n for n, _c in (getattr(owner, "attached_refs", []) or [])]
        if not names:
            try:
                self.notify("nothing attached this session", timeout=3)
            except Exception:
                # Best-effort UX hint only.
                pass
            return
        for name in names:
            try:
                _detach_named(self._state, name, self._console, None)
            except Exception:
                # A single bad entry must not strand the rest of the sweep.
                pass
        self._refresh_status()
        try:
            self.notify(f"detached {len(names)} attachment(s) from the turn", timeout=3)
        except Exception:
            # Best-effort UX hint only.
            pass

    def action_copy_export(self) -> None:
        """F5 — copy through the V0b export seam. A live terminal text selection copies
        to the clipboard verbatim; otherwise the focused panel item's location renders
        in the destination's dialect (:func:`to_shell_arg` — relative path, ``path:NN``,
        address, or a materialized snapshot) into the input line, and an item whose
        scheme declares no export copies its native address to the clipboard instead."""
        try:
            selected = self.screen.get_selected_text()
        except Exception:
            selected = None
        if selected:
            self.copy_to_clipboard(selected)
            try:
                self.notify("selection copied to the clipboard", timeout=2)
            except Exception:
                # Best-effort toast; the copy already landed.
                pass
            return
        address = self._current_selection_address()
        if not address:
            return
        from xlii.addressing import to_shell_arg

        cwd = getattr(self._state, "shell_cwd", None) if self._state is not None else None
        arg = to_shell_arg(address, cwd=cwd)
        if arg.ok:
            self._append_to_input(arg.value)
            return
        # No export declared for this scheme — the native address still copies.
        self.copy_to_clipboard(address)
        try:
            self.notify(f"{arg.reason or 'no shell export'} — native address on the clipboard",
                        timeout=3)
        except Exception:
            # Best-effort toast; the copy already landed.
            pass

    def action_copy_address(self) -> None:
        """Panels → Copy address — the focused panel item's NATIVE address
        (``docs://readme``) to the clipboard (F5's explicit native case)."""
        address = self._current_selection_address()
        if not address:
            try:
                self.notify("no panel selection to copy", timeout=3)
            except Exception:
                # Best-effort UX hint only.
                pass
            return
        self.copy_to_clipboard(address)
        try:
            self.notify(f"copied {address}", timeout=2)
        except Exception:
            # Best-effort toast; the copy already landed.
            pass

    def _handle_doorway_key(self, key: str) -> None:
        """Dispatch an Alt-<letter> doorway hotkey delegated from the focused input."""
        self.action_doorway(key.split("+")[-1])

    def _commander_hotkey(self, key: str) -> bool:
        """Act on a commander ``<modifier>+<letter>`` hotkey and report whether it was one.

        The single dispatch point shared by every focus context — the focused input, a focused
        dock pane, and the app-level fallback — so the underlined menu mnemonics and the content
        doorways behave identically no matter what holds focus. A menu accelerator WINS a letter it
        shares with a doorway (Alt+P → Project menu, not the plan pane); the plan/tasks panes stay
        reachable from their menu entries. Returns True when the key opened a menu or a doorway."""
        title = getattr(self, "_menu_accel_keys", {}).get(key)
        if title is not None:
            self._open_menu(title, self._menu_x(title))
            return True
        if key in getattr(self, "_doorway_keys", ()):
            self._handle_doorway_key(key)
            return True
        return False

    def _menu_x(self, title: str) -> int:
        """The dropdown's left column for a keyboard-opened menu — its title's offset in the bar,
        so the dropdown drops under the title exactly as a click would place it."""
        from xlii.tui.menu_bar import title_spans

        return next((start for start, _end, t in title_spans() if t == title), 0)

    # --- the top menu bar (Xlii/Project/Tools/Attach/Commands/Options/Panel Workbench/Help) --------

    def _push_dropdown(self, title: str, items: "list[tuple[str, str, bool]]", x: int,
                       on_item: "Callable[[str], None]") -> None:
        """Open a :class:`MenuDropdown` and own its dismiss protocol in ONE place.

        Every dropdown — top-level menus AND sub-dropdowns (Console categories, F7 New…) —
        dismisses with one of three shapes: a ``("switch", title, x)`` tuple (the user clicked
        another bar title while open → open that menu), a string item id (run ``on_item``), or
        ``None`` (Esc / click-away → just close). Centralizing it here means a per-dropdown
        callback only ever sees a real string id — a callback that assumed that and called
        ``.startswith`` on the switch tuple used to crash the whole TUI."""
        from xlii.tui.menu_bar import MenuDropdown

        def _done(result: Any) -> None:
            if isinstance(result, tuple) and result and result[0] == "switch":
                _, new_title, new_x = result
                self._open_menu(new_title, new_x)
            elif result:
                on_item(result)

        self.push_screen(MenuDropdown(title, items, x=x), _done)

    def _open_menu(self, title: str, x: int = 0) -> None:
        """Open ``title``'s dropdown under the bar; run the chosen item's action on dismiss."""
        items = self._menu_items(title)
        if not items:
            return
        self._push_dropdown(title, items, x, self._run_menu_action)

    def _menu_items(self, title: str) -> "list[tuple[str, str, bool]]":
        """The ``(item_id, label, enabled)`` rows for a menu — dynamic (the Options labels reflect
        live prefs). The contents live here (not in the widget) so a menu action reuses the app's
        existing seams. Seven menus: Xlii / Project (artifacts) / Tools
        (work) / Commands (slash + attach verbs) / Options / Panel Workbench
        (the flat index) / Help (howto + about).
        **Menus DO, they don't type** (Fleet rule 4): every row opens a panel, runs an
        action, or launches a select-flow — a row that needs input claims THE input line
        (:meth:`_claim_line`), never leaves a prefill sitting in the REPL."""
        if title == "Xlii":
            # The mode-switch lane (V3a/V3b) is wired now, so these DO: each row runs
            # its in-session switch command through the shared REPL dispatch — /code
            # runs the code-entry gate (V3a), /chat the safe-chat profile (V3b),
            # /scratch the code-surface overlay. The current mode carries a ✓; scratch
            # is a code-surface overlay, so it's only offered while on the code surface.
            prof = getattr(self._state, "profile", None)
            surface = (getattr(prof, "mode", None) or "code")
            in_scratch = bool(getattr(self._state, "scratch", False))
            tick = lambda cur: ("✓ " if cur else "  ")  # noqa: E731 — a label helper
            return [
                ("xlii:clear", "  Clear transcript", True),
                ("xlii:homehub", "  Home Hub", True),
                ("xlii:mode:chat", f"{tick(surface == 'chat')}Chat mode", True),
                ("xlii:mode:code", f"{tick(surface == 'code' and not in_scratch)}Code mode", True),
                ("xlii:mode:scratch", f"{tick(in_scratch)}Scratch mode", surface == "code"),
            ] + self._bind_menu_rows("xlii") + [
                ("xlii:install", "  Install node…", True),
                ("xlii:jids", "  XMPP addresses…", True),
                ("xlii:exit", "  Exit", True),
            ]
        if title == "Project":
            # Artifacts only — durable project state. The locker is per-turn
            # ride-along context, so it lives in Attach (as Tray), not here.
            rows = [
                ("proj:newfile", "  New project file…", True),
                ("proj:newfolder", "  New project folder…", True),
                ("proj:create", "  Adopt folder…", True),
                ("proj:file", "  File                 (panel)", True),
                ("proj:wiki", "  Wiki                 (panel)", True),
                ("proj:plans", "  Plans                (panel)", True),
                ("proj:sync", "  Sync                 (panel)", True),
            ]
            return rows + self._bind_menu_rows("project")
        if title == "Tools":
            return [
                ("tools:jobs", "  Jobs", True),
                ("tools:runtask", "  Run task…", True),
                ("tools:skills", "  Skills", True),
                ("tools:newterm", "  New terminal", True),
                ("tools:gigwork", "  Gigwork", True),
                ("tools:plugin", "  Plugin…", True),
                ("tools:shelltools", "  Shell tools…", True),
                ("tools:taskbuilder", "  Task builder…     (F10)", True),
                ("tools:remote", "  Remotes…", True),
            ] + self._bind_menu_rows("tools")
        if title == "Commands":
            # Attach verbs live here (they are slash clients). Skills / Rules /
            # Ref / Locker stay in Panel Workbench — those are panes, not cmds.
            items = [
                ("attach:selection", "  Attach selection   (F4)", True),
                ("attach:detach", "  Detach all         (F6)", True),
                ("attach:show", "  Attachments", True),
                ("cmd:sep", "  ────────", False),
                ("cmd:xlii", "  Xlii…            (Ctrl-K)", True),
            ]
            for name in self._CONSOLE_CATEGORIES:  # System… / Network… / Packages… / Searches…
                items.append((f"cmd:cat:{name}", f"  {name}…", True))
            items.append(("cmd:history", "  Input history…", True))
            return items
        if title == "Options":
            # One grammar: toggles CYCLE in place · `…` items open panels · bare
            # actions just fire. The two former multi-row/popup settings are now
            # single cycle-on-click rows whose item id CARRIES the next value, so
            # each rides an existing dispatch arm — no new handlers.
            ring = ("alt", "ctrl+alt", "ctrl+shift+alt")
            cur = "+".join(_parse_modifier(self._resolve_hotkey_modifier()))
            nxt = ring[(ring.index(cur) + 1) % len(ring)] if cur in ring else "alt"

            from xlii.chat_tiers import AUTO, CONCRETE_TIERS
            from xlii.tui.status import chat_tier as _chat_tier
            from xlii.tui.status_strip import _FKeyBar

            tiers = (AUTO, *CONCRETE_TIERS, "off")   # auto→fast→expert→heavy→off→auto
            cur_tier = _chat_tier(self._state) or "off"
            nxt_tier = (tiers[(tiers.index(cur_tier) + 1) % len(tiers)]
                        if cur_tier in tiers else AUTO)
            fkeys_on = True
            try:
                fkeys_on = bool(self.query_one(_FKeyBar).display)
            except Exception:
                cfg = getattr(self._state, "cfg", None) if self._state is not None else None
                fkeys_on = True if cfg is None else bool(getattr(cfg, "face_fkeys", True))
            return [
                (f"opt:hotkey:{nxt}", f"  Doorway key: {cur}-<letter>", True),
                ("opt:config", "  Config…", True),
                ("opt:fkeys", f"  F-keys: {'visible' if fkeys_on else 'hidden'}", True),
                (f"opt:tier:{nxt_tier}", f"  Chat tier: {cur_tier}", True),
                ("opt:theme", "  Theme…", True),
                ("opt:bindmake", "  Bind chrome…", True),
                ("opt:screenshot", "  Save screenshot", True),
            ]
        if title == "Panel Workbench":
            # The flat index + pane ops: every pane by honest name, alphabetical,
            # for people who don't think in families (Project/Tools/Attach are the
            # semantic menus). Complete — including the Remotes doorway the old
            # list omitted.
            return [
                ("doorway:a", "  Artifacts", True),
                ("doorway:f", "  Files", True),
                ("doorway:h", "  Gigwork", True),
                ("doorway:g", "  Git", True),
                ("doorway:i", "  Locker", True),
                ("doorway:p", "  Plan", True),
                ("doorway:m", "  Ref", True),
                ("doorway:r", "  Remotes", True),
                ("doorway:d", "  Rules", True),
                ("doorway:s", "  Skills", True),
                ("doorway:t", "  Tasks", True),
                ("doorway:w", "  Wiki", True),
                ("panel:copyaddr", "  Copy address", True),
                ("panel:close", "  Close panel", True),
            ]
        if title == "Help":
            from xlii.about import howto_menu_rows

            items: list[tuple[str, str, bool]] = [
                ("howto:", "  How to use xlii", True),
            ]
            last_group = ""
            for row in howto_menu_rows():
                tid = str(row.get("id") or "").strip()
                if not tid:
                    continue
                group = str(row.get("group") or "").strip()
                if group and group != last_group:
                    items.append((f"help:g:{group}", f"  ── {group} ──", False))
                    last_group = group
                label = str(row.get("menu") or row.get("title") or tid)
                items.append((f"howto:{tid}", f"  {label}", True))
            items.append(("help:about", "  About", True))
            return items
        return []

    def _xli_dir(self):
        project = getattr(self._state, "project", None) if self._state is not None else None
        return getattr(project, "xli_dir", None) if project is not None else None

    def _bind_menu_rows(self, menu: str) -> list:
        try:
            from xlii.binds import load_binds, menu_binds

            rows = []
            for b in menu_binds(load_binds(self._xli_dir()), menu):
                hint = f"     ({b.fkey})" if b.fkey else ""
                rows.append((f"bind:{b.task}", f"  {b.display()}{hint}", True))
            return rows
        except Exception:
            return []

    def _run_bind(self, task: str) -> None:
        from xlii.binds import bind_run_line

        try:
            line = bind_run_line(task, self._xli_dir())
        except Exception:
            line = f"/tasks run {task} "
        if line.endswith(" "):
            self._prefill_input(line)
        else:
            self._submit_prompt(line.rstrip())

    def _run_menu_action(self, item_id: str) -> None:
        """Dispatch a menu selection to the app's existing actions."""
        if item_id.startswith("bind:"):
            self._run_bind(item_id.split(":", 1)[1])
            return
        if item_id.startswith("doorway:"):
            self.action_doorway(item_id.split(":", 1)[1])
        elif item_id in ("tools:newterm", "xlii:newterm"):
            self._launch_terminal()                 # open an external terminal (cwd from config)
        elif item_id == "xlii:clear":
            self._clear_transcript()
        elif item_id in ("xlii:mode:chat", "xlii:mode:code", "xlii:mode:scratch"):
            # Mode switch: run the in-session command through the shared dispatch
            # (the same path as typing it). h_code runs the V3a code-entry gate,
            # h_chat the V3b safe profile, /scratch toggles the code overlay; each
            # already reports "already in X" and refuses cleanly, and _process
            # re-homes the status bar afterward.
            self._submit_prompt("/" + item_id.rsplit(":", 1)[1])
        elif item_id == "xlii:exit":
            self.action_quit()                      # Decision #2: Exit lives here, not on F10
        elif item_id == "proj:newfile":
            self._new_named("file")
        elif item_id == "proj:newfolder":
            self._claim_line(
                "new project folder — enter a name:",
                on_submit=self._run_new_folder_task,
            )
        elif item_id == "proj:create":
            # Claim the input for a target dir, then initialize + switch into it.
            # Explicit init=True creates the project non-interactively, so the gate
            # never reaches its stdin launch prompt (which the TUI can't answer).
            self._claim_line(
                "adopt folder as project:",
                on_submit=self._create_project,
                initial=str(getattr(self._state, "shell_cwd", "") or ""),
            )
        elif item_id == "proj:file":
            self.action_doorway("f")
        elif item_id == "proj:wiki":
            self.action_doorway("w")
        elif item_id == "proj:plans":
            self.action_doorway("p")
        elif item_id == "proj:git":
            self.action_doorway("g")
        elif item_id == "proj:sync":
            self._submit_prompt("/sync")            # the menu is a client of the command
        elif item_id == "tools:jobs":
            self._open_in_dock_view("jobs://")      # the live jobs list in Pane 2
        elif item_id == "tools:runtask":
            self._open_in_dock_view("tasks://")     # panel select: pick → seeds /tasks run <name>
        elif item_id == "tools:skills":
            self.action_doorway("s")
        elif item_id == "tools:gigwork":
            self._open_in_dock_view("gigmake://")
        elif item_id == "tools:plugin":
            # The panel IS the parallel act (subscription, not invocation) — the
            # old /get pre-prompt was the wrong verb (the-fold B ask #1, post-D).
            self._show_panel_view(self._preferred_panel_side(), "plugins")
        elif item_id == "tools:shelltools":
            self._open_console_tools()              # Track J: the grouped lint/format catalog
        elif item_id == "tools:taskbuilder":
            self.action_task_builder()              # the F10 builder — compose a /tasks pipe
        elif item_id in ("opt:bindmake", "tools:bindmake"):
            self._open_in_dock_view("bindmake://")
        elif item_id == "tools:remote":
            self._open_in_dock_view("remotemake://")
        elif item_id == "xlii:install":
            self._open_in_dock_view("install://")
        elif item_id == "xlii:jids":
            self._open_in_dock_view("jidmake://")
        elif item_id == "cmd:xlii":
            self.action_open_palette()              # Xlii… = the xlii-command select-flow
        elif item_id.startswith("cmd:cat:"):
            self._open_console_category(item_id.split(":", 2)[2])   # System… / Network… / …
        elif item_id == "cmd:history":
            self._show_panel_view(self._preferred_panel_side(), "history")
        elif item_id.startswith("opt:hotkey:"):
            self.set_hotkey_modifier(item_id.split(":", 2)[2])
            try:
                self.notify(f"doorway key → {'+'.join(_parse_modifier(self._resolve_hotkey_modifier()))}-<letter>",
                            timeout=3)
            except Exception as exc:
                # Notification is best-effort UI feedback; do not break menu flow.
                print(f"Failed to show hotkey notification: {exc}", file=_sys.stderr)
        elif item_id == "opt:config":
            self._show_panel_view(self._preferred_panel_side(), "config")
        elif item_id == "opt:fkeys":
            self._toggle_fkeys_visible()
        elif item_id.startswith("opt:tier:"):
            # Cycle-on-click: the row id carries the NEXT tier; submit it through
            # the real /tier command (the menu is a client of the command) so the
            # transcript echoes and the status chip repaints for free.
            self._submit_prompt(f"/tier {item_id.split(':', 2)[2]}")
        elif item_id == "opt:theme":
            self._show_panel_view(self._preferred_panel_side(), "themes")
        elif item_id == "opt:screenshot":
            self._do_screenshot()                       # Options → Save screenshot (SVG to Desktop)
        elif item_id == "panel:copyaddr":
            self.action_copy_address()              # F5's native case: docs://readme → clipboard
        elif item_id == "panel:close":
            self.hide_panel()
        elif item_id == "attach:selection":
            self.action_attach_selection()          # the F4 verb, menu-fronted
        elif item_id == "attach:detach":
            self.action_detach_all()                # the F6 verb, menu-fronted
        elif item_id == "attach:show":
            self._submit_prompt("/attachments")     # the menu is a client of the command
        elif item_id == "howto:" or item_id.startswith("howto:"):
            topic = item_id.split(":", 1)[1].strip()
            self._submit_prompt(f"/howto {topic}".rstrip())
        elif item_id == "help:about":
            self._show_about()
        elif item_id == "xlii:homehub":
            self.action_home_panel()

    def _run_new_folder_task(self, name: str, *, kind: str = "code") -> None:
        """Project → New project folder — run the stock system task ``new-folder``."""
        from xlii.tasks import TaskParseError, new_folder_command

        try:
            line = new_folder_command(name, kind=kind)
        except TaskParseError as e:
            try:
                self.notify(str(e), severity="warning", timeout=4)
            except Exception:
                # Best-effort validation toast; the parse error still blocks the task.
                pass
            return
        self._submit_prompt(line)

    @work(thread=True)
    def _create_project(self, path: str) -> None:
        """Adopt an existing folder as an xlii project and live-switch into it.

        The Project → Adopt client of the code-entry gate (V3a). ``init=True`` is
        the EXPLICIT launch choice, so the gate stamps ``.xlii/`` (a fast
        project.json write — no snapshot, no Collection) without ever reaching its
        interactive stdin prompt, which the full-screen TUI can't answer. Runs on a
        worker thread (file I/O + a live surface swap); the status bar re-homes on
        the main thread after."""
        from xlii.tui import blocks
        from xlii.tui.events import MetaMessage

        state = self._state
        from xlii.desk_files import adopt_remote, is_remote_files_address
        from xlii.project_paths import resolve_adopt_path

        if is_remote_files_address(path):
            try:
                project = adopt_remote(path)
            except Exception as e:  # noqa: BLE001
                self.write_block(blocks.meta_block(
                    MetaMessage(f"adopt failed: {type(e).__name__}: {e}", "error")))
                return
            try:
                from xlii.repl_cmds.switch import switch_to_code_project

                switch_to_code_project({"state": state, "console": state.console}, project)
            except Exception as e:
                self.write_block(blocks.meta_block(
                    MetaMessage(f"created, but switching in failed: {e}", "warn")))
            return

        root, err = resolve_adopt_path(
            path,
            shell_cwd=getattr(state, "shell_cwd", None),
            selected=None,
        )
        if root is None:
            self.write_block(blocks.meta_block(
                MetaMessage(err or "need a folder to adopt", "warn")))
            return
        tier = ("freeball" if getattr(state, "freeball", False)
                else "yolo" if getattr(state, "yolo", False) else "safe")
        try:
            from xlii.session_boot import gate_code_entry

            result = gate_code_entry(root, init=True, interactive=False,
                                     trust_tier=tier, console=state.console)
        except Exception as e:  # creation is best-effort; never crash the app
            self.write_block(blocks.meta_block(
                MetaMessage(f"adopt failed: {type(e).__name__}: {e}", "error")))
            return
        if result.project is None:
            self.write_block(blocks.meta_block(
                MetaMessage(f"could not adopt {root}", "warn")))
            return
        try:
            from xlii.repl_cmds.switch import switch_to_code_project

            switch_to_code_project({"state": state, "console": state.console}, result.project)
        except Exception as e:
            self.write_block(blocks.meta_block(
                MetaMessage(f"created, but switching in failed: {e}", "warn")))
        self._on_main(self._refresh_status)
        self._on_main(self._refresh_git_view)

    def _claim_line(self, prompt: str, on_submit: "Callable[[str], None]", *,
                    initial: str = "", on_cancel: "Optional[Callable[[], None]]" = None) -> bool:
        """Claim THE input line for one ask (the CLAIM_INPUT fulfilment — Fleet rule 3's
        minibuffer). The line relabels to ``prompt``, seeds ``initial``, and Enter delivers
        the text to ``on_submit``; Esc always hands the line back to the REPL. Returns False
        (with a nudge) when the line is already claimed — single-tenant, never queued."""
        from xlii.panes import InputClaim

        try:
            inp = self.query_one("#input", _PromptInput)
        except Exception:
            return False
        granted = inp.claim_input(
            InputClaim(prompt=prompt, on_submit=on_submit, initial=initial, on_cancel=on_cancel)
        )
        if not granted:
            try:
                self.notify("finish or Esc the current ask first", timeout=3)
            except Exception:
                # Best-effort UX hint only; a refused ask needs no toast to stay refused.
                pass
        return granted

    def _run_shell_shortcut(self, cmd: str) -> None:
        """A menu shell pick that DOES (Fleet rule 4 — the owner is explicit: menus are for
        people who don't want to type). A placeholder-free command runs at once; a command
        carrying a ``<placeholder>`` needs input, so THE input line transforms (claimed,
        seeded, editable — Enter runs the edited line, Esc cancels). Never a blind prefill
        left sitting in the REPL."""
        line = f"!{cmd}"
        if "<" not in cmd:
            self._submit_prompt(line)
            return
        self._claim_line(
            "fill the <…> — Enter runs · Esc cancels",
            self._submit_prompt,
            initial=line,
        )

    def _toggle_fkeys_visible(self) -> None:
        """Options → F-keys: show/hide the bottom F-key hint bar (sticky pref)."""
        from xlii.tui.status_strip import _FKeyBar

        try:
            bar = self.query_one(_FKeyBar)
        except Exception:
            return
        bar.display = not bar.display
        cfg = getattr(self._state, "cfg", None) if self._state is not None else None
        if cfg is not None:
            try:
                cfg.face_fkeys = bool(bar.display)
                save = getattr(cfg, "save", None)
                if callable(save):
                    save()
            except Exception:
                # The bar's visibility already toggled; only persisting that choice is lost.
                pass
        try:
            self.notify(f"F-keys bar {'shown' if bar.display else 'hidden'}", timeout=2)
        except Exception:
            # Best-effort toast; the toggle itself already landed.
            pass

    def _xtool_dock_path(self) -> "Optional[Path]":
        from xlii.tui.tools_catalog import dock_path_from_address

        return dock_path_from_address(self._current_selection_address())

    def _open_console_tools(self, x: int = 0) -> None:
        """Tools → Shell tools…: grouped lint/format catalog. Every pick seeds ``!<argv>``."""
        from xlii.tui.tools_catalog import fingerprints_for_state, group_menu_items, prefill_line

        items = group_menu_items(fingerprints_for_state(self._state))

        def _picked(item_id: str) -> None:
            if item_id.startswith("xtgrp:"):
                self._open_console_tool_group(item_id.split(":", 1)[1], x)
            elif item_id.startswith("xtool:"):
                line = prefill_line(item_id.split(":", 1)[1], dock_path=self._xtool_dock_path())
                if line:
                    self._prefill_input(line)

        self._push_dropdown("Shell tools", items, x, _picked)

    def _open_console_tool_group(self, group: str, x: int = 0) -> None:
        from xlii.xtool_catalog import GROUP_LABELS
        from xlii.tui.tools_catalog import group_has_legacy, prefill_line, tool_menu_items

        items = list(tool_menu_items(group, legacy=False))
        if group_has_legacy(group):
            items.append((f"xtmore:{group}", "  More…", True))

        def _picked(item_id: str) -> None:
            if item_id.startswith("xtmore:"):
                self._open_console_tool_more(item_id.split(":", 1)[1], x)
            elif item_id.startswith("xtool:"):
                line = prefill_line(item_id.split(":", 1)[1], dock_path=self._xtool_dock_path())
                if line:
                    self._prefill_input(line)

        self._push_dropdown(GROUP_LABELS.get(group, group), items, x, _picked)

    def _open_console_tool_more(self, group: str, x: int = 0) -> None:
        from xlii.xtool_catalog import GROUP_LABELS
        from xlii.tui.tools_catalog import prefill_line, tool_menu_items

        def _picked(item_id: str) -> None:
            if item_id.startswith("xtool:"):
                line = prefill_line(item_id.split(":", 1)[1], dock_path=self._xtool_dock_path())
                if line:
                    self._prefill_input(line)

        self._push_dropdown(f"{GROUP_LABELS.get(group, group)} — More", tool_menu_items(group, legacy=True), x, _picked)

    def _open_console_category(self, name: str, x: int = 0) -> None:
        """Commands → <category>…: a sub-dropdown of that category's shell shortcuts. Picks DO
        (run when concrete, claim the line when a ``<placeholder>`` needs filling — see
        ``_run_shell_shortcut``); they never prefill the REPL (Fleet rule 4)."""
        cmds = self._CONSOLE_CATEGORIES.get(name, ())
        items = [(f"csh:{i}", f"  ! {c}", True) for i, c in enumerate(cmds)]

        def _picked(item_id: str) -> None:
            if item_id.startswith("csh:"):
                i = int(item_id.split(":", 1)[1])
                if 0 <= i < len(cmds):
                    self._run_shell_shortcut(cmds[i])

        self._push_dropdown(name, items, x, _picked)

    def _launch_terminal(self, run: str = "") -> None:
        """Tools → New terminal: open an external emulator at the configured dest."""
        from xlii.desk import resolve_terminal_cwd
        from xlii.interactive import launch_in_external_terminal

        cfg = getattr(self._state, "cfg", None)
        cwd = resolve_terminal_cwd(self._state, cfg)
        pref = str(getattr(cfg, "tui_terminal", "") or "")
        ok, msg = launch_in_external_terminal(cwd, run=run, preferred=pref)
        self.notify(msg, severity="warning" if not ok else "information",
                    timeout=6 if not ok else 3)

    # --- F7 New… — create a doc / skill / persona / folder -------------------

    def action_new(self, x: int = 0) -> None:
        """F7 / Project→New… — pick a kind, name it, create it, and open it in Pane 2."""
        kinds = [("new:doc", "  Rule", True), ("new:skill", "  Skill", True),
                 ("new:persona", "  Persona", True), ("new:wiki", "  Wiki page", True),
                 ("new:file", "  File", True), ("new:folder", "  Folder", True)]
        self._push_dropdown("New", kinds, x,
                            lambda item_id: self._new_named(item_id.split(":", 1)[1]))

    def _new_named(self, kind: str) -> None:
        """Name the artifact being created through the CLAIMED input line (V2c — the
        minibuffer rule; was NameInputModal). The line relabels, and Enter creates it."""
        self._claim_line(
            f"new {kind} — enter a name:",
            lambda name: self._create_artifact(kind, name),
        )

    def _create_artifact(self, kind: str, name: str) -> None:
        """Create the named artifact (validated; templated) and open it in Pane 2. A bad name /
        collision surfaces as a toast, never a crash. Templates only for now — xlii-assisted authoring
        is a later pass."""
        from pathlib import Path

        try:
            if kind == "doc":
                from xlii.doc import create_doc

                create_doc(name)
                address = f"docs://{name}"
            elif kind == "skill":
                from xlii.skills import create_skill

                root = getattr(getattr(self._state, "project", None), "project_root", None)
                create_skill(name, project_root=Path(str(root)) if root else None)
                address = f"skills://{name}"
            elif kind == "persona":
                from xlii.persona import create_persona

                create_persona(name)
                address = f"persona://{name}"
            elif kind == "wiki":
                from xlii import wiki as W
                from xlii.active_session import xli_dir_of

                xli_dir = xli_dir_of(self._state)
                if xli_dir is None:
                    raise ValueError("the wiki lives in a project's .xlii — open a project first")
                if not W.is_valid_name(name):
                    raise ValueError(f"invalid wiki page name: {name!r} (letters/digits/._- only)")
                if W.page_exists(xli_dir, name):
                    raise FileExistsError(f"wiki page {name!r} already exists")
                W.write_page(xli_dir, name, W.DEFAULT_WIKI_TEMPLATE)  # born unverified — /wiki verify to promote
                address = f"wiki://{name}"
            elif kind == "file":
                new_file = self._new_folder_parent() / name
                if new_file.exists():
                    raise FileExistsError(f"file exists already: {new_file}")
                new_file.parent.mkdir(parents=True, exist_ok=True)
                new_file.touch()
                address = f"file://{new_file}"
            elif kind == "folder":
                new_dir = self._new_folder_parent() / name
                new_dir.mkdir(parents=True, exist_ok=False)
                address = f"file://{new_dir}"
            else:
                return
        except (ValueError, FileExistsError, OSError) as e:
            try:
                self.notify(str(e), severity="error", timeout=5)
            except Exception:
                # Best-effort UI notification only; ignore notify failures to avoid masking
                # the original artifact-creation error handling path.
                pass
            return
        try:
            self.notify(f"created {kind} · {name}", timeout=3)
        except Exception:
            # Best-effort UI notification only; creation succeeded, so do not fail on toast errors.
            pass
        self._open_in_dock_view(address)
        self._refresh_status()

    def _new_folder_parent(self):
        """Where a new folder lands: the dir the file panel is showing, else the dock's file
        root (live shell cwd → project root → process cwd — ``file_dock_root_address``; the
        default dock root is the ``home://`` hub, which is not a filesystem place)."""
        from pathlib import Path

        addr = self._current_dock_address()
        if addr and addr.startswith("file://"):
            p = Path(addr[len("file://"):])
            if p.is_dir():
                return p
        from xlii.tui.dock_surface import file_dock_root_address

        root = file_dock_root_address(self._state)
        return Path(root[len("file://"):]) if root.startswith("file://") else Path.cwd()

    def _current_dock_address(self) -> Optional[str]:
        """The full address of the Dock's working-slot pane, or None when no Dock is docked."""
        try:
            from xlii.tui.dock_surface import DockSurface

            surface = self.query_one(DockSurface)
        except Exception:
            return None
        dock = surface.dock
        pane = dock.pane(dock.slot_ids[-1])
        return str(pane.address) if pane is not None else None

    # --- F8 Remove… — delete the selected doc / skill / persona (danger-gated) -

    _REMOVABLE_SCHEMES = ("docs", "skills", "persona")

    def action_remove(self) -> None:
        """F8 rem — delete the item highlighted in the panel (a doc/skill/persona), behind a
        typed-phrase confirm in the claimed input line (V2c; the destroy-all house style). A
        file/mark/etc. selection (or nothing) is a no-op with a nudge, so F8 can never
        delete something the user didn't clearly pick."""
        from xlii.addressing import Address

        address = self._current_selection_address()
        scheme = Address.parse(address).scheme if address else ""
        if not address or scheme not in self._REMOVABLE_SCHEMES:
            try:
                self.notify("select a doc / skill / persona in the panel to remove", timeout=3)
            except Exception:
                # Best-effort UX hint only; never fail the remove action path if notify is unavailable.
                pass
            return
        name = Address.parse(address).key

        def _confirmed(text: str) -> None:
            if text == name:
                self._do_remove(address)
            else:
                try:
                    self.notify(f"remove aborted — typed {text!r}, expected {name!r}",
                                severity="warning", timeout=4)
                except Exception:
                    # Best-effort toast; the abort (the safe path) already happened.
                    pass

        # Typed-phrase confirm in the claimed input — the destroy-all house style
        # (type the thing's identity, never 'y'), no popup.
        self._claim_line(
            f"delete {scheme} '{name}'? Type the name to confirm:",
            _confirmed,
        )

    def _do_remove(self, address: str) -> None:
        from pathlib import Path

        from xlii.addressing import Address

        a = Address.parse(address)
        name = a.key
        try:
            if a.scheme == "docs":
                from xlii.doc import delete_doc

                ok = delete_doc(name)
            elif a.scheme == "skills":
                from xlii.skills import delete_skill

                root = getattr(getattr(self._state, "project", None), "project_root", None)
                ok = delete_skill(name, project_root=Path(str(root)) if root else None)
            elif a.scheme == "persona":
                from xlii.persona import delete_persona

                ok = bool(delete_persona(name)[0])
            else:
                return
        except Exception as e:  # never let a delete crash the app
            try:
                self.notify(f"remove failed: {e}", severity="error", timeout=5)
            except Exception:
                # Best-effort error reporting: if notification fails, suppress to avoid cascading failure.
                pass
            return
        try:
            self.notify(f"removed {a.scheme} · {name}" if ok else f"{name} not found", timeout=3)
        except Exception:
            # Notification is best-effort only; never fail remove flow on UI toast errors.
            _ = None
        self._open_in_dock_view(f"{a.scheme}://")   # re-list the store; the deleted item is gone
        self._refresh_status()

    # --- F10 Task builder — compose a /tasks pipe (the automation on-ramp) -----

    def action_task_builder(self) -> None:
        """F10 / Tools→Task builder… — the assisted composer for a ``/tasks`` pipe, as a
        Dock PANE (V2c — the minibuffer rule, no popup). Steps are rows; Enter claims the
        input line to edit; every exit rides the Dock's outcome seams (PREFILL for
        run/run-bg/draft — review-before-run; CLAIM_INPUT for add/edit/save-name)."""
        from xlii.tui.dock_surface import DockSurface
        from xlii.tui.task_builder import TaskBuilderPane

        def _place() -> bool:
            try:
                surface = self.query_one(DockSurface)
            except Exception:
                return False
            pane = TaskBuilderPane(
                saved=self._saved_pipeline_names(),
                on_change=surface.repaint,
                on_error=self._builder_flash,
                on_save=lambda draft, name: self._write_pipeline(draft, name),
                on_prefill=self._prefill_input,
            )
            dock = surface.dock
            dock.place(dock.slot_ids[-1], pane, focus=True)
            surface.repaint()
            self._focus_dock()
            return True

        if not _place():
            # No dock yet — mount the chassis first, then place on the next tick.
            self._show_panel_view(self._preferred_panel_side(), "vfs")
            self.call_after_refresh(_place)

    def _builder_flash(self, message: str) -> None:
        """The builder pane's error sink (a bad step, a rejected save name)."""
        try:
            self.notify(message, severity="warning", timeout=4)
        except Exception:
            # Best-effort toast; a compose error is never fatal.
            pass

    def _project_xli_dir(self) -> Optional[Path]:
        d = getattr(getattr(self._state, "project", None), "xli_dir", None)
        return Path(str(d)) if d else None

    def _saved_pipeline_names(self) -> "list[str]":
        try:
            from xlii import tasks as T

            d = self._project_xli_dir()
            return T.list_pipelines(d) if d is not None else []
        except Exception:
            return []

    def _write_pipeline(self, draft: Any, name: str) -> None:
        """Persist a builder draft as ``.xlii/tasks/<name>.toml`` and seed ``/tasks run <name>``
        so the saved recipe is one Enter from its first run."""
        from xlii import tasks as T

        d = self._project_xli_dir()
        if d is None:
            try:
                self.notify("no project — a saved task needs an .xlii dir", severity="error",
                            timeout=5)
            except Exception:
                # Notification is best-effort; failure here must not block command flow.
                pass
            return
        if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
            try:
                self.notify("task names are letters/digits/._- only", severity="error", timeout=5)
            except Exception as exc:
                _sys.stderr.write(f"notify failed while reporting invalid task name: {exc}\n")
            return
        path = T.pipeline_path(d, name)
        if path.exists():
            try:
                self.notify(f"{name} already exists — /tasks edit {name} to change it",
                            severity="error", timeout=5)
            except Exception:
                # Notification failures are non-fatal; keep control flow unchanged.
                pass
            return
        T.write_pipeline_toml(d, name, draft.toml(name))
        try:
            self.notify(f"saved task · {name}", timeout=3)
        except Exception:
            # Best-effort UI feedback only; saving succeeded, so ignore notify failures.
            pass
        self._prefill_input(f"/tasks run {name}")

