"""Typed events — the vocabulary every input mode (shell, bang, slash, agent)
feeds into Renderer.emit(). One dataclass per kind of "something happened"; the
renderer maps each to a block.

Invariant: presentation is cosmetic. The full text carried here is the same
bytes the model already received — truncation for display happens in the
renderer, never to the content the agent acts on.

Leaf module: imports nothing from xlii.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

# Where a shell command came from — drives the header's "you" / "agent" tag.
ShellSource = Literal["user_shell", "user_bang", "agent_bash"]


@dataclass
class ShellRan:
    """A shell command finished — typed at the prompt, via `!bang`, or by the
    agent's bash tool. The single event behind the unified ShellBlock so the
    same `git status` looks identical regardless of who ran it."""
    command: str
    cwd: Path
    stdout: str
    stderr: str
    returncode: int
    duration_s: Optional[float] = None
    source: ShellSource = "user_shell"
    intent: Optional[str] = None  # shellgate classification, e.g. "read-only"


@dataclass
class ToolStarted:
    """A tool is about to run (the dim `→ tool args` announce line)."""
    name: str
    args_preview: str = ""


@dataclass
class ToolFinished:
    """A non-shell agent tool finished (read_file, grep, edit_file, …)."""
    name: str
    args_preview: str
    content: str
    is_error: bool = False
    duration_s: Optional[float] = None


@dataclass
class MetaMessage:
    """Slash-command / meta output — replaces scattered ad-hoc console.print."""
    text: str
    level: Literal["info", "success", "warn", "error", "mode"] = "info"


@dataclass
class UserTurn:
    """What the user typed — agent prompt, slash, or shell.

    ``kind`` is ``talk`` / ``slash`` / ``shell`` so the face can color the
    line. Empty = unknown (older events).
    """
    text: str
    kind: str = ""


@dataclass
class AssistantAnswer:
    """The agent's final Markdown reply. `streamed` lets the caller suppress a
    duplicate print when the body was already streamed live (T4 concern).
    mode/mode_color/role are the turn RECORD (tense-chrome): the mode word,
    frame color, and equipped role snapshotted when the turn finished. Empty
    strings mean "unknown / pre-tense-chrome event" — renderers must then fall
    back to the legacy presentation exactly."""
    markdown: str
    model: Optional[str] = None
    streamed: bool = False
    mode: str = ""
    mode_color: str = ""
    role: str = ""
    skill: str = ""


# --- Face-wire events (protocol v2) ------------------------------------------
# Emitted by the face server (serve --face), not by Renderer.emit(): control
# and chrome state a remote face needs that a terminal gets for free. They live
# here because the wire rule is "new event kinds start as dataclasses in
# turn_events.py" — one vocabulary, never parallel ad-hoc shapes.


@dataclass
class ConfirmRequest:
    """A gated action awaits the user's approve/deny (the rail/shellgate ask,
    surfaced over the wire instead of a blocking terminal prompt). The client
    answers with an inbound ``confirm`` message carrying the same ``id``."""
    id: str
    prompt: str
    danger: str = ""


@dataclass
class ModeState:
    """The input-chrome snapshot after each handled input: mode word + frame
    color, the way out (exit_hint), placeholder/hint text, and the routing
    posture — everything the face needs to label its input box honestly."""
    mode: str
    color: str
    exit_hint: str
    placeholder: str
    hint: str
    ask_primary: bool
    posture: str
    # /off-clearable faux-mode that owns the flip chip (howto, ops, plan, …).
    # Empty = talk/lab ``?``/``$``.
    overlay: str = ""


@dataclass
class FileOut:
    """A file left the session outbox for this face (image previews inline via
    b64; the durable path is always set when we know it so the tape can label
    ``.xlii/artifacts/…`` and Focus can pin that file)."""
    name: str
    kind: str
    b64: str = ""
    path: str = ""
    address: str = ""  # ``artifacts://name`` or ``file://…`` for Focus / canvas


@dataclass
class Prefill:
    """Server asks the face to seed its input box with text, un-executed —
    the InputSink.prefill verb on the wire (menus/panes, review-before-run)."""
    text: str


# --- panes on the face (typed-workbenches B1) ---------------------------------
# The face's deck of headless panes (xlii/panes/contract + panes/dock.py's
# registry), projected onto the wire. One deck snapshot = the whole strip;
# re-projection IS the refresh, same state-ownership rule as the panes
# themselves. Inbound client ops are plain dicts ("pane_action"), same as
# "input"/"set_posture" — outbound kinds live here per the protocol rule.


@dataclass
class PaneRow:
    """One row of a pane's projection on the wire (mirrors panes.RenderedRow)."""
    text: str
    address: str
    kind: str  # "container" | "leaf"
    selected: bool = False
    accent: bool = False
    tone: str = ""
    # Face Config knobs: [{value, label}, …] + current value for a <select>.
    choices: list = field(default_factory=list)
    value: str = ""


@dataclass
class PaneAction:
    """One selection-action a pane offers (mirrors panes.Action minus Outcome —
    the outcome executes server-side when the client names the action)."""
    name: str
    label: str


@dataclass
class PaneState:
    """One mounted pane: its rendered rows + the actions its selection offers."""
    id: str  # the slot id — the workbench pane name (explorer, git, tasks, …)
    title: str  # the mounted address
    rows: list = field(default_factory=list)  # list[PaneRow]
    actions: list = field(default_factory=list)  # list[PaneAction]
    empty: bool = False
    note: str = ""  # degrade note ("failed to mount"), "" when healthy
    image_b64: str = ""  # optional inline PNG (scanned PDF page)
    image_alt: str = ""
    form: Optional[dict] = None  # pluginform spec (html + fields); None = not a form pane


@dataclass
class PaneDeck:
    """The whole strip: the active workbench type and every mounted pane."""
    workbench: str
    panes: list = field(default_factory=list)  # list[PaneState]
    # desk | phone — capability cut lives on the server; layout is client-side.
    posture: str = "desk"


@dataclass
class ChromeState:
    """The face's HUD rail (F1) — the always-visible session cues, one event.

    Sent on connect and after every handled input (alongside ``mode_state``,
    which stays the input-chrome snapshot). All values are display strings,
    computed server-side; the rail renders, it never infers.

    ``quick_launch`` (three-faces Q1): ordered dumb doors for the strip above
    the input — ``[{id, label, action}, …]`` from the active workbench pack.

    ``surface`` (three-faces Q5): base face door — ``scratch`` (home) | ``chat``
    | ``code`` (project lab). Distinct from posture ``[M]``/``[$]``.
    """
    project: str = ""
    workbench: str = ""
    persona: str = ""
    model: str = ""
    posture: str = "chat"
    surface: str = "code"  # scratch | chat | code
    providers_ready: int = 0  # keyed providers with their env var set
    providers_total: int = 0
    quick_launch: list = field(default_factory=list)
    # Chat reasoning-depth dial (chat_tiers): off | auto | fast | expert | heavy
    chat_tier: str = "off"
    # Bash/trust ladder: safe | yolo | freeball
    trust: str = "safe"
    # Context occupancy (``270K / 500K``) and session billed total.
    meter: str = ""
    session: str = ""
    # Side dock: left|right, and width as percent of workspace (0 = auto).
    pane_side: str = "right"
    pane_width_pct: int = 0
    # Live desk cwd (compact, e.g. ~/Downloads). Empty when unknown.
    cwd: str = ""
    # Background jobs for the status rail (not F-key / pack doors).
    jobs_active: int = 0
    jobs_unseen: int = 0
    # Per-job pills: in-flight + finished-unseen. Face strip, not a count chip.
    jobs: list = field(default_factory=list)  # [{id, kind, name, status, glyph, label, unseen}]
    # Visual desk slots: ``stream`` or a pane id; empty string = closed.
    slot_a: str = "stream"
    slot_b: str = ""
    slot_focus: str = "a"
    slot_catalog: list = field(default_factory=list)  # [{id, label}]
    # Project Shadow: ``on`` when the folder's journal is recording.
    journal: str = ""
    # Agent Chromium (one process-wide session): "" | "window" | "hidden".
    browser: str = ""
    browser_url: str = ""
    # Last opened desks (lab + talk), newest first — Xlii menu after Home.
    recent: list = field(default_factory=list)  # [{name, path, kind, label}]
    # Task chrome binds (menu rows + F-key overlays).
    binds: list = field(default_factory=list)  # [{task, menu, fkey, label, line}]
    # Tools → New terminal dest hint (this project | home folder | root | ~/…).
    term_cwd: str = "this project"
    # Face web skin (dark | light | slate | mojo). Empty = client default.
    face_skin: str = ""
    # Options prefs the glass restores (F-keys strip, bold type).
    face_fkeys: bool = True
    face_bold: bool = False
    # Help menu: howto shards + About (credits + rotating tagline).
    howto: list = field(default_factory=list)  # [{id, title}]
    about_line: str = ""
    about_credit: str = ""
    about_taglines: list = field(default_factory=list)
    about_version: str = ""
    about_facts: list = field(default_factory=list)
    about_signs: str = ""
    # Fabric roster keys (throne-side). Face Project menu: New on <node>….
    fabric_nodes: list = field(default_factory=list)
    # Occupancy glass: "" | "locked" | "black". Mouth: desk | me | none.
    # mouth_via: fabric node that currently holds me@ (empty = this glass).
    glass: str = ""
    mouth: str = "desk"
    mouth_via: str = ""
    # Tailnet glass: desk | phone. Phone grants the D2 pane cut.
    view_posture: str = "desk"


@dataclass
class CommandEntry:
    """One slash command for the face's code-posture completion popup."""
    name: str
    description: str = ""


@dataclass
class CommandCatalog:
    """Slash-command snapshot for the face ``[$]`` completions popup (M1.3).

    Sent on connect. Chat posture does not use this catalog — product law is
    prose + plugin menu only under ``[M]``."""
    commands: list = field(default_factory=list)  # list[CommandEntry]


@dataclass
class PluginParamEntry:
    """One action parameter for the face Plugins menu (M2.1)."""
    name: str
    description: str = ""
    required: bool = False
    default: str = ""
    form: bool = False
    secret: bool = False


@dataclass
class PluginActionEntry:
    """One invocable action on a plugin."""
    id: str
    description: str = ""
    params: list = field(default_factory=list)  # list[PluginParamEntry]


@dataclass
class PluginEntry:
    """One installed plugin for the face Plugins menu."""
    id: str
    name: str
    description: str = ""
    effect: str = ""
    trust: str = ""
    subscribed: bool = False
    ready: bool = True  # auth env vars present (or none required)
    actions: list = field(default_factory=list)  # list[PluginActionEntry]


@dataclass
class PluginCatalog:
    """Installed plugins + actions for the face **Plugins** menu (M2.1).

    Chat power path is this menu (not slash). Sent on connect; client may
    refresh after subscribe. Inbound runs use client ``plugin_call`` (same
    ``invoke_action`` runner as ``/plugin call``)."""
    plugins: list = field(default_factory=list)  # list[PluginEntry]
