"""Tool context, result types, and bash intent constants."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional

import xlii.shellgate as _sg
from xlii.client import Clients
from xlii.config import GlobalConfig, ProjectConfig

if TYPE_CHECKING:
    from xlii.turn_events import ShellRan

# How much of a long output to keep before truncating, per tool call.
MAX_OUTPUT_BYTES = 30_000


@dataclass
class ToolContext:
    project: ProjectConfig
    clients: Clients
    cfg: GlobalConfig
    pool: Any = None        # ClientPool when running in main agent; None for workers
    console: Any = None     # rich.Console for ad-hoc UI; None in workers
    dirty_paths: set[str] = field(default_factory=set)
    yolo: bool = False      # skip per-intent confirmation gate when True
    plan_mode: bool = False # read-only tool palette (/plan); blocks mutating sub-delegates
    auto_approve: frozenset[str] = field(default_factory=lambda: DEFAULT_AUTO_APPROVE)
    is_worker: bool = False # workers cannot run intent > read-only
    worker_writes: bool = False  # writer-workers: write palette + relaxed bash gate
    # NOTE: there is deliberately NO extra_collection_ids field. search_project
    # reaches the project's OWN Collection only — personas are sealed islands
    # (menu-families §5, d96c9d35). Its absence is the structural guard: any
    # attempt to thread extra collections back in is a TypeError here.
    # Subscribed plugin IDs for this session. plugin_search and plugin_get
    # only see plugins in this list (mandatory subscription model — keeps
    # the active set bounded as the catalog grows).
    subscribed_plugins: list[str] = field(default_factory=list)
    # The user's live shell cwd when it has diverged from the project root
    # (shell-primary "flip" mode; proposals/done/flipmode.md). Advisory only — tools
    # stay bound to the project root; this lets a tool surface the divergence.
    user_shell_cwd: Optional[Path] = None
    # Server-tool sub-call usage (Responses API). Server tools fire one-shot
    # responses.create() calls outside the main chat loop; the agent drains
    # these accumulators after each tool batch into its CallStats.
    server_prompt_tokens: int = 0
    server_completion_tokens: int = 0
    server_cost: float = 0.0
    server_calls: int = 0
    # Autonomous loop (L2+): block writes to test paths when set.
    loop_lock_tests: bool = False
    # Media-out delivery channel (`xlii ask --outbox`): a caller-owned dir the
    # send_file tool queues files into; the caller (the XMPP daemon) uploads
    # them encrypted and they land in the peer's chat. None = no channel —
    # send_file is then neither advertised nor allowed to run.
    outbox_dir: Optional[Path] = None
    # Debug mode Instrument phase (cursor-workflows.md B0): when set, edit_file
    # refuses any added line missing DEBUG_MARKER, and write_file is refused
    # outright — so all instrumentation is greppable for the cleanup gate.
    debug_instrument: bool = False
    # Path-scoped write profile (plan-write-domain P0) — DERIVED per turn from
    # the active mode, never stored on agent/session state, so there is no flag
    # to forget or desync. Entries are RESOLVED absolute dir paths (strings);
    # write_file/edit_file check the resolved target against them (see
    # tool_gating.write_path_refusal). allow None = unrestricted; deny wins.
    write_allow: Optional[tuple[str, ...]] = None
    write_deny: tuple[str, ...] = ()
    # True when any server-tool sub-call used a model missing from the
    # pricing table — the accumulated cost is then a LOWER BOUND, not $0.
    server_cost_unknown: bool = False
    # Per-turn counter naming spilled tool-output files (A2 dynamic context).
    spill_seq: int = 0
    # Face hook: open a closed plugin form (secrets never come back through chat).
    open_plugin_form: Optional[Callable[[dict], None]] = None
    # Mojo-keeper K6: the live REPL sitting (desk_switch / memory_set) and the
    # Face door emitter (pane_open / land). None on headless / worker turns.
    sitting: Any = None
    emit_door: Optional[Callable[[dict], None]] = None
    door_surface: str = ""

    def record_server_usage(
        self, model: str, prompt_tokens: int, completion_tokens: int
    ) -> None:
        from xlii.cost import estimate_cost
        self.server_prompt_tokens += prompt_tokens
        self.server_completion_tokens += completion_tokens
        c = estimate_cost(self.cfg.pricing, model, prompt_tokens, completion_tokens)
        if c is not None:
            self.server_cost += c
        else:
            self.server_cost_unknown = True
        self.server_calls += 1

    def drain_server_usage(self) -> tuple[int, int, float, int]:
        """Return accumulated server-tool usage and reset the counters."""
        out = (
            self.server_prompt_tokens,
            self.server_completion_tokens,
            self.server_cost,
            self.server_calls,
        )
        self.server_prompt_tokens = 0
        self.server_completion_tokens = 0
        self.server_cost = 0.0
        self.server_calls = 0
        return out


# Bash intents — declared by the agent on every bash call so the human can
# see what the call is *meant* to do (transparency) and so we gate the
# riskier ones with a y/N prompt (cheap circuit-breaker for hallucinations).
# The category strings themselves live in shellgate (the independent
# classifier) so the declared vocabulary and the classified vocabulary can
# never drift apart; the INTENT_* names are this module's policy-side aliases.
INTENT_READ_ONLY = _sg.READ_ONLY             # ls, cat, grep, find, git status/log/diff, pytest
INTENT_MODIFIES_PROJECT = _sg.MODIFIES_PROJECT  # git add/commit, file rewrites via shell, sed -i in tree
INTENT_MODIFIES_SYSTEM = _sg.MODIFIES_SYSTEM    # apt, sudo, anything outside project, system config
INTENT_NETWORK = _sg.NETWORK                  # curl, wget, pip install, npm install, git push/pull/fetch

VALID_INTENTS = {INTENT_READ_ONLY, INTENT_MODIFIES_PROJECT, INTENT_MODIFIES_SYSTEM, INTENT_NETWORK}
GATED_INTENTS = {INTENT_MODIFIES_SYSTEM, INTENT_NETWORK}
# Standing grants a session starts with. Empty: a grant only matters for a
# GATED intent (and modifies-system can never be granted), so the only
# meaningful member is `network` — read-only / modifies-project never prompt
# regardless and storing them here was a display-only phantom.
DEFAULT_AUTO_APPROVE: frozenset[str] = frozenset()


def normalize_declared_intent(
    declared_intent: Optional[str],
) -> tuple[Optional[str], bool]:
    """Return (normalized_intent, invalid_provided).

    normalized_intent is the declared intent when it is a known member of
    VALID_INTENTS, otherwise None.

    invalid_provided is True only when the caller supplied a non-empty value
    that is not in VALID_INTENTS. This lets authorization code distinguish
    "missing intent" from "present but invalid intent" for logging/telemetry
    while preserving fallback behavior.
    """
    if declared_intent is None or declared_intent == "":
        return None, False
    if declared_intent in VALID_INTENTS:
        return declared_intent, False
    return None, True


def default_auto_approve() -> set[str]:
    return set(DEFAULT_AUTO_APPROVE)


def shell_command_needs_confirm(
    classified: str,
    *,
    yolo: bool,
    auto_approve: set[str] | frozenset[str],
) -> bool:
    """Return True when a classified shell command needs human confirmation.

    Shared policy for ``tool_handlers._check_intent_and_gate`` (agent bash) and
    ``shell_toolkit.gate_shell_command`` (REPL /sh, ``!``, failure nudge).

    ``yolo`` is True for both the yolo and freeball tiers: ``TrustState.yolo``
    derives from the single tier slot, so freeball ⇒ yolo holds by construction
    and this gate needs no separate freeball input.

    ``modifies-system`` always confirms — yolo and ``auto_approve`` cannot
    waive it. Yolo still skips the *network* prompt. ``h_approve`` already
    refuses to grant system, but the policy seam must not trust that a
    caller-supplied set went through the handler."""
    if classified == INTENT_MODIFIES_SYSTEM:
        return True
    if yolo:
        return False
    if classified not in GATED_INTENTS:
        return False
    if classified in auto_approve:
        return False
    return True


@dataclass
class ShellAuth:
    """Outcome of :func:`authorize_shell` — classify + worker ceiling + confirm."""

    classified: str
    effective: str
    allow: bool
    needs_confirm: bool
    refusal: str = ""


def authorize_shell(
    cmd: str,
    *,
    project_root: Optional[Path] = None,
    declared_intent: Optional[str] = None,
    yolo: bool = False,
    auto_approve: set[str] | frozenset[str] = frozenset(),
    is_worker: bool = False,
    worker_writes: bool = False,
) -> ShellAuth:
    """Single policy for agent bash, REPL ``/sh`` / ``!``, tasks, and loop judges.

    Classifies independently of the model-declared intent. Workers are capped
    in code. ``modifies-system`` is never auto-approved and never waived by yolo.
    """
    from xlii.shellgate import classify_command, SEVERITY

    classified = classify_command(cmd, project_root)
    normalized_declared_intent, invalid_declared_intent = normalize_declared_intent(
        declared_intent
    )
    if normalized_declared_intent is not None:
        effective = (
            classified
            if SEVERITY[classified] > SEVERITY[normalized_declared_intent]
            else normalized_declared_intent
        )
    else:
        effective = classified
    if invalid_declared_intent:
        import logging

        logging.getLogger(__name__).warning(
            "authorize_shell received invalid declared intent %r; falling back to classified intent %r",
            declared_intent,
            classified,
        )

    if is_worker and not worker_writes:
        if (declared_intent or INTENT_READ_ONLY) != INTENT_READ_ONLY or classified != INTENT_READ_ONLY:
            return ShellAuth(
                classified=classified,
                effective=effective,
                allow=False,
                needs_confirm=False,
                refusal=(
                    f"bash refused: workers may only run read-only commands; declared "
                    f"intent={declared_intent!r}, classified as {classified!r}. "
                    "Report this back to the orchestrator instead."
                ),
            )
    elif is_worker and worker_writes:
        if classified == INTENT_MODIFIES_SYSTEM or effective == INTENT_MODIFIES_SYSTEM:
            return ShellAuth(
                classified=classified,
                effective=effective,
                allow=False,
                needs_confirm=False,
                refusal=(
                    f"bash refused: writer-workers may not run system commands; "
                    f"declared intent={declared_intent!r}, classified={classified!r}."
                ),
            )
        if not yolo and (effective == INTENT_NETWORK or classified == INTENT_NETWORK):
            return ShellAuth(
                classified=classified,
                effective=effective,
                allow=False,
                needs_confirm=False,
                refusal=(
                    f"bash refused: writer-workers may not run network commands "
                    f"without --yolo; declared intent={declared_intent!r}, "
                    f"classified={classified!r}."
                ),
            )

    needs = shell_command_needs_confirm(
        effective, yolo=yolo, auto_approve=auto_approve,
    )
    return ShellAuth(
        classified=classified,
        effective=effective,
        allow=True,
        needs_confirm=needs,
    )


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    # Structured display event for the bash tool — the same capture the model
    # sees, packaged so the agent can render a ShellBlock (one bash presentation,
    # human or agent). None for every other tool. See xlii.tui.shell.
    shell: Optional["ShellRan"] = None

ToolFn = Callable[["ToolContext", dict[str, Any]], "ToolResult"]
