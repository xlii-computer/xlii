"""Task pipeline dry-run path enumeration and plan rendering."""

from __future__ import annotations

from typing import Optional

from .model import KIND_AGENT, KIND_SHELL, Pipeline
from .parse import (
    edges_by_from,
    pipeline_has_branching,
    split_branch_ids,
    step_index_maps,
)

# --------------------------------------------------------------------------- #
#  Rendering (dry-run / show)
# --------------------------------------------------------------------------- #

def enumerate_paths(pipeline: Pipeline, *, max_paths: int = 64) -> list[str]:
    """Every distinct control-flow route through a branching pipe (T+P4 dry-run
    preview). A hop reads ``a ─label→ b``: a plain ``→`` for linear flow, ``ok`` /
    ``fail`` for a shell rc gate, ``branch=<label>`` for a verdict edge, or
    ``split{a‖b‖c}·policy`` for a fan-out. A route that would revisit a step is cut
    with ``↻`` (the runner fails closed there); ``⊘`` marks a branch that stops.
    Capped at ``max_paths`` (a trailing ``…`` means more routes exist)."""
    id_to_index, step_ids = step_index_maps(pipeline)
    steps = pipeline.steps
    n = len(steps)
    by_from = edges_by_from(pipeline)
    branch_indices = {id_to_index[b] for b in split_branch_ids(pipeline)}

    def skip(idx: Optional[int]) -> Optional[int]:
        while idx is not None and idx < n and idx in branch_indices:
            idx += 1
        return idx if (idx is not None and idx < n) else None

    def transitions(i: int, routed: bool) -> list[tuple[Optional[str], Optional[int], bool]]:
        """(label, target, target-is-a-verdict-arm) hops. ``routed`` means this step
        was itself reached by a verdict edge, so it stops after running unless it is
        an agent that re-routes (mirrors the runner's ``stop_after_current``)."""
        step = steps[i]
        sid = step_ids[i]
        if step.is_split():
            return [(f"split{{{'‖'.join(step.split)}}}·{step.policy}", skip(id_to_index[step.join]), False)]
        outs = by_from.get(sid)
        if step.kind == KIND_AGENT and outs:
            return [(f"branch={b}", skip(id_to_index[t]), True) for b, t in sorted(outs.items())]

        def _fail_target() -> Optional[int]:
            # A shell's failure route: on_failure, else continue_on_error → next,
            # else stop. (--keep-going is a runtime flag, not an authored route.)
            if step.on_failure:
                return skip(id_to_index[step.on_failure])
            return skip(i + 1) if step.continue_on_error else None

        if routed:
            # A verdict arm stops after it runs (stop_after_current) — EXCEPT a shell
            # arm that FAILS still follows on_failure / continue_on_error, because the
            # runner's stop only wins when the arm succeeds.
            if step.kind == KIND_SHELL and (step.on_failure or step.continue_on_error):
                return [("ok", None, False), ("fail", _fail_target(), False)]
            return []
        if step.kind == KIND_SHELL and (step.on_success or step.on_failure):
            succ = skip(id_to_index[step.on_success]) if step.on_success else skip(i + 1)
            return [("ok", succ, False), ("fail", _fail_target(), False)]
        return [(None, skip(i + 1), False)]

    paths: list[str] = []
    truncated = False

    def dfs(i: int, prefix: str, visited: "frozenset[int]", routed: bool, depth: int) -> None:
        nonlocal truncated
        if len(paths) >= max_paths:
            truncated = True
            return
        if depth > 256:  # runner is iterative; bound preview recursion on a huge chain
            paths.append(f"{prefix}…")
            return
        sid = step_ids[i]
        if i in visited:
            paths.append(f"{prefix}{sid} ↻")
            return
        visited = visited | {i}
        node = f"{prefix}{sid}"
        trans = transitions(i, routed)
        if not trans:
            paths.append(node)  # terminal (a routed arm, or a step with no next)
            return
        for label, target, child_routed in trans:
            if len(paths) >= max_paths:
                truncated = True
                return
            arrow = " → " if label is None else f" ─{label}→ "
            if target is None:
                paths.append(node if label is None else f"{node}{arrow}⊘")
            else:
                dfs(target, f"{node}{arrow}", visited, child_routed, depth + 1)

    start = skip(0)
    if start is None:
        return []
    dfs(start, "", frozenset(), False, 0)
    if truncated:
        paths.append("…")
    return paths


def render_plan(pipeline: Pipeline) -> list[str]:
    lines = [f"pipeline: {pipeline.name} ({len(pipeline.steps)} step"
             f"{'s' if len(pipeline.steps) != 1 else ''})"]
    if pipeline.description:
        lines.append(f"  {pipeline.description}")
    if pipeline.task_class:
        lines.append(f"  class: {pipeline.task_class}")
    if pipeline.params:
        lines.append("  params:")
        for p in pipeline.params:
            bits = []
            if p.required:
                bits.append("required")
            if p.default:
                bits.append(f"default={p.default}")
            if p.enum:
                bits.append("one of " + "|".join(p.enum))
            meta = f" ({', '.join(bits)})" if bits else ""
            desc = f" — {p.help}" if p.help else ""
            lines.append(f"    {{{{{p.name}}}}}{meta}{desc}")
    id_to_index, step_ids = step_index_maps(pipeline)
    for i, s in enumerate(pipeline.steps, 1):
        sid = step_ids[i - 1]
        tag = f" [{sid}]" if sid != str(i) else ""
        if s.is_split():
            lines.append(f"  {i}.{tag} [split] {', '.join(s.split)}")
            lines.append(f"      policy {s.policy} → join {s.join}")
            continue
        lines.append(f"  {i}.{tag} [{s.kind}] {s.raw or s.body}")
        if s.kind == KIND_SHELL and (s.on_success or s.on_failure):
            succ = s.on_success or "(next)"
            fail = s.on_failure or "(stop)"
            lines.append(f"      on success → {succ} · on failure → {fail}")
    by_from = edges_by_from(pipeline)
    for sid in step_ids:
        outs = by_from.get(sid)
        if not outs:
            continue
        arms = ", ".join(f"{branch} → {target}" for branch, target in sorted(outs.items()))
        lines.append(f"      verdict edges [{sid}]: {arms}")
    # T+P4 — path preview: the distinct routes control can take (branching pipes).
    if pipeline_has_branching(pipeline):
        routes = enumerate_paths(pipeline)
        if routes:
            shown = [r for r in routes if r != "…"]
            suffix = "+" if routes and routes[-1] == "…" else ""
            lines.append(f"  paths ({len(shown)}{suffix}):")
            for r in routes:
                lines.append(f"    • {r}")
    return lines


