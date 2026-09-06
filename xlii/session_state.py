"""SessionState — single owner of per-session flags and attachments.

Grades plan Phase 4: fields live in **nested value objects** so new state has a
home and the flat surface does not grow forever. Call sites keep using
``session.yolo``, ``session.attached_docs``, etc. via property facades.

**Where new state goes**

| Kind | Nest |
|------|------|
| Trust ladder (yolo / freeball / auto_approve) | ``session.trust`` |
| Docs / refs / locker | ``session.attachments`` |
| Cost / budget / turn stats | ``session.meter`` |
| howto / image overlays | ``session.overlays`` |
| Model pins / loadout / role / temp | ``session.model_pins`` |
| Context compaction | ``session.compact`` |
| One-shot / ephemeral IO, hooks, scope | top-level residual (keep thin) |

Do **not** add a new top-level field without a nest home (or an explicit residual
note here). Prefer ``session.<nest>.field`` for new code; facades stay for compat.

Construction: prefer ``SessionState()`` then assign, or ``SessionState.from_flat(yolo=…)``
when you need the old keyword style (``SessionState(yolo=True)`` is not a dataclass
field anymore).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path as _Path
from typing import Any, Optional

from xlii.agent_stats import TurnStats
from xlii.tool_context import default_auto_approve


# --------------------------------------------------------------------------- #
# Nested value objects
# --------------------------------------------------------------------------- #


# Trust-ladder tiers — the one position on safe → yolo → freeball.
TIER_SAFE = "safe"
TIER_YOLO = "yolo"
TIER_FREEBALL = "freeball"


@dataclass
class TrustState:
    """safe → yolo → freeball ladder + auto-approve intents.

    A single ``tier`` slot holds the ladder position; ``yolo``/``freeball`` are
    derived views of it, so freeball ⇒ yolo holds by construction (it used to
    be two booleans kept in sync by hand in ``h_yolo``)."""

    tier: str = TIER_SAFE
    auto_approve: set[str] = field(default_factory=default_auto_approve)
    # One-shot `/yolo --freeball <task>`: the tier to restore after that turn.
    restore: Optional[str] = None

    @property
    def yolo(self) -> bool:
        return self.tier != TIER_SAFE

    @yolo.setter
    def yolo(self, v: bool) -> None:
        if v:
            if self.tier == TIER_SAFE:
                self.tier = TIER_YOLO  # never downgrades a live freeball
        else:
            self.tier = TIER_SAFE  # yolo off stands its superset down too

    @property
    def freeball(self) -> bool:
        return self.tier == TIER_FREEBALL

    @freeball.setter
    def freeball(self, v: bool) -> None:
        if v:
            self.tier = TIER_FREEBALL
        elif self.tier == TIER_FREEBALL:
            self.tier = TIER_YOLO  # stand down to plain yolo, not safe


@dataclass
class AttachmentState:
    """Durable / session knowledge attachments — files in, and the way out."""

    # Bookmark pointers only (empty collection-id slot); persona-Collection
    # refs are BANNED — see xlii/refs.py's tombstone (menu-families §5).
    attached_refs: list[tuple[str, str]] = field(default_factory=list)
    attached_docs: list[tuple[str, str]] = field(default_factory=list)
    attached_files: list[dict] = field(default_factory=list)
    # Media-out (`xlii ask --outbox`): the caller-granted delivery dir the
    # send_file tool queues into — the outbound counterpart of the lists above.
    outbox_dir: Optional[_Path] = None


@dataclass
class MeterState:
    """Session cost meter + budget awareness."""

    session_cost: float = 0.0
    session_tokens: int = 0
    budget_usd: Optional[float] = None
    budget_env_cleared: bool = False
    last_turn_stats: Optional[TurnStats] = None
    budget_note: Optional[str] = None
    # Turn receipts (P1): the last turn's evidence-vs-claims receipt — turn
    # telemetry, so it lives beside last_turn_stats. Durable trail is
    # .xlii/receipts.jsonl.
    last_turn_receipt: Any = None
    # One-shot (plan-write-domain P1 D1c): ("plan-file"|"chat-snapshot",
    # expected_opening) — which source fed plan-last.md at the last approval,
    # bound to the rewritten text the approval planted. Receipt-adjacent audit,
    # so it lives here; build_receipt consumes it and stamps ONLY the turn that
    # opens with that text. Best-effort, memory-only — never persisted.
    pending_plan_source: Optional[tuple] = None


@dataclass
class OverlayState:
    """Ephemeral conversational overlays (howto) + preview prefs."""

    howto_mode: bool = False
    image_preview_backend: str = "auto"
    # chat-tiers R1: the sticky chat tier (fast|expert|heavy|auto), or None for
    # the plain `chat` role. Read by resolve_orchestrator_model to steer the
    # chat-slot model — a companion to `conversational` and sibling to
    # `howto_mode`, never a ModeController (it gates nothing, only model choice).
    # Defaults to `auto` (Vector D, proposal-locked once B/C landed): fresh chat
    # sessions route per message; `/tier off` restores the plain chat role.
    chat_tier: Optional[str] = "auto"
    # V3b: the chat surface's awareness — /chat --read-proj opts the session
    # into the read-only palette (awareness = read); default project-blind.
    # Only consulted when conversational (the chat surface); code ignores it.
    chat_read_proj: bool = False


@dataclass
class ModelPinState:
    """Loadout / persona / skill model and temperature pins."""

    loadout_cfg_snapshot: Optional[dict] = None
    next_turn_temp_override: Optional[float] = None
    next_turn_temp_chat_only: bool = False
    model_override: Optional[str] = None
    temperature_override: Optional[float] = None
    model_pin_stack: list[Optional[str]] = field(default_factory=list)
    active_role: Optional[str] = None
    # Talk / /mojo oneshot: extra ``.xlii`` dirs whose plugins.txt unions with
    # the persona project's (the desk you are sitting in). Empty on lab.
    plugin_xli_dirs: tuple = ()


@dataclass
class CompactState:
    """Context compaction preferences."""

    compact_auto: bool = False
    compact_auto_env_cleared: bool = False
    compact_recent: int = 4


# --------------------------------------------------------------------------- #
# SessionState
# --------------------------------------------------------------------------- #


@dataclass
class SessionState:
    """The single owner of per-session mutable flags and attachments.

    Exactly one instance exists per session. Agent holds it; REPLState (when
    a REPL is running) delegates to the same object via properties. Slash
    command handlers and the agent loop therefore always see the same values
    — the old per-turn sync_to_agent/sync_from_agent round-trip (and its
    state-reversion bug class) is structurally impossible now.
    """

    # --- nested homes (Phase 4) ---
    trust: TrustState = field(default_factory=TrustState)
    attachments: AttachmentState = field(default_factory=AttachmentState)
    meter: MeterState = field(default_factory=MeterState)
    overlays: OverlayState = field(default_factory=OverlayState)
    model_pins: ModelPinState = field(default_factory=ModelPinState)
    compact: CompactState = field(default_factory=CompactState)

    # --- thin top-level residual (keep ≤ ~12) ---
    # plan_mode here is a legacy mirror for some status paths; Agent.plan_mode
    # is authoritative via active_mode / PlanController.
    plan_mode: bool = False
    conversational: bool = False
    user_shell_cwd: Optional[_Path] = None
    pending_plan_refresher: Optional[str] = None
    loop_lock_tests: bool = False
    hook_control: Optional[bool] = None
    scope_paths: set[str] = field(default_factory=set)
    last_shell: Any = None
    last_output: Any = None
    # bg-default P2: /btw steering notes for the in-flight turn. Drained into
    # history as a user message at the next tool boundary (the same safe yield
    # point request_cancel uses); queued between turns, it folds into the next
    # turn's first boundary. Session-scoped, never persisted.
    btw_inbox: list = field(default_factory=list)
    # mojo-keeper K1: surface-owned worker hire for a persona oneshot.
    # "none" (default — live /chat, talk only) · "read" (oneshot default) ·
    # "write" (folder desk). Occupancy sitting is not this gate.
    hire: str = "none"

    # ----- flat construction compat -----

    @classmethod
    def from_flat(cls, **kwargs: Any) -> "SessionState":
        """Build a SessionState from legacy flat keyword args (``yolo=``, …)."""
        top_keys = {
            "plan_mode",
            "conversational",
            "user_shell_cwd",
            "pending_plan_refresher",
            "loop_lock_tests",
            "hook_control",
            "scope_paths",
            "last_shell",
            "last_output",
            "hire",
            "trust",
            "attachments",
            "meter",
            "overlays",
            "model_pins",
            "compact",
        }
        top = {k: kwargs.pop(k) for k in list(kwargs) if k in top_keys}
        s = cls(**top)
        for k, v in kwargs.items():
            if not hasattr(s, k):
                raise TypeError(f"SessionState.from_flat() got unexpected keyword {k!r}")
            setattr(s, k, v)
        return s

    # ----- trust facades -----

    @property
    def yolo(self) -> bool:
        return self.trust.yolo

    @yolo.setter
    def yolo(self, v: bool) -> None:
        self.trust.yolo = bool(v)

    @property
    def chat_read_proj(self) -> bool:
        """V3b chat awareness facade (OverlayState home; not a top-level field)."""
        return self.overlays.chat_read_proj

    @chat_read_proj.setter
    def chat_read_proj(self, v: bool) -> None:
        self.overlays.chat_read_proj = bool(v)

    @property
    def auto_approve(self) -> set[str]:
        return self.trust.auto_approve

    @auto_approve.setter
    def auto_approve(self, v: set[str]) -> None:
        self.trust.auto_approve = set(v)

    @property
    def freeball(self) -> bool:
        return self.trust.freeball

    @freeball.setter
    def freeball(self, v: bool) -> None:
        self.trust.freeball = bool(v)

    @property
    def trust_tier(self) -> str:
        return self.trust.tier

    @trust_tier.setter
    def trust_tier(self, v: str) -> None:
        self.trust.tier = v

    @property
    def freeball_restore(self) -> Optional[str]:
        return self.trust.restore

    @freeball_restore.setter
    def freeball_restore(self, v: Optional[str]) -> None:
        self.trust.restore = v

    # ----- attachments facades -----

    @property
    def attached_refs(self) -> list[tuple[str, str]]:
        return self.attachments.attached_refs

    @attached_refs.setter
    def attached_refs(self, v: list[tuple[str, str]]) -> None:
        self.attachments.attached_refs = v

    @property
    def attached_docs(self) -> list[tuple[str, str]]:
        return self.attachments.attached_docs

    @attached_docs.setter
    def attached_docs(self, v: list[tuple[str, str]]) -> None:
        self.attachments.attached_docs = v

    @property
    def attached_files(self) -> list[dict]:
        return self.attachments.attached_files

    @attached_files.setter
    def attached_files(self, v: list[dict]) -> None:
        self.attachments.attached_files = v

    @property
    def outbox_dir(self) -> Optional[_Path]:
        return self.attachments.outbox_dir

    @outbox_dir.setter
    def outbox_dir(self, v: Optional[_Path]) -> None:
        self.attachments.outbox_dir = v

    # ----- meter facades -----

    @property
    def session_cost(self) -> float:
        return self.meter.session_cost

    @session_cost.setter
    def session_cost(self, v: float) -> None:
        self.meter.session_cost = float(v)

    @property
    def session_tokens(self) -> int:
        return self.meter.session_tokens

    @session_tokens.setter
    def session_tokens(self, v: int) -> None:
        self.meter.session_tokens = int(v)

    @property
    def budget_usd(self) -> Optional[float]:
        return self.meter.budget_usd

    @budget_usd.setter
    def budget_usd(self, v: Optional[float]) -> None:
        self.meter.budget_usd = v

    @property
    def budget_env_cleared(self) -> bool:
        return self.meter.budget_env_cleared

    @budget_env_cleared.setter
    def budget_env_cleared(self, v: bool) -> None:
        self.meter.budget_env_cleared = bool(v)

    @property
    def last_turn_stats(self) -> Optional[TurnStats]:
        return self.meter.last_turn_stats

    @last_turn_stats.setter
    def last_turn_stats(self, v: Optional[TurnStats]) -> None:
        self.meter.last_turn_stats = v

    @property
    def budget_note(self) -> Optional[str]:
        return self.meter.budget_note

    @budget_note.setter
    def budget_note(self, v: Optional[str]) -> None:
        self.meter.budget_note = v

    @property
    def last_turn_receipt(self) -> Any:
        return self.meter.last_turn_receipt

    @last_turn_receipt.setter
    def last_turn_receipt(self, v: Any) -> None:
        self.meter.last_turn_receipt = v

    @property
    def pending_plan_source(self) -> Optional[tuple]:
        return self.meter.pending_plan_source

    @pending_plan_source.setter
    def pending_plan_source(self, v: Optional[tuple]) -> None:
        self.meter.pending_plan_source = v

    # ----- overlay facades -----

    @property
    def howto_mode(self) -> bool:
        return self.overlays.howto_mode

    @howto_mode.setter
    def howto_mode(self, v: bool) -> None:
        self.overlays.howto_mode = bool(v)

    @property
    def chat_tier(self) -> Optional[str]:
        return self.overlays.chat_tier

    @chat_tier.setter
    def chat_tier(self, v: Optional[str]) -> None:
        self.overlays.chat_tier = v

    @property
    def image_preview_backend(self) -> str:
        return self.overlays.image_preview_backend

    @image_preview_backend.setter
    def image_preview_backend(self, v: str) -> None:
        self.overlays.image_preview_backend = v or "auto"

    # ----- model pin facades -----

    @property
    def loadout_cfg_snapshot(self) -> Optional[dict]:
        return self.model_pins.loadout_cfg_snapshot

    @loadout_cfg_snapshot.setter
    def loadout_cfg_snapshot(self, v: Optional[dict]) -> None:
        self.model_pins.loadout_cfg_snapshot = v

    @property
    def next_turn_temp_override(self) -> Optional[float]:
        return self.model_pins.next_turn_temp_override

    @next_turn_temp_override.setter
    def next_turn_temp_override(self, v: Optional[float]) -> None:
        self.model_pins.next_turn_temp_override = v

    @property
    def next_turn_temp_chat_only(self) -> bool:
        return self.model_pins.next_turn_temp_chat_only

    @next_turn_temp_chat_only.setter
    def next_turn_temp_chat_only(self, v: bool) -> None:
        self.model_pins.next_turn_temp_chat_only = bool(v)

    @property
    def model_override(self) -> Optional[str]:
        return self.model_pins.model_override

    @model_override.setter
    def model_override(self, v: Optional[str]) -> None:
        self.model_pins.model_override = v

    @property
    def temperature_override(self) -> Optional[float]:
        return self.model_pins.temperature_override

    @temperature_override.setter
    def temperature_override(self, v: Optional[float]) -> None:
        self.model_pins.temperature_override = v

    @property
    def model_pin_stack(self) -> list[Optional[str]]:
        return self.model_pins.model_pin_stack

    @model_pin_stack.setter
    def model_pin_stack(self, v: list[Optional[str]]) -> None:
        self.model_pins.model_pin_stack = v

    @property
    def active_role(self) -> Optional[str]:
        return self.model_pins.active_role

    @active_role.setter
    def active_role(self, v: Optional[str]) -> None:
        self.model_pins.active_role = v

    @property
    def plugin_xli_dirs(self) -> tuple:
        """Talk / /mojo oneshot desk-plugin dirs (ModelPinState home)."""
        return self.model_pins.plugin_xli_dirs

    @plugin_xli_dirs.setter
    def plugin_xli_dirs(self, v: tuple) -> None:
        self.model_pins.plugin_xli_dirs = tuple(v)

    # ----- compact facades -----

    @property
    def compact_auto(self) -> bool:
        return self.compact.compact_auto

    @compact_auto.setter
    def compact_auto(self, v: bool) -> None:
        self.compact.compact_auto = bool(v)

    @property
    def compact_auto_env_cleared(self) -> bool:
        return self.compact.compact_auto_env_cleared

    @compact_auto_env_cleared.setter
    def compact_auto_env_cleared(self, v: bool) -> None:
        self.compact.compact_auto_env_cleared = bool(v)

    @property
    def compact_recent(self) -> int:
        return self.compact.compact_recent

    @compact_recent.setter
    def compact_recent(self, v: int) -> None:
        self.compact.compact_recent = int(v)


# --------------------------------------------------------------------------- #
# XLII_SESSION nested-session protocol (godzilla-mothra B2 — one kernel owner)
# --------------------------------------------------------------------------- #

XLII_SESSION_ENV = "XLII_SESSION"
XLII_SESSION_PROJECT_ENV = "XLII_SESSION_PROJECT"


@dataclass(frozen=True)
class NestedSessionVerdict:
    """Structured outcome of :func:`detect_nested_session`.

    The kernel owns detection and the refuse/warn policy; the face renders
    (Panel / warning line) from this verdict. ``outcome`` is one of:

    * ``"proceed"`` — no outer session; continue silently.
    * ``"blocked"`` — same-project re-entry refused (abort; caller returns 1).
    * ``"forced"`` — same-project re-entry allowed via ``force``.
    * ``"warn"``    — nested, but a different (or no) project; heads-up only.
    """

    outcome: str
    outer_pid: str = ""
    same_project: bool = False

    @property
    def proceed(self) -> bool:
        return self.outcome != "blocked"


def mark_session_active(project_root: Optional[_Path]) -> None:
    """Advertise this interactive session to child processes via the environment
    (inherited by anything launched from the shell-primary REPL), so a nested
    `xlii code`/`chat` can detect it and refuse same-project re-entry."""
    os.environ[XLII_SESSION_ENV] = str(os.getpid())
    if project_root is not None:
        os.environ[XLII_SESSION_PROJECT_ENV] = str(_Path(project_root).resolve())


def detect_nested_session(project_root: Optional[_Path] = None, *,
                          force: bool = False) -> NestedSessionVerdict:
    """Detect an outer xlii session via the inherited XLII_SESSION env vars.

    A second session for the SAME project shares on-disk state (attachments,
    workspaces, manifest) and races Collection syncs — changes can be silently
    lost — so it's refused unless `force`. Re-entry for a different project (or
    a project-less chat) only warns. Detection runs BEFORE any screen takeover
    so the face's message is actually visible (a post-launch nudge isn't)."""
    if not os.environ.get(XLII_SESSION_ENV):
        return NestedSessionVerdict("proceed")
    outer = os.environ.get(XLII_SESSION_ENV, "?")
    outer_proj = os.environ.get(XLII_SESSION_PROJECT_ENV, "")
    same = bool(project_root and outer_proj
                and _Path(outer_proj) == _Path(project_root).resolve())
    if same and not force:
        return NestedSessionVerdict("blocked", outer, True)
    if same:
        return NestedSessionVerdict("forced", outer, True)
    return NestedSessionVerdict("warn", outer, False)
