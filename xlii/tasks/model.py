"""Task engine model: errors, dataclasses, and grammar constants."""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Optional

_CONSOLE_SWAP_LOCK = threading.RLock()

# --------------------------------------------------------------------------- #
#  Grammar constants
# --------------------------------------------------------------------------- #

PIPE_SEP = "|>"           # the "fat pipe" — distinct from shell `|` so a step
#                           may itself contain a normal shell pipe unambiguously.
PREV_TOKEN = "{{prev}}"   # safe handle: shell-quoted in shell steps, raw elsewhere.
RAW_TOKEN = "{{prev:raw}}"  # explicit opt-in to raw (word-splitting) substitution.

KIND_SHELL = "shell"
KIND_AGENT = "agent"
KIND_SLASH = "slash"

# The Task+ split/join control-flow fields (now live on :class:`Step` as of T+P3).
# T+P1: per-step ``on_success`` / ``on_failure`` + optional ``id`` on :class:`Step`.
# T+P2: :class:`Edge` tables + agent ``TASK+`` trailer routing on :class:`Pipeline`.
# T+P3: ``split`` / ``join`` (+ ``policy``) fan-out/fan-in on :class:`Step`.
# Inline ``|>`` stays linear-only (branching is TOML-only).
TASKPLUS_CONTROL_FIELDS = frozenset({
    "split",
    "join",
})

TASKPLUS_TRAILER_PREFIX = "TASK+ "

# Join policies (T+P3). ``all`` — every branch must succeed (a quality gate);
# ``any`` — succeed if any branch does; ``first_ok`` — succeed if any does and
# carry only the first successful branch.
JOIN_POLICIES = frozenset({"all", "any", "first_ok"})

STEP_ID_RE = re.compile(r"^[a-z0-9-]+$")

# Declared task-param names (TOML ``[params.<name>]``). ``prev`` is reserved: it
# is the implicit carry (arg #0), so a param may not shadow it.
PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
RESERVED_PARAM_NAMES = frozenset({"prev"})
# Optional TOML ``class`` on a pipeline. ``system`` is the product/ops class
# (new-folder, later remotes…) — users can set the same on their own copies.
TASK_CLASS_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SYSTEM_TASK_CLASS = "system"
FOLDER_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Documented non-goals (T+P0). Kept as data so tests can pin the contract.
TASKPLUS_NON_GOALS = (
    "replace /loop or /rail",
    "visual BPMN editor",
    "cross-project workflow server",
    "implicit AI-chosen edges without trailer/schema",
    "make every stock task a graph",
    "unbounded retries inside a pipe",
)

TASKS_DIRNAME = "tasks"   # under .xlii/
RUNS_DIRNAME = ".runs"    # under .xlii/tasks/

# Default truncate-on-inject budget; the FULL carry is always persisted/returned,
# only what gets injected into the *next* step is trimmed (an agent step handed
# 2 MB of stdout would otherwise blow the context budget).
DEFAULT_CARRY_MAX_CHARS = 20_000

_TASKS_DEFAULTS: dict[str, Any] = {
    "keep_going": False,
    "confirm_shell": True,
    "carry_max_chars": DEFAULT_CARRY_MAX_CHARS,
    "agent_step_model": None,
}


# --------------------------------------------------------------------------- #
#  Errors
# --------------------------------------------------------------------------- #

class TaskError(Exception):
    """Base error for the tasks engine."""


class TaskParseError(TaskError):
    """A pipeline (inline or TOML) is malformed."""


class TaskNotFound(TaskError):
    """A named saved pipeline does not exist."""


# --------------------------------------------------------------------------- #
#  Model
# --------------------------------------------------------------------------- #

@dataclass
class Step:
    """One pipe stage. ``body`` is the executable text:

    - shell → the command line
    - agent → the prompt (no leading ``?``)
    - slash → the full ``/command …`` line
    """

    kind: str
    body: str
    raw: str = ""
    continue_on_error: bool = False
    id: str = ""
    on_success: str = ""
    on_failure: str = ""
    # T+P3 split/join: a split step runs its ``split`` branch ids concurrently,
    # then continues at ``join``. ``policy`` decides success (all/any/first_ok).
    # A step with a non-empty ``split`` executes no body of its own.
    split: list[str] = field(default_factory=list)
    join: str = ""
    policy: str = "all"

    def is_split(self) -> bool:
        return bool(self.split)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Step":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Edge:
    """A Task+ branch arm (TOML ``[[edge]]``). Only agent steps emit the verdict."""

    from_id: str
    branch: str
    to_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Edge":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Param:
    """A declared task parameter (TOML ``[params.<name>]``). A value *binds* into
    a ``{{name}}`` token; it never computes. ``{{prev}}`` is the implicit arg #0."""

    name: str
    required: bool = False
    default: str = ""
    enum: list[str] = field(default_factory=list)
    help: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Param":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class Pipeline:
    name: str
    steps: list[Step]
    description: str = ""
    edges: list[Edge] = field(default_factory=list)
    params: list[Param] = field(default_factory=list)
    task_class: str = ""  # TOML ``class`` — ``system`` is the product/ops class


@dataclass
class StepOutcome:
    index: int            # 1-based
    kind: str
    resolved: str         # the command/prompt actually executed (post-substitution)
    carry: str            # full text output of this step
    ok: bool
    detail: str = ""      # exit/err note when not ok
    blocked: bool = False  # the security gate refused it


@dataclass
class PipelineOutcome:
    steps: list[StepOutcome]
    carry: str
    ok: bool                       # False if a non-continue step failed/blocked
    had_errors: bool = False       # any step not ok (even if --keep-going carried on)
    failed_index: Optional[int] = None  # 0-based index of the stopping step


@dataclass
class TaskRun:
    """Persisted run state for ``/tasks resume`` (mirrors LoopController.load/save)."""

    version: int = 1
    name: str = "inline"
    run_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    status: str = "active"   # active | done | failed | cancelled
    cursor: int = 0          # 0-based index of the NEXT step to run
    cursor_id: str = ""      # step id at cursor (stable resume anchor; D6)
    carry: str = ""          # carry after the last completed step
    carry0: str = ""
    keep_going: bool = False
    last_error: str = ""
    steps: list[dict] = field(default_factory=list)  # [{kind, body, ...}]
    edges: list[dict] = field(default_factory=list)  # [{from_id, branch, to_id}]
    verdict_routed_to: Optional[int] = None  # selected branch arm to stop after on resume
    untrusted_pending: bool = False  # carry came through an agent step; re-gate the next shell/slash on resume
    args: dict = field(default_factory=dict)  # bound task params, restored on resume so {{name}} still resolves
    split_carries: dict = field(default_factory=dict)  # T+P3: per-branch carries ({{arm.<id>}} on resume)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "TaskRun":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    def pipeline(self) -> Pipeline:
        return Pipeline(
            name=self.name,
            steps=[Step.from_dict(s) for s in self.steps],
            edges=[Edge.from_dict(e) for e in self.edges],
        )


Emitter = Callable[..., None]


def _noop(*_a: Any, **_k: Any) -> None:
    return None


_CHECKPOINT_UNSET = object()

