"""``DockSurface`` — the Textual front-end as a **projection of a `Dock`**.

Kernel Vector #2, the surface step. The headless half is done: a :class:`~xlii.panes.dock.Dock`
owns slots of panes, each pane a pure projection of ``(address, selection)`` that renders to a
:class:`~xlii.panes.Rendered` tree and offers bounded :class:`~xlii.panes.Action`\\ s. This module
is the thin adapter that *draws* that tree into Textual widgets and routes real keystrokes back
into the Dock — and **nothing more**. It holds no view-state of its own: every repaint re-reads
``pane.render()``, so the screen can't drift from the kernel. That is the whole point — the
"TUI reimplements the REPL / no single owner" desync class dies when the surface is a projection.

Two seams, deliberately split so most of it is testable without a terminal:

* :data:`KEY_MAP` + :func:`slot_renderable` — pure functions (Textual not required). The key
  table maps a real Textual key name to a logical ``handle`` token; the renderable builder turns
  a ``Rendered`` into a Rich renderable. Both unit-testable with no app.
* :class:`DockSurface` (Textual-guarded, like ``tui/panels.py``) — the widget. It maps keys
  through :data:`KEY_MAP` into the focused pane's :meth:`~xlii.panes.Pane.handle`; when ``handle``
  declines (e.g. Enter on a leaf), it runs the pane's **primary action** through
  :meth:`~xlii.panes.dock.Dock.dispatch` ("open in the other pane"); then it repaints from the
  Dock. ``Tab`` cycles slot focus (a surface concern, never a pane's).

Note it never imports ``xlii.tui.console`` or prints to a module global — it only returns
renderables into widgets (the handoff's gotcha #2). The transcript pane, where turns actually
print and ``ENQUEUE_TURN`` gets executed, is the next step; this surface is browse/view only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rich.console import Group, RenderableType
from rich.text import Text

from xlii.panes import Rendered

__all__ = [
    "KEY_MAP",
    "slot_renderable",
    "dock_root_address",
    "file_dock_root_address",
    "DockSurface",
    "dock_view",
    "register_dock_view",
    "transcript_view",
    "register_transcript_view",
]

# Real Textual key name → the logical token a pane's handle() understands. Tab is
# NOT here (surface cycles slot focus). Left/Backspace mean "go to parent"; Right
# / PageUp / PageDown turn a PDF page; Ctrl+H toggles hidden files. A key
# absent from the map falls through so global bindings (quit, etc.) still fire.
KEY_MAP = {
    "up": "up",
    "down": "down",
    "home": "home",
    "end": "end",
    "enter": "enter",
    "left": "back",
    "backspace": "back",
    "right": "right",
    "pagedown": "pagedown",
    "pageup": "pageup",
    "ctrl+h": "hidden",
}


# A RenderedRow's semantic ``tone`` → a Rich style. The pane names the *kind* of change (git status);
# the surface owns the colour, so a themer can retune it in one place (VS Code's SCM palette).
_TONE_STYLES = {
    "added": "green",
    "modified": "yellow",
    "removed": "red",
    "renamed": "cyan",
    "untracked": "green",
    # plan:// rows: a queued implementer amendment outranks progress colour;
    # an item-less plan reads dim.
    "amended": "magenta",
    "empty": "dim",
}


def slot_renderable(rendered: "Rendered | None", *, empty_note: str = "(empty slot)") -> RenderableType:
    """Turn a slot's :class:`~xlii.panes.Rendered` into a Rich renderable. Pure — no Textual.

    The selected row is marked with a ``›`` cursor and reversed; containers render bold so the
    browseable rows read at a glance. ``None`` (an unmounted slot) and an empty listing both
    degrade to a dim note rather than a blank."""
    if rendered is None:
        return Text(empty_note, style="dim italic")
    head = Text(rendered.title or "(unmounted)", style="bold cyan")
    media = getattr(rendered, "media", None)
    if media is not None and media.kind == "image":
        # Pure path: the caption only — the actual pixels are a live-surface concern (terminal
        # graphics). A non-graphics surface still reads "this is an image", never garbled bytes.
        return Group(head, Text(""), Text(media.caption or "[image]", style="dim"))
    if rendered.empty:
        return Group(head, Text(""), Text("(empty)", style="dim italic"))
    lines: list[RenderableType] = [head, Text("")]
    for row in rendered.rows:
        selected = row.selected
        cursor = "› " if selected else "  "
        if selected:
            body_style = "reverse"
        elif row.kind == "caption":
            body_style = "dim"
        elif row.kind == "container":
            body_style = "bold"
        else:
            body_style = _TONE_STYLES.get(getattr(row, "tone", ""), "")
        line = Text()
        line.append(cursor, style="reverse" if selected else "")
        # A green ● marks an "accented" row — e.g. the skill currently riding the turn. Only accented
        # rows get the glyph (no reserved column) so unaccented listings render byte-identically.
        if getattr(row, "accent", False):
            line.append("● ", style="green")
        line.append(row.text, style=body_style)
        lines.append(line)
    return Group(*lines)


def file_dock_root_address(state: Any) -> str:
    """Files doorway address — ``files_root`` when set, else live cwd / project root."""
    from xlii.desk_files import files_address

    project = getattr(state, "project", None) if state is not None else None
    cwd = getattr(state, "shell_cwd", None) if state is not None else None
    return files_address(project, shell_cwd=cwd)


def dock_root_address(state: Any) -> str:
    """Default Dock chassis (Track I): ``home://`` panel hub, not a silent file tree.

    One-shot override: if ``state._dock_open_address`` is set, consume it and open
    there (cold open of a named scheme without an intermediate vfs/home paint).
    """
    if state is not None:
        oneshot = getattr(state, "_dock_open_address", None)
        if oneshot:
            try:
                delattr(state, "_dock_open_address")
            except Exception:
                try:
                    state._dock_open_address = None
                except Exception:
                    # Neither delete nor assign is allowed, so there is no one-shot override to consume.
                    pass
            return str(oneshot)
    return "home://"


# --- the turn sink: a pane's ENQUEUE_TURN -> the app's ONE turn pipeline ------
# This is what makes the pane actions (summarize a file, re-ask a turn) actually run. It feeds
# the app's `_submit_prompt` — the same entry the user's keystrokes use — so the turn is echoed,
# run, and persisted by the ONE pipeline; it NEVER re-runs run_turn/write_turn (no double-write).

_TURN_CONTEXT_CAP = 16_000  # bytes of context inlined into the prompt


def _with_context(prompt: str, context: str) -> str:
    """Fold the pane's context address into the prompt: read it through the VFS and inline a
    bounded, decoded excerpt (an image is referenced, not decoded to mojibake; a read error
    degrades to a bare reference). Pure — no Textual."""
    if not context:
        return prompt
    try:
        from xlii.addressing import classify, resolve, vfs_read, vfs_stat

        kind = classify(vfs_stat(context))
        if kind == "image":
            return f"{prompt}\n\n(the file in question: {context})"
        if kind == "pdf":
            from xlii.pdf_text import extract_pdf_text

            r = resolve(context)
            text = extract_pdf_text(r.path) if r.path else None
            if not text:
                return f"{prompt}\n\n(the PDF in question: {context})"
            if len(text) > _TURN_CONTEXT_CAP:
                text = text[:_TURN_CONTEXT_CAP] + "\n… (truncated)"
            return f"{prompt}\n\n--- {context} ---\n{text}\n--- end ---"
        text = vfs_read(context).decode("utf-8", errors="replace")
        if len(text) > _TURN_CONTEXT_CAP:
            text = text[:_TURN_CONTEXT_CAP] + "\n… (truncated)"
        return f"{prompt}\n\n--- {context} ---\n{text}\n--- end ---"
    except Exception:
        return f"{prompt}\n\n(context: {context})"


class AppTurnSink:
    """A Dock turn sink that submits a pane's ENQUEUE_TURN through the app's one turn entry.

    Deliberately does NOT touch the live Conversation: the turn lifecycle is
    owned by the kernel spine (:func:`xlii.conversation.drive_turn`, which
    ``_agent_turn`` delegates to since Phase 3). Starting here as well raced
    the busy guard — a pane submit during a streaming turn replaced the live
    in-flight with a truncated prompt, and the running turn then completed the
    WRONG pair.
    """

    def __init__(self, app: Any) -> None:
        self._app = app

    def submit(self, prompt: str, *, context: str = "") -> None:
        try:
            self._app._submit_prompt(_with_context(prompt, context))
        except Exception:
            # A torn-down or headless app has nothing to submit the prompt into.
            pass


class AppSessionSink:
    """A Dock session sink that runs a pane's ATTACH/DETACH against the app's live ``REPLState``.

    Dispatches by scheme through :mod:`xlii.attach` (skill/doc → the /doc channel), then repaints
    the status strip so the ``[skills N]`` rider count and the pane's green dot update at once. Pure
    best-effort — a bad address or an absent session can never crash the surface."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def attach(self, address: str) -> None:
        from xlii.attach import attach_address, attachable, focus_address

        try:
            state = getattr(self._app, "_state", None)
            # Attachable riders (skills/docs/wiki/...) go straight to the attach
            # channel with the raw address string; only file-ish schemes take the
            # Focus (once) path. Falling back to attach_address when focus_address
            # declined covers non-focusable file addresses.
            if attachable(address) or focus_address(state, address) is None:
                attach_address(state, address)
            self._refresh()
        except Exception:
            # A bad address must not break the Dock; the attach is simply not staged.
            pass

    def detach(self, address: str) -> None:
        from xlii.attach import detach_address

        try:
            detach_address(getattr(self._app, "_state", None), address)
            self._refresh()
        except Exception:
            # Either the address was already detached, or this state can't hold attachments.
            pass

    def _refresh(self) -> None:
        if hasattr(self._app, "_refresh_status"):
            try:
                self._app._refresh_status()
            except Exception:
                # The status bar repaints on the next refresh.
                pass


class AppMediaSink:
    """A Dock media sink that renders a pane's SHOW_MEDIA image into the REPL transcript (Pane 1).

    The locker lists images in Pane 2, but the picture appears in the REPL like normal — via the same
    renderable-sink path ``/image`` uses (``maybe_preview`` → the installed ``app.write_block`` sink).
    Best-effort: a bad path or a torn-down app can never crash the surface."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def show(self, address: str) -> None:
        try:
            from xlii.addressing import Address

            addr = Address.parse(address)
            if addr.scheme != "file" or not addr.target:
                return
            path = str(Path(addr.target).expanduser())
            from xlii.terminal_image import maybe_preview

            maybe_preview(path, enabled=True, backend="auto",
                          console=getattr(self._app, "_console", None), force=True)
        except Exception:
            # Per the class contract: a bad address or a torn-down app must not
            # crash the surface — the image simply doesn't render.
            pass


class AppInputSink:
    """A Dock input sink that seeds a pane's PREFILL command into the app's command line.

    A saved task → ``/tasks run <name>``, a bookmark → ``/ref <mark>``: the pane hands the command and
    the surface drops it into the input un-executed (``_prefill_input`` — cursor at the end, focused) so
    the user reviews before running. Best-effort — a torn-down app can never crash the surface.

    Also the TUI's :class:`~xlii.panes.ClaimSink`: a pane's ``CLAIM_INPUT`` ask morphs the app's
    input line (the minibuffer rule) via :meth:`claim`."""

    def __init__(self, app: Any) -> None:
        self._app = app
        # The pane-side bridges (TasksPane compose chain, Gitpanel stash asks)
        # have no sink handle of their own — they route through whichever sink
        # the TUI mounted last. Class-level rebind is idempotent across Dock
        # remounts. Local imports: dock_surface is imported by both modules.
        from xlii.panes.git import GitpainPrefillBridge
        from xlii.tui.task_builder import ClaimPrefillBridge

        ClaimPrefillBridge.bind(claim=self.claim, prefill=self.prefill)
        GitpainPrefillBridge.bind_prefill(self.prefill)

    def prefill(self, text: str) -> None:
        try:
            inp = self._app.query_one("#input")
            if getattr(inp, "claim_active", False):
                # The line is single-tenant: seeding a command mid-claim first
                # releases the ask exactly as Esc would (the asker learns via
                # on_cancel) — a prefill must never silently BECOME the answer.
                inp.release_claim(cancelled=True)
            self._app._prefill_input(text)
        except Exception:
            # No input line is mounted (headless, or teardown) -- there is nothing to seed.
            pass

    def claim(self, claim: Any) -> bool:
        """Answer a pane's one-line ask the TUI's way: morph the input line
        (:meth:`_PromptInput.claim_input` — stash draft, seed initial, retitle the frame).
        Returns ``False`` — ask refused, no callbacks invoked — when the line is already
        claimed or there is no input to morph; the Dock reports refusals via ``on_cancel``."""
        try:
            return bool(self._app.query_one("#input").claim_input(claim))
        except Exception:
            return False


class AppJobSink:
    """A Dock job sink that runs a pane's SPAWN_JOB against the session JobRegistry.

    First emitter: ``tasks://<name>`` → load the saved pipeline and dispatch it as a
    ``KIND_TASK`` background job (same path as ``/tasks run --background``). Panes stay
    data-only; the Dock stays headless; turns still ride ``_submit_prompt``. Best-effort."""

    def __init__(self, app: Any) -> None:
        self._app = app

    def spawn(self, address: str, *, text: str = "") -> None:
        try:
            from xlii.addressing import Address
            from xlii.repl_cmds.tasks import spawn_saved_task

            addr = Address.parse(address)
            if addr.scheme != "tasks":
                return
            name = (text or addr.key or addr.target or "").strip()
            if not name:
                return
            state = getattr(self._app, "_state", None)
            job_id = spawn_saved_task(state, name)
            if job_id and hasattr(self._app, "notify"):
                try:
                    self._app.notify(
                        f"▶ background job {job_id} · {name}",
                        severity="information",
                        timeout=4,
                    )
                except Exception:
                    # The job is already spawned; only its toast is lost.
                    pass
            if hasattr(self._app, "_refresh_status"):
                try:
                    self._app._refresh_status()
                except Exception:
                    # The job badge appears on the next status refresh.
                    pass
        except Exception:
            # Per the class contract: an unknown task or a torn-down app must
            # not crash the surface — the job simply isn't spawned.
            pass


# --- the widget (Textual-only, guarded exactly like tui/panels.py) -----------

try:
    from textual import events
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import Button, Static

    _TEXTUAL = True
except Exception:  # pragma: no cover - exercised only without [tui]
    _TEXTUAL = False


if _TEXTUAL:

    # Single-letter pane hotkeys → the action of that name, when the DockSurface holds focus (the
    # mc/ranger idiom: focus the panel, single keys act). a/v/d mirror the attach/view/detach buttons;
    # s/u/c/x/r drive the GitPane's file actions and y/b/h its repo actions (sync/branch/stash) — the
    # footer shows a button per verb. A key whose action a pane doesn't offer is a harmless no-op
    # (_run_named_action returns False → the key falls through), so this stays backward-compatible for
    # every other pane.
    _PANE_ACTION_KEYS = {"a": "attach", "v": "view", "d": "detach",
                         "s": "stage", "u": "unstage", "c": "commit", "g": "generate", "x": "discard",
                         "r": "review", "y": "sync", "b": "branch", "h": "stash"}

    # Rows above the first listing line in slot_renderable: the bold title + one blank. A body click
    # at content-y N therefore targets pane row (N - _ROW_OFFSET).
    _ROW_OFFSET = 2

    class _SlotBody(Static):
        """The pane's row area. A click selects the row under the cursor (mouse parity with the arrow
        keys) and focuses the slot, so the same click that picks an item also arms the a/v/d keys."""

        def __init__(self, slot_id: str) -> None:
            super().__init__("")
            self._slot_id = slot_id

        def on_click(self, event) -> None:  # type: ignore[override]
            row = getattr(event, "y", 0) - _ROW_OFFSET
            for anc in self.ancestors:
                if isinstance(anc, DockSurface):
                    anc.select_row(self._slot_id, row)
                    break

    class _PaneButton(Button):
        """A compact, non-focusable Button that remembers which slot + verb it drives (so button
        ids never collide across slots, and click routing needs no id parsing)."""

        can_focus = False

        def __init__(self, label: str, slot_id: str, act: str, cls: str) -> None:
            super().__init__(label, classes=cls)
            self.slot_id = slot_id
            self.act = act

    class _SlotView(Vertical):
        """One slot: a ‹back / ✕close header, a scrollable body showing the pane's projection, and a
        footer whose action buttons are built data-driven from the pane's offered actions (a skill →
        attach/view/detach, git → view/stage/commit/…). Flat, border-less on a $panel background to
        match the docked side-panels (Themes etc.); still carries a ``-focused`` class for callers.
        Back/close are on EVERY pane."""

        DEFAULT_CSS = """
        _SlotView {
            width: 1fr;
            height: 1fr;
            background: $panel;
            padding: 0 1;
        }
        _SlotView .slot-header {
            height: 1;
            width: 1fr;
        }
        _SlotView .slot-spacer {
            width: 1fr;
        }
        _SlotView .slot-scroll {
            height: 1fr;
            width: 1fr;
        }
        _SlotView .slot-actions {
            height: auto;
            width: 1fr;
        }
        _SlotView .slot-actions-row {
            height: 1;
            width: 1fr;
            align-horizontal: left;
        }
        _SlotView .pane-btn {
            height: 1;
            min-width: 3;
            width: auto;
            border: none;
            padding: 0 1;
            margin: 0 1 0 0;
            background: $panel-darken-2;
            color: $text;
        }
        _SlotView .pane-btn:hover {
            background: $accent;
        }
        _SlotView .pane-btn:disabled {
            color: $text-disabled;
        }
        """

        # Buttons per action-bar row before wrapping to the next — sized so a rowful fits a
        # half-width panel; a pane with more actions (GitPane) wraps to a second row.
        _ACTIONS_PER_ROW = 4

        def __init__(self, slot_id: str) -> None:
            super().__init__(id=f"slot-{slot_id}")
            self.slot_id = slot_id
            self._body = _SlotBody(slot_id)
            self.last_render: RenderableType | None = None  # the projection last shown (testability)
            self._act_buttons: dict = {}
            self._act_names: "tuple[str, ...]" = ()  # the button set now mounted (rebuild only on change)

        def compose(self):  # type: ignore[override]
            with Horizontal(classes="slot-header"):
                yield _PaneButton("‹", self.slot_id, "back", "pane-btn nav-btn")
                yield Static("", classes="slot-spacer")
                yield _PaneButton("✕", self.slot_id, "close", "pane-btn nav-btn")
            with VerticalScroll(classes="slot-scroll"):
                yield self._body
            # The action bar is filled data-driven from the pane's offered actions (see
            # _sync_action_buttons), so each pane shows exactly its own verbs as real buttons.
            yield Vertical(classes="slot-actions", id=f"acts-{self.slot_id}")

        def update_view(self, rendered: "Rendered | None", *, focused: bool,
                        action_names: "tuple[str, ...]" = ()) -> None:
            self.last_render = slot_renderable(rendered)
            body: RenderableType = self.last_render
            media = getattr(rendered, "media", None) if rendered is not None else None
            if media is not None and media.kind == "image" and media.path:
                # Live upgrade: draw the actual image as chafa symbols — a Rich renderable that
                # fits a Static and works in any terminal. Best-effort; falls back to the caption.
                try:
                    from xlii.terminal_image import image_block

                    img = image_block(media.path, backend="blocks")
                    if img is not None:
                        body = Group(Text(rendered.title, style="bold cyan"), Text(""), img)
                except Exception:
                    # chafa/PIL unavailable, or the file isn't decodable -- body keeps the caption set above.
                    pass
            self._body.update(body)
            self._sync_action_buttons(action_names)
            self.set_class(focused, "-focused")

        def _sync_action_buttons(self, action_names: "tuple[str, ...]") -> None:
            """Rebuild the footer so it holds exactly the actions the pane offers — one real button
            per verb (attach/view/detach for a skill, view/stage/commit/… for git), wrapping to a
            second row past :data:`_ACTIONS_PER_ROW`. Rebuilds only when the *set* of names changes
            (stable across nav within a pane), so there's no per-repaint churn. The button's ``act``
            is the action name; a click dispatches that action's outcome (see on_button_pressed)."""
            names = tuple(action_names)
            if names == self._act_names:
                return
            self._act_names = names
            try:
                container = self.query_one(f"#acts-{self.slot_id}", Vertical)
            except Exception:
                return
            for child in list(container.children):
                child.remove()
            self._act_buttons = {}
            for i in range(0, len(names), self._ACTIONS_PER_ROW):
                row = []
                for name in names[i:i + self._ACTIONS_PER_ROW]:
                    btn = _PaneButton(name, self.slot_id, name, "pane-btn act-btn")
                    self._act_buttons[name] = btn
                    row.append(btn)
                container.mount(Horizontal(*row, classes="slot-actions-row"))

    class DockSurface(Horizontal):
        """A row of slot views projecting a :class:`~xlii.panes.dock.Dock`. Focusable so it owns
        navigation keys; every change repaints from the Dock (no cached view-state)."""

        can_focus = True

        DEFAULT_CSS = """
        DockSurface {
            width: 1fr;
            height: 1fr;
        }
        """

        def __init__(self, dock, **kwargs) -> None:
            super().__init__(**kwargs)
            self._dock = dock
            self._views: dict[str, _SlotView] = {}

        @property
        def dock(self):
            return self._dock

        def compose(self):  # type: ignore[override]
            for sid in self._dock.slot_ids:
                view = _SlotView(sid)
                self._views[sid] = view
                yield view

        def on_mount(self) -> None:
            self.focus()
            # Wire the pane actions into the app's turn pipeline so summarize / re-ask run a turn,
            # and its session so attach/detach buttons ride/unride a skill (best-effort — both are
            # no-ops standalone, where a pane just browses).
            app = getattr(self, "app", None)
            if app is not None and hasattr(app, "_submit_prompt"):
                # Always use AppTurnSink for delivery (ensures streaming, render, hooks,
                # receipt all run — the ONE pipeline). Conversation lifecycle is owned by
                # the kernel spine (drive_turn), which _submit_prompt's agent path enters.
                self._dock.set_turn_sink(AppTurnSink(app))
            if app is not None and getattr(app, "_state", None) is not None:
                self._dock.set_session_sink(AppSessionSink(app))
            if app is not None and hasattr(app, "write_block"):
                self._dock.set_media_sink(AppMediaSink(app))
            if app is not None and hasattr(app, "_prefill_input"):
                self._dock.set_input_sink(AppInputSink(app))
            if app is not None and getattr(app, "_state", None) is not None:
                self._dock.set_job_sink(AppJobSink(app))
            self.repaint()

        def repaint(self) -> None:
            """Re-project every slot from the Dock — the only way the screen updates."""
            for sid, view in self._views.items():
                pane = self._dock.slots.get(sid)
                if pane is not None and hasattr(pane, "set_block"):
                    other = self._dock.slots.get(self._dock._other_slot(sid))
                    block = ""
                    if other is not None and other is not pane:
                        from xlii.home_catalog import pane_id_for_address

                        block = pane_id_for_address(
                            str(getattr(other, "address", "") or "")
                        )
                    pane.set_block(block)
                rendered = pane.render() if pane is not None else None
                names = tuple(a.name for a in pane.actions()) if pane is not None else ()
                view.update_view(rendered, focused=(sid == self._dock.focused), action_names=names)

        def on_key(self, event: "events.Key") -> None:
            # The commander hotkeys (<modifier>+<letter> menu accelerators + content doorways) work
            # while a pane holds focus too — the app owns the key sets, so delegate rather than
            # duplicate them here.
            app = getattr(self, "app", None)
            if app is not None and getattr(app, "_commander_hotkey", None) \
                    and app._commander_hotkey(event.key):
                event.stop()
                return
            if self._route(event.key):
                event.stop()
                self.repaint()

        def on_button_pressed(self, event: "Button.Pressed") -> None:
            """A pane button was clicked: ‹back steps the slot to its parent, ✕close undocks the
            panel, and an action button (attach/view/detach) dispatches that action's outcome
            through the Dock — attach/detach via the session sink, view via a layout retarget."""
            btn = event.button
            slot_id = getattr(btn, "slot_id", None)
            act = getattr(btn, "act", None)
            if slot_id is None or act is None:
                return
            event.stop()
            if act == "close":
                self._close_panel()
                return
            if act == "back":
                self._step_back(slot_id)
                self.repaint()
                return
            pane = self._dock.slots.get(slot_id)
            if pane is None:
                return
            if act == "clear":
                clearer = getattr(pane, "clear_typed", None)
                if callable(clearer):
                    clearer()
                    app = getattr(self, "app", None)
                    if app is not None and hasattr(app, "_load_history"):
                        try:
                            app._load_history()
                        except Exception:
                            # The history list repaints on the next panel mount.
                            pass
                    self.repaint()
                    return
            action = next((a for a in pane.actions() if a.name == act), None)
            if action is not None:
                self._dispatch_outcome(action.outcome, from_slot=slot_id)
                self.repaint()

        def _open_history_panel(self) -> bool:
            """Track F — ``history://`` from home opens the input-history panel, not a VFS pane."""
            app = getattr(self, "app", None)
            if app is None:
                return False
            side = app._preferred_panel_side() if hasattr(app, "_preferred_panel_side") else "right"
            show = getattr(app, "_show_panel_view", None)
            if not callable(show):
                return False
            try:
                return bool(show(side, "history"))
            except Exception:
                return False

        def _dispatch_outcome(self, outcome, *, from_slot: str) -> None:
            if getattr(outcome, "address", None) == "history://":
                if self._open_history_panel():
                    return
            addr = str(getattr(outcome, "address", "") or "")
            if getattr(outcome, "kind", "") == "prefill" and addr.startswith(
                ("canvas://", "artifacts://")
            ):
                from xlii.attach import focus_address

                try:
                    focus_address(getattr(self.app, "_state", None), addr, once=False)
                except Exception:
                    # Focus is a convenience; the outcome above already dispatched.
                    pass
            try:
                self._dock.dispatch(outcome, from_slot=from_slot)
            except Exception as exc:
                print(f"warning: dock dispatch failed: {exc}")

        def _run_named_action(self, name: str) -> bool:
            """Dispatch the focused pane's action named ``name`` (attach/view/detach), if it offers
            one. Returns True when it fired so the key is consumed + the slot repaints."""
            pane = self._dock.pane()
            if pane is None:
                return False
            if name == "clear":
                clearer = getattr(pane, "clear_typed", None)
                if callable(clearer):
                    clearer()
                    app = getattr(self, "app", None)
                    if app is not None and hasattr(app, "_load_history"):
                        try:
                            app._load_history()
                        except Exception:
                            # Named-action path: the history list repaints on the next panel mount.
                            pass
                    return True
            action = next((a for a in pane.actions() if a.name == name), None)
            if action is None:
                return False
            try:
                self._dispatch_outcome(action.outcome, from_slot=self._dock.focused)
            except Exception:
                # A failing action must not take the Dock down; the slot stays as it was.
                pass
            return True

        def select_row(self, slot_id: str, row: int) -> None:
            """A click in a slot's body: focus the slot (keyboard follows the mouse, so a/v/d act on
            what you clicked) and select the clicked row when the pane supports it.

            Caption rows are not selectable. A ``key:`` caption (hidden-files
            toggle, sort cycle) is local nav — same as the face.
            """
            self.focus()
            self._dock.focus(slot_id)
            pane = self._dock.slots.get(slot_id)
            picker = getattr(pane, "select_index", None) if pane is not None else None
            if pane is None or row < 0:
                self.repaint()
                return
            try:
                rows = pane.render().rows
            except Exception:
                rows = ()
            if row < len(rows):
                clicked = rows[row]
                if getattr(clicked, "kind", "") == "caption":
                    addr = getattr(clicked, "address", "") or ""
                    if addr.startswith("key:"):
                        pane.handle(addr[4:])
                    self.repaint()
                    return
                if picker is not None:
                    picker(sum(1 for r in rows[:row] if getattr(r, "kind", "") != "caption"))
                    self.repaint()
                    return
            if picker is not None:
                picker(row)
            self.repaint()

        def _step_back(self, slot_id: str) -> bool:
            """Back out of ``slot_id``'s pane: let the pane handle it, else step to the parent
            directory, else return to ``home://`` (Track I2 — scheme roots bottom out at the hub).
            ``home://`` itself no-ops via HomePane.handle("back")."""
            pane = self._dock.slots.get(slot_id)
            if pane is None:
                return False
            if pane.handle("back"):
                return True
            from xlii.panes.explorer import ExplorerPane

            parent = ExplorerPane._parent(pane.address)
            if parent is not None:
                self._dock.open_address(str(parent), slot=slot_id, focus=True)
                return True
            # Scheme root with no parent → panel home (not a dead ‹).
            if pane.address.scheme != "home":
                self._dock.open_address("home://", slot=slot_id, focus=True)
                return True
            return False

        def _close_panel(self) -> None:
            """✕ — undock the whole panel (app), or hide this surface standalone."""
            app = getattr(self, "app", None)
            if app is not None and hasattr(app, "hide_panel"):
                try:
                    app.hide_panel()
                    return
                except Exception:
                    # No app-level panel host -- fall through to hiding this widget directly.
                    pass
            try:
                self.display = False
            except Exception:
                # Already unmounted; there is nothing left to hide.
                pass

        def _route(self, key: str) -> bool:
            """Apply a key. Tab cycles focus; mapped keys go to the focused pane's handle(); an
            Enter that handle() declines runs the pane's primary action through the Dock."""
            if key == "tab":
                self._cycle_focus()
                return True
            if key == "escape":
                # hand the keyboard back to the REPL input when docked in the app; a no-op
                # standalone (no #input) so it falls through to any global binding.
                try:
                    self.app.query_one("#input").focus()
                    return True
                except Exception:
                    return False
            if key in _PANE_ACTION_KEYS:
                # a/v/d act on the focused pane's selection (attach/view/detach) — the keyboard
                # peer of the footer buttons, so you never have to reach for the mouse mid-browse.
                return self._run_named_action(_PANE_ACTION_KEYS[key])
            token = KEY_MAP.get(key)
            if token is None:
                return False  # let global bindings have it
            pane = self._dock.pane()
            if pane is None:
                return False
            if pane.handle(token):
                return True
            if token == "back":
                # A pane that can't go back itself (a morphed file view/image) steps the slot
                # back to its parent directory — the breadcrumb out of a file to the tree, which
                # makes the single working pane a true explorer↔file morph.
                from xlii.panes.explorer import ExplorerPane

                parent = ExplorerPane._parent(pane.address)
                if parent is not None:
                    self._dock.open_address(str(parent), slot=self._dock.focused, focus=True)
                    return True
                return False
            if token == "enter":
                actions = pane.actions()
                if actions:
                    # Run the pane's primary action: a layout outcome (open a folder/file) or a
                    # turn outcome (summarize / re-ask) through the wired turn-sink. Guarded so an
                    # unwired sink (standalone) or a bad address can never crash the app.
                    self._dispatch_outcome(actions[0].outcome, from_slot=self._dock.focused)
                return True
            return False

        def _cycle_focus(self) -> None:
            ids = self._dock.slot_ids
            i = ids.index(self._dock.focused)
            self._dock.focus(ids[(i + 1) % len(ids)])

    def dock_view(state: Any, *, actions: Any = None) -> "DockSurface":
        """A panel-view provider (the ``panels.register_panel_view`` shape): build a **single
        working-slot** `Dock` rooted at the session cwd and project it. One slot = the converged
        "2 panels" layout (transcript + one working pane that *morphs*, never a third split slot):
        opening a file re-mounts this same slot (``RETARGET_SLOT`` degenerates to it), and
        Backspace steps it back to the tree. ``actions`` is accepted for the provider contract
        and unused — a DockSurface acts through its own Dock, not PanelActions."""
        from xlii.panes.dock import Dock

        dock = Dock(slots=("A",))
        dock.open_address(dock_root_address(state), slot=dock.slot_ids[0], focus=True)
        return DockSurface(dock)

    def register_dock_view(name: str = "vfs") -> None:
        """Register the Dock surface under the panel-view registry so it's reachable through the
        published seam (additive, last-wins). The launch hook / a command can call this; routing
        a non-tree view through ``AppPanelHost`` is the remaining wiring step (see the handoff)."""
        from xlii.tui import panels

        panels.register_panel_view(name, lambda st, *, actions=None: dock_view(st, actions=actions))

    def transcript_view(state: Any, *, actions: Any = None) -> "DockSurface":
        """A panel-view provider that projects the live conversation (``conv://.``) as a
        TranscriptPane. Built via `Dock.place` rather than `open_address` so an empty/absent
        ``.xlii/turns`` doesn't trip the stat-based pane pick — an empty conversation just shows
        no rows. Read-only for now: re-ask needs a live `TurnSink`, the watched wiring step."""
        from xlii.conversation import ensure_conversation
        from xlii.panes.dock import Dock
        from xlii.panes.transcript import TranscriptPane

        # Kernel convergence: the pane must observe the SAME Conversation the turn
        # pipeline drives (ensure_conversation reuses state.conversation) — a fresh
        # instance here would never see the in-flight the app starts/completes.
        live = ensure_conversation(state)

        dock = Dock(slots=("A",))  # single working slot — no empty second pane
        pane = TranscriptPane("conv://.", live_conv=live)
        dock.place(dock.slot_ids[0], pane, focus=True)
        return DockSurface(dock)

    def register_transcript_view(name: str = "transcript") -> None:
        """Register the conversation surface under the panel-view registry (additive, last-wins)
        so ``/file-tab transcript`` docks it."""
        from xlii.tui import panels

        panels.register_panel_view(name, lambda st, *, actions=None: transcript_view(st, actions=actions))

    def open_in_dock(app: Any, address: str, *, focus: bool = True) -> bool:
        """Route ``address`` into a live Dock surface's *working* slot (the last slot; slot A
        holds the root explorer/transcript) and repaint. The seam ``/image`` uses to render an
        image into the open pane instead of the transcript — and the one a clicked chip (step 4)
        will reuse. Returns ``False`` when no Dock surface is mounted, so the caller falls back to
        an inline/transcript preview. App-keyboard focus is left on the REPL input (we only set the
        Dock's own focused slot, for the accent border)."""
        try:
            surface = app.query_one(DockSurface)
        except Exception:
            return False
        dock = surface.dock
        try:
            dock.open_address(address, slot=dock.slot_ids[-1], focus=focus)
        except Exception:
            return False
        surface.repaint()
        return True

else:  # pragma: no cover - no [tui]: the widget/provider need textual

    def dock_view(state: Any, *, actions: Any = None):  # type: ignore[misc]
        return None

    def register_dock_view(name: str = "vfs") -> None:  # type: ignore[misc]
        return None

    def open_in_dock(app: Any, address: str, *, focus: bool = True) -> bool:  # type: ignore[misc]
        return False

    def transcript_view(state: Any, *, actions: Any = None):  # type: ignore[misc]
        return None

    def register_transcript_view(name: str = "transcript") -> None:  # type: ignore[misc]
        return None
