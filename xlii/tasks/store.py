"""Task pipeline persistence: saved TOML pipes and run-state JSON."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from xlii.atomicio import write_text_atomic

from .model import (
    FOLDER_NAME_RE,
    KIND_AGENT,
    KIND_SHELL,
    KIND_SLASH,
    PARAM_NAME_RE,
    RESERVED_PARAM_NAMES,
    RUNS_DIRNAME,
    STEP_ID_RE,
    TASK_CLASS_RE,
    TASKS_DIRNAME,
    Edge,
    Param,
    Pipeline,
    Step,
    TaskError,
    TaskNotFound,
    TaskParseError,
    TaskRun,
    _TASKS_DEFAULTS,
)
from .parse import step_index_maps

# --------------------------------------------------------------------------- #
#  Persistence — saved pipelines (.xlii/tasks/*.toml)
# --------------------------------------------------------------------------- #

def tasks_dir(xli_dir: Path | str) -> Path:
    return Path(xli_dir) / TASKS_DIRNAME


def stock_tasks_dir() -> Path:
    """Bundled read-only showcase pipelines (Track E0)."""
    return Path(__file__).resolve().parent.parent / "stock_tasks"


def runs_dir(xli_dir: Path | str) -> Path:
    return tasks_dir(xli_dir) / RUNS_DIRNAME


def pipeline_path(xli_dir: Path | str, name: str) -> Path:
    return tasks_dir(xli_dir) / f"{name}.toml"


def list_pipelines(xli_dir: Path | str) -> list[str]:
    return [name for name, _origin in list_pipeline_entries(xli_dir)]


def list_pipeline_entries(xli_dir: Path | str) -> list[tuple[str, str]]:
    """Sorted ``(name, origin)`` pairs — ``origin`` is ``project`` or ``stock``."""
    project: set[str] = set()
    d = tasks_dir(xli_dir)
    if d.exists():
        project = {p.stem for p in d.glob("*.toml")}
    stock: set[str] = set()
    sdir = stock_tasks_dir()
    if sdir.is_dir():
        stock = {p.stem for p in sdir.glob("*.toml")}
    out: list[tuple[str, str]] = []
    for name in sorted(project):
        out.append((name, "project"))
    for name in sorted(stock - project):
        out.append((name, "stock"))
    return out


def pipeline_origin(xli_dir: Path | str, name: str) -> str:
    if pipeline_path(xli_dir, name).exists():
        return "project"
    if (stock_tasks_dir() / f"{name}.toml").is_file():
        return "stock"
    raise TaskNotFound(f"no saved pipeline named {name!r}")


def _read_pipeline_toml(path: Path, name: str) -> Pipeline:
    """Parse one on-disk pipeline TOML (project or stock)."""
    import tomllib

    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise TaskParseError(f"{name}.toml is not valid TOML: {e}") from e

    raw_steps = data.get("step")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise TaskParseError(f"{name}.toml has no [[step]] blocks")

    steps: list[Step] = []
    for i, blk in enumerate(raw_steps, 1):
        if not isinstance(blk, dict):
            raise TaskParseError(f"{name}.toml step {i} is not a table")
        # T+P3: a split step fans out to branch ids and has no body of its own.
        raw_split = blk.get("split")
        if raw_split is not None:
            if not isinstance(raw_split, list) or not raw_split:
                raise TaskParseError(
                    f"{name}.toml step {i}: split must be a non-empty list of step ids"
                )
            if any(str(blk.get(k, "")).strip() for k in ("run", "ask", "slash")):
                raise TaskParseError(
                    f"{name}.toml step {i}: a split step takes no run/ask/slash body"
                )
            branches = [str(b).strip() for b in raw_split]
            steps.append(Step(
                kind=KIND_SHELL,  # nominal; a split executes no body (dispatched via is_split)
                body="",
                raw="split[" + ", ".join(branches) + "]",
                continue_on_error=bool(blk.get("continue_on_error", False)),
                id=str(blk.get("id", "") or "").strip(),
                split=branches,
                join=str(blk.get("join", "") or "").strip(),
                policy=str(blk.get("policy", "all") or "all").strip(),
            ))
            continue
        present = [k for k in ("run", "ask", "slash") if str(blk.get(k, "")).strip()]
        if len(present) != 1:
            raise TaskParseError(
                f"{name}.toml step {i}: needs exactly one of run/ask/slash "
                f"(found {present or 'none'})"
            )
        key = present[0]
        val = str(blk[key]).strip()
        kind = _TOML_KEY_TO_KIND[key]
        if kind == KIND_SLASH and not val.startswith("/"):
            raise TaskParseError(f"{name}.toml step {i}: slash step must start with '/'")
        steps.append(Step(
            kind=kind,
            body=val,
            raw=val,
            continue_on_error=bool(blk.get("continue_on_error", False)),
            id=str(blk.get("id", "") or "").strip(),
            on_success=str(blk.get("on_success", "") or "").strip(),
            on_failure=str(blk.get("on_failure", "") or "").strip(),
        ))

    edges: list[Edge] = []
    raw_edges = data.get("edge")
    if raw_edges is not None:
        if not isinstance(raw_edges, list):
            raise TaskParseError(f"{name}.toml edge must be a list of tables")
        for j, blk in enumerate(raw_edges, 1):
            if not isinstance(blk, dict):
                raise TaskParseError(f"{name}.toml edge {j} is not a table")
            from_id = str(blk.get("from", "") or "").strip()
            to_id = str(blk.get("to", "") or "").strip()
            when = blk.get("when")
            branch = ""
            if isinstance(when, dict):
                branch = str(when.get("branch", "") or "").strip()
            if not from_id or not to_id:
                raise TaskParseError(f"{name}.toml edge {j}: from and to are required")
            edges.append(Edge(from_id=from_id, branch=branch, to_id=to_id))

    params: list[Param] = []
    raw_params = data.get("params")
    if raw_params is not None:
        if not isinstance(raw_params, dict):
            raise TaskParseError(f"{name}.toml [params] must be a table of param specs")
        for pname, spec in raw_params.items():
            if not PARAM_NAME_RE.match(pname):
                raise TaskParseError(
                    f"{name}.toml param {pname!r}: name must match [a-z][a-z0-9_-]*"
                )
            if pname in RESERVED_PARAM_NAMES:
                raise TaskParseError(f"{name}.toml param {pname!r} is reserved")
            if not isinstance(spec, dict):
                raise TaskParseError(f"{name}.toml [params.{pname}] must be a table")
            enum = spec.get("enum", [])
            if enum and not isinstance(enum, list):
                raise TaskParseError(f"{name}.toml param {pname!r}: enum must be a list")
            params.append(Param(
                name=pname,
                required=bool(spec.get("required", False)),
                default=str(spec.get("default", "") or ""),
                enum=[str(x) for x in enum],
                help=str(spec.get("help", "") or ""),
            ))

    pipe = Pipeline(
        name=str(data.get("name", name)),
        steps=steps,
        description=str(data.get("description", "")),
        edges=edges,
        params=params,
        task_class=_read_task_class(data, name),
    )
    step_index_maps(pipe)
    return pipe


_TOML_KEY_TO_KIND = {"run": KIND_SHELL, "ask": KIND_AGENT, "slash": KIND_SLASH}


def _read_task_class(data: dict, name: str) -> str:
    raw = str(data.get("class", "") or "").strip()
    if not raw:
        return ""
    if not TASK_CLASS_RE.match(raw):
        raise TaskParseError(
            f"{name}.toml class {raw!r}: must match [a-z][a-z0-9_-]* (max 32)"
        )
    return raw


def peek_task_class(path: Path) -> str:
    """Best-effort ``class`` from a pipeline TOML (empty on missing/bad file)."""
    import tomllib

    try:
        data = tomllib.loads(Path(path).read_text())
    except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    raw = str(data.get("class", "") or "").strip()
    return raw if TASK_CLASS_RE.match(raw) else ""


def pipeline_file(xli_dir: Path | str, name: str) -> Path:
    """Winning on-disk file for *name* (project copy, else stock)."""
    path = pipeline_path(xli_dir, name)
    if path.exists():
        return path
    return stock_tasks_dir() / f"{name}.toml"


def claim_folder_name(raw: str) -> str:
    """A simple folder name — no path separators, no spaces.

    Used by ``xlii new`` and Project → New project folder (the system task).
    """
    name = (raw or "").strip()
    if not name or name in (".", "..") or not FOLDER_NAME_RE.fullmatch(name):
        raise TaskParseError("need a simple name (letters, digits, . _ -)")
    return name


def new_folder_command(name: str, *, kind: str = "code") -> str:
    """The exact ``/tasks run new-folder …`` line the New-project-folder door submits."""
    safe = claim_folder_name(name)
    stamped = (kind or "code").strip() or "code"
    if stamped not in ("code", "collection"):
        raise TaskParseError(f"unknown kind {stamped!r}")
    bits = ["/tasks", "run", "new-folder", safe]
    if stamped != "code":
        bits.append(f"kind={stamped}")
    bits.append("--yes")
    return " ".join(bits)


def listing_badge(origin: str, task_class: str = "") -> str:
    """Pane / ``/tasks list`` badge. ``system`` wins over ``stock``."""
    klass = (task_class or "").strip()
    if klass:
        return klass
    if origin == "stock":
        return "stock"
    return ""


def load_pipeline(xli_dir: Path | str, name: str) -> Pipeline:
    """Load + validate a saved pipeline (project wins; else bundled stock)."""
    path = pipeline_path(xli_dir, name)
    if path.exists():
        return _read_pipeline_toml(path, name)
    stock_path = stock_tasks_dir() / f"{name}.toml"
    if stock_path.is_file():
        return _read_pipeline_toml(stock_path, name)
    raise TaskNotFound(f"no saved pipeline named {name!r} ({path})")


# NOTE: filled via ``.replace("%NAME%", name)`` (NOT str.format) so every {{ }}
# token below is emitted verbatim — the scaffold must teach the real substitution
# tokens, not braces mangled by format escaping.
_SKELETON = """# .xlii/tasks/%NAME%.toml — a /tasks pipeline.  Run it with: /tasks run %NAME%
# Full authoring guide: /describe tasks  (or docs/help/commands/tasks.md)
name = "%NAME%"
description = ""
# class = "system"   # optional — product/ops recipes; /tasks list badges it

# Each [[step]] has EXACTLY ONE of: run (shell) | ask (agent) | slash.
# Each step's text output (the "carry") flows into the next. Reference it with
# {{prev}} (shell-quoted in shell steps) or {{prev:raw}} (verbatim).

[[step]]
run = "echo hello"

# [[step]]
# ask = "summarize the previous output in one line"

# [[step]]
# slash = "/doc add notes {{prev}}"

# ── Task+ (add only what you need) ───────────────────────────────────────────
# PARAMS — declare inputs, reference each as {{base}} etc.:
#   [params.base]
#   default = "HEAD~1"
#   [[step]]
#   run = "git diff {{base}}"
#
# BRANCH BY EXIT CODE — give steps an id, route with on_success / on_failure:
#   [[step]]
#   id = "tests"
#   run = "pytest -q"
#   on_failure = "triage"   # one-sided: on failure ALSO run step "triage"
#
# BRANCH BY AGENT VERDICT (clean either/or) — an ask step ends with a FINAL line
# `TASK+ {"branch": "clean"}`, then [[edge]] tables route it:
#   [[edge]]
#   from = "classify"
#   when = { branch = "clean" }
#   to = "clean_step"
#
# PARALLEL FAN-OUT — run shell branch ids concurrently, then join:
#   [[step]]
#   id = "fan"
#   split = ["lint", "types"]
#   join = "report"
#   policy = "all"   # all | any | first_ok
"""


def scaffold_pipeline(xli_dir: Path | str, name: str, *, overwrite: bool = False) -> Path:
    """Write a skeleton ``.toml`` — one live step plus commented Task+ recipes."""
    path = pipeline_path(xli_dir, name)
    if path.exists() and not overwrite:
        raise TaskError(f"{path} already exists — use /tasks edit {name}")
    write_text_atomic(path, _SKELETON.replace("%NAME%", name), mode=0o644)
    return path


def write_pipeline_toml(xli_dir: Path | str, name: str, toml_text: str) -> Path:
    path = pipeline_path(xli_dir, name)
    write_text_atomic(path, toml_text, mode=0o644)
    return path


_KIND_TO_TOML = {KIND_SHELL: "run", KIND_AGENT: "ask", KIND_SLASH: "slash"}


def _toml_str(value: str) -> str:
    return json.dumps("" if value is None else str(value), ensure_ascii=False)


def pipeline_as_spec(pipe: Pipeline) -> dict[str, Any]:
    """JSON-safe view of a pipeline for the task maker."""
    steps: list[dict[str, Any]] = []
    for s in pipe.steps:
        if s.is_split():
            steps.append({
                "kind": "split",
                "id": s.id,
                "body": "",
                "split": list(s.split),
                "join": s.join,
                "policy": s.policy or "all",
            })
            continue
        steps.append({
            "kind": s.kind,
            "id": s.id,
            "body": s.body or s.raw,
            "on_success": s.on_success,
            "on_failure": s.on_failure,
        })
    params = [
        {
            "name": p.name,
            "required": bool(p.required),
            "default": p.default,
            "enum": list(p.enum),
            "help": p.help,
        }
        for p in pipe.params
    ]
    edges = [
        {"from": e.from_id, "branch": e.branch, "to": e.to_id}
        for e in pipe.edges
    ]
    return {
        "name": pipe.name,
        "description": pipe.description or "",
        "class": pipe.task_class or "",
        "steps": steps,
        "params": params,
        "edges": edges,
    }


def emit_pipeline_toml(spec: dict[str, Any]) -> str:
    """Write the maker's spec back to TOML (name, class, params, steps, edges)."""
    name = str(spec.get("name") or "").strip()
    if not name or name in (".", "..") or not FOLDER_NAME_RE.fullmatch(name):
        raise TaskParseError("task name must be letters, digits, . _ -")
    desc = str(spec.get("description") or "")
    klass = str(spec.get("class") or "").strip()
    if klass and not TASK_CLASS_RE.match(klass):
        raise TaskParseError(f"class {klass!r} is not a valid task class")
    steps = spec.get("steps") or []
    if not steps:
        raise TaskParseError("a task needs at least one step")
    lines = [
        f"name = {_toml_str(name)}",
    ]
    if klass:
        lines.append(f"class = {_toml_str(klass)}")
    if desc:
        lines.append(f"description = {_toml_str(desc)}")
    lines.append("")
    for p in spec.get("params") or []:
        pname = str(p.get("name") or "").strip()
        if not pname:
            continue
        if not PARAM_NAME_RE.match(pname) or pname in RESERVED_PARAM_NAMES:
            raise TaskParseError(f"bad param name {pname!r}")
        lines.append(f"[params.{pname}]")
        if p.get("required"):
            lines.append("required = true")
        if p.get("default"):
            lines.append(f"default = {_toml_str(str(p.get('default')))}")
        enum = p.get("enum") or []
        if enum:
            inner = ", ".join(_toml_str(str(x)) for x in enum)
            lines.append(f"enum = [{inner}]")
        if p.get("help"):
            lines.append(f"help = {_toml_str(str(p.get('help')))}")
        lines.append("")
    for i, raw in enumerate(steps, 1):
        kind = str(raw.get("kind") or KIND_SHELL).strip()
        sid = str(raw.get("id") or "").strip()
        lines.append("[[step]]")
        if sid:
            if not STEP_ID_RE.match(sid):
                raise TaskParseError(f"step {i}: id must be lowercase letters, digits, dashes")
            lines.append(f"id = {_toml_str(sid)}")
        if kind == "split":
            branches = [str(b).strip() for b in (raw.get("split") or []) if str(b).strip()]
            if not branches:
                raise TaskParseError(f"step {i}: split needs branch ids")
            inner = ", ".join(_toml_str(b) for b in branches)
            lines.append(f"split = [{inner}]")
            if raw.get("join"):
                lines.append(f"join = {_toml_str(str(raw.get('join')))}")
            if raw.get("policy"):
                lines.append(f"policy = {_toml_str(str(raw.get('policy')))}")
            lines.append("")
            continue
        key = _KIND_TO_TOML.get(kind)
        if key is None:
            raise TaskParseError(f"step {i}: kind must be shell, slash, or ask — not {kind!r}")
        body = str(raw.get("body") or "").strip()
        if not body:
            raise TaskParseError(f"step {i}: empty {key}")
        if key == "slash" and not body.startswith("/"):
            raise TaskParseError(f"step {i}: slash must start with /")
        lines.append(f"{key} = {_toml_str(body)}")
        if raw.get("on_success"):
            lines.append(f"on_success = {_toml_str(str(raw.get('on_success')))}")
        if raw.get("on_failure"):
            lines.append(f"on_failure = {_toml_str(str(raw.get('on_failure')))}")
        lines.append("")
    for e in spec.get("edges") or []:
        frm = str(e.get("from") or "").strip()
        to = str(e.get("to") or "").strip()
        if not frm or not to:
            continue
        lines.append("[[edge]]")
        lines.append(f"from = {_toml_str(frm)}")
        branch = str(e.get("branch") or "").strip()
        if branch:
            lines.append(f"when = {{ branch = {_toml_str(branch)} }}")
        lines.append(f"to = {_toml_str(to)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_pipeline_spec(xli_dir: Path | str, spec: dict[str, Any]) -> Path:
    """Persist a maker spec and validate it by loading the result."""
    name = str(spec.get("name") or "").strip()
    text = emit_pipeline_toml(spec)
    path = write_pipeline_toml(xli_dir, name, text)
    load_pipeline(xli_dir, name)
    return path


# --------------------------------------------------------------------------- #
#  Persistence — run state (.xlii/tasks/.runs/*.json)
# --------------------------------------------------------------------------- #

def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", name or "").strip("-")
    return s or "inline"


def new_run(
    pipeline: Pipeline,
    *,
    carry0: str = "",
    keep_going: bool = False,
    args: Optional[dict[str, str]] = None,
) -> TaskRun:
    now = datetime.now(timezone.utc)
    return TaskRun(
        name=pipeline.name,
        run_id=now.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex,
        created_at=now.isoformat(timespec="seconds"),
        updated_at=now.isoformat(timespec="seconds"),
        status="active",
        cursor=0,
        carry0=carry0,
        keep_going=keep_going,
        steps=[s.to_dict() for s in pipeline.steps],
        edges=[e.to_dict() for e in pipeline.edges],
        args=dict(args or {}),
    )


def run_file(xli_dir: Path | str, run: TaskRun) -> Path:
    return runs_dir(xli_dir) / f"{_slug(run.name)}-{run.run_id}.json"


def save_run(xli_dir: Path | str, run: TaskRun) -> Path:
    run.updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path = run_file(xli_dir, run)
    write_text_atomic(path, json.dumps(run.to_dict(), indent=2) + "\n", mode=0o644)
    return path


def iter_runs(xli_dir: Path | str) -> list[TaskRun]:
    d = runs_dir(xli_dir)
    if not d.exists():
        return []
    runs: list[tuple[float, TaskRun]] = []
    for p in d.glob("*.json"):
        try:
            runs.append((p.stat().st_mtime, TaskRun.from_dict(json.loads(p.read_text()))))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    runs.sort(key=lambda t: t[0], reverse=True)
    return [r for _, r in runs]


def latest_run(xli_dir: Path | str, *, statuses: Optional[set[str]] = None) -> Optional[TaskRun]:
    for run in iter_runs(xli_dir):
        if statuses is None or run.status in statuses:
            return run
    return None


def prune_runs(xli_dir: Path | str, *, keep: int = 20) -> int:
    """Keep only the ``keep`` most recent run files; return how many were removed."""
    d = runs_dir(xli_dir)
    if not d.exists():
        return 0
    entries: list[tuple[float, Path]] = []
    for p in d.glob("*.json"):
        try:
            entries.append((p.stat().st_mtime, p))
        except OSError:
            continue
    entries.sort(key=lambda t: t[0], reverse=True)
    files = [p for _, p in entries]
    removed = 0
    for p in files[keep:]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            # An unremovable run file is skipped and not counted as removed.
            pass
    return removed


# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #

def resolve_tasks_defaults(ctx: dict[str, Any]) -> dict[str, Any]:
    """``tasks_defaults`` resolution (global cfg, then project override).

    Read defensively via ``getattr`` so the engine works whether or not the
    config dataclass carries a ``tasks_defaults`` field yet (that field is added
    out-of-lane); a missing source is simply skipped.
    """
    out = dict(_TASKS_DEFAULTS)
    state = ctx.get("state")
    cfg = getattr(state, "cfg", None) or ctx.get("cfg")
    project = getattr(state, "project", None) or ctx.get("project")
    for src in (cfg, project):
        if src is None:
            continue
        block = getattr(src, "tasks_defaults", None)
        if isinstance(block, dict):
            out.update({k: v for k, v in block.items() if k in out})
    return out

