"""Deterministic Task+ skeletons — the face/TUI maker writes these, not a blank file.

Linear pipes need nothing extra. The other shapes are the decisions the TUI
builder never asked: a param, a verdict branch, a shell-rc skip, a split/join.
Each renderer returns TOML ``load_pipeline`` accepts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from xlii import tasks as T

SHAPES = ("linear", "params", "verdict", "rc", "split")

_PARAM_RING = ("base", "path", "query", "ref", "topic")
_BRANCH_RING = ("yes,no", "clean,dirty", "ok,fail", "ship,hold")
_ARM_RING = ("a,b", "lint,types", "unit,integ")
_POLICY_RING = ("all", "any", "first_ok")


def param_ring() -> tuple[str, ...]:
    return _PARAM_RING


def branch_ring() -> tuple[str, ...]:
    return _BRANCH_RING


def arm_ring() -> tuple[str, ...]:
    return _ARM_RING


def policy_ring() -> tuple[str, ...]:
    return _POLICY_RING


def split_csv(raw: str, *, n: int = 2) -> list[str]:
    parts = [p.strip() for p in (raw or "").split(",") if p.strip()]
    if len(parts) < n:
        parts.extend(f"arm{i}" for i in range(len(parts) + 1, n + 1))
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "-", p).strip("-") or "x"
        if slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return out[:8]


def stock_clone_names() -> list[str]:
    d = T.stock_tasks_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.toml"))


def draft_intent(
    shape: str,
    *,
    param: str = "base",
    branches: str = "yes,no",
    arms: str = "a,b",
    policy: str = "all",
) -> str:
    """One-line --from description that matches the chosen shape."""
    s = (shape or "linear").lower()
    if s == "params":
        return f"a pipeline that takes a {param} parameter and uses it"
    if s == "verdict":
        labs = " / ".join(split_csv(branches))
        return f"classify the situation as {labs} and branch on that verdict"
    if s == "rc":
        return "run a check; on failure triage, otherwise note success"
    if s == "split":
        ids = " and ".join(split_csv(arms))
        return f"run {ids} in parallel ({policy}), then summarize"
    return "a short linear pipe: gather something, then ask the agent to summarize"


def render_shape(
    name: str,
    shape: str,
    *,
    param: str = "base",
    branches: str = "yes,no",
    arms: str = "a,b",
    policy: str = "all",
) -> str:
    """TOML for *name* in *shape*. Raises ``TaskParseError`` on a bad shape."""
    s = (shape or "").strip().lower()
    if s not in SHAPES:
        raise T.TaskParseError(
            f"unknown task shape {shape!r} — pick one of {', '.join(SHAPES)}"
        )
    nm = json.dumps(name)
    if s == "linear":
        return (
            f"# /tasks run {name}\n"
            f"name = {nm}\n"
            f"description = \"linear pipe — gather, then ask\"\n"
            "\n[[step]]\n"
            "run = \"git status -sb\"\n"
            "\n[[step]]\n"
            "ask = \"Summarize the status above in 2-4 bullets.\"\n"
        )
    if s == "params":
        p = split_csv(param, n=1)[0]
        return (
            f"# /tasks run {name} {p}=VALUE\n"
            f"name = {nm}\n"
            f"description = \"takes a {p} parameter\"\n"
            f"\n[params.{p}]\n"
            "required = true\n"
            f"help = \"the {p} this pipe needs\"\n"
            "\n[[step]]\n"
            f"run = \"printf %s {{{{{p}}}}}\"\n"
            "\n[[step]]\n"
            f"ask = \"Use {{{{{p}}}}} from the pipe. Reply in a few bullets.\"\n"
        )
    if s == "verdict":
        labs = split_csv(branches)
        if len(labs) < 2:
            labs = ["yes", "no"]
        lines = [f'TASK+ {{"branch": "{lab}"}}' for lab in labs]
        ask = (
            "Decide which label fits. One short sentence, then a FINAL line "
            "that is exactly one of:\n" + "\n".join(lines)
        )
        out = [
            f"# /tasks run {name}",
            f"name = {nm}",
            f'description = "verdict branch ({", ".join(labs)})"',
            "",
            "[[step]]",
            'id = "look"',
            'run = "git status -sb"',
            "",
            "[[step]]",
            'id = "classify"',
            f"ask = \"\"\"{ask}\"\"\"",
        ]
        for lab in labs:
            out += [
                "",
                "[[step]]",
                f'id = "{lab}"',
                f'ask = "The verdict was {lab}. Say what to do next in one sentence."',
            ]
        for lab in labs:
            out += [
                "",
                "[[edge]]",
                'from = "classify"',
                f"when = {{ branch = \"{lab}\" }}",
                f'to = "{lab}"',
            ]
        return "\n".join(out) + "\n"
    if s == "rc":
        return (
            f"# /tasks run {name}\n"
            f"name = {nm}\n"
            'description = "shell-rc: on failure also triage"\n'
            "\n[[step]]\n"
            'id = "check"\n'
            'run = "python -m pytest -q"\n'
            'on_failure = "triage"\n'
            "\n[[step]]\n"
            'id = "ok"\n'
            'ask = "The check passed. One-line all-clear."\n'
            "\n[[step]]\n"
            'id = "triage"\n'
            'ask = "The check failed. Name the likely cause and the first thing to look at."\n'
        )
    # split
    ids = split_csv(arms)
    pol = policy if policy in _POLICY_RING else "all"
    quoted = ", ".join(json.dumps(i) for i in ids)
    out = [
        f"# /tasks run {name}",
        f"name = {nm}",
        f'description = "split {", ".join(ids)} then join ({pol})"',
        "",
        "[[step]]",
        'id = "fan"',
        f"split = [{quoted}]",
        'join = "report"',
        f'policy = "{pol}"',
    ]
    for i in ids:
        out += [
            "",
            "[[step]]",
            f'id = "{i}"',
            f"run = {json.dumps('printf ' + i)}",
        ]
    out += [
        "",
        "[[step]]",
        'id = "report"',
        'ask = "Summarize each arm above."',
    ]
    return "\n".join(out) + "\n"


def write_shaped(
    xli_dir: Path | str,
    name: str,
    shape: str,
    *,
    overwrite: bool = False,
    **opts,
) -> Path:
    path = T.pipeline_path(xli_dir, name)
    if path.exists() and not overwrite:
        raise T.TaskError(f"{path} already exists — /tasks edit {name}")
    text = render_shape(name, shape, **opts)
    return T.write_pipeline_toml(xli_dir, name, text)


def clone_stock(
    xli_dir: Path | str,
    dest: str,
    source: str,
    *,
    overwrite: bool = False,
) -> Path:
    """Copy a stock (or project) pipeline to *dest*, rewriting ``name``."""
    dest = (dest or "").strip()
    source = (source or "").strip()
    if not dest or not source:
        raise T.TaskError("clone needs a destination name and a source task")
    src = T.pipeline_path(xli_dir, source)
    if not src.exists():
        stock = T.stock_tasks_dir() / f"{source}.toml"
        if not stock.is_file():
            raise T.TaskNotFound(f"no pipeline named {source!r} to clone")
        src = stock
    dest_path = T.pipeline_path(xli_dir, dest)
    if dest_path.exists() and not overwrite:
        raise T.TaskError(f"{dest_path} already exists — /tasks edit {dest}")
    text = src.read_text(encoding="utf-8")
    if re.search(r'(?m)^name\s*=', text):
        text = re.sub(r'(?m)^name\s*=\s*.*$', f"name = {json.dumps(dest)}", text, count=1)
    else:
        text = f"name = {json.dumps(dest)}\n" + text
    return T.write_pipeline_toml(xli_dir, dest, text)


def iter_shapes() -> Iterable[str]:
    return SHAPES
