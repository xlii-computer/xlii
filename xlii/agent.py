"""Agent loop: Grok chat completion with tool-use, until no more tool calls.

Two flavors:
  - Agent       : main interactive agent. Full tool set incl. dispatch_subagent.
  - WorkerAgent : read-only investigator dispatched by the main agent.
                  Tools: read_file, list_dir, glob, grep, bash, search_project.
                  No write_file / edit_file / dispatch_subagent.
                  Returns a single string summary, no conversation history retained.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional, Sequence

from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.markdown import Markdown
from rich.text import Text

from xlii.agent_dispatch import _ToolDispatchMixin
from xlii.agent_render import (
    _streaming_tail,
)
from xlii.agent_stats import (
    TurnStats,
    _cache_headers,
    _detect_unsupported_claim,
)
from xlii.client import Clients
from xlii.config import GlobalConfig, ProjectConfig
from xlii.multimodal import model_supports_vision, prepare_user_turn
from xlii.pool import ClientPool
from xlii.debug_mode import DebugController
from xlii.mode_controller import DiscoveryController, ModeController, OpsController, PlanController
from xlii.rail import RailController
from xlii.tools import (
    ToolContext,
    dispatch_subagent_schema,
    tool_schemas,
)
from xlii.turn_prompt import (
    build_code_system_prompt,
    effective_system_prompt,
)
from xlii.turn_events import AssistantAnswer
from xlii.shell_run import styled_enabled

# Backward-compat re-exports: these symbols now live in the split modules, but
# external call sites still do `from xlii.agent import <name>` (the godzilla
# refactor's re-export-façade rule). Not used locally — kept for the façade.
from xlii.agent_render import _format_tool_preview  # noqa: F401
from xlii.agent_stats import CallStats  # noqa: F401
from xlii.session_state import SessionState  # noqa: F401 — façade + used below
from xlii.tools import WORKER_REGISTRY  # noqa: F401  (tests patch xlii.agent.WORKER_REGISTRY)
from xlii.turn_prompt import (  # noqa: F401
    DISCOVERY_MODE_PREAMBLE,
    OPS_MODE_PREAMBLE,
    PLAN_MODE_PREAMBLE,
    load_prompt,
)
from xlii.worker_agent import WorkerAgent  # noqa: F401


# --------------------------------------------------------------------------- #
#  Main agent
# --------------------------------------------------------------------------- #

def tool_iteration_cap(cfg: Any, *, chatish: bool) -> tuple[str, int]:
    """Return (config_knob_name, cap) for the orchestrator tool loop."""
    if chatish and hasattr(cfg, "max_chat_tool_iterations"):
        return "max_chat_tool_iterations", cfg.max_chat_tool_iterations
    return "max_tool_iterations", cfg.max_tool_iterations


def set_tool_iteration_cap(cfg: Any, *, chatish: bool, n: int) -> tuple[str, int]:
    """Set the active cap and return (knob_name, previous_value)."""
    knob, _ = tool_iteration_cap(cfg, chatish=chatish)
    old = getattr(cfg, knob)
    setattr(cfg, knob, n)
    return knob, old


def drain_btw_inbox_notes(session: Any, history: list) -> list[str]:
    """Fold queued ``/btw`` steering notes into *history* as one user message.

    Called at the turn loop's tool boundary (the same safe yield point
    ``request_cancel`` uses) — never mid-tool. The note is marked as an
    interjection so the model reads it as mid-task steering, not a new task.
    Returns the drained notes so a failed turn can restore them before rolling
    back history. Best-effort by construction: a session without the inbox
    (older fakes) is simply a no-op.
    """
    inbox = getattr(session, "btw_inbox", None)
    if not inbox:
        return []
    notes = [str(n).strip() for n in inbox if str(n).strip()]
    inbox.clear()
    if not notes:
        return []
    history.append({
        "role": "user",
        "content": "[steering — the user interjected mid-turn via /btw]\n"
                   + "\n".join(f"- {n}" for n in notes),
    })
    return notes


def drain_btw_inbox(session: Any, history: list) -> bool:
    """Backward-compatible boolean wrapper around :func:`drain_btw_inbox_notes`."""
    return bool(drain_btw_inbox_notes(session, history))


def _restore_btw_inbox(session: Any, notes: list[str]) -> None:
    """Put drained notes back at the front of the inbox when a turn is rolled back."""
    if not notes:
        return
    inbox = getattr(session, "btw_inbox", None)
    if inbox is None:
        return
    inbox[:0] = notes


def resolve_orchestrator_model(
    *,
    cfg: Any,
    model_override: Optional[str] = None,
    conversational: bool = False,
    howto_mode: bool = False,
    chat_tier: Optional[str] = None,
    user_message: Optional[str] = None,
) -> tuple[str, str]:
    """Resolve the model id and config role for the orchestrator slot.

    The orchestrator slot serves code (`orchestrator`), persona chat (`chat`),
    /howto help (`help`), or a sticky loadout/persona/skill pin
    (`model_override`). Howto wins over conversational when both are set so
    help stays on the cheap role. Returns ``(model_id, role)``.

    chat-tiers R1: when the role is ``chat`` and a ``chat_tier`` is set, the
    model comes from that tier's model profile (fast→economy, expert/heavy→
    reason) instead of the plain ``chat`` role. ``auto`` is routed per-message by
    :mod:`xlii.chat_router` — pass ``user_message`` so it can classify (without
    it, ``auto`` resolves to expert). A sticky ``model_override`` (persona/
    loadout/skill pin) still wins — pinning a model is deliberate. With no tier
    set, behavior is byte-identical to before.
    """
    if howto_mode:
        role = "help"
    elif conversational:
        role = "chat"
    else:
        role = "orchestrator"
    if model_override:
        return model_override, role
    if role == "chat" and chat_tier:
        from xlii.chat_tiers import resolve_tier_model

        tier_model = resolve_tier_model(cfg, chat_tier, user_message=user_message)
        if tier_model:
            return tier_model, role
    return cfg.get_model_for_role(role), role


def resolve_orchestrator_model_for_session(
    *,
    cfg: Any,
    agent: Any = None,
    state: Any = None,
) -> tuple[str, str]:
    """Resolve the orchestrator slot from live agent/session + optional REPL state."""
    session = getattr(agent, "session", None) if agent is not None else None
    conversational = bool(getattr(session, "conversational", False))
    howto_mode = bool(
        getattr(state, "howto_mode", False)
        if state is not None
        else getattr(session, "howto_mode", False)
    )
    override = getattr(agent, "model_override", None) if agent is not None else None
    chat_tier = getattr(session, "chat_tier", None) if session is not None else None
    return resolve_orchestrator_model(
        cfg=cfg,
        model_override=override,
        conversational=conversational,
        howto_mode=howto_mode,
        chat_tier=chat_tier,
    )


# SessionState lives in xlii.session_state (nested trust/attachments/meter/…);
# re-exported above for `from xlii.agent import SessionState`.


@dataclass
class Agent(_ToolDispatchMixin):
    pool: ClientPool
    project: ProjectConfig
    cfg: GlobalConfig
    history: list[dict[str, Any]] = field(default_factory=list)
    console: Console = field(default_factory=Console)
    # Unified mode slot (godzilla refactor Track A): at most one of plan / rail /
    # debug is active. Read via plan_mode / rail / debug delegating properties.
    active_mode: Optional[ModeController] = None
    # Session flags + attachments — single owner (see SessionState docstring).
    session: SessionState = field(default_factory=SessionState)
    # Optional gig larynx for this agent (persona oneshot on a keyless node).
    # PlanController.chat_backend still wins when a hired planner is active.
    chat_backend: Optional[Any] = None
    # Set in __post_init__ — the system prompt before any /doc attachments.
    # Kept separate so _effective_system_prompt can rebuild fresh each turn.
    base_system_prompt: str = ""
    # Cooperative stop (bg-default P0): set from another thread via
    # request_cancel(); run_turn honors it at tool boundaries only — never
    # mid-write — and clears it on every turn start so a stale request from
    # a turn that already ended can't cancel the next one.
    _cancel_requested: bool = field(default=False, repr=False)

    def request_cancel(self) -> None:
        """Ask the in-flight turn to stop at the next tool boundary (thread-safe:
        a bare bool flip). Also kills any live :func:`xlii.shell_run.capture`
        process group (agent ``bash`` or user shell sharing that runner).
        No-op if no turn is running — run_turn clears the flag."""
        self._cancel_requested = True
        try:
            from xlii.shell_run import kill_live_shell

            kill_live_shell()
        except Exception:
            # The cancel flag above is already set; killing the live shell is an extra courtesy on top of it.
            pass

    # --- delegating accessors (storage lives in self.session) ---
    @property
    def plan_mode(self) -> bool:
        return isinstance(self.active_mode, PlanController)

    @property
    def read_only_tool_palette(self) -> bool:
        """True when the active mode restricts tools to the read-only palette."""
        am = self.active_mode
        if am is None:
            return False
        if isinstance(am, (PlanController, DiscoveryController)):
            return True
        if isinstance(am, DebugController) and am.is_read_only_phase:
            return True
        if isinstance(am, RailController) and am.is_read_only_stage:
            return True
        return False

    @plan_mode.setter
    def plan_mode(self, v: bool) -> None:
        if v:
            self.set_mode(PlanController())
        elif isinstance(self.active_mode, PlanController):
            self.set_mode(None)

    @property
    def discovery_mode(self) -> bool:
        return isinstance(self.active_mode, DiscoveryController)

    @discovery_mode.setter
    def discovery_mode(self, v: bool) -> None:
        if v:
            self.set_mode(DiscoveryController())
        elif isinstance(self.active_mode, DiscoveryController):
            self.set_mode(None)

    @property
    def ops_mode(self) -> bool:
        return isinstance(self.active_mode, OpsController)

    @ops_mode.setter
    def ops_mode(self, v: bool) -> None:
        if v:
            self.set_mode(OpsController())
        elif isinstance(self.active_mode, OpsController):
            self.set_mode(None)

    @property
    def rail(self) -> Optional[RailController]:
        return self.active_mode if isinstance(self.active_mode, RailController) else None

    @rail.setter
    def rail(self, v: Optional[RailController]) -> None:
        if v is None:
            if isinstance(self.active_mode, RailController):
                self.set_mode(None)
        else:
            self.set_mode(v)

    @property
    def debug(self) -> Optional[DebugController]:
        return self.active_mode if isinstance(self.active_mode, DebugController) else None

    @debug.setter
    def debug(self, v: Optional[DebugController]) -> None:
        if v is None:
            if isinstance(self.active_mode, DebugController):
                self.set_mode(None)
        else:
            self.set_mode(v)

    def set_mode(self, new: Optional[ModeController]) -> None:
        """Single mutator for plan / rail / debug modes.

        Exactly one ``ModeController`` may be active. Entering a new mode clears
        siblings. Clearing (``None``) drops the active mode.
        """
        old = self.active_mode

        if old is not new and old is not None:
            old.on_exit(self)

        self.active_mode = None
        self.session.plan_mode = False

        if new is None:
            return

        self.active_mode = new
        if isinstance(new, PlanController):
            self.session.plan_mode = True

        new.on_enter(self)

    @property
    def howto_mode(self) -> bool:
        return self.session.howto_mode

    @howto_mode.setter
    def howto_mode(self, v: bool) -> None:
        self.session.howto_mode = v

    @property
    def image_preview_backend(self) -> str:
        return self.session.image_preview_backend

    @image_preview_backend.setter
    def image_preview_backend(self, v: str) -> None:
        self.session.image_preview_backend = v or "auto"

    @property
    def yolo(self) -> bool:
        return self.session.yolo

    @yolo.setter
    def yolo(self, v: bool) -> None:
        self.session.yolo = v

    @property
    def freeball(self) -> bool:
        return self.session.freeball

    @freeball.setter
    def freeball(self, v: bool) -> None:
        self.session.freeball = v

    @property
    def auto_approve(self) -> set[str]:
        return self.session.auto_approve

    @auto_approve.setter
    def auto_approve(self, v: set[str]) -> None:
        self.session.auto_approve = set(v)

    @property
    def next_turn_temp_override(self) -> Optional[float]:
        return self.session.next_turn_temp_override

    @next_turn_temp_override.setter
    def next_turn_temp_override(self, v: Optional[float]) -> None:
        self.session.next_turn_temp_override = v

    @property
    def model_override(self) -> Optional[str]:
        return self.session.model_override

    @model_override.setter
    def model_override(self, v: Optional[str]) -> None:
        self.session.model_override = v

    @property
    def temperature_override(self) -> Optional[float]:
        return self.session.temperature_override

    @temperature_override.setter
    def temperature_override(self, v: Optional[float]) -> None:
        self.session.temperature_override = v

    @property
    def attached_refs(self) -> list[tuple[str, str]]:
        return self.session.attached_refs

    @attached_refs.setter
    def attached_refs(self, v: list[tuple[str, str]]) -> None:
        self.session.attached_refs = v

    @property
    def attached_docs(self) -> list[tuple[str, str]]:
        return self.session.attached_docs

    @attached_docs.setter
    def attached_docs(self, v: list[tuple[str, str]]) -> None:
        self.session.attached_docs = v

    def _turn_backend(self) -> Optional[Any]:
        """Chat brain for this turn: hired planner first, else this agent's gig."""
        am = self.active_mode
        if am is not None:
            hired = getattr(am, "chat_backend", None)
            if hired is not None:
                return hired
        return getattr(self, "chat_backend", None)

    def __post_init__(self) -> None:
        if not self.history:
            # Per-project override: .xlii/prompts/main.md shadows the package prompt.
            self.base_system_prompt = build_code_system_prompt(self.project)
            self.history.append({"role": "system", "content": self._effective_system_prompt()})
        else:
            # History is pre-populated (e.g. persona chat with last-N turns
            # already loaded). Capture history[0] as the base so /doc re-renders
            # work cleanly.
            if self.history and self.history[0].get("role") == "system":
                self.base_system_prompt = self.history[0]["content"]

    def _effective_system_prompt(self) -> str:
        """Base system prompt + any attached /doc content + the active mode
        directive. Rebuilt each turn in run_turn() so /doc, /undoc, and mode
        changes take effect immediately without replaying history.

        Mode directives live here (not on the user message) so they apply only
        while active — baking them into history meant an approved plan's
        execution turns still carried "you are in plan mode, do not write"
        text from earlier turns."""
        mode_directive = (
            self.active_mode.get_system_directive()
            if self.active_mode is not None
            else None
        )
        from xlii.project_rules import current_scope_paths, rules_addendum

        base = self.base_system_prompt
        note = getattr(self.session, "budget_note", None)
        if note:
            base = base + (
                f"\n\n[BUDGET] {note} Factor this into any costly action you propose "
                "(large swarms, loops, big context). You cannot change the account."
            )
        project = getattr(self, "project", None)
        if project is not None:
            scope = current_scope_paths(self.session, project.project_root)
            rules = rules_addendum(project.xli_dir, scope)
            if rules:
                base = base + "\n\n" + rules
        return effective_system_prompt(
            base, self.attached_docs, mode_directive
        )

    @property
    def clients(self) -> Clients:
        return self.pool.primary()

    def _model_role(self) -> str:
        """The config model role for this turn's orchestrator slot.

        - code → ``orchestrator``
        - persona chat → ``chat``
        - /howto overlay → ``help`` (cheap; not the chat flagship)

        A persona/loadout ``model_override`` still wins over this (resolved at
        the call site for the model id; role string still reflects the surface).
        """
        _, role = resolve_orchestrator_model(
            cfg=self.cfg,
            model_override=self.model_override,
            conversational=self.session.conversational,
            howto_mode=self.session.howto_mode,
        )
        return role

    def orchestrator_model_and_role(
        self, user_message: Optional[str] = None,
        tier_override: Optional[str] = None,
    ) -> tuple[str, str]:
        """Active orchestrator-slot model and the config role it resolved from.

        *user_message* lets the ``auto`` chat tier route per-message; callers
        without a message in hand (status line, vision-gate probes) get the
        no-message default (auto→expert). *tier_override* is the one-shot
        ``>>tier`` sigil (Vector D) — it outranks the sticky session tier for
        this resolution only.
        """
        return resolve_orchestrator_model(
            cfg=self.cfg,
            model_override=self.model_override,
            conversational=self.session.conversational,
            howto_mode=self.session.howto_mode,
            chat_tier=tier_override or getattr(self.session, "chat_tier", None),
            user_message=user_message,
        )

    def run_turn(
        self, user_message: str, attachments: Optional[Sequence[Any]] = None,
        tier_text: Optional[str] = None,
        cancelled: Optional[Callable[[], bool]] = None,
    ) -> tuple[str, set[str], TurnStats]:
        # A stop requested against a previous turn (or against a shell command,
        # which can't honor it) must not cancel THIS turn.
        self._cancel_requested = False

        def _cancel_requested() -> bool:
            if self._cancel_requested:
                return True
            if cancelled is not None and cancelled():
                self._cancel_requested = True
                return True
            return False

        # chat-tiers: the raw user intent, captured before the shell-context /
        # plan-refresher prefixes are folded in below, so the `auto` tier's
        # router classifies what the user actually typed. ``tier_text`` is the
        # same capture for callers whose message arrives PRE-augmented (the
        # persona one-shot fuses journal+wiki ambient ahead of the prompt) —
        # without it the router classifies the injected context (URLs in a
        # journal dump read as a fresh-data ask → a greeting fanned out into
        # a heavy deep search).
        _raw_user_message = tier_text if tier_text is not None else user_message
        resolved_chat_tier = None
        deep_search_turn = False
        _sigil_tier = None
        if self.active_mode is None and self.session.conversational:
            from xlii.chat_tiers import (
                STRATEGY_DEEP_SEARCH,
                resolve_tier,
                split_tier_sigil,
            )

            # `>>tier message` (Vector D): a one-shot override — wins over the
            # sticky /tier for THIS message, stripped so the model never sees
            # the sigil. Recognized only where the tier machinery consumes it
            # (a conversational turn); elsewhere the text passes through intact.
            # Split on the RAW prompt: in a pre-augmented message the sigil
            # sits at the head of the prompt, which is the TAIL of the blob.
            _sigil_tier, _stripped = split_tier_sigil(_raw_user_message)
            if _sigil_tier is not None:
                if user_message.endswith(_raw_user_message):
                    user_message = (
                        user_message[: len(user_message) - len(_raw_user_message)]
                        + _stripped
                    )
                else:
                    user_message = _stripped
                _raw_user_message = _stripped
                self.console.print(
                    f"[dim]tier ↑ {_sigil_tier} (this message)[/dim]"
                )
            resolved_chat_tier = resolve_tier(
                _sigil_tier or getattr(self.session, "chat_tier", None),
                user_message=_raw_user_message,
                cfg=self.cfg,
            )
            deep_search_turn = (
                resolved_chat_tier is not None
                and resolved_chat_tier.strategy == STRATEGY_DEEP_SEARCH
            )
        # Path-scoped write profile (plan-write-domain P0) — DERIVED from the
        # active mode every turn, never stored, so there is no flag to forget:
        # plan mode writes only under <xli_dir>/plans/ (planning IS writing);
        # every other profile denies plans/ (the implementer can't edit the
        # spec — plan_check is its one sanctioned write there).
        if self.active_mode is not None:
            # getattr default: fakes / third-party controllers may predate the
            # protocol method (same posture as is_instrument_phase below).
            _write_scope = getattr(self.active_mode, "write_scope", None)
            write_allow, write_deny = (
                _write_scope(self.project.xli_dir)
                if callable(_write_scope) else (None, ())
            )
        else:
            _plans = (Path(self.project.xli_dir) / "plans").resolve()
            write_allow, write_deny = None, (str(_plans),)
        # Refresh the system prompt so /doc and /undoc take effect immediately.
        # Cheap when no docs are attached (string identity check); rebuilds
        # only when the attached set changed.
        if self.history and self.history[0].get("role") == "system":
            sys_content = self._effective_system_prompt()
            if write_allow:
                # Name the REAL writable domain — keyed off the derived profile,
                # never the mode: under state_dir_override / a symlinked .xlii
                # the plans dir is NOT <project_root>/.xlii/plans, and the model
                # must be told the path that will actually be accepted.
                sys_content += (
                    "\n\nWritable domain this turn: "
                    + ", ".join(write_allow)
                    + " — write your plan file(s) there; writes anywhere else "
                    "are refused."
                )
            self.history[0] = {"role": "system", "content": sys_content}

        if self.active_mode is None and self.session.conversational:
            try:
                from xlii.door_tools import register as register_door_tools

                register_door_tools()
            except Exception:
                pass
        if self.active_mode is not None:
            schemas = self.active_mode.tool_schemas(self.project.xli_dir)
            # Plan-surface T2: a hired planner never sees xAI-plane tools —
            # the same schema-time capability strip WorkerAgent applies to gig
            # workers (grep/glob/read remain: degraded search, not blindness).
            _mode_backend = getattr(self.active_mode, "chat_backend", None)
            if _mode_backend is not None:
                schemas = [
                    s for s in schemas
                    if _mode_backend.allows_tool(s["function"]["name"])
                ]
        else:
            schemas = tool_schemas() + [dispatch_subagent_schema()]
            # chat-tiers: offer the deep-search escape hatch only to turns that
            # resolved to expert. Resolved heavy runs the coordinator
            # automatically below; resolved fast must stay cheap.
            if (
                self.session.conversational
                and resolved_chat_tier is not None
                and resolved_chat_tier.name == "expert"
            ):
                from xlii.chat_router import request_deep_search_schema

                schemas = schemas + [request_deep_search_schema()]
        # howto-fast T2 — the /howto overlay is a Q&A palette, not a coding one:
        # read-only investigation plus command_help (the pull side of howto's
        # compact command index) / xai_docs / request_deep_search. Gated ahead
        # of the chat block because howto is entered from BOTH surfaces and is
        # the narrower, more specific contract: on a chat desk its allowlist
        # must win, or the model loses command_help exactly where it needs it.
        # Same shape as the chat gate — only the controllerless case, so
        # controller modes keep their curated palettes untouched.
        if self.active_mode is None and self.session.howto_mode:
            from xlii.mode_contract import howto_tools_policy

            policy = howto_tools_policy()
            schemas = [
                s for s in schemas if policy.permits(s["function"]["name"])
            ]
        # V3b — chat is the safe REPL: the chat surface's capability profile
        # (mode_contract) strips write tools + subagent dispatch outright.
        # Project-blind by default; /chat --read-proj widens the session to
        # the read-only palette. K1 then re-adds dispatch_subagent when the
        # caller armed session.hire (read or write) — Mojo stays project-blind;
        # the worker is her hands. hire="none" keeps talk-only (live /chat).
        # Occupancy sitting is not this gate. Only the chat surface
        # (conversational, no controller) is gated — code, controller modes,
        # and non-chat sessions are untouched. `elif`: a howto turn already
        # took the narrower Q&A palette above, and hiring a worker is not a
        # help question — /howto off returns the desk to Mojo's hands.
        elif self.active_mode is None and self.session.conversational:
            from xlii.mode_contract import chat_tools_policy

            policy = chat_tools_policy(
                read_proj=getattr(self.session, "chat_read_proj", False)
            )
            schemas = [
                s for s in schemas if policy.permits(s["function"]["name"])
            ]
            if self.session.hire != "none":
                schemas += [dispatch_subagent_schema()]
        # K6 — door tools only when CapabilityProfile.door_tools permits.
        # Chat (conversational, no controller) allows CHAT_DOOR_TOOLS; code
        # posture without a controller must not advertise them even if they
        # remain registered from an earlier talk turn. The howto overlay is
        # its own posture on the same axis (howto-fast T2): it keeps
        # explain_xlii — asking the shipped self-doc IS the help question —
        # and drops the five doors that move the desk or write.
        from xlii.mode_contract import CHAT_DOOR_TOOLS, get_mode

        if self.active_mode is None and self.session.howto_mode:
            door_policy = get_mode("howto").capabilities.door_tools
        elif self.active_mode is None and self.session.conversational:
            door_policy = get_mode("chat").capabilities.door_tools
        else:
            door_policy = get_mode("code").capabilities.door_tools
        schemas = [
            s for s in schemas
            if s["function"]["name"] not in CHAT_DOOR_TOOLS
            or door_policy.permits(s["function"]["name"])
        ]
        # Hide plugin_search / plugin_get when no plugins are subscribed —
        # otherwise the agent has tools that always return NO_PLUGIN_MATCH.
        from xlii.plugin import live_subscriptions as _live_subs
        if not _live_subs(self.project, self.session):
            schemas = [
                s for s in schemas
                if s["function"]["name"] not in {"plugin_search", "plugin_get"}
            ]
        # Hide read_email / search_email / send_email when no account is
        # configured — otherwise chat burns its tight iteration cap retrying
        # a guaranteed "no email accounts" fail.
        from xlii.tool_schemas import (
            apply_email_account_gate,
            apply_outbox_gate,
            apply_xai_docs_gate,
        )
        schemas = apply_email_account_gate(schemas, self.cfg)
        schemas = apply_xai_docs_gate(schemas, self.cfg)
        # Media-out: send_file exists exactly when the session has an outbox —
        # the daemon's `ask --outbox` grant, or a local surface's own (TUI /
        # inline; xlii/outbox.py). The grant deliberately runs AFTER the
        # chat-surface strip (delivery goes only to the peer already in this
        # conversation) but NEVER past a mode controller's curated palette:
        # with an outbox on every local session, an unconditional grant would
        # inject paid generate_image into /plan's read-only palette. A mode
        # turn passes None — the gate then only STRIPS a dead send_file.
        schemas = apply_outbox_gate(
            schemas,
            getattr(self.session, "outbox_dir", None)
            if self.active_mode is None else None)
        # Gig larynx (persona oneshot on a keyless node, or a hired planner):
        # strip xAI-plane tools the foreign brain cannot call.
        turn_backend = self._turn_backend()
        if turn_backend is not None:
            schemas = [
                s for s in schemas
                if turn_backend.allows_tool(s["function"]["name"])
            ]
        # Dispatch-time palette enforcement (plan-write-domain P0): exactly the
        # tool names advertised to the model THIS turn. A call to anything else
        # is refused at dispatch — belt-and-suspenders under the mode gating,
        # mirroring WorkerAgent's role boundary.
        advertised_tools = {s["function"]["name"] for s in schemas}

        # Live-shell awareness (proposals/done/flipmode.md, Phase 2): when the user's
        # terminal has wandered outside the project, tell the model where it
        # physically is. Injected into the USER message — NOT the cached system
        # prompt — and consumed one-shot so it never accumulates in history.
        user_shell_cwd = getattr(self.session, "user_shell_cwd", None)
        if user_shell_cwd is not None:
            user_message = (
                f"[shell context: my terminal is currently in {user_shell_cwd}, "
                f"outside the project root {self.project.project_root}. Your file "
                f"and bash tools still operate on the project; if I'm asking about "
                f"the current directory rather than the project, say so — you may "
                f"suggest `xlii init` to make it its own project.]\n\n{user_message}"
            )
            self.session.user_shell_cwd = None

        # One-shot plan refresher (/plan continue): fold the saved plan into THIS
        # user turn once, then clear. It enters history as part of this turn and
        # ages out normally — a reminder, not a per-turn /doc attachment.
        plan_refresher = getattr(self.session, "pending_plan_refresher", None)
        if plan_refresher:
            user_message = (
                f"[Resuming a plan I saved earlier — here it is for reference:]\n\n"
                f"{plan_refresher}\n\n"
                f"[Pick up from this plan for what follows.]\n\n{user_message}"
            )
            self.session.pending_plan_refresher = None

        # Multimodal turn build: live image attachments make `content` a parts
        # array (text + image_url); text-only turns stay a plain string, so
        # behavior is byte-identical when nothing is attached. The base64 payload
        # is dropped from history after the turn (see the finally below).
        prepared = prepare_user_turn(user_message, attachments)
        # Vision gate: sending an image to a model that can't see it gets no
        # server-side rejection — the request just stalls (looks like a hang). So
        # resolve the orchestrator model up front and, if it's not vision-capable,
        # omit the images, tell the user, and note it so the model doesn't guess.
        orch_model, orch_role = self.orchestrator_model_and_role(
            _raw_user_message, tier_override=_sigil_tier
        )
        send_images = prepared.has_images and model_supports_vision(orch_model)
        if prepared.has_images and not send_images:
            if self.model_override:
                hint = (
                    "Switch the pinned session model to a vision model (grok-4 family), e.g. "
                    "`/model grok-4`, or apply `/model --profile vision`."
                )
            else:
                # Point the fix at the slot actually in play: chat turns use
                # `chat`, howto uses `help` — `--orchestrator` would do nothing
                # on those surfaces. The vision profile sets the main roles.
                if orch_role == "chat":
                    hint = (
                        "Switch the chat model to a vision model (grok-4 family), e.g. "
                        "`xlii models set --chat grok-4`, or apply the vision profile."
                    )
                elif orch_role == "help":
                    hint = (
                        "Howto is on the help role — pin a vision-capable "
                        "`help_model` in config.json, or leave howto and use a "
                        "vision chat/orchestrator model."
                    )
                else:
                    hint = (
                        "Switch the orchestrator model to a vision model (grok-4 family), e.g. "
                        "`xlii models set --orchestrator grok-4`, or apply the vision profile."
                    )
            self.console.print(
                f"[yellow]⚠ {orch_model} can't see images — they were omitted.[/yellow] "
                f"[dim]{hint}[/dim]"
            )
            user_content: Any = (
                prepared.compact
                + "\n\n[System note: image attachment(s) were omitted — the active "
                "model cannot process images. Do not guess at their contents; tell "
                "the user to switch to a vision-capable model.]"
            )
        else:
            user_content = prepared.outgoing
        user_idx = len(self.history)
        self.history.append({"role": "user", "content": user_content})
        self._current_turn_question = _raw_user_message
        from xlii.plugin import live_subscriptions
        subs = live_subscriptions(self.project, self.session)
        ctx = ToolContext(
            project=self.project,
            clients=self.clients,
            cfg=self.cfg,
            pool=self.pool,
            console=self.console,
            yolo=self.yolo,
            plan_mode=self.read_only_tool_palette,
            auto_approve=frozenset(self.auto_approve),
            subscribed_plugins=subs,
            user_shell_cwd=user_shell_cwd,
            loop_lock_tests=getattr(self.session, "loop_lock_tests", False),
            outbox_dir=getattr(self.session, "outbox_dir", None),
            debug_instrument=getattr(self.active_mode, "is_instrument_phase", False),
            write_allow=tuple(write_allow) if write_allow is not None else None,
            write_deny=tuple(write_deny),
            open_plugin_form=getattr(self.session, "open_plugin_form", None),
            sitting=getattr(self, "sitting", None),
            emit_door=getattr(self.session, "emit_door", None),
            door_surface=getattr(self.session, "door_surface", "") or "",
        )
        stats = TurnStats()
        drained_btw_notes: list[str] = []
        # Orchestrator model: a persona's sticky model_override (loadout) wins
        # over the configured role model (resolved above as orch_model for the
        # vision gate). Workers always use the config role.
        stats.orch.model = orch_model
        stats.workers.model = self.cfg.get_model_for_role("worker")
        model = stats.orch.model
        cache_hdrs = _cache_headers(self.project.conversation_id)
        # Plan-surface T2 / node gig larynx: a ChatBackend drives this turn's
        # orchestrator calls — its model and endpoint replace the home plane
        # for exactly these turns. /execute replaces the controller, so
        # execution is home-plane by construction.
        mode_backend = self._turn_backend()
        if mode_backend is not None and getattr(mode_backend, "model", ""):
            model = mode_backend.model
            stats.orch.model = model

        try:
            if deep_search_turn:
                call_id = f"auto_deep_search_{user_idx}"
                reason = "resolved chat tier requires coordinated deep search"
                self.history.append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "request_deep_search",
                            "arguments": json.dumps({"reason": reason}),
                        },
                    }],
                })
                digest = self._run_deep_search(
                    {"reason": reason}, question=_raw_user_message,
                )
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": digest,
                })
                stats.tool_calls += 1
        except Exception:
            self._current_turn_question = ""
            # Failed before the model ever answered — roll the turn out of
            # history (see the main-loop except below for the full contract).
            if plan_refresher:
                self.session.pending_plan_refresher = plan_refresher
            del self.history[user_idx:]
            raise

        # Resolve temperature for this turn: one-shot /temp override wins, then a
        # sticky persona temperature_override (loadout), else the configured temp.
        slot_role = self._model_role()
        # help shares chat temperature (no separate help_temperature knob).
        _chatish = slot_role in ("chat", "help")
        if self.next_turn_temp_override is not None:
            chat_only = getattr(self.session, "next_turn_temp_chat_only", False)
            if not chat_only or _chatish:
                temperature = self.next_turn_temp_override
                self.next_turn_temp_override = None
                self.session.next_turn_temp_chat_only = False
            elif self.temperature_override is not None:
                temperature = self.temperature_override
            else:
                # Reached only when the override is chat-only and this turn is
                # not chat-ish, so the orchestrator temp is the only option.
                temperature = self.cfg.orchestrator_temp()
        elif self.temperature_override is not None:
            temperature = self.temperature_override
        elif _chatish:
            temperature = self.cfg.chat_temp()
        else:
            temperature = self.cfg.orchestrator_temp()

        # Chat/persona turns get a tighter tool-loop cap than coding turns: a
        # conversational reply (the phone mouth, /mojo) should answer fast, not
        # spiral through the full coding budget of tool round-trips.
        _iter_knob, _iter_cap = tool_iteration_cap(self.cfg, chatish=_chatish)
        try:
            for _ in range(_iter_cap):
                if _cancel_requested():
                    # Stop requested while the last batch ran — history ends
                    # with tool results, a valid resume point for the next turn.
                    self._cancel_requested = False
                    return ("(turn cancelled — stopped at a tool boundary)",
                            ctx.dirty_paths, stats)
                # /btw steering (bg-default P2): fold queued user notes into
                # history at this same safe yield point, so the next model
                # step sees them mid-task.
                drained_btw_notes.extend(drain_btw_inbox_notes(self.session, self.history))
                stats.orch.iterations += 1
                msg, usage, streamed = self._stream_orchestrator_iteration(
                    model=model,
                    schemas=schemas,
                    temperature=temperature,
                    cache_hdrs=cache_hdrs,
                    backend=mode_backend,
                )
                if usage is not None:
                    stats.orch.absorb_usage(usage, model, self.cfg.pricing)
                    # Keep the LAST call's prompt size as live context occupancy
                    # (absorb_usage *sums* into orch; this overwrites to the latest,
                    # so after the loop it holds the most recent call's figures).
                    stats.context_tokens = usage.prompt_tokens
                    _cached = getattr(
                        getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0
                    ) or 0
                    stats.cached_tokens = int(_cached)

                entry: dict[str, Any] = {"role": "assistant"}
                if msg.content:
                    entry["content"] = msg.content
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                self.history.append(entry)

                if not msg.tool_calls:
                    # Inspect the actual content (not the empty string we'll return
                    # if streamed) so the claim detector still works post-stream.
                    # Read-only mode stages/phases are prose-only by design and
                    # legitimately call zero tools — don't flag them.
                    from xlii.turn_receipt import claim_gates_mode
                    suppress_claim = (
                        (self.active_mode is not None
                         and self.active_mode.suppresses_claim_check)
                        or claim_gates_mode(self.cfg) == "off"
                    )
                    claim = None if suppress_claim else _detect_unsupported_claim(
                        msg.content or "", stats, ctx.dirty_paths
                    )
                    if claim is not None:
                        stats.warnings.append(claim)
                    # Content was already streamed live; return empty text so the
                    # REPL doesn't print it again. The history still holds the real
                    # content for the next turn's context.
                    return ("" if streamed else (msg.content or ""), ctx.dirty_paths, stats)

                if _cancel_requested():
                    # Stop requested during the model stream: skip the batch, but
                    # answer every declared call so history stays API-valid (an
                    # assistant tool_calls entry must have matching tool results).
                    for tc in msg.tool_calls:
                        self.history.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": "(cancelled by user before execution)",
                        })
                    self._cancel_requested = False
                    return ("(turn cancelled — stopped before the next tool step)",
                            ctx.dirty_paths, stats)
                # Bracket this step's tool calls so a styled surface (the TUI)
                # can fold THIS batch into its own collapsible drawer — one per
                # model step, with reasoning left loose between them. Duck-typed
                # + best-effort: a plain rich Console has neither method, so the
                # inline REPL renders exactly as before.
                _begin_group = getattr(self.console, "begin_tool_group", None)
                if callable(_begin_group):
                    try:
                        _begin_group()
                    except Exception:
                        # Tool-group framing is console decoration; the tool calls still run.
                        pass
                try:
                    self._execute_tool_batch(
                        msg.tool_calls, ctx, stats, allowed_tools=advertised_tools
                    )
                finally:
                    _end_group = getattr(self.console, "end_tool_group", None)
                    if callable(_end_group):
                        try:
                            _end_group()
                        except Exception:
                            # Tool-group framing is console decoration; the turn still completes.
                            pass

                # Drain server-tool sub-call usage (web_search / x_search / code_execute)
                # into orchestrator stats. Cost is pre-computed by ToolContext.
                in_t, out_t, cost, n = ctx.drain_server_usage()
                if ctx.server_cost_unknown:
                    stats.warnings.append(
                        "server-tool model missing from pricing table — displayed cost "
                        "is a lower bound, not $0"
                    )
                    ctx.server_cost_unknown = False
                if n:
                    stats.orch.absorb_server_tool(in_t, out_t, cost)
                    stats.server_tool_calls += n

            return (
                f"(stopped: hit {_iter_knob} — bump it in config if needed)",
                ctx.dirty_paths,
                stats,
            )
        except Exception:
            # Turn-failure rollback: a raised turn (API 5xx, tool crash) must
            # not leave its user message — or partial assistant/tool entries —
            # in history. The error box says "Try again"; if the ghost stays, a
            # LATER successful turn (even on another surface, e.g. howto)
            # answers the stale question — the 2026-07-16 outage symptom.
            # Exception only, deliberately not BaseException: an INTERRUPT
            # keeps the partial turn so /btw steering keeps its referent
            # (repair_interrupted_history restores API validity instead).
            # Restore /btw notes before deleting their history copy. The inbox
            # was cleared when they were folded in, so rollback without restore
            # would silently lose user steering. Same for /plan continue's
            # one-shot refresher: it was cleared before the rolled-back user
            # message entered history, so a retry must get the plan again.
            _restore_btw_inbox(self.session, drained_btw_notes)
            if plan_refresher:
                self.session.pending_plan_refresher = plan_refresher
            del self.history[user_idx:]
            raise
        finally:
            self._current_turn_question = ""
            # One-shot freeball (`/yolo --freeball <task>`): this turn ran
            # gates-down; restore the prior tier now the turn is over. In the
            # finally so an interrupted or failed turn still reverts — leaving the
            # gates down would be the dangerous failure mode. No-op for the session
            # TOGGLE form and every normal turn (freeball_restore stays None).
            restore = getattr(self.session, "freeball_restore", None)
            if restore is not None:
                self.session.trust_tier = restore
                self.session.freeball_restore = None

            from xlii.project_rules import extend_scope_from_dirty

            project = getattr(self, "project", None)
            if project is not None:
                extend_scope_from_dirty(
                    self.session, ctx.dirty_paths, project.project_root
                )
            # Drop the base64 image payload from history once the turn is done, so
            # it isn't re-sent on every later turn; leave a text placeholder. The
            # locker re-injects live files next turn if still enabled (U0). Only
            # needed when images were actually sent (send_images).
            if send_images and user_idx < len(self.history):
                self.history[user_idx]["content"] = prepared.compact

    # ------------------------------------------------------------------ #

    def _stream_orchestrator_iteration(
        self,
        *,
        model: str,
        schemas: list,
        temperature: float,
        cache_hdrs: Optional[dict],
        backend: Optional[Any] = None,
    ) -> tuple[Any, Any, bool]:
        """One orchestrator chat-completions call, streamed.

        Streams content deltas live to the terminal. Tool-call deltas are
        accumulated silently — the user sees discrete tool events (with
        previews) in the next phase. Returns (msg, usage, streamed_text)
        in the same shape the non-streaming code expects:
        msg.content / msg.tool_calls[i].function.name / .arguments / .id

        `streamed_text` is True iff any content was printed live; the caller
        uses this to suppress double-printing in the REPL.
        """
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=self.history,
            tools=schemas,
            tool_choice="auto",
            temperature=temperature,
            stream=True,
            stream_options={"include_usage": True},
        )
        if cache_hdrs:
            kwargs["extra_headers"] = cache_hdrs

        # Plan-surface T2: a hired planner's backend replaces the home client
        # for this call. backend.create carries the worker-path guarantees —
        # xAI cache headers stripped, provider temperature pins, cache marks.
        if backend is not None:
            stream = backend.create(**kwargs)
        else:
            stream = self.clients.chat.chat.completions.create(**kwargs)

        content_parts: list[str] = []
        # Reasoning models (grok-4.20-reasoning, etc.) emit private "thinking"
        # tokens in delta.reasoning_content separately from the user-facing
        # answer in delta.content. Capture them so we can surface them when
        # the model produces reasoning without a final content segment.
        reasoning_parts: list[str] = []
        tool_buf: dict[int, dict[str, str]] = {}
        usage: Any = None
        live: Optional[Live] = None
        streamed_any = False

        try:
            for chunk in stream:
                # Final usage chunk arrives once stream_options.include_usage is set.
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # Reasoning content from reasoning models. Don't render live —
                # treat it as private thinking. Surface it after the stream
                # ends *only if* no actual content arrived (diagnostic mode).
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    reasoning_parts.append(reasoning)

                if getattr(delta, "content", None):
                    content_parts.append(delta.content)
                    # Kernel Conversation live row (Phase 6): duck-typed hook
                    # installed by drive_turn on the console — never import the
                    # TUI from here; main-log streaming below is unchanged.
                    _chunk_cb = getattr(self.console, "on_content_chunk", None)
                    if _chunk_cb is not None:
                        try:
                            _chunk_cb(delta.content)
                        except Exception:
                            # A failing stream consumer must not abort the turn -- content is still accumulated
                            # below.
                            pass
                    # supports_live is absent on real rich Consoles (→ True); only
                    # the Textual transcript console sets it False, since Live's
                    # cursor control can't drive a widget. There, we skip the live
                    # preview and the final answer is rendered once by the caller.
                    if live is None and getattr(self.console, "supports_live", True):
                        # Blank line before the answer; open the Live widget
                        # lazily so iterations with only tool_calls emit
                        # nothing visible from this helper.
                        self.console.print()
                        live = Live(
                            Text(""),
                            console=self.console,
                            refresh_per_second=10,
                            # crop = Live never paints beyond the viewport, so it
                            # only ever addresses lines it can actually redraw;
                            # transient = wipe the preview when the stream ends so
                            # the single final Markdown render below is all that
                            # stays in scrollback. Together these kill the
                            # duplicate-text bug that "visible" + full re-render hit
                            # on any answer taller than the terminal.
                            vertical_overflow="crop",
                            transient=True,
                        )
                        live.start()
                        streamed_any = True
                    if live is not None:
                        def _live_frame(body_text: str) -> Panel:
                            _mode, _mcolor, _role = getattr(
                                self, "turn_record", ("", "", "")
                            )
                            _chip = _mode
                            if _role:
                                _chip = f"{_chip} · {_role}" if _chip else _role
                            # The Panel below adds 2 border rows and re-wraps
                            # the body at width-4 (borders + padding) — charge
                            # the tail for both so the framed region fits the
                            # viewport and Live's crop can't hide the newest
                            # tokens.
                            _body = _streaming_tail(
                                body_text, self.console,
                                frame_rows=2, frame_cols=4,
                            )
                            if _mode:
                                _color = _mcolor or "cyan"
                                return Panel(
                                    _body,
                                    title=Text(f"┤ {_chip} ├", style=f"bold {_color}"),
                                    title_align="right",
                                    border_style=_color,
                                    box=box.ROUNDED,
                                )
                            _title = Text()
                            _title.append("xlii", style="cyan bold")
                            if model:
                                _title.append(f" · {model}", style="grey50")
                            return Panel(
                                _body,
                                title=_title,
                                title_align="left",
                                border_style="cyan",
                                box=box.ROUNDED,
                            )

                        live.update(_live_frame("".join(content_parts)))

                if getattr(delta, "tool_calls", None):
                    for tcd in delta.tool_calls:
                        idx = tcd.index
                        buf = tool_buf.setdefault(
                            idx, {"id": "", "name": "", "arguments": ""}
                        )
                        if getattr(tcd, "id", None):
                            buf["id"] = tcd.id
                        fn = getattr(tcd, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                buf["name"] += fn.name
                            if getattr(fn, "arguments", None):
                                buf["arguments"] += fn.arguments
        finally:
            if live is not None:
                # The transient preview is wiped on stop; emit the complete
                # answer once, fully rendered. A single print scrolls naturally
                # and — unlike the old growing-buffer Live re-render — cannot
                # duplicate, no matter how long the answer is. Runs in `finally`
                # so an interrupted stream still flushes what arrived.
                live.stop()
                if content_parts:
                    md = "".join(content_parts)
                    if styled_enabled():
                        # Frame the answer in the unified grammar (assistant rule
                        # + Markdown). streamed=False so the renderer prints it —
                        # the transient live preview above was already wiped.
                        _mode, _mcolor, _role = getattr(self, "turn_record", ("", "", ""))
                        self._renderer().emit(
                            AssistantAnswer(markdown=md, model=model, streamed=False,
                                            mode=_mode, mode_color=_mcolor, role=_role)
                        )
                    else:
                        self.console.print(Markdown(md))
            elif content_parts and tool_buf:
                # No live preview (the Textual transcript, supports_live=False) and
                # this step ALSO narrated why it's calling these tools. Surface that
                # interim reasoning as a loose block so it shows BETWEEN the folded
                # tool groups — parity with the inline REPL, which streams it live.
                # Only when tool_buf is set: the final answer (no tool calls) is
                # rendered once by the turn's render slice, so printing it here too
                # would double it. Best-effort — a render hiccup must not fail the turn.
                interim = "".join(content_parts).strip()
                if interim:
                    try:
                        self.console.print(Markdown(interim))
                    except Exception:
                        # The interim render is cosmetic; the accumulated text is returned regardless.
                        pass

        # If a reasoning model produced thinking tokens but no actual content
        # and no tool_calls, the user would see nothing — surface the
        # reasoning so the failure mode is diagnostic rather than silent.
        if reasoning_parts and not content_parts and not tool_buf:
            # NOTE: no local `from rich.panel import Panel` here — a function-local
            # import would shadow the module-level Panel for the WHOLE method and
            # leave `_live_frame`'s closure reading an unbound cell (NameError on
            # the first streamed chunk). Use the module import (line 22).
            from rich.markdown import Markdown as _Markdown
            reasoning_text = "".join(reasoning_parts).strip()
            self.console.print()
            self.console.print(
                Panel(
                    _Markdown(reasoning_text) if reasoning_text else "[dim](empty reasoning)[/dim]",
                    title="[yellow]reasoning only — no final answer was produced[/yellow]",
                    border_style="yellow",
                    padding=(0, 1),
                )
            )
            self.console.print(
                "[dim]The reasoning model thought through the question but "
                "didn't emit a final answer. Try rephrasing, or ask a "
                "follow-up to push it past the reasoning phase.[/dim]"
            )
            streamed_any = True  # suppress empty-text re-print in REPL

        content = "".join(content_parts) or None
        tool_calls: Optional[list[Any]] = None
        if tool_buf:
            tool_calls = []
            for idx in sorted(tool_buf.keys()):
                buf = tool_buf[idx]
                tool_calls.append(
                    SimpleNamespace(
                        id=buf["id"],
                        type="function",
                        function=SimpleNamespace(
                            name=buf["name"],
                            arguments=buf["arguments"],
                        ),
                    )
                )
        msg = SimpleNamespace(content=content, tool_calls=tool_calls)
        return msg, usage, streamed_any

    # ------------------------------------------------------------------ #

    def _renderer(self):
        """A Renderer bound to THIS agent's console (the Agent makes its own
        Console, so the module singleton would write to a different stream).
        Cached, rebuilt if the console is swapped."""
        from xlii.ui import Renderer
        r = getattr(self, "_renderer_cache", None)
        if r is None or r.console is not self.console:
            # The TUI transcript console flags itself compact so tool/shell
            # bodies render as a tight ~8-line preview instead of the roomy
            # inline-REPL default; the full output still reaches the model.
            compact = getattr(self.console, "compact_tool_output", False)
            r = Renderer(self.console, compact=compact)
            self._renderer_cache = r
        return r
