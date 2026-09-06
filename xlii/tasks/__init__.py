"""`/tasks` — the horizontal pipe primitive (see proposals/tasks-pipe-workflow.md).

An ordered pipe of steps where each step's text output (the *carry*) flows into
the next. A step is a **shell command**, an **agent prompt** (``?…``), or a
**slash command** (``/…``). This is the deterministic, user-ordered complement
to ``/loop``'s churn-to-green: ``/tasks`` is *"do A, pipe to B, hand both to C."*

This module is the **engine** (parser + sequential runner + persistence); the
REPL surface lives in :mod:`xlii.repl_cmds.tasks`.

**Task+ linear default** (``proposals/task+plus.md`` T+P0): every pipeline is
strictly sequential until an opt-in edge / gate / split is present. A pipe with
no Task+ features must behave byte-identically to today's runner. **T+P2** adds
``[[edge]]`` verdict arms driven by a last-line agent ``TASK+ {json}`` trailer
(fail closed when edges exist but the trailer is missing or unmatched).
**not** replace ``/loop`` or ``/rail``, invent a BPMN editor, allow unbounded
retries inside a pipe, or choose edges from free-form AI prose without a
validated trailer/schema.

Merge-contract note (interaction-layer-II, Vector F): slash-step capture
*consumes* Vector B's capture seam (#3). :func:`_scrape_slash_output` is the one
capture chokepoint — it delegates to ``shell_toolkit.run_capturing`` when that
seam is present, and otherwise records locally with a throwaway ``rich.Console``
so the vector ships green in isolation. There is intentionally no *second*
long-lived capturing console: when B lands, this one call site is the only swap.
"""

from __future__ import annotations

# Barrel re-export of the former god-file surface. Names not in __all__ are
# still imported so `from xlii import tasks; tasks.X` keeps working.
# ruff: noqa: F401

from .model import (
    DEFAULT_CARRY_MAX_CHARS,
    FOLDER_NAME_RE,
    JOIN_POLICIES,
    KIND_AGENT,
    KIND_SHELL,
    KIND_SLASH,
    PARAM_NAME_RE,
    PIPE_SEP,
    PREV_TOKEN,
    RAW_TOKEN,
    RESERVED_PARAM_NAMES,
    RUNS_DIRNAME,
    STEP_ID_RE,
    SYSTEM_TASK_CLASS,
    TASK_CLASS_RE,
    TASKPLUS_CONTROL_FIELDS,
    TASKPLUS_NON_GOALS,
    TASKPLUS_TRAILER_PREFIX,
    TASKS_DIRNAME,
    Edge,
    Emitter,
    Param,
    Pipeline,
    PipelineOutcome,
    Step,
    StepOutcome,
    TaskError,
    TaskNotFound,
    TaskParseError,
    TaskRun,
    _CHECKPOINT_UNSET,
    _CONSOLE_SWAP_LOCK,
    _TASKS_DEFAULTS,
    _noop,
)
from .parse import (
    _TOKEN_RE,
    _normalize_step_id,
    bind_task_args,
    classify_step,
    edges_by_from,
    parse_inline,
    pipeline_has_branching,
    references,
    references_prev,
    split_branch_ids,
    split_taskplus_trailer,
    step_index_maps,
    substitute,
    substitute_prev,
    truncate_for_inject,
)
from .plan import enumerate_paths, render_plan
from .store import (
    _KIND_TO_TOML,
    _SKELETON,
    _TOML_KEY_TO_KIND,
    _read_pipeline_toml,
    _read_task_class,
    _slug,
    _toml_str,
    claim_folder_name,
    emit_pipeline_toml,
    iter_runs,
    latest_run,
    list_pipeline_entries,
    list_pipelines,
    listing_badge,
    load_pipeline,
    new_folder_command,
    new_run,
    peek_task_class,
    pipeline_as_spec,
    pipeline_file,
    pipeline_origin,
    pipeline_path,
    prune_runs,
    resolve_tasks_defaults,
    run_file,
    runs_dir,
    save_run,
    scaffold_pipeline,
    stock_tasks_dir,
    tasks_dir,
    write_pipeline_spec,
    write_pipeline_toml,
)
from .engine import (
    _agent_prompt,
    _arm_bindings,
    _checkpoint,
    _confirm_step,
    _gate_prompt,
    _join_digest,
    _last_assistant_text,
    _record_shell_capture,
    _repl_scope,
    _resolve_cwd,
    _resolve_next_index,
    _resolve_split_branches,
    _run_agent_step,
    _run_shell_step,
    _run_split,
    _run_split_shell,
    _scrape_slash_output,
    _scrape_slash_output_step,
    _taskplus_branch_error,
    pipeline_in_flight,
    run_pipeline,
    validate_pipeline,
)

__all__ = [
    "PIPE_SEP", "PREV_TOKEN", "RAW_TOKEN",
    "KIND_SHELL", "KIND_AGENT", "KIND_SLASH",
    "TaskError", "TaskParseError", "TaskNotFound",
    "Step", "Pipeline", "StepOutcome", "PipelineOutcome", "TaskRun",
    "classify_step", "parse_inline", "references_prev", "substitute_prev",
    "truncate_for_inject", "validate_pipeline", "run_pipeline", "pipeline_in_flight",
    "render_plan",
    "tasks_dir", "runs_dir", "pipeline_path", "list_pipelines", "load_pipeline",
    "scaffold_pipeline", "write_pipeline_toml",
    "pipeline_as_spec", "emit_pipeline_toml", "write_pipeline_spec",
    "new_run", "save_run", "iter_runs", "latest_run", "prune_runs", "run_file",
    "resolve_tasks_defaults",
]
