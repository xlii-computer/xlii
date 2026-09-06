"""Coding Rail — a disciplined, staged pipeline for coding turns.

Instead of letting the agent jump straight to code, the rail walks a request
through six gated stages:

    0  Requirements Lock      (read-only)
    1  Architecture & Plan    (read-only)
    2  Edge Cases             (read-only)
    3  Pseudocode             (read-only)
    4  Implementation         (writes unlocked)
    5  Self-Review            (writes unlocked)

The user is the gate. Each stage injects an enforcement addendum into the turn;
the human reviews the output and advances with `/rail next` (or `/rail back` to
redo). This mirrors xlii's existing plan mode but with finer granularity, and
leans on the project's core thesis — *user curation as the load-bearing layer*.

Enforcement has two layers:

  1. Prompt: `get_system_directive()` pins the model to the current stage
     (injected into the system prompt each turn, current stage only).
  2. Tools:  stages 0-3 are read-only — the agent loop restricts the tool
     surface to the plan-mode (investigation) set, so the model physically
     *cannot* write files or run shell while it is still thinking. Writes only
     unlock at stage 4. This is the real teeth; the prompt alone is advisory.
"""

from enum import Enum


class RailStage(Enum):
    REQUIREMENTS_LOCK = 0
    ARCHITECTURE_PLAN = 1
    EDGE_CASES = 2
    PSEUDOCODE = 3
    IMPLEMENTATION = 4
    SELF_REVIEW = 5


LAST_STAGE = RailStage.SELF_REVIEW

# Stages 0-3 are pure thinking — no file writes, no shell, no fan-out. The agent
# loop maps these to the read-only plan-mode tool set. Writes unlock at stage 4.
READ_ONLY_STAGES: frozenset[RailStage] = frozenset(
    {
        RailStage.REQUIREMENTS_LOCK,
        RailStage.ARCHITECTURE_PLAN,
        RailStage.EDGE_CASES,
        RailStage.PSEUDOCODE,
    }
)


RAIL_STAGE_PROMPTS: dict[RailStage, str] = {
    RailStage.REQUIREMENTS_LOCK: (
        "You are now strictly in STAGE 0 – Requirements Lock. "
        "Restate the full task in your own words. List every explicit and implicit requirement. "
        "Ask for clarification on anything ambiguous BEFORE proceeding. "
        "Never output code, pseudocode, or implementation details."
    ),
    RailStage.ARCHITECTURE_PLAN: (
        "You are now strictly in STAGE 1 – Architecture & Plan. "
        "Break the solution into high-level components/modules. Show data flow, key classes/functions, "
        "and chosen patterns. Explain why you chose them. Keep this high-level (NO code yet)."
    ),
    RailStage.EDGE_CASES: (
        "You are now strictly in STAGE 2 – Edge Cases & Completeness Checklist. "
        "Explicitly list every edge case, error condition, input validation, security consideration, "
        "and performance constraint. Confirm your plan handles all of them. NO code yet."
    ),
    RailStage.PSEUDOCODE: (
        "You are now strictly in STAGE 3 – Pseudocode / Step-by-Step Logic. "
        "Write detailed pseudocode or numbered implementation steps for every component. "
        "Do not write real code yet."
    ),
    RailStage.IMPLEMENTATION: (
        "You are now strictly in STAGE 4 – Full Implementation. "
        "Output the complete, production-ready code only. Use proper formatting, type hints, "
        "and comments. Never leave TODOs."
    ),
    RailStage.SELF_REVIEW: (
        "You are now strictly in STAGE 5 – Self-Review. "
        "Run your own internal review: Does this match the original requirements 100%? "
        "Are there any half-done sections? Did I miss an edge case? Is the code clean? "
        "Fix anything that fails, then output exactly “REVIEW PASSED – READY” when complete."
    ),
}


class RailController:
    """Tracks the rail stage and supplies the per-stage system directive.

    The controller holds no I/O — the agent loop reads ``current_stage`` /
    ``is_read_only_stage`` to gate tools and ``get_system_directive`` for the
    prompt, while the REPL slash commands drive ``advance`` / ``back`` /
    ``reset``.
    """

    def __init__(self, seeded_from_plan: bool = False) -> None:
        self.current_stage: RailStage = RailStage.REQUIREMENTS_LOCK
        # True when the rail was launched from an approved /plan. The early
        # stages then confirm/refine against that plan instead of re-deriving
        # requirements and architecture from scratch.
        self.seeded_from_plan: bool = seeded_from_plan

    # -- queries ---------------------------------------------------------- #

    @property
    def is_read_only_stage(self) -> bool:
        """True while the rail is still in a thinking stage (0-3)."""
        return self.current_stage in READ_ONLY_STAGES

    @property
    def is_final_stage(self) -> bool:
        return self.current_stage is LAST_STAGE

    @property
    def stage_label(self) -> str:
        return self.current_stage.name.replace("_", " ").title()

    def get_system_directive(self) -> str:
        """The rail enforcement block for the current stage.

        Injected into the *system prompt* each turn (rebuilt fresh by
        ``Agent._effective_system_prompt``), NOT prepended to the stored user
        message. Keeping it out of persisted history means only the CURRENT
        stage's rules are ever in context — earlier stages' "never write code"
        directives do not pile up and contradict the implementation stage, and
        the cached conversation prefix stays clean.
        """
        lines = [
            "=== CODING RAIL — ACTIVE ===",
            self.get_status_header(),
            RAIL_STAGE_PROMPTS[self.current_stage],
            (
                "Writes are LOCKED for this stage — you have read-only "
                "investigation tools only (no write_file / edit_file / bash)."
                if self.is_read_only_stage
                else "Writes are UNLOCKED for this stage."
            ),
        ]
        if self.seeded_from_plan:
            lines.append(
                "An APPROVED PLAN from plan mode already exists in the "
                "conversation above. Treat it as the agreed baseline: confirm "
                "and refine each point against it rather than re-deriving from "
                "scratch."
            )
        lines.append(
            "Stay strictly within this stage for this turn. Do not jump ahead to "
            "or carry out later stages — the user controls advancement with "
            "`/rail next`."
        )
        lines.append("=== END RAIL ===")
        return "\n".join(lines)

    def get_status_header(self) -> str:
        return (
            f"[RAIL STAGE {self.current_stage.value}/{LAST_STAGE.value} "
            f"– {self.stage_label}]"
        )

    # -- transitions ------------------------------------------------------ #

    def advance(self) -> bool:
        """Move to the next stage. Returns False if already at the last stage."""
        if self.is_final_stage:
            return False
        self.current_stage = RailStage(self.current_stage.value + 1)
        return True

    def back(self) -> bool:
        """Move to the previous stage. Returns False if already at stage 0."""
        if self.current_stage.value == 0:
            return False
        self.current_stage = RailStage(self.current_stage.value - 1)
        return True

    def reset(self) -> None:
        """Return to stage 0 to restart the rail on a fresh task."""
        self.current_stage = RailStage.REQUIREMENTS_LOCK
        self.seeded_from_plan = False

    # -- ModeController conformance (godzilla refactor, Track A) ---------- #

    @property
    def suppresses_claim_check(self) -> bool:
        return self.is_read_only_stage

    def tool_schemas(self, project_xli_dir=None) -> list[dict]:
        from xlii.tools import (
            dispatch_subagent_schema,
            plan_mode_schemas,
            tool_schemas,
        )

        if self.is_read_only_stage:
            return plan_mode_schemas()
        return tool_schemas() + [dispatch_subagent_schema()]

    def write_scope(
        self, project_xli_dir=None
    ) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
        # The rail's write stages are the flagship implementer path (/execute
        # rail) — the implementer cannot edit the spec, so plans/ is denied.
        from xlii.mode_controller import non_planner_write_scope

        return non_planner_write_scope(project_xli_dir)

    def status_tag(self) -> tuple[str, str] | None:
        return (f"RAIL {self.current_stage.value}/{LAST_STAGE.value}", "RAIL")

    def on_enter(self, agent) -> None:
        pass

    def on_exit(self, agent) -> None:
        pass
