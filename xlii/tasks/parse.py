"""Task pipeline parsing, classification, and bind-only substitution."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any, Optional

from .model import (
    JOIN_POLICIES,
    KIND_AGENT,
    KIND_SHELL,
    KIND_SLASH,
    PIPE_SEP,
    PREV_TOKEN,
    RAW_TOKEN,
    STEP_ID_RE,
    TASKPLUS_TRAILER_PREFIX,
    Param,
    Pipeline,
    Step,
    TaskParseError,
)

# --------------------------------------------------------------------------- #
#  Parsing & classification
# --------------------------------------------------------------------------- #

def classify_step(raw: str) -> Step:
    """Infer a step's type from its leading prefix (the flip-mode grammar):

    ``?…`` → agent · ``/…`` → slash · anything else → shell.
    """
    s = raw.strip()
    if not s:
        raise TaskParseError("empty step")
    if s.startswith("?"):
        return Step(kind=KIND_AGENT, body=s[1:].strip(), raw=s)
    if s.startswith("/"):
        return Step(kind=KIND_SLASH, body=s, raw=s)
    return Step(kind=KIND_SHELL, body=s, raw=s)


def parse_inline(inline: str, *, name: str = "inline") -> Pipeline:
    """Parse a one-line ``a |> b |> c`` pipe into a :class:`Pipeline`."""
    text = (inline or "").strip()
    if not text:
        raise TaskParseError("no steps — usage: /tasks run 'cmd |> ?prompt |> /slash'")
    steps: list[Step] = []
    for chunk in text.split(PIPE_SEP):
        if chunk.strip() == "":
            raise TaskParseError("empty step in pipeline (check your `|>` separators)")
        steps.append(classify_step(chunk))
    return Pipeline(name=name, steps=steps)


def pipeline_has_branching(pipeline: Pipeline) -> bool:
    """True when a saved pipeline carries Task+ gates, verdict edges, or splits."""
    return bool(pipeline.edges) or any(
        s.on_success or s.on_failure or s.is_split() for s in pipeline.steps
    )


def split_taskplus_trailer(text: str) -> tuple[str, Optional[dict[str, Any]]]:
    """Split human carry from the last-line ``TASK+ {json}`` trailer (if any)."""
    lines = (text or "").rstrip().splitlines()
    if not lines:
        return text or "", None
    last = lines[-1].strip()
    if not last.startswith(TASKPLUS_TRAILER_PREFIX):
        return text, None
    payload = last[len(TASKPLUS_TRAILER_PREFIX):].strip()
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return text, None
    if not isinstance(data, dict):
        return text, None
    human = "\n".join(lines[:-1])
    return human, data


def edges_by_from(pipeline: Pipeline) -> dict[str, dict[str, str]]:
    """``from_id -> {branch label -> to_id}`` for agent verdict routing."""
    out: dict[str, dict[str, str]] = {}
    for edge in pipeline.edges:
        out.setdefault(edge.from_id, {})[edge.branch] = edge.to_id
    return out


def _normalize_step_id(raw: str, *, index: int, where: str) -> str:
    sid = (raw or str(index)).strip()
    if not STEP_ID_RE.match(sid):
        raise TaskParseError(
            f"{where}: step id {sid!r} must match [a-z0-9-] (or omit for index default)"
        )
    return sid


def step_index_maps(pipeline: Pipeline) -> tuple[dict[str, int], list[str]]:
    """Build ``id -> 0-based index`` and the resolved id list (1-based default)."""
    id_to_index: dict[str, int] = {}
    ids: list[str] = []
    for i, step in enumerate(pipeline.steps):
        sid = _normalize_step_id(step.id, index=i + 1, where=f"step {i + 1}")
        if sid in id_to_index:
            raise TaskParseError(f"duplicate step id {sid!r}")
        id_to_index[sid] = i
        ids.append(sid)
    for i, step in enumerate(pipeline.steps, 1):
        for label, target in (("on_success", step.on_success), ("on_failure", step.on_failure)):
            if target and target not in id_to_index:
                raise TaskParseError(f"step {i}: {label} targets unknown id {target!r}")
    seen_branches: dict[str, set[str]] = {}
    for j, edge in enumerate(pipeline.edges, 1):
        if edge.from_id not in id_to_index:
            raise TaskParseError(f"edge {j}: from {edge.from_id!r} is not a step id")
        if edge.to_id not in id_to_index:
            raise TaskParseError(f"edge {j}: to {edge.to_id!r} is not a step id")
        if not edge.branch:
            raise TaskParseError(f"edge {j}: when.branch is required")
        taken = seen_branches.setdefault(edge.from_id, set())
        if edge.branch in taken:
            raise TaskParseError(
                f"edge {j}: duplicate branch {edge.branch!r} from {edge.from_id!r}"
            )
        taken.add(edge.branch)
    # T+P3 split/join validation.
    param_names = {p.name for p in pipeline.params}
    global_branches: set[str] = set()
    all_joins: set[str] = set()
    for i, step in enumerate(pipeline.steps, 1):
        if not step.is_split():
            continue
        if step.policy not in JOIN_POLICIES:
            raise TaskParseError(
                f"step {i}: split policy {step.policy!r} must be one of {sorted(JOIN_POLICIES)}"
            )
        if not step.join:
            raise TaskParseError(f"step {i}: a split step needs a join target")
        if step.join not in id_to_index:
            raise TaskParseError(f"step {i}: join targets unknown id {step.join!r}")
        sid = ids[i - 1]
        if step.join == sid:
            raise TaskParseError(f"step {i}: join {step.join!r} cannot be the split step itself")
        all_joins.add(step.join)
        seen: set[str] = set()
        for bid in step.split:
            if bid not in id_to_index:
                raise TaskParseError(f"step {i}: split branch {bid!r} is not a step id")
            if bid in seen:
                raise TaskParseError(f"step {i}: duplicate split branch {bid!r}")
            seen.add(bid)
            if bid in (sid, step.join):
                raise TaskParseError(
                    f"step {i}: split branch {bid!r} cannot be the split step or its join"
                )
            if bid in global_branches:
                raise TaskParseError(f"step {i}: split branch {bid!r} is used by more than one split")
            if bid == "prev" or bid in param_names:
                raise TaskParseError(
                    f"step {i}: split branch {bid!r} collides with the carry/param name "
                    f"— 'prev' and declared params are reserved binding names"
                )
            global_branches.add(bid)
            branch = pipeline.steps[id_to_index[bid]]
            if branch.kind != KIND_SHELL:
                raise TaskParseError(
                    f"step {i}: split branch {bid!r} must be a shell step "
                    "(agent/slash fan-out is not supported yet)"
                )
            if branch.is_split():
                raise TaskParseError(
                    f"step {i}: split branch {bid!r} cannot itself be a split (no nesting)"
                )
            if branch.on_success or branch.on_failure:
                raise TaskParseError(
                    f"step {i}: split branch {bid!r} may not declare on_success/on_failure "
                    "(a branch runs inside its split, not in linear flow)"
                )
    # A branch id may be reached ONLY by its split — never by a join / on_success /
    # on_failure / edge, or ``_skip_branches`` would swallow that deliberate jump and
    # silently land on the wrong step.
    routing_targets: set[str] = set(all_joins)
    for step in pipeline.steps:
        if step.on_success:
            routing_targets.add(step.on_success)
        if step.on_failure:
            routing_targets.add(step.on_failure)
    for edge in pipeline.edges:
        routing_targets.add(edge.to_id)
    clash = global_branches & routing_targets
    if clash:
        raise TaskParseError(
            f"split branch(es) {sorted(clash)} are also a routing target "
            "(join/on_success/on_failure/edge) — a split branch may be reached only by its split"
        )
    return id_to_index, ids


def split_branch_ids(pipeline: Pipeline) -> set[str]:
    """Every step id that is a branch of some split — these run only *via* their
    split, never in linear order."""
    out: set[str] = set()
    for step in pipeline.steps:
        out.update(step.split)
    return out


# A step template token: ``{{name}}`` or ``{{name:raw}}``. Names bind to values
# from the substitution environment; they never *compute* (no expressions).
_TOKEN_RE = re.compile(r"\{\{([A-Za-z0-9_.-]+)(:raw)?\}\}")


def references_prev(template: str) -> bool:
    return PREV_TOKEN in template or RAW_TOKEN in template


def references(template: str, name: str) -> bool:
    """True when ``template`` uses ``{{name}}`` or ``{{name:raw}}``."""
    return ("{{" + name + "}}") in template or ("{{" + name + ":raw}}") in template


def substitute(template: str, bindings: dict[str, str], *, kind: str) -> str:
    """Substitute a **bind-only** environment into a step template.

    ``bindings`` maps a name to its value; the carry ``{{prev}}`` is simply entry
    #0 of that environment (see :func:`substitute_prev`). For each name,
    ``{{name:raw}}`` inserts the value verbatim, while ``{{name}}`` is
    **shell-quoted** in a shell step (a single token — blunts argument injection)
    and raw in agent/slash steps (no shell parses those). Unknown tokens are left
    untouched, and it is a single pass, so a value can never be re-scanned into
    another binding's token. Names *bind*; they never compute (no expressions).
    """
    def _repl(m: "re.Match[str]") -> str:
        name, raw = m.group(1), m.group(2)
        if name not in bindings:
            return m.group(0)
        value = str(bindings[name])
        if raw:
            return value
        return shlex.quote(value) if kind == KIND_SHELL else value

    return _TOKEN_RE.sub(_repl, template)


def substitute_prev(template: str, carry: str, *, kind: str) -> str:
    """The carry-only case of :func:`substitute` — ``{{prev}}`` is arg #0."""
    return substitute(template, {"prev": carry}, kind=kind)


def bind_task_args(params: "list[Param]", tokens: "list[str]") -> dict[str, str]:
    """Resolve invocation tokens against declared params → ``{name: value}``.

    ``name=value`` (or ``--name=value``) binds by name; a bare token fills the
    next unbound declared param in declaration order. Fail **closed**
    (``TaskParseError``) on an unknown ``--flag``, an extra positional, a missing
    required param, or an enum violation; declared defaults fill the rest. Runs at
    invocation, before the pipeline starts (missing-required fails now, not mid-run).
    """
    by_name = {p.name: p for p in params}
    order = list(params)
    bound: dict[str, str] = {}
    pos = 0
    for tok in tokens:
        if "=" in tok:
            lhs = tok.split("=", 1)[0]
            bare = lhs.lstrip("-")
            if bare in by_name:
                bound[bare] = tok.split("=", 1)[1]
                continue
            if lhs.startswith("--"):
                raise TaskParseError(f"unknown param {lhs!r}")
            # else: a bare positional whose value happens to contain '='
        while pos < len(order) and order[pos].name in bound:
            pos += 1
        if pos >= len(order):
            raise TaskParseError(
                f"unexpected argument {tok!r} (task takes {len(order)} param(s))"
            )
        bound[order[pos].name] = tok
        pos += 1
    for p in params:
        if p.name not in bound and p.default:
            bound[p.name] = p.default
        if p.name not in bound and p.required:
            hint = f" — {p.help}" if p.help else ""
            raise TaskParseError(f"missing required param '{p.name}'{hint}")
        if p.name in bound and p.enum and bound[p.name] not in p.enum:
            raise TaskParseError(
                f"param '{p.name}' must be one of {', '.join(p.enum)} (got {bound[p.name]!r})"
            )
    return bound


def truncate_for_inject(carry: str, max_chars: Optional[int]) -> str:
    """Head+tail trim of an over-budget carry before it is injected downstream.

    The full carry is still persisted/returned; only the injected copy shrinks.
    """
    if not max_chars or max_chars <= 0 or len(carry) <= max_chars:
        return carry
    # Avoid degenerate output for tiny budgets (for example max_chars == 1).
    # In that case, a simple hard cut is clearer than head/tail+elision markup.
    if max_chars < 3:
        return carry[:max_chars]
    head = max_chars // 2
    tail = max_chars - head
    if head + tail >= len(carry):
        return carry
    elided = len(carry) - head - tail
    return f"{carry[:head]}\n…[{elided} chars elided]…\n{carry[-tail:]}"


