"""Debug mode — a staged hypothesize→fix loop for non-deterministic bugs.

Sibling to plan mode and the Coding Rail (xlii/rail.py). Where plan mode is
read-only design and the rail is staged design→implementation, debug mode walks
a *bug* through evidence-gathering phases, with the teeth at the tool layer:

    0  Hypothesize   (read-only)        — form hypotheses from the code
    1  Instrument    (marked writes)    — add `# xlii-debug:` log lines only
    2  Reproduce     (bash)             — run the repro, collect evidence
    3  Analyze       (read-only)        — read logs, confirm/refute hypotheses
    4  Fix           (full writes)      — the real fix
    5  Verify        (read-only + gate) — re-run; /debug exit is blocked until
                                          every instrumentation marker is gone

Enforcement mirrors the rail's two layers:

  1. Prompt: ``get_system_directive()`` pins the model to the current phase
     (injected into the system prompt each turn, current phase only).
  2. Tools:  the agent loop maps ``tool_mode`` to a per-phase schema set
     (``debug_instrument_schemas`` / ``debug_reproduce_schemas`` /
     ``plan_mode_schemas`` / full). The Instrument phase is the novel piece:
     ``edit_file`` is offered but ``ToolContext.debug_instrument`` makes
     ``t_edit_file`` reject any added line that lacks ``DEBUG_MARKER`` — so
     every trace the model leaves is greppable, and the Verify cleanup gate can
     prove the source is clean before exit.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Optional


# The token every instrumentation line must carry so the cleanup gate can find
# (and the user can grep) leftover debug traces. Language-agnostic: it lives in
# a comment in whatever language the file is, e.g. `# xlii-debug:` / `// xlii-debug`.
DEBUG_MARKER = "xlii-debug"


class DebugPhase(Enum):
    HYPOTHESIZE = 0
    INSTRUMENT = 1
    REPRODUCE = 2
    ANALYZE = 3
    FIX = 4
    VERIFY = 5


LAST_PHASE = DebugPhase.VERIFY

# Read-only investigation phases reuse the plan-mode tool set (no writes, no
# shell). Instrument, Reproduce, and Fix each get their own narrowed palette.
_READ_ONLY_PHASES: frozenset[DebugPhase] = frozenset(
    {DebugPhase.HYPOTHESIZE, DebugPhase.ANALYZE, DebugPhase.VERIFY}
)


DEBUG_PHASE_PROMPTS: dict[DebugPhase, str] = {
    DebugPhase.HYPOTHESIZE: (
        "You are now strictly in PHASE 0 – Hypothesize. Read the relevant code and "
        "state 1-3 concrete, falsifiable hypotheses for the bug. For each, name the "
        "evidence that would confirm or refute it. Do NOT change any files yet."
    ),
    DebugPhase.INSTRUMENT: (
        "You are now strictly in PHASE 1 – Instrument. Add logging/tracing to gather "
        f"the evidence named in Phase 0. Every line you add MUST carry a `{DEBUG_MARKER}` "
        "marker in a comment (e.g. `log(...)  # xlii-debug`) — the tool layer will "
        "REJECT any added line without it, and you cannot leave debug mode until all "
        "markers are removed. Only `edit_file` into existing files; do not write new "
        "files or run the program yet."
    ),
    DebugPhase.REPRODUCE: (
        "You are now strictly in PHASE 2 – Reproduce. Run the repro command (bash) to "
        "trigger the bug and capture the instrumented output. Do not edit files; just "
        "collect evidence. If it does not reproduce, say so and go `/debug back`."
    ),
    DebugPhase.ANALYZE: (
        "You are now strictly in PHASE 3 – Analyze. Read the captured output and state "
        "which hypothesis the evidence confirms or refutes, and the precise root cause. "
        "Do NOT change files yet — this is read-only reasoning."
    ),
    DebugPhase.FIX: (
        "You are now strictly in PHASE 4 – Fix. Apply the real fix for the confirmed "
        "root cause (full write tools are unlocked). Do not add new instrumentation. "
        "You may start removing the `# xlii-debug` markers as you go."
    ),
    DebugPhase.VERIFY: (
        "You are now strictly in PHASE 5 – Verify & Cleanup. Re-run the repro to confirm "
        f"the fix, then REMOVE every `{DEBUG_MARKER}` instrumentation line you added. "
        "`/debug exit` stays blocked until a repo scan finds zero markers."
    ),
}


class DebugController:
    """Tracks the debug phase and supplies the per-phase directive + tool mode.

    Holds no I/O — the agent loop reads ``tool_mode`` / ``is_instrument_phase``
    to gate tools and ``get_system_directive`` for the prompt, while the REPL
    slash commands drive ``advance`` / ``back``. The Verify cleanup gate lives
    in the ``/debug`` handler (it needs the project tree); see
    :func:`find_debug_markers`.
    """

    def __init__(self) -> None:
        self.current_phase: DebugPhase = DebugPhase.HYPOTHESIZE

    # -- queries ---------------------------------------------------------- #

    @property
    def is_read_only_phase(self) -> bool:
        return self.current_phase in _READ_ONLY_PHASES

    @property
    def is_instrument_phase(self) -> bool:
        return self.current_phase is DebugPhase.INSTRUMENT

    @property
    def is_final_phase(self) -> bool:
        return self.current_phase is LAST_PHASE

    @property
    def tool_mode(self) -> str:
        """Which palette the agent loop should hand this turn:
        ``read_only`` | ``instrument`` | ``reproduce`` | ``write``."""
        if self.current_phase in _READ_ONLY_PHASES:
            return "read_only"
        if self.current_phase is DebugPhase.INSTRUMENT:
            return "instrument"
        if self.current_phase is DebugPhase.REPRODUCE:
            return "reproduce"
        return "write"  # FIX

    @property
    def phase_label(self) -> str:
        return self.current_phase.name.replace("_", " ").title()

    def get_status_header(self) -> str:
        return (
            f"[DEBUG PHASE {self.current_phase.value}/{LAST_PHASE.value} "
            f"– {self.phase_label}]"
        )

    def get_system_directive(self) -> str:
        """The debug enforcement block for the current phase — injected into the
        system prompt each turn (current phase only), never into history."""
        if self.is_read_only_phase:
            palette = (
                "Writes are LOCKED for this phase — read-only investigation tools "
                "only (no write_file / edit_file / bash)."
            )
        elif self.is_instrument_phase:
            palette = (
                "Only `edit_file` is unlocked, and every added line must carry a "
                f"`{DEBUG_MARKER}` marker or the edit is refused."
            )
        elif self.current_phase is DebugPhase.REPRODUCE:
            palette = "Only `bash` is unlocked (run the repro); file writes are LOCKED."
        else:  # FIX
            palette = "Full write tools are UNLOCKED for this phase."
        return "\n".join(
            [
                "=== DEBUG MODE — ACTIVE ===",
                self.get_status_header(),
                DEBUG_PHASE_PROMPTS[self.current_phase],
                palette,
                (
                    "Stay strictly within this phase for this turn. The user advances "
                    "with `/debug next` (or `/debug back` to redo)."
                ),
                "=== END DEBUG ===",
            ]
        )

    # -- transitions ------------------------------------------------------ #

    def advance(self) -> bool:
        """Move to the next phase. Returns False if already at the last phase."""
        if self.is_final_phase:
            return False
        self.current_phase = DebugPhase(self.current_phase.value + 1)
        return True

    def back(self) -> bool:
        """Move to the previous phase. Returns False if already at phase 0."""
        if self.current_phase.value == 0:
            return False
        self.current_phase = DebugPhase(self.current_phase.value - 1)
        return True

    def reset(self) -> None:
        """Restart debug staging at the first phase, keeping the mode active.

        Mirrors ``RailController.reset``: a cleared history (via ``/reset`` or a
        project switch) means a new bug, so the phase must rewind to Hypothesize —
        otherwise the session stays gated at the prior phase (e.g. Fix/Verify) and
        applies the wrong tool palette + directive to the next bug.
        """
        self.current_phase = DebugPhase.HYPOTHESIZE

    # -- ModeController conformance (godzilla refactor, Track A) ---------- #

    @property
    def suppresses_claim_check(self) -> bool:
        return self.is_read_only_phase

    def tool_schemas(self, project_xli_dir=None) -> list[dict]:
        from xlii.tools import (
            debug_instrument_schemas,
            debug_reproduce_schemas,
            dispatch_subagent_schema,
            plan_mode_schemas,
            tool_schemas,
        )

        mode = self.tool_mode
        if mode == "read_only":
            return plan_mode_schemas()
        if mode == "instrument":
            return debug_instrument_schemas()
        if mode == "reproduce":
            return debug_reproduce_schemas()
        return tool_schemas() + [dispatch_subagent_schema()]

    def write_scope(
        self, project_xli_dir=None
    ) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
        # Not the planner ⇒ plans/ denied — the fix/verify phases write the
        # repo, never the spec.
        from xlii.mode_controller import non_planner_write_scope

        return non_planner_write_scope(project_xli_dir)

    def status_tag(self) -> tuple[str, str] | None:
        return (
            f"DEBUG {self.current_phase.value}/{LAST_PHASE.value}",
            "DEBUG",
        )

    def on_enter(self, agent) -> None:
        pass

    def on_exit(self, agent) -> None:
        pass


def find_debug_markers(
    project_root: Path, extra_ignores: Optional[list[str]] = None
) -> list[tuple[str, int, str]]:
    """Scan the project for leftover ``DEBUG_MARKER`` instrumentation lines.

    Powers the Verify cleanup gate: ``/debug exit`` is blocked while this returns
    anything. Walks the same files sync/grep see (honors the ignore spec) so
    `.xlii/`, venvs, and `.git` never trip the gate, and so this very source file
    (which documents the marker as a quoted token, not a live marker) is skipped
    along with the rest of the package only if ignored — callers pass the user's
    project root, not the xlii tree.

    Returns ``(relpath, 1-based-lineno, line_text)`` for each match.
    """
    from xlii.ignore import load_ignore_spec

    spec = load_ignore_spec(project_root, extra_ignores or [])
    hits: list[tuple[str, int, str]] = []
    for path in sorted(project_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(project_root).as_posix()
        if spec.match_file(rel) or spec.match_file(rel + "/"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if DEBUG_MARKER not in text:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if DEBUG_MARKER in line:
                hits.append((rel, i, line))
    return hits
