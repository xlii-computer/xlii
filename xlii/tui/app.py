"""The Textual app shell — :class:`XliiApp`, the transcript + input host.

``xlii code --tui`` runs this App: a transcript that renders the SAME block
renderables as the inline REPL over a bottom input, proving the render kernel is
view-agnostic. REPLState / Agent.run_turn stay the brain; this is an alternate
*view*, not a fork of the logic.

**Grades Phase 5 split:** large method groups live in mixins composed here —

* :mod:`xlii.tui.app_menu_mixin` — commander menus, doorways, F-keys, New/Remove
* :mod:`xlii.tui.app_panel_mixin` — dock panels / tree / gallery
* :mod:`xlii.tui.app_input_mixin` — palette, history, completion popups
* :mod:`xlii.tui.app_turn_mixin` — slash / shell / agent turn orchestration
* :mod:`xlii.tui.app_css` — shell layout CSS (theme-derived chrome)

This module keeps CSS/BINDINGS, lifecycle (``compose`` / ``on_mount`` /
``on_unmount``), and transcript I/O. **Note:** ``on_unmount`` now does only the
instant, must-run-on-any-teardown work (release confirms, save state, hand back
the shell cwd); the slow steps (journal summary, shell-habit compile) moved to
``xlii.exit_sequence.run_graceful_exit``, run on the restored terminal with a
line per step so exit never looks frozen. Do not add blocking work back here.

Public entry points ``launch`` / ``run_tui_over_session`` stay in
``tui_textual`` and construct this App.

textual is imported at the top, so this module must only be imported once textual
is known present — the launch path guards that.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static

from xlii.tui import shortcodes
from xlii.tui.input_surface import (
    _ChipRow,
    _InputActionButton,
    _ModePrefix,
    _PromptInput,
)
from xlii.tui.app_css import _tui_app_css
from xlii.session_meter import context_window as _context_window
from xlii.session_meter import ktok as _ktok
from xlii.tui.status_strip import _FKeyBar
from xlii.hints import BUILTIN_HINTS as _BUILTIN_HINTS
from xlii.tui.selection import SelectableTranscriptLog
from xlii.tui.transcript import TranscriptLog
from xlii.tui.transcript_console import _TranscriptConsole


# Slash tokens the front-end owns directly (never routed to the registry).
# /terminal·/inline (A1) are the named inverse of /tui — they drop back to the
# inline REPL — and are handled here rather than via the code-REPL registry so
# they exit the app on the main thread, symmetric to /exit·/quit.
_TUI_SLASH = {"/exit", "/quit", "/clear", "/cls", "/clear-screen", "/terminal", "/inline"}
# A bare command token being typed: leading slash + name chars, nothing after
# (no space/args yet). While this matches, the quick-reference popup is live.
_SLASH_TOKEN = re.compile(r"^/([a-z0-9-]*)$")
# `/recall <partial>` — the cross-persona mark picker (RP6). The popup offers
# `<persona>:<mark>` candidates so recalling an idea is ergonomic in /plan.
_RECALL_ARG = re.compile(r"^/recall\s+(\S*)$")
# `@partial` at the tail of the input — file/url pinning (Phase 6). Require a
# word boundary so `foo@bar` (email) does not open the file suggester.
_AT_FRAG = re.compile(r"(?:^|\s)@([^\s@]*)$")


def _recall_suggestions(state, partial: str) -> list[tuple[str, str]]:
    """`(candidate, timestamp)` mark addresses matching `partial`, across every
    persona (addressable as `<persona>:<mark>`) plus the active local store.
    Capped so the popup stays a quick reference, not a wall."""
    try:
        from xlii.repl_cmds.chat import _all_mark_stores
        from xlii.transcript import list_marks
    except Exception:
        return []
    needle = partial.lower()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    try:
        stores = _all_mark_stores(state)
    except Exception:
        return []
    for label, turns_dir, addressable in stores:
        for name, ts in list_marks(turns_dir):
            cand = f"{label}:{name}" if addressable else name
            if cand in seen or needle not in cand.lower():
                continue
            seen.add(cand)
            out.append((cand, ts))
    return out[:20]

# Heartbeat spinner frames (Braille), cycled while a turn runs.
_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


@dataclass(frozen=True)
class _FrontEndCmd:
    """A front-end-owned command (/exit, /quit, /clear) surfaced in the popup so
    it's discoverable next to the registry commands. Quacks like REPLCommand
    enough for the popup (name / description / aliases / matches)."""

    name: str
    description: str
    aliases: tuple = ()

    def matches(self, token: str) -> bool:
        return token == self.name or token in self.aliases


_TUI_SLASH_CMDS = [
    _FrontEndCmd("exit", "Leave the TUI"),
    _FrontEndCmd("quit", "Leave the TUI"),
    _FrontEndCmd("clear", "Clear the transcript", aliases=("cls", "clear-screen")),
]


class ConfirmModal(ModalScreen[str]):
    """Gate for a risky bash command (the intent-gate), shown over the
    transcript. Resolves 'y' on `y`; 'n' on `n` / `esc` / `enter` — deny is the
    safe default so a stray keypress never approves a modifies-system command.
    If the prompt advertises a copy option (contains "[c]"), `c` resolves to 'c'
    — copy the command to run in a real terminal instead of running it here.

    This is what makes the per-intent confirmation work under Textual at all: the
    inline gate's blocking input() can't read the keyboard while Textual owns the
    terminal (the worker thread would deadlock — see tool_handlers._check_intent_
    and_gate). The worker routes through this modal instead via
    XliiApp._confirm_via_modal."""

    DEFAULT_CSS = """
    ConfirmModal {
        align: center middle;
    }
    ConfirmModal > #confirm-dialog {
        width: 70%;
        max-width: 100;
        height: auto;
        padding: 1 2;
        border: thick $warning;
        background: $surface;
    }
    ConfirmModal #confirm-body {
        width: 100%;
        height: auto;
    }
    ConfirmModal #confirm-keys {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $text-muted;
        text-style: bold;
    }
    """

    BINDINGS = [
        Binding("y", "confirm", "approve", show=True),
        Binding("n", "deny", "deny", show=True),
        Binding("c", "copy", "copy", show=False),
        Binding("escape", "deny", "deny", show=False),
        Binding("enter", "deny", "deny", show=False),
    ]

    def __init__(self, prompt: str) -> None:
        super().__init__()
        # The prompt is plain text (e.g. "approve? [y/N]"); wrap in Text() so the
        # literal `[y/N]` isn't mistaken for Rich markup.
        self._prompt = prompt.strip()
        # The /sh gate offers "[c] copy for your terminal"; only then is `c` live.
        self._allow_copy = "[c]" in self._prompt

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(Text(self._prompt), id="confirm-body")
            # A terse prompt (e.g. "approve? [y/N]") gets a key legend. A verbose,
            # self-describing prompt that already lists "[y] run · [c] copy …" does
            # not — otherwise the keys would show twice.
            if "[y]" not in self._prompt:
                yield Static(
                    Text.from_markup("[b]y[/b] approve     [b]n[/b] deny (default)"),
                    id="confirm-keys",
                )

    def action_confirm(self) -> None:
        self.dismiss("y")

    def action_deny(self) -> None:
        self.dismiss("n")

    def action_copy(self) -> None:
        # `c` is only meaningful when the prompt advertised it; otherwise ignore
        # the keypress so it can't act as an accidental deny.
        if self._allow_copy:
            self.dismiss("c")


class PromptModal(ModalScreen[Optional[str]]):
    """One text question from a command handler — ConfirmModal's free-text twin.

    Same reason for existing: a handler's blocking ``input()``/``getpass()``
    can't read the keyboard while Textual owns the terminal (the worker thread
    deadlocks with the busy spinner running — the /remote guided-add hang).
    Handlers route through :mod:`xlii.console_prompt`, which lands here via the
    ``request_input`` capability on the transcript console
    (:meth:`XliiApp._prompt_via_modal`). ``secret=True`` masks the field
    (passwords/passphrases never echo). Enter submits (empty is a real answer);
    Esc cancels → ``None`` and the caller aborts its flow."""

    DEFAULT_CSS = """
    PromptModal {
        align: center middle;
    }
    PromptModal > #prompt-dialog {
        width: 70%;
        max-width: 100;
        height: auto;
        padding: 1 2;
        border: thick $accent;
        background: $surface;
    }
    PromptModal #prompt-body {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }
    PromptModal #prompt-keys {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "cancel", show=False),
    ]

    def __init__(self, prompt: str, *, secret: bool = False) -> None:
        super().__init__()
        self._prompt = prompt.strip()
        self._secret = secret

    def compose(self) -> ComposeResult:
        from textual.widgets import Input

        with Vertical(id="prompt-dialog"):
            yield Static(Text(self._prompt), id="prompt-body")
            yield Input(password=self._secret, id="prompt-input")
            yield Static(
                Text.from_markup("[b]enter[/b] answer (empty ok)     [b]esc[/b] cancel"),
                id="prompt-keys",
            )

    def on_mount(self) -> None:
        self.query_one("#prompt-input").focus()

    def on_input_submitted(self, event) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


# --- doorway hotkeys: a configurable modifier (portability) -------------------
# Alt-<letter> is the default, but some terminals grab Alt for their own menus, so the modifier is a
# config setting (tui_hotkey_modifier: "alt" | "ctrl+alt" | "ctrl+shift+alt" | …). The letter is the
# content type's first letter (its accelerator); the modifier just prefixes it.
_DOORWAY_LETTERS = "sdmiwfg"  # skills · rules(docs) · ref(marks) · tray(locker) · wiki · files · git
# NB: 'p' and 't' are NOT doorway letters — they are menu accelerators (Project / Tools),
# which win the shared letter (menus-win); likewise 'n' (Panel
# Workbench) and 'h' (Help) are menu accelerators. The plan/tasks panes stay
# reachable via their menu entries and the palette.
_MODIFIER_ALIASES = {"control": "ctrl", "option": "alt", "meta": "alt", "cmd": "super",
                     "command": "super", "win": "super", "windows": "super"}
_MODIFIER_TOKENS = ("ctrl", "alt", "shift", "super")


def _parse_modifier(raw: str) -> "list[str]":
    """Normalize a modifier string ('alt', 'ctrl+alt', 'shift-ctrl-alt', …) to canonical tokens.
    Unknown/empty → ['alt'] (never leaves the doorways unreachable)."""
    toks: list[str] = []
    for t in (raw or "").lower().replace("-", "+").split("+"):
        t = _MODIFIER_ALIASES.get(t.strip(), t.strip())
        if t in _MODIFIER_TOKENS and t not in toks:
            toks.append(t)
    return toks or ["alt"]


def _doorway_key_set(raw: str) -> "set[str]":
    """Every Textual key string that should trigger a doorway hotkey for ``raw``. Includes ALL
    orderings of the modifier tokens (Textual's canonical order varies by version) followed by each
    doorway letter, so matching is order-independent. The letter is always last → callers recover it
    with ``key.split('+')[-1]``."""
    import itertools

    mods = _parse_modifier(raw)
    return {
        "+".join([*perm, letter])
        for letter in _DOORWAY_LETTERS
        for perm in itertools.permutations(mods)
    }


def _menu_accel_key_map(raw: str) -> "dict[str, str]":
    """Every ``<modifier>+<accel-letter>`` key string → the menu title it opens, over ALL modifier
    orderings (mirroring :func:`_doorway_key_set`). Alt+X → Xlii, Alt+P → Project, Alt+T → Tools,
    Alt+C → Commands, Alt+O → Options, Alt+N → Panel Workbench,
    Alt+H → Help. These are the underlined
    menu-bar mnemonics, finally wired to the keyboard; they take precedence over a doorway that
    shares the letter."""
    import itertools

    from xlii.tui.menu_bar import accelerator_letters

    mods = _parse_modifier(raw)
    out: dict[str, str] = {}
    for title, letter in accelerator_letters().items():
        for perm in itertools.permutations(mods):
            out["+".join([*perm, letter])] = title
    return out


from xlii.tui.app_menu_mixin import AppMenuMixin
from xlii.tui.app_panel_mixin import AppPanelMixin
from xlii.tui.app_input_mixin import AppInputMixin
from xlii.tui.app_turn_mixin import AppTurnMixin

class XliiApp(AppMenuMixin, AppPanelMixin, AppInputMixin, AppTurnMixin, App):
    """The transcript + input shell. Construction takes the pieces it needs
    (agent, run_turn, state) so tests can inject fakes."""

    # Layout: the commander _MenuBar is the top row (Console…Panel). Below it a
    # pinned question bar (does not scroll), then the transcript row (1fr) — a
    # Horizontal hosting #log
    # flanked by two dockable #panel slots (Vector P · J1) — then the heartbeat +
    # completion popup (both hidden by default) and the Input. There is NO Footer
    # and NO bottom dock — the Input is the last flow item, so nothing fights for
    # the bottom edge (a second bottom-docked widget clips its border off-screen).
    # #log-row carries a bottom margin so the output never butts the input box.
    CSS = _tui_app_css()

    BINDINGS = [
        Binding("ctrl+d", "quit", "Quit", show=True),
        Binding("ctrl+k", "open_palette", "Palette", show=True),
        Binding("ctrl+t", "open_tool_drawer", "Tools", show=True),
        Binding("ctrl+b", "focus_tabs", "Tabs", show=True),
        Binding("ctrl+r", "copy_mode", "Copy mode", show=False),  # transcript block-range copy-mode (Vector E R2)
        # The commander F-row (campaign V2b). F10 is tasks, NOT quit — the
        # deliberate break with the F10-quit canon (Decision #2); Exit lives in
        # the Xlii menu (+ /quit, ctrl+d).
        Binding("f1", "help", "Help", show=False),  # help — /howto
        Binding("f2", "home_panel", "Panel", show=False),  # the home:// launcher in Pane 2
        Binding("f3", "view_selection", "View", show=False),  # view the pane selection
        Binding("f4", "edit_selection", "Edit", show=False),  # MC: edit the selection
        Binding("f5", "copy_export", "Copy", show=False),  # selection / pane item → the export seam
        Binding("f6", "detach_all", "Detach", show=False),  # detach ALL attachments from the turn
        Binding("f7", "new", "Add", show=False),  # add… → create a doc/skill/persona/file/folder
        Binding("f8", "remove", "Rem", show=False),  # rem… → delete the selected doc/skill/persona
        Binding("f9", "jobs_panel", "Jobs", show=False),  # the live jobs list in Pane 2
        Binding("f10", "task_builder", "Tasks", show=False),  # the task builder — compose a /tasks pipe
        # NB: the <modifier>-<letter> doorway hotkeys are NOT static bindings — the modifier is a
        # config setting (tui_hotkey_modifier), so the keys are built per-session into
        # `self._doorway_keys` and delegated from whatever widget holds focus (input / DockSurface).
    ]

    # Doorway letter → the content scheme it opens in Pane 2. 'f' (files) is special: the vfs file
    # explorer rooted at cwd, whose panes are file://-scheme. 'r' (remote) opens the remote://
    # union picker — EVERY configured connection whatever its wire (ftp/ftps/sftp/…), each node
    # addressed by its honest scheme; the per-wire pickers (ftp:// sftp:// dav:// smb://) filter
    # to their own protocol, so this doorway must use the union or hosts silently vanish.
    _DOORWAY_SCHEMES = {"a": "artifacts", "s": "skills", "d": "docs", "m": "mark", "i": "locker", "w": "wiki", "t": "tasks", "g": "git", "p": "plan", "r": "remote", "h": "gigwork"}

    # Commands-menu shell shortcuts — live property so Packages follows the host
    # package manager (apt/dnf/pacman/…). Source: xlii.console_catalog.
    @property
    def _CONSOLE_CATEGORIES(self) -> "dict[str, tuple[str, ...]]":
        from xlii.console_catalog import console_categories

        return console_categories()

    def __init__(self, *, project_name: str, agent: Any, run_turn: Callable, state: Any):
        super().__init__()
        self._project_name = project_name
        self._agent = agent
        self._run_turn = run_turn
        self._state = state
        # Kernel convergence (Phase 1+): ONE live Conversation shared by the app, the panes,
        # and the future single turn owner. ensure_conversation reuses the instance the session
        # launcher (code.py/chat.py) already attached to state — never a second, divergent copy.
        from xlii.conversation import ensure_conversation

        self._conversation = ensure_conversation(state)
        # Doorway hotkeys, built from the configured modifier (default alt) — delegated from whatever
        # widget holds focus (input / DockSurface), so a terminal that eats Alt can switch to ctrl+alt.
        self._doorway_keys = _doorway_key_set(self._resolve_hotkey_modifier())
        # The underlined menu-bar mnemonics (Alt+X/P/T/C/O/A), wired to the keyboard alongside the
        # doorways and rebuilt together whenever the modifier changes (set_hotkey_modifier).
        self._menu_accel_keys = _menu_accel_key_map(self._resolve_hotkey_modifier())
        self._console = _TranscriptConsole(self)
        self._busy = False
        self._agent_job_id: Optional[str] = None   # bg-default P1: live turn job
        # Worker threads blocked on a confirm modal (the bash intent-gate). Held
        # so on_unmount can release them — teardown must never leave a thread
        # waiting on a screen that's gone (it would deadlock the process exit).
        self._pending_confirms: set[threading.Event] = set()
        # The live modal screens those waiters are blocked ON. Held so the stop
        # button can dismiss them as deny/cancel (cancel_pending_confirms) — a
        # turn blocked on a question has no tool boundary for request_cancel to
        # reach, so without this stop is a no-op against an open confirm.
        self._pending_modals: set[Any] = set()
        self._meter_text = ""  # last context-meter string; rendered into the profile bar
        # Heartbeat state (the spinner above the input while a turn runs).
        self._turn_started = 0.0
        self._spin_i = 0
        # Quick-reference popup state (driven from the main thread only).
        self._popup_open = False
        self._popup_matches: list[Any] = []
        self._popup_index = 0
        self._popup_kind = "command"  # command | recall | palette | at
        self._suppress_popup = False  # set while history nav drives the input
        self._palette_active = False
        self._palette_all: list[Any] = []
        # Command history (up/down), oldest→newest. Shares the inline REPL's
        # .xlii/repl_history file so the two views see one history. _load_history
        # fills these on mount; navigation walks _hist with a cursor at _hist_pos.
        self._history: Any = None
        self._hist: list[str] = []
        self._hist_pos = 0
        self._hist_draft = ""
        # Vector P (J1): split-screen side-panel state. _panel_side orders the
        # two children in #log-row ('left'|'right'). _panel_view names the open
        # face (explorer/locker/None); _panel_file_target is set for file-view.
        self._panel_open = False
        self._panel_side = "right"
        self._panel_view: Optional[str] = None
        self._panel_file_target: Optional[Path] = None

    # -- layout -----------------------------------------------------------

    def on_key(self, event: "events.Key") -> None:
        """App-level fallback for the commander ``<modifier>+<letter>`` hotkeys — the underlined
        menu mnemonics (Alt+X/P/T/C/O/A) and the content doorways (Alt+S/D/M/I/W/F/G). A focused
        input or dock catches these first and stops them; anything ELSE with focus (the transcript,
        a pane, nothing) lets the event bubble to here, so the hotkeys fire no matter what holds
        focus — the whole point of a commander bar. Only on the base screen: a pushed modal
        (a dropdown, a confirm, a prompt) owns the keyboard while it is up."""
        if len(self.screen_stack) > 1:
            return
        if self._commander_hotkey(event.key):
            event.stop()
            event.prevent_default()

    def compose(self) -> ComposeResult:
        from xlii.tui.menu_bar import _MenuBar

        yield _MenuBar(on_open=self._open_menu)   # commander top bar
        # Pinned query bar: the scroll wrapper owns the round frame + height
        # cap; the inner Static keeps the #question id the turn path updates.
        question_scroll = VerticalScroll(id="question-scroll")
        question_scroll.can_focus = False        # never steal the input's focus
        question_scroll.border_title = "you"
        with question_scroll:
            yield Static(id="question")
        # Vector P (J1): #log-row holds exactly two panes — the transcript and the
        # one binary file-tab panel (#panel). panel_side orders them.
        with Horizontal(id="log-row"):
            # Vector E: the selection-aware transcript — drag-select + OSC 52 copy
            # and whole-answer / whole-block copy affordances. A TranscriptLog
            # subclass, so `query_one("#log", TranscriptLog)` and write_block are
            # unchanged.
            yield SelectableTranscriptLog(id="log")
            yield Vertical(id="panel")
        yield Static(id="heartbeat")
        yield OptionList(id="completions")
        yield shortcodes._ShortcodePopup(id="shortcode-popup")
        # Plan-surface T1: the working plan's one-line readout rides directly
        # above the input (plan 3/7 ▸ next item); click opens the item pane.
        from xlii.tui.plan_strip import _PlanStrip

        yield _PlanStrip(on_open=self._open_plan_items)
        with Horizontal(id="input-row"):
            prefix = _ModePrefix("$ ", id="input-prefix")
            prefix.tooltip = "click to flip: $ shell-first ↔ ? ask-first"
            yield prefix
            with Horizontal(id="input-box"):
                yield _PromptInput(placeholder=_BUILTIN_HINTS["code"], id="input")
            yield _InputActionButton("stop", id="input-action")
        yield _ChipRow(id="input-chips", on_activate_tab=lambda k, p: self._activate_tab(k, p))
        # Commander chrome, bottom-up: the F-key hint bar (clickable — chips fire their
        # binding via _handle_fkey), then the status line at the very bottom.
        yield _FKeyBar(on_fkey=self._handle_fkey)
        yield Static(id="status")

    def on_mount(self) -> None:
        self.title = "xlii"
        self.sub_title = self._project_name
        # Track C: transcript paper + trim theme (filtered to canvas polarity).
        cfg = getattr(self._state, "cfg", None) if self._state is not None else None
        try:
            mode = getattr(cfg, "tui_canvas", "dark") if cfg is not None else "dark"
            self._apply_canvas(mode, persist=False, notify=False)
        except Exception:
            # Canvas restore is best-effort; a bad config must not block app launch.
            pass
        if cfg is not None:
            saved_side = str(getattr(cfg, "tui_panel_side", "") or "right").lower()
            if saved_side in ("left", "right"):
                try:
                    self.set_panel_side(saved_side, persist=False)
                except Exception:
                    # Invalid/stale panel-side config should not block app launch.
                    pass
            try:
                from xlii.tui.status_strip import _FKeyBar

                self.query_one(_FKeyBar).display = bool(getattr(cfg, "face_fkeys", True))
            except Exception:
                # F-key strip restore is best-effort — same pref as Face Options.
                pass
        # Plan-surface T1: the strip reads the working plan once at mount
        # (the listener + turn end keep it live from here).
        try:
            self._refresh_plan_surfaces()
        except Exception:
            # Best-effort plan strip/pane paint at mount must not block app launch.
            pass
        # Route turn-time output (agent tool blocks, slash-command output,
        # end-of-turn sync) into the transcript. The agent and the REPLState
        # each carry their own console handle; repoint both.
        if self._agent is not None:
            self._agent.console = self._console
        if self._state is not None:
            self._state.console = self._console
            # Command handlers that need one line from the user (guided /remote
            # add, /admin set-key secrets) route through xlii.console_prompt,
            # which prefers this capability — a modal — over blocking stdin
            # (which would deadlock under Textual; the ConfirmModal story).
            self._console.request_input = self._prompt_via_modal
            self._console.xlii_foreground = True
            # Let shell_toolkit.copy_for_terminal route OSC 52 through Textual's
            # driver (for "[c] copy for your terminal" on gated commands).
            self._state._clipboard = self.copy_to_clipboard
            # Let shell_toolkit route sudo / full-screen commands (which need a real
            # terminal) through the suspend handover instead of the captured runner.
            self._state._run_interactive = lambda c, cwd: self._run_fullscreen(c, cwd)
            agent = getattr(self._state, "agent", None)
            if agent is not None:
                from xlii.session_meter import init_budget_from_env

                init_budget_from_env(agent.session)
        # Drive the heartbeat spinner (shows only while a turn is running).
        self.set_interval(0.1, self._tick_heartbeat)
        self._refresh_status()  # mode · cwd · attachments strip under the header
        self._load_history()    # up/down command history (shared repl_history file)
        self._show_splash()
        # Session boot may queue text for the first prompt (startup-task
        # capture): mirror the bare REPL's prompt-default by seeding the input
        # line at mount — editable, never auto-submitted. Consume it once.
        queued = (getattr(self._state, "pending_input", "") or "") if self._state is not None else ""
        if queued:
            self._state.pending_input = ""
            self._set_input(self.query_one("#input", _PromptInput), queued)
        self.query_one("#input", _PromptInput).focus()

    def _show_splash(self) -> None:
        """Fill the empty transcript with a welcome banner (not a loading gate).

        Splash art is nfo-first: this launch prefers a custom `.nfo` in the project's own folder
        (``.xlii/splash.nfo`` or repo-root ``xlii.nfo``), then the system setup version
        (``~/.config/xlii/splash.nfo``), and finally the shipped default (``xlii/tui/splash.nfo``).
        All are rendered verbatim; the setup copy is seeded on first run so it's there to hack.
        """
        from xlii.tui.splash import (
            default_splash_text,
            ensure_setup_splash,
            read_nfo,
            resolve_splash_nfo,
            splash_renderable,
        )

        ensure_setup_splash()  # drop the hackable default into the setup folder, once
        proj = getattr(self._state, "project", None)
        path = resolve_splash_nfo(
            project_root=getattr(proj, "project_root", None),
            xli_dir=getattr(proj, "xli_dir", None),
        )
        nfo_text = read_nfo(path) if path else default_splash_text()
        self.write_block(splash_renderable(project=self._project_name, nfo_text=nfo_text))

    def on_unmount(self) -> None:
        # Release any worker thread still blocked on a confirm modal so teardown
        # can't deadlock — they resolve to deny (the screen is gone).
        for ev in list(self._pending_confirms):
            ev.set()
        # Match the inline REPL's exit contract (repl.py): persist session state
        # and hand the live shell cwd back to the parent shell. Runs once on
        # teardown, so /exit, /quit and ctrl+d all flush.
        try:
            if self._state is not None and hasattr(self._state, "save"):
                self._state.save()
                from xlii.repl import _write_exit_cwd

                _write_exit_cwd(self._state)
        except Exception:
            # Teardown must not raise: a failed save or cwd handback still has to let the app exit.
            pass
        # JRN-1: the journal's exit step lives in the announced
        # xlii.exit_sequence.run_graceful_exit, called from run_tui_over_session
        # once app.run() returns and the real console is restored (only on a
        # full quit; a /terminal drop-back stays silent and the inline session
        # runs it on its own eventual exit). Since fast exit, that step DEFERS
        # the sub-batch tail (raw entries are already durable on disk; the next
        # session's catch-up job summarizes them) instead of paying the LLM
        # flush — so nothing slow runs at teardown on either surface. on_unmount
        # keeps only the instant, must-run-on-any-teardown bits above.

    @property
    def _transcript(self) -> TranscriptLog:
        return self.query_one("#log", TranscriptLog)

    def write_block(self, renderable: Any) -> None:
        """Transcript write, safe from any thread. From a worker thread we hop to
        the main thread (querying/writing widgets off-thread is unsafe); on the
        main thread we write directly — call_from_thread would raise there.
        _write_main does the DOM lookup + write together."""
        # Defense-in-depth: a straggler write after the app has torn down (e.g. a
        # stale _TranscriptConsole reference) would otherwise raise "App is not
        # running" from call_from_thread. There's no transcript left to write to,
        # so drop it rather than crash the caller.
        if not self.is_running:
            return
        if self._thread_id == threading.get_ident():
            self._write_main(renderable)
        else:
            self.call_from_thread(self._write_main, renderable)

    def _write_main(self, renderable: Any) -> None:
        self._transcript.write(renderable)

    def _clear_transcript(self) -> None:
        """Reset the transcript (shared by /clear and a bare `clear`). Main
        thread only — it queries + mutates widgets."""
        self._transcript.clear()
        self._set_question("")

    def _on_main(self, fn: Callable, *args: Any) -> None:
        """Run `fn(*args)` on the main thread — call_from_thread off-thread,
        directly when already there (call_from_thread would raise on the main
        thread). Same hop contract as write_block, for non-transcript widgets."""
        if self._thread_id == threading.get_ident():
            fn(*args)
        else:
            self.call_from_thread(fn, *args)

    def _confirm_via_modal(self, prompt: str) -> str:
        """Thread-safe replacement for the intent-gate's blocking input().

        Called on the agent worker thread (the bash gate imports
        xlii.tools._confirm fresh per call, and launch() points that global at
        this method while the TUI runs). We push a ConfirmModal on the UI thread
        and block this worker thread on an Event until the user answers,
        returning the chosen key ('y'/'n', or 'c' when the prompt offers copy)
        so the gate's `.strip().lower()` checks are unchanged.

        Denies (the safe default) if we're already on the UI thread (can't block
        there) or the app tears down with the prompt still open — on_unmount
        releases the waiter, and an unanswered gate must not approve."""
        if self._thread_id == threading.get_ident():
            return "n"
        done = threading.Event()
        box = {"answer": "n"}

        def _show() -> None:
            modal = ConfirmModal(prompt)

            def _resolved(answer: Optional[str]) -> None:
                self._pending_modals.discard(modal)
                box["answer"] = answer or "n"
                done.set()

            self._pending_modals.add(modal)
            self.push_screen(modal, _resolved)

        self._pending_confirms.add(done)
        try:
            self.call_from_thread(_show)
            done.wait()
        except Exception:
            return "n"
        finally:
            self._pending_confirms.discard(done)
        return box["answer"]

    def _prompt_via_modal(self, prompt: str, *, secret: bool = False) -> Optional[str]:
        """Thread-safe replacement for a handler's blocking input()/getpass —
        ConfirmModal's free-text twin (see xlii.console_prompt for the seam).

        Called on the command worker thread via the transcript console's
        ``request_input`` capability. Pushes a PromptModal on the UI thread and
        blocks this worker on an Event until the user answers. ``None`` (cancel)
        if we're already on the UI thread (can't block there), the user hits
        Esc, or the app tears down with the prompt open — on_unmount releases
        the waiter through the same _pending_confirms set, and an unanswered
        prompt must read as a cancel, never an empty answer."""
        if self._thread_id == threading.get_ident():
            return None
        done = threading.Event()
        box: dict = {"answer": None}

        def _show() -> None:
            modal = PromptModal(prompt, secret=secret)

            def _resolved(answer: Optional[str]) -> None:
                self._pending_modals.discard(modal)
                box["answer"] = answer
                done.set()

            self._pending_modals.add(modal)
            self.push_screen(modal, _resolved)

        self._pending_confirms.add(done)
        try:
            self.call_from_thread(_show)
            done.wait()
        except Exception:
            return None
        finally:
            self._pending_confirms.discard(done)
        return box["answer"]

    def cancel_pending_confirms(self) -> None:
        """Resolve every open confirm/prompt modal as deny/cancel — the stop
        button's path into a turn that is blocked on a *question* (bash gate,
        failure-nudge run/edit/dismiss) rather than a tool. Dismissing through
        the modal's own callback — not just setting the waiter events — both
        unblocks the worker thread AND removes the modal from the screen.
        Main thread only (dismiss touches the screen stack).

        After the dismiss loop, also set every pending waiter Event directly
        (mirrors ``on_unmount``): with two confirm modals open at once, Textual's
        dismiss/pop_screen ordering can strand one waiter forever otherwise.
        Deny-by-default semantics unchanged — ``ev.set()`` only unblocks; the
        answer box stays None/deny.
        """
        for modal in list(self._pending_modals):
            self._pending_modals.discard(modal)
            try:
                modal.dismiss(None)
            except Exception:
                pass  # already dismissed / tearing down — the waiter is safe either way
        for ev in list(self._pending_confirms):
            try:
                ev.set()
            except Exception:
                # The waiter is safe either way -- an already-set or discarded event needs no waking.
                pass

    # -- frame chrome (pinned question · heartbeat · context meter) -------

    def _set_question(self, text: str) -> None:
        """Pin the active question above the transcript so it stays in view as
        the answer scrolls. Empty text hides the bar. Text wraps under the
        frame's 3-row cap; the remainder scrolls inside the bar (the wrapper
        owns the round `you` frame — pinned-query-bar-style P0/P1)."""
        bar = self.query_one("#question", Static)
        try:
            wrap = self.query_one("#question-scroll", VerticalScroll)
        except Exception:
            wrap = None  # mount timing / tests composing the bare Static
        if not text:
            bar.display = False
            if wrap is not None:
                wrap.display = False
            return
        bar.update(Text(text, style="bold"))
        bar.display = True
        if wrap is not None:
            wrap.display = True
            wrap.scroll_home(animate=False)

    def _tick_heartbeat(self) -> None:
        """Spinner + elapsed above the input while a turn runs; hidden otherwise.
        Driven by on_mount's set_interval, so it runs on the main thread."""
        try:
            hb = self.query_one("#heartbeat", Static)
        except Exception:
            return  # not mounted yet / torn down — the timer can fire either side
        if self._busy or self._agent_job_active():
            elapsed = time.monotonic() - self._turn_started
            frame = _SPINNER[self._spin_i % len(_SPINNER)]
            self._spin_i += 1
            # bg-default P1: a background agent turn keeps the spinner + armed ■
            # while the input stays free (the wording says so).
            if self._pending_confirms:
                # The turn is blocked on a question, not on work — say so, or an
                # unanswered confirm reads as a hung "working Ns" forever.
                label = "waiting for your answer — stop = deny"
            elif self._busy:
                label = "working"
            else:
                label = "agent working — input free · /btw to steer"
            hb.update(Text(f"{frame} {label} {elapsed:.0f}s", style="cyan"))
            hb.display = True
            self._paint_action_button(armed=True)
        elif hb.display:
            hb.display = False
            self._paint_action_button(armed=False)

    def _paint_action_button(self, *, armed: bool) -> None:
        """While a turn runs the `stop` button is live (red, tooltip); idle it
        dims to inert chrome. Driven by the heartbeat tick (main thread)."""
        try:
            action = self.query_one("#input-action", _InputActionButton)
        except Exception:
            return
        if armed:
            action.styles.color = "red"
            action.styles.text_style = "bold"
            action.tooltip = "stop — halt the agent at the next tool boundary"
        else:
            # Labelled but inert: dim so it never looks clickable-active when
            # there is no turn to stop.
            action.styles.color = "#808080"
            action.styles.text_style = "none"
            action.tooltip = None

    def _apply_meter(self, stats: Any) -> None:
        """Recompute the context meter from a finished turn's stats and repaint
        the profile bar: `used / cap · N% cached` (drops `/cap` for unknown
        models and the cache tail when nothing was cache-served). The meter is
        the bar's rightmost segment (RP3)."""
        used = getattr(stats, "context_tokens", 0) or 0
        if used:
            cached = getattr(stats, "cached_tokens", 0) or 0
            model = getattr(getattr(stats, "orch", None), "model", "") or ""
            cap = _context_window(model)
            meter = f"{_ktok(used)} / {_ktok(cap)}" if cap else _ktok(used)
            if cached:
                meter += f" · {round(100 * cached / used)}% cached"
            self._meter_text = meter
        self._refresh_status()

    def _refresh_status(self) -> None:
        """Repaint the profile bar from live state (RP3): `mode · id · loadout ·
        model · meter`. Cheap; called on mount, after every processed input
        (mode/profile/loadout/attachment changes — incl. a /code<->/chat switch),
        and after each turn (with the refreshed meter)."""
        try:
            from xlii.tui.status import profile_bar
            self.query_one("#status", Static).update(
                profile_bar(self._state, meter=self._meter_text)
            )
        except Exception:
            # The status bar may not be mounted yet (early refresh, or teardown); the next refresh repaints it.
            pass
        try:
            comp = self.query_one("#completions", OptionList)
            if not self._popup_open:
                comp.display = False
        except Exception:
            # Best-effort UI refresh: completions may be unavailable transiently
            # during mount/teardown; ignore to keep status repaint non-fatal.
            pass
        self._paint_input_frame()
        # F-key row is the commander verbs — workbench must not remap it.
        try:
            self._refresh_fkey_bar()
        except Exception:
            # Best-effort UI refresh: the F-key strip can be transiently
            # unavailable during mount/teardown; keep status repaint non-fatal.
            pass

