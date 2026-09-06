"""Mode contract — WHAT a mode is, as declarative data (godzilla-mothra V0d).

Stage 3 of the campaign makes ``/chat`` a safe-REPL capability profile and puts
one project gate on both ``code`` doors. This module defines the vocabulary for
that ahead of time — entry gate, capability profile, awareness — so Stage 3
configures a contract instead of inventing one under pressure.

**Descriptive only.** The registry below DESCRIBES today's modes; nothing
enforces these contracts yet. Enforcement stays where it lives:

- ``xlii/mode_controller.py`` — the ``ModeController`` protocol ENFORCES a
  gated mode turn-by-turn (tool palette, write scope, system directive). A
  contract complements a controller: the controller is the mode's engine, the
  contract is its spec sheet.
- ``xlii/commands.py`` — ``REPLCommand.repls`` filters the slash-command
  surface per REPL ("code"/"chat"); each contract's ``entry.surfaces`` mirrors
  where the mode's entry command is registered.
- ``xlii/tool_schemas.py`` — the palette functions are the live source of
  truth for tool sets. The frozensets here are the *built-in* name sets those
  functions produce today (project tools may extend a palette at runtime,
  which is why unrestricted profiles say ``allow=None`` rather than lie with
  a list). ``tests/test_mode_contract.py`` locks each snapshot to its palette
  function, so drift fails loudly instead of rotting silently.

Kernel-pure by construction: this module imports only the stdlib — no
``xlii.tui``, no rich/textual, and no other ``xlii`` module (the trust-tier
strings mirror ``xlii/session_state.py``; a test pins them together).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------------- #
# Vocabulary constants
# --------------------------------------------------------------------------- #

# Awareness — how much of the project a mode may perceive/affect. This axis
# records CAPABILITY, not posture: a mode is "read" only when nothing in its
# palette can mutate the repo proper (a shell counts as a writer; plan mode
# stays "read" because its writers are path-gated to <xli_dir>/plans/ via the
# controller's write_scope). Directives like ops' "read-only first" live in
# the summary, never in this field.
AWARENESS_PROJECT_BLIND = "project-blind"
AWARENESS_READ = "read"
AWARENESS_WRITE = "write"
AWARENESS_LEVELS = (AWARENESS_PROJECT_BLIND, AWARENESS_READ, AWARENESS_WRITE)

# Kind — where the mode lives.
# controller: occupies Agent.active_mode (mutually exclusive, set_mode).
# surface:    a REPL profile (the "code"/"chat" surfaces of profile.mode).
# overlay:    rides on a surface without occupying the controller slot
#             (scratch/no-sync, howto routing, foreground harness).
KIND_CONTROLLER = "controller"
KIND_SURFACE = "surface"
KIND_OVERLAY = "overlay"
MODE_KINDS = (KIND_CONTROLLER, KIND_SURFACE, KIND_OVERLAY)

# REPL surfaces (the two values of REPLCommand.repls / profile.mode).
SURFACE_CODE = "code"
SURFACE_CHAT = "chat"
ALL_SURFACES = (SURFACE_CODE, SURFACE_CHAT)

# Trust-ladder tiers — mirrors of xlii.session_state.TIER_* (kept as literals
# so this module never imports the session layer; the sync is test-pinned).
TIER_SAFE = "safe"
TIER_YOLO = "yolo"
TIER_FREEBALL = "freeball"
TRUST_TIERS = (TIER_SAFE, TIER_YOLO, TIER_FREEBALL)

_TIER_RANK = {tier: rank for rank, tier in enumerate(TRUST_TIERS)}


def trust_tier_rank(tier: str) -> int:
    """Position of *tier* on the safe → yolo → freeball ladder (0/1/2).

    The comparable form of the ladder, so "trust tier ≥ N" gates are one
    integer compare. Unknown tiers are a hard error — a gate silently treating
    a typo as "safe" would be a security decision made by accident."""
    try:
        return _TIER_RANK[tier]
    except KeyError:
        raise ValueError(
            f"unknown trust tier {tier!r} (expected one of {', '.join(TRUST_TIERS)})"
        ) from None


# --------------------------------------------------------------------------- #
# Capability profile — allowlist/denylist over a namespace of names,
# defined ONCE and used for both tools and slash commands.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NamePolicy:
    """Allow/deny policy over a flat namespace of names.

    ``allow=None`` means "everything not denied" (the namespace is open-ended:
    project tools and plugin commands appear at runtime, so unrestricted
    profiles must not pretend to enumerate). A non-None ``allow`` is a strict
    allowlist. ``deny`` wins over ``allow`` — the same precedence
    ``ModeController.write_scope`` gives its path pairs."""

    allow: Optional[frozenset[str]] = None
    deny: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        # Coerce, don't trust: a caller handing in a live set must not be able
        # to mutate the policy after construction (frozen= alone doesn't stop
        # that — it only freezes the *reference*).
        if self.allow is not None and not isinstance(self.allow, frozenset):
            object.__setattr__(self, "allow", frozenset(self.allow))
        if not isinstance(self.deny, frozenset):
            object.__setattr__(self, "deny", frozenset(self.deny))

    def permits(self, name: str) -> bool:
        if name in self.deny:
            return False
        return self.allow is None or name in self.allow

    @classmethod
    def everything(cls) -> "NamePolicy":
        return cls()

    @classmethod
    def allow_only(cls, names) -> "NamePolicy":
        return cls(allow=frozenset(names))

    @classmethod
    def all_except(cls, names) -> "NamePolicy":
        return cls(deny=frozenset(names))


PERMIT_ALL = NamePolicy()


@dataclass(frozen=True)
class CapabilityProfile:
    """What a mode may reach: agent tools + slash commands, one policy each.

    Today no mode gates slash commands (the per-command ``repls`` axis in
    xlii.commands is the only live filter), so every described contract says
    ``slash_commands=PERMIT_ALL``; Stage 3's safe-chat replaces that with an
    explicit allowlist."""

    tools: NamePolicy = PERMIT_ALL
    slash_commands: NamePolicy = PERMIT_ALL
    # Mojo-keeper K6: allow-list of door tools a surface may advertise.
    # Empty by default; chat fills it with CHAT_DOOR_TOOLS.
    door_tools: NamePolicy = field(
        default_factory=lambda: NamePolicy.allow_only(frozenset())
    )


# --------------------------------------------------------------------------- #
# Entry gate — what must be true to enter a mode, as data + one evaluator.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GateContext:
    """The facts the evaluator judges against — a plain snapshot so callers
    (and tests) never need a live Agent to ask "could I enter this mode?"."""

    has_project: bool = False
    trust_tier: str = TIER_SAFE
    loop_active: bool = False
    surface: str = SURFACE_CODE

    def __post_init__(self) -> None:
        # Fail at construction, not at some later gate: a corrupted/typo'd
        # session tier silently satisfying every ungated mode is exactly the
        # accidental security decision this module refuses to make.
        trust_tier_rank(self.trust_tier)
        if self.surface not in ALL_SURFACES:
            raise ValueError(
                f"unknown surface {self.surface!r} "
                f"(expected one of {', '.join(ALL_SURFACES)})"
            )


@dataclass(frozen=True)
class GateVerdict:
    """Evaluation result: ``ok`` plus one human-readable reason per failed
    condition (all failures, not just the first — an entry refusal should say
    everything that's wrong in one breath)."""

    ok: bool
    reasons: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class EntryGate:
    """Declarative entry conditions. Defaults are the open gate.

    ``min_trust_tier=None`` means any tier (no mode gates on the trust ladder
    today; the field exists so Stage 3 can, e.g. startup-task --auto).
    ``not_during_loop`` mirrors the live "/loop off first" refusal every
    controller-mode command applies. ``surfaces`` mirrors where the mode's
    entry command is registered (REPLCommand.repls)."""

    requires_project: bool = False
    min_trust_tier: Optional[str] = None
    not_during_loop: bool = False
    surfaces: tuple[str, ...] = ALL_SURFACES

    def __post_init__(self) -> None:
        if not isinstance(self.surfaces, tuple):
            object.__setattr__(self, "surfaces", tuple(self.surfaces))
        if self.min_trust_tier is not None:
            trust_tier_rank(self.min_trust_tier)  # unknown tier → ValueError
        if not self.surfaces:
            raise ValueError(
                "a gate needs at least one surface — an always-denied mode "
                "must say so explicitly, not via an empty tuple"
            )
        for surface in self.surfaces:
            if surface not in ALL_SURFACES:
                raise ValueError(
                    f"unknown surface {surface!r} "
                    f"(expected one of {', '.join(ALL_SURFACES)})"
                )

    def evaluate(self, ctx: GateContext) -> GateVerdict:
        """THE evaluator: every gate field judged against *ctx*, all failures
        reported. Unknown vocabulary never reaches here — both this gate and
        the GateContext validate their tiers/surfaces at construction."""
        reasons: list[str] = []
        if self.requires_project and not ctx.has_project:
            reasons.append("requires an initialized project")
        if self.min_trust_tier is not None:
            need = trust_tier_rank(self.min_trust_tier)
            have = trust_tier_rank(ctx.trust_tier)
            if have < need:
                reasons.append(
                    f"requires trust tier ≥ {self.min_trust_tier} "
                    f"(session is {ctx.trust_tier})"
                )
        if self.not_during_loop and ctx.loop_active:
            reasons.append("cannot enter while a /loop is active")
        if ctx.surface not in self.surfaces:
            reasons.append(f"not available on the {ctx.surface} surface")
        return GateVerdict(ok=not reasons, reasons=tuple(reasons))


# --------------------------------------------------------------------------- #
# The contract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ModeStage:
    """One stage/phase of a staged mode (rail stages, debug phases): its own
    tool policy and awareness. ``name`` matches the controller's enum member,
    lowercased, so tests can walk controller and contract in lockstep."""

    name: str
    tools: NamePolicy
    awareness: str

    def __post_init__(self) -> None:
        if self.awareness not in AWARENESS_LEVELS:
            raise ValueError(
                f"stage {self.name!r}: unknown awareness {self.awareness!r}"
            )


@dataclass(frozen=True)
class ModeContract:
    """The declarative description of one mode.

    ``awareness`` is the mode's overall project posture; for staged modes it is
    the envelope (the most the mode ever reaches) and ``stages`` carries the
    per-stage truth. ``aliases`` are alternate lookup names (e.g. /research for
    discovery), never a second identity."""

    name: str
    kind: str
    awareness: str
    summary: str = ""
    entry: EntryGate = field(default_factory=EntryGate)
    capabilities: CapabilityProfile = field(default_factory=CapabilityProfile)
    stages: tuple[ModeStage, ...] = ()
    aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.stages, tuple):
            object.__setattr__(self, "stages", tuple(self.stages))
        if not isinstance(self.aliases, tuple):
            object.__setattr__(self, "aliases", tuple(self.aliases))
        if self.kind not in MODE_KINDS:
            raise ValueError(f"mode {self.name!r}: unknown kind {self.kind!r}")
        if self.awareness not in AWARENESS_LEVELS:
            raise ValueError(
                f"mode {self.name!r}: unknown awareness {self.awareness!r}"
            )


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #


class UnknownModeError(KeyError):
    """Lookup of a mode name (or alias) nothing registered."""


_REGISTRY: dict[str, ModeContract] = {}


def register_mode(contract: ModeContract, *, replace: bool = False) -> None:
    """Register *contract* under its name and aliases.

    A name/alias collision is a hard error unless ``replace=True`` names the
    same primary mode — silent shadowing is how surprises ship (the same
    stance register_repl_command takes)."""
    keys = (contract.name, *contract.aliases)
    if len(set(keys)) != len(keys):
        raise ValueError(
            f"mode contract {contract.name!r} repeats a name/alias: {keys}"
        )
    for key in keys:
        existing = _REGISTRY.get(key)
        if existing is not None and not (replace and existing.name == contract.name):
            raise ValueError(
                f"mode contract name collision: {key!r} is already registered "
                f"by mode {existing.name!r}"
            )
    if replace:
        # Purge EVERY key of the mode being replaced, so an alias the new
        # version drops cannot keep resolving to the superseded contract.
        for key in [k for k, c in _REGISTRY.items() if c.name == contract.name]:
            del _REGISTRY[key]
    for key in keys:
        _REGISTRY[key] = contract


def get_mode(name: str) -> ModeContract:
    """The contract registered under *name* (or an alias of it).

    Unknown modes are an explicit ``UnknownModeError`` naming what IS known —
    a gate consulting the registry must never fall back to a guess."""
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownModeError(
            f"unknown mode {name!r} (known: {', '.join(mode_names())})"
        ) from None


def mode_names() -> tuple[str, ...]:
    """Sorted primary names of every registered mode (aliases not included)."""
    return tuple(sorted({c.name for c in _REGISTRY.values()}))


def all_modes() -> tuple[ModeContract, ...]:
    """Every registered contract, one entry per mode, registration order."""
    seen: dict[str, ModeContract] = {}
    for contract in _REGISTRY.values():
        seen.setdefault(contract.name, contract)
    return tuple(seen.values())


# --------------------------------------------------------------------------- #
# Built-in tool-name snapshots (test-pinned to xlii/tool_schemas.py)
# --------------------------------------------------------------------------- #

# plan_mode_schemas() — every plan_mode_safe built-in: the read-only palette
# shared by discovery, rail stages 0-3, and debug's read-only phases.
READ_ONLY_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "glob",
        "grep",
        "search_project",
        "map",
        "web_search",
        "xai_docs",
        "x_search",
        "plugin_search",
        "plugin_get",
        "codex_run_task",
        "read_email",
        "search_email",
    }
)

# plan_write_schemas() — planning IS writing, but only in <xli_dir>/plans/
# (the controller's write_scope carries the path gate).
PLAN_WRITE_TOOLS = READ_ONLY_TOOLS | {"write_file", "edit_file"}

# ops_mode_schemas() — read-only files + bash for host probes.
OPS_TOOLS = READ_ONLY_TOOLS | {"bash"}

# debug_instrument_schemas() — edit_file only, every added line marker-gated.
DEBUG_INSTRUMENT_TOOLS = READ_ONLY_TOOLS | {"edit_file"}

# debug_reproduce_schemas() — bash only (run the repro); file writes locked.
DEBUG_REPRODUCE_TOOLS = READ_ONLY_TOOLS | {"bash"}


# --------------------------------------------------------------------------- #
# The safe chat REPL (V3b) — the chat surface's capability profile
# --------------------------------------------------------------------------- #

# Project-blind default: conversation + own-memory recall + external reads.
# NO file-system tools (read_file/list_dir/glob/grep/map), no bash, no writers,
# no execution (code_execute / codex_run_task), no fan-out (dispatch_subagent),
# no send_email. search_project stays — the persona's OWN turn store is chat's
# memory, read-only by construction. request_deep_search stays — expert-tier
# search escape hatch.
#
# Research assistant (2026-08): ``browser`` (local Chromium extract) and
# ``plugin_call`` (subscribed L2 APIs: arxiv, archivebox, …) are deliberate
# outbound reads — not repo writes. Hostile paste can still hit the network;
# it still cannot touch the workspace tree.
# ``xai_docs`` is DNA: the house brain reading how Grok / the xAI API
# actually run (hosted docs MCP). Not xlii's wiki; not an MCP marketplace.
# Mail tools are *allowed* here; live advertise still strips them when no
# email account is configured (apply_email_account_gate) so chat cannot
# spend iterations on a guaranteed fail.
CHAT_BLIND_TOOLS = frozenset(
    {
        "search_project",
        "web_search",
        "xai_docs",
        "x_search",
        "browser",
        "plugin_search",
        "plugin_get",
        "plugin_call",
        "read_email",
        "search_email",
        "request_deep_search",
    }
)

# Mojo-keeper K6 — operate xlii by intent. Advertised on conversational turns.
CHAT_DOOR_TOOLS = frozenset(
    {
        "desk_where",
        "desk_switch",
        "desk_new",
        "pane_open",
        "memory_set",
        "explain_xlii",
        "post_job",
    }
)

# --read-proj (awareness = read): the discovery shape — the read-only palette
# (plan_mode_schemas / READ_ONLY_TOOLS) plus chat's deep-search hatch + the
# research tools above. But codex_run_task is dropped: it sits in
# READ_ONLY_TOOLS only because the controller modes that use it set a
# read-only CONTROLLER, and codex's agent-mode (workspace-write) guard keys
# on that (ctx.plan_mode). Chat is controllerless (active_mode is None ⇒
# ctx.plan_mode False), so the guard never fires — leaving it in would make
# an injection-reachable file-writing agent available in a mode sold as
# read-only. deep-search stays (it's a read).
CHAT_READ_TOOLS = (
    (READ_ONLY_TOOLS - {"codex_run_task"})
    | {"request_deep_search", "browser", "plugin_call"}
)

# The chat slash surface: conversation-local verbs + read-only meta only.
# Everything touching repo/project state, system config, trust tiers,
# sync/upload, jobs/tasks/exec, the filesystem, remotes, or session admin
# is denied by absence (deny-by-default allowlist). Plugin *invoke*
# (/plugin call, /get) is talk-legal — subscribed APIs, not repo writes.
# Subscribe/new/remove stay on the same verb; they only touch plugins.txt.
# Persona MEMORY curation (mark/bookmarks/forget) and editing your own
# persona/doc store (/edit) stay. /edit is narrowed in chat to
# --id/--doc/bare; repo-write facets are denied in-handler.
CHAT_SLASH_COMMANDS = frozenset(
    {
        # meta / help
        "help",
        "describe",
        "commands",
        "status",
        "context",
        "cost",
        "history",
        "howto",
        # subscribed APIs (compose / weather / …) — talk mouth
        "plugin",
        "get",
        # pipes + chrome binds (system tasks live here; /project switch is lab-only)
        "tasks",
        "bind",
        # side panels (face + TUI host seam — open home/projects/… without [$])
        "panel",
        "home",
        # surface switching
        "chat",
        "code",
        "persona",
        "off",
        # conversation management
        "reset",
        "clear",
        "compact",
        "cancel",
        "btw",
        "attachments",
        "clear-attachments",
        "detach",
        # conversation parameters
        "tier",
        "model",
        # lab gateway — Face [M] flips to [$] then runs /plan. Talk never
        # self-starts PlanController (research desks are not code projects).
        "plan",
        # memory recall (review-before-run read) + the persona's own store
        "recall",
        "mark",
        "bookmarks",
        "forget",
        "edit",
    }
)


# The /howto overlay's palette (howto-fast T2). Howto answers "how do I use
# xlii" — it needs to LOOK things up, never to change the project, so the full
# coding palette it used to inherit was pure prompt weight plus a write surface
# a hostile help topic could aim. `command_help` is the tool the compact command
# index in the attachment points at (howto.py `_live_commands`); `xai_docs` is
# DNA (how Grok/the API actually run); `request_deep_search` is the same
# read-only escalation chat's expert tier offers, permitted here so a howto turn
# that already resolved to expert keeps it.
#
# Dropped from READ_ONLY_TOOLS for the same reason chat drops them:
# codex_run_task's workspace-write guard keys on a read-only CONTROLLER, and
# howto is an overlay (active_mode is None ⇒ ctx.plan_mode False), so it would
# be a file-writing agent inside a mode sold as Q&A. Mail is dropped as simply
# off-topic — a help answer never reads the operator's inbox.
#
# One of K6's six doors survives: `explain_xlii` reads the shipped self-doc
# (xwiki) — the same retrieval `/howto wiki` runs by hand, so on a howto turn it
# IS the help question. The other five move the desk, open panes, or write, and
# a help answer needs none of them.
HOWTO_DOOR_TOOLS = CHAT_DOOR_TOOLS & {"explain_xlii"}

HOWTO_TOOLS = (
    (READ_ONLY_TOOLS - {"codex_run_task", "read_email", "search_email"})
    | {"command_help", "request_deep_search"}
    | HOWTO_DOOR_TOOLS
)


def howto_tools_policy() -> NamePolicy:
    """The /howto overlay's live tool policy (howto-fast T2): read-only
    investigation plus the help-specific lookups. Enforcement reads this at
    agent turn setup, in front of the chat gate — howto is entered from BOTH
    surfaces, and its palette is the narrower, more specific one."""
    return NamePolicy.allow_only(HOWTO_TOOLS)


def chat_tools_policy(*, read_proj: bool = False) -> NamePolicy:
    """The chat surface's live tool policy (V3b): project-blind by default;
    ``/chat --read-proj`` widens the session to the read-only palette (the
    discovery shape). Door tools (K6) ride the same allow-list. Enforcement
    reads this at agent turn setup."""
    base = CHAT_READ_TOOLS if read_proj else CHAT_BLIND_TOOLS
    return NamePolicy.allow_only(base | CHAT_DOOR_TOOLS)


# --------------------------------------------------------------------------- #
# Today's modes, described (verified against the live tree 2026-07-16)
# --------------------------------------------------------------------------- #

_CONTROLLER_GATE_CODE = EntryGate(not_during_loop=True, surfaces=(SURFACE_CODE,))

_RAIL_READ_STAGE_TOOLS = NamePolicy.allow_only(READ_ONLY_TOOLS)

_BUILTIN_CONTRACTS = (
    ModeContract(
        name="default",
        kind=KIND_CONTROLLER,
        awareness=AWARENESS_WRITE,
        summary=(
            "No controller active — the execute profile. Full palette plus "
            "dispatch_subagent; write scope is the non-planner rule "
            "(unrestricted, <xli_dir>/plans/ denied). Conversational expert "
            "turns may add request_deep_search."
        ),
    ),
    ModeContract(
        name="plan",
        kind=KIND_CONTROLLER,
        awareness=AWARENESS_READ,
        summary=(
            "Plan investigation mode (PlanController). Repo-read-only; the "
            "palette adds write_file/edit_file but the controller's "
            "write_scope allows ONLY <xli_dir>/plans/ — the plan lives in a "
            "file, not in chat."
        ),
        entry=EntryGate(not_during_loop=True),
        capabilities=CapabilityProfile(tools=NamePolicy.allow_only(PLAN_WRITE_TOOLS)),
    ),
    ModeContract(
        name="discovery",
        kind=KIND_CONTROLLER,
        awareness=AWARENESS_READ,
        summary=(
            "Read-only discussion/research (DiscoveryController): the sticky "
            "'just talk about the code' gate — plan-mode palette, no "
            "deliverable, no /execute."
        ),
        entry=_CONTROLLER_GATE_CODE,
        capabilities=CapabilityProfile(tools=NamePolicy.allow_only(READ_ONLY_TOOLS)),
        aliases=("research",),
    ),
    ModeContract(
        name="ops",
        kind=KIND_CONTROLLER,
        awareness=AWARENESS_WRITE,
        summary=(
            "OS diagnostics/workflow (OpsController): read-only file palette "
            "plus bash for host probes. Awareness records capability, not "
            "posture: the mode carries no file writers, but bash (and "
            "codex_run_task's agent mode, which only read-only palettes "
            "block) can still mutate the repo — 'read-only first' is the "
            "directive, shellgate the only brake."
        ),
        entry=_CONTROLLER_GATE_CODE,
        capabilities=CapabilityProfile(tools=NamePolicy.allow_only(OPS_TOOLS)),
    ),
    ModeContract(
        name="rail",
        kind=KIND_CONTROLLER,
        awareness=AWARENESS_WRITE,
        summary=(
            "Coding Rail (RailController): six gated stages — a read-only "
            "thinking prefix (0-3), then writes unlock for implementation and "
            "self-review. plans/ stays denied (the implementer never edits "
            "the spec)."
        ),
        entry=_CONTROLLER_GATE_CODE,
        stages=(
            ModeStage("requirements_lock", _RAIL_READ_STAGE_TOOLS, AWARENESS_READ),
            ModeStage("architecture_plan", _RAIL_READ_STAGE_TOOLS, AWARENESS_READ),
            ModeStage("edge_cases", _RAIL_READ_STAGE_TOOLS, AWARENESS_READ),
            ModeStage("pseudocode", _RAIL_READ_STAGE_TOOLS, AWARENESS_READ),
            ModeStage("implementation", PERMIT_ALL, AWARENESS_WRITE),
            ModeStage("self_review", PERMIT_ALL, AWARENESS_WRITE),
        ),
    ),
    ModeContract(
        name="debug",
        kind=KIND_CONTROLLER,
        awareness=AWARENESS_WRITE,
        summary=(
            "Staged bug hunt (DebugController): hypothesize → instrument "
            "(edit_file only, xlii-debug marker-gated) → reproduce (bash "
            "only) → analyze → fix (writes unlocked) → verify; exit blocked "
            "while instrumentation markers remain."
        ),
        entry=_CONTROLLER_GATE_CODE,
        stages=(
            ModeStage(
                "hypothesize",
                NamePolicy.allow_only(READ_ONLY_TOOLS),
                AWARENESS_READ,
            ),
            ModeStage(
                "instrument",
                NamePolicy.allow_only(DEBUG_INSTRUMENT_TOOLS),
                AWARENESS_WRITE,
            ),
            ModeStage(
                "reproduce",
                NamePolicy.allow_only(DEBUG_REPRODUCE_TOOLS),
                # bash is unlocked to run the repro — file writers stay
                # locked, but a shell stage can mutate the repo, so this is
                # "write" by capability even though the directive says
                # repro-only.
                AWARENESS_WRITE,
            ),
            ModeStage(
                "analyze", NamePolicy.allow_only(READ_ONLY_TOOLS), AWARENESS_READ
            ),
            ModeStage("fix", PERMIT_ALL, AWARENESS_WRITE),
            ModeStage(
                "verify", NamePolicy.allow_only(READ_ONLY_TOOLS), AWARENESS_READ
            ),
        ),
    ),
    ModeContract(
        name="code",
        kind=KIND_SURFACE,
        awareness=AWARENESS_WRITE,
        summary=(
            "The code surface (profile.mode == 'code'). Entry is project-"
            "gated through ONE shared gate (V3a): session_boot.gate_code_entry "
            "— `xlii code` runs it via build_code_session and in-session "
            "/code runs it on the same EntryGate (detect → init → resolve), "
            "so a scratch/chat session no longer just refuses when no code "
            "surface is stashed."
        ),
        entry=EntryGate(requires_project=True),
    ),
    ModeContract(
        name="chat",
        kind=KIND_SURFACE,
        awareness=AWARENESS_PROJECT_BLIND,
        summary=(
            "The chat/persona surface (profile.mode == 'chat') — the safe REPL "
            "(V3b). Detached from any code project by design (RP7), and now "
            "GATED, not just described: the capability profile strips write "
            "tools (project-blind palette: own-memory recall + external reads) "
            "and all but conversation-local slash commands. `/chat --read-proj` "
            "opts the session into read-only project awareness (the discovery "
            "palette) — awareness records capability, so the contract's "
            "awareness is the DEFAULT posture (project-blind)."
        ),
        capabilities=CapabilityProfile(
            tools=NamePolicy.allow_only(CHAT_BLIND_TOOLS | CHAT_DOOR_TOOLS),
            slash_commands=NamePolicy.allow_only(CHAT_SLASH_COMMANDS),
            door_tools=NamePolicy.allow_only(CHAT_DOOR_TOOLS),
        ),
    ),
    ModeContract(
        name="scratch",
        kind=KIND_OVERLAY,
        awareness=AWARENESS_WRITE,
        summary=(
            "Ephemeral never-sync overlay on the code surface "
            "(REPLState.scratch; forces no_sync=True — the locked Q3 "
            "contract). A sync contract, not a tool gate: the palette is "
            "unchanged. `xlii scratch` spawns a session starting in it."
        ),
        entry=EntryGate(surfaces=(SURFACE_CODE,)),
    ),
    ModeContract(
        name="howto",
        kind=KIND_OVERLAY,
        # Awareness records CAPABILITY: the gated palette carries no writer and
        # no shell, so the overlay can no longer mutate the repo (it was WRITE
        # while it inherited the coding palette).
        awareness=AWARENESS_READ,
        summary=(
            "Q&A overlay (session.overlays.howto_mode): routes bare input and "
            "model choice to the help role, and now GATES the palette too "
            "(howto-fast T2) — read-only investigation plus command_help / "
            "xai_docs / request_deep_search, and explain_xlii alone of K6's "
            "doors. It answers how to use the tool; it never needs to write "
            "the project, and the coding palette it used to inherit was "
            "prompt weight on every help turn."
        ),
        capabilities=CapabilityProfile(
            tools=NamePolicy.allow_only(HOWTO_TOOLS),
            door_tools=NamePolicy.allow_only(HOWTO_DOOR_TOOLS),
        ),
    ),
    ModeContract(
        name="harness",
        kind=KIND_OVERLAY,
        awareness=AWARENESS_WRITE,
        summary=(
            "Foreground harness mode (e.g. /cursor on): bare input drives an "
            "external harness session (cursor, claude, …) instead of the "
            "xlii agent. xlii's palette is bypassed for those turns — the "
            "harness brings its own tools; entry additionally needs the "
            "harness binary detected."
        ),
    ),
)


def _register_builtin_contracts() -> None:
    for contract in _BUILTIN_CONTRACTS:
        register_mode(contract)


_register_builtin_contracts()
