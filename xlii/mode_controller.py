"""Mode controller protocol — unified surface for plan / rail / debug modes.

Track A of the godzilla refactor (proposals/godzilla-refactor.md): one
``ModeController`` slot on the Agent replaces the duplicated rail→debug→plan
ladders. Phase A1 defines the protocol and makes existing controllers conform;
consumers are rewired in later phases.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable


@runtime_checkable
class ModeController(Protocol):
    """Shared surface the agent turn loop routes through for gated modes."""

    @property
    def suppresses_claim_check(self) -> bool:
        """True when read-only stages may legitimately call zero tools."""
        ...

    def tool_schemas(self, project_xli_dir: Any = None) -> list[dict]:
        """Tool palette for the current mode stage/phase."""
        ...

    def write_scope(
        self, project_xli_dir: Any = None
    ) -> tuple[Optional[tuple[str, ...]], tuple[str, ...]]:
        """Path-scoped write profile for this mode (plan-write-domain P0):
        ``(allow, deny)`` of RESOLVED absolute dir paths as strings. ``allow``
        None = unrestricted; deny wins. The uniform rule: the planner allows
        ONLY ``<xli_dir>/plans/``; every other mode denies it ("not the planner
        ⇒ plans/ denied" — the rail's write stages are the flagship implementer
        path and must not edit the spec either; see non_planner_write_scope).
        Readers must use a defensive ``getattr`` (fakes and third-party
        controllers may predate this; they get unrestricted ``(None, ())``)."""
        ...

    def get_system_directive(self) -> str:
        """Enforcement block injected into the system prompt each turn."""
        ...

    def status_tag(self) -> Optional[tuple[str, str]]:
        """(label, mode-key) for status rendering, or None when inactive."""
        ...

    def advance(self) -> bool:
        """Move to the next stage/phase. Returns False at the terminal step."""
        ...

    def back(self) -> bool:
        """Move to the previous stage/phase. Returns False at the first step."""
        ...

    def on_enter(self, agent: Any) -> None:
        """Hook when this mode becomes active (mutual-exclusion site in A2)."""
        ...

    def on_exit(self, agent: Any) -> None:
        """Hook when this mode is cleared."""
        ...


def non_planner_write_scope(
    project_xli_dir: Any = None,
) -> tuple[Optional[tuple[str, ...]], tuple[str, ...]]:
    """The write profile of every mode that is NOT the planner: repo per its
    palette, ``<xli_dir>/plans/`` denied — the implementer (rail write stages,
    debug fix, ops) cannot edit the spec. Shared by Ops/Discovery/Rail/Debug
    and mirrored by the execute (no-mode) profile in ``Agent.run_turn``.
    For read-only palettes the deny is belt-and-braces."""
    if project_xli_dir is None:
        return (None, ())
    from pathlib import Path

    plans = (Path(project_xli_dir) / "plans").resolve()
    return (None, (str(plans),))


class PlanController:
    """Plan investigation mode — one instance per entry (``started_at`` marks
    when this planning session began; the persistence layer uses it to tell a
    fresh ``plans/current.md`` from a stale one).

    Repo-read-only, but planning IS writing (plan-write-domain P0): the palette
    adds write_file/edit_file, path-gated to ``<xli_dir>/plans/`` via
    ``write_scope`` — the plan lives in a file, not in chat.

    ``chat_backend`` (plan-surface T2) is a hired planner: a gigwork
    ChatBackend that drives THIS plan session's orchestrator turns instead of
    the home plane. It lives on the controller, so ``/execute`` and ``/cancel``
    (``set_mode``) end the hire by construction — plan mode is the one room a
    foreign brain may drive, and it leaves with the room."""

    def __init__(self, chat_backend: Optional[Any] = None) -> None:
        import time

        self.started_at: float = time.time()
        self.chat_backend = chat_backend

    @property
    def suppresses_claim_check(self) -> bool:
        return False

    def tool_schemas(self, project_xli_dir: Any = None) -> list[dict]:
        from xlii.tools import plan_write_schemas

        return plan_write_schemas()

    def write_scope(
        self, project_xli_dir: Any = None
    ) -> tuple[Optional[tuple[str, ...]], tuple[str, ...]]:
        from pathlib import Path

        plans = (Path(project_xli_dir) / "plans").resolve()
        return ((str(plans),), ())

    def get_system_directive(self) -> str:
        from xlii.agent import PLAN_MODE_PREAMBLE

        directive = PLAN_MODE_PREAMBLE.rstrip()
        if self.chat_backend is not None:
            # One foreign-brain line (the proposal's "prompt tuning"): the
            # hired planner should know the xAI server plane is out of reach.
            directive += (
                "\n\nThis planning session runs on a hired non-xAI brain: "
                "xAI server tools (search_project / web_search / x_search) are "
                "unavailable — investigate with grep, glob, and file reads."
            )
        return directive

    def status_tag(self) -> Optional[tuple[str, str]]:
        if self.chat_backend is not None:
            label = getattr(self.chat_backend, "label", "?")
            return (f"PLAN·gigwork[{label}]", "PLAN")
        return ("PLAN", "PLAN")

    def advance(self) -> bool:
        return False

    def back(self) -> bool:
        return False

    def on_enter(self, agent: Any) -> None:
        pass

    def on_exit(self, agent: Any) -> None:
        pass


class OpsController:
    """OS diagnostics / workflow mode — read-only file tools plus bash for host
    probes. Read-only-first posture; destructive shell still passes shellgate."""

    @property
    def suppresses_claim_check(self) -> bool:
        return True

    def tool_schemas(self, project_xli_dir: Any = None) -> list[dict]:
        from xlii.tool_schemas import ops_mode_schemas

        return ops_mode_schemas()

    def write_scope(
        self, project_xli_dir: Any = None
    ) -> tuple[Optional[tuple[str, ...]], tuple[str, ...]]:
        return non_planner_write_scope(project_xli_dir)

    def get_system_directive(self) -> str:
        from xlii.agent import OPS_MODE_PREAMBLE

        return OPS_MODE_PREAMBLE.rstrip()

    def status_tag(self) -> Optional[tuple[str, str]]:
        return ("OPS", "OPS")

    def advance(self) -> bool:
        return False

    def back(self) -> bool:
        return False

    def on_enter(self, agent: Any) -> None:
        pass

    def on_exit(self, agent: Any) -> None:
        pass


class DiscoveryController:
    """Read-only discussion/research mode — like plan mode, the tools are locked
    to read-only, but there is no plan deliverable and no /execute step. It's a
    sticky "just talk about the code" gate so the user doesn't have to repeat
    "don't change anything" each session. Stateless, one instance per entry."""

    @property
    def suppresses_claim_check(self) -> bool:
        # A discussion turn legitimately answers from context with zero tool
        # calls — don't flag it as an unsupported claim.
        return True

    def tool_schemas(self, project_xli_dir: Any = None) -> list[dict]:
        from xlii.tools import plan_mode_schemas

        # The read-only palette (read_file/list_dir/glob/grep/search_project) —
        # research needs to read the code, never write it. Unlike plan mode,
        # discovery has no deliverable, so no plans/ write domain either.
        return plan_mode_schemas()

    def write_scope(
        self, project_xli_dir: Any = None
    ) -> tuple[Optional[tuple[str, ...]], tuple[str, ...]]:
        return non_planner_write_scope(project_xli_dir)

    def get_system_directive(self) -> str:
        from xlii.agent import DISCOVERY_MODE_PREAMBLE

        return DISCOVERY_MODE_PREAMBLE.rstrip()

    def status_tag(self) -> Optional[tuple[str, str]]:
        return ("DISC", "DISC")

    def advance(self) -> bool:
        return False

    def back(self) -> bool:
        return False

    def on_enter(self, agent: Any) -> None:
        pass

    def on_exit(self, agent: Any) -> None:
        pass
