"""Mojo-keeper K6 — door tools: operate xlii by intent, not slash text.

Registered via ``register_agent_tool`` so they ride schema / registry /
PARALLEL_SAFE. Advertised on conversational turns through ``CHAT_DOOR_TOOLS``.
Handlers are thin wrappers over existing doors. Never raise into the turn.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from xlii.tool_context import ToolContext, ToolResult
from xlii.tool_schemas import AgentTool, register_agent_tool

DOOR_NAMES = (
    "desk_where",
    "desk_switch",
    "desk_new",
    "pane_open",
    "memory_set",
    "explain_xlii",
    "post_job",
)


def register() -> None:
    """Idempotent: (re)bind the keeper door tools into the agent registry."""
    for tool in _TOOLS:
        register_agent_tool(tool)


def _expand_user_path(raw: str) -> Path:
    """Resolve ``~`` via the path seam — never ``Path.home()`` / ``expanduser``."""
    from xlii.project_paths import user_home

    s = (raw or "").strip()
    if s == "~":
        return user_home()
    if s.startswith("~/"):
        return user_home() / s[2:]
    return Path(s)


def _sitting_ctx(ctx: ToolContext) -> dict[str, Any]:
    sitting = getattr(ctx, "sitting", None)
    return {"state": sitting, "console": ctx.console, "agent": getattr(sitting, "agent", None)}


def t_desk_where(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    try:
        from xlii.bearings import bearings_block

        sitting = getattr(ctx, "sitting", None) or ctx
        surface = getattr(ctx, "door_surface", None) or "repl"
        block = bearings_block(
            sitting,
            surface=surface,
            cfg=ctx.cfg,
            persona_project=getattr(ctx, "project", None),
        )
        lines = [block.rstrip(), "", "projects on this body:"]
        from xlii.registry import Registry

        node = ""
        try:
            from xlii.farm import jobs_cfg

            node = str((jobs_cfg(ctx.cfg).get("node") or "") or "").strip()
            if not node:
                node = str(getattr(ctx.cfg, "node_name", "") or "").strip()
        except Exception:
            node = ""
        entries = Registry.load().entries
        shown = 0
        for e in sorted(entries, key=lambda x: x.name.lower()):
            enode = (getattr(e, "node", None) or "").strip()
            if enode and node and enode != node:
                continue
            lines.append(f"  {e.name}  {e.path}")
            shown += 1
        if shown == 0:
            lines.append("  (none)")
        return ToolResult("\n".join(lines))
    except Exception as e:
        return ToolResult(f"desk_where failed: {type(e).__name__}: {e}", is_error=True)


def t_desk_switch(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    name = str(args.get("project") or "").strip()
    if not name:
        return ToolResult("need a project name", is_error=True)
    sitting = getattr(ctx, "sitting", None)
    try:
        from xlii.project_resolver import resolve_registered_project

        res = resolve_registered_project(name)
        entry = res.entry
        node = (getattr(entry, "node", None) or "").strip() if entry is not None else ""
        if node:
            here = ""
            try:
                from xlii.farm import jobs_cfg

                here = str((jobs_cfg(ctx.cfg).get("node") or "") or "").strip()
            except Exception:
                here = ""
            if here and node != here:
                return ToolResult(f"not on this body — on {node}")
            if not res.ok:
                return ToolResult(f"not on this body — on {node}")
        if not res.ok:
            reason = res.reason or "no matches"
            return ToolResult(f"no such project: {name} ({reason})")
        if sitting is None:
            return ToolResult("no interactive session to switch", is_error=True)
        emit = getattr(ctx, "emit_door", None)
        if callable(emit):
            # Face: land via join_project / go_home (_emit_door handles land:).
            emit({"action": f"land:{res.project.name}"})
            return ToolResult(f"switched to {res.project.name}")
        from xlii.repl_cmds.switch import switch_to_code_project

        switch_to_code_project(_sitting_ctx(ctx), res.project)
        return ToolResult(f"switched to {res.project.name}")
    except Exception as e:
        return ToolResult(f"desk_switch failed: {type(e).__name__}: {e}", is_error=True)


def t_desk_new(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    from xlii.project_paths import user_home

    raw_path = str(args.get("path") or "").strip()
    name = str(args.get("name") or "").strip()
    if not raw_path and not name:
        return ToolResult("need path or name", is_error=True)
    try:
        home = user_home()
        if not os.access(home, os.W_OK):
            return ToolResult("refused: no writable home on this body", is_error=True)
    except Exception:
        return ToolResult("refused: no writable home on this body", is_error=True)
    if not raw_path:
        raw_path = str(user_home() / name)
    try:
        path = _expand_user_path(raw_path)
        if not path.is_absolute():
            path = user_home() / path
        path = path.resolve()
    except Exception as e:
        return ToolResult(f"bad path: {type(e).__name__}: {e}", is_error=True)
    if not name:
        name = path.name
    prompt = (
        f"create project {name!r} at {path} and register it?\n"
        "this writes a new folder and runs xlii init --local.\n"
        "[y/N] "
    )
    if not getattr(ctx, "yolo", False):
        from xlii.tools import _confirm

        ans = _confirm(prompt)
        if str(ans or "").strip().lower() not in {"y", "yes"}:
            return ToolResult("refused: desk_new not confirmed", is_error=True)
    try:
        path.mkdir(parents=True, exist_ok=True)
        from xlii.sync import init_project

        project = init_project(None, path, name=name, local_only=True)
        emit = getattr(ctx, "emit_door", None)
        if callable(emit):
            # Face: land via join_project / _land_folder (Home → go_home).
            emit({"action": f"land:{project.name}"})
        else:
            sitting = getattr(ctx, "sitting", None)
            if sitting is not None:
                from xlii.repl_cmds.switch import switch_to_code_project

                switch_to_code_project(_sitting_ctx(ctx), project)
        return ToolResult(f"created {project.name} at {path} (registered, local-only)")
    except Exception as e:
        return ToolResult(f"desk_new failed: {type(e).__name__}: {e}", is_error=True)


def t_pane_open(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    pid = str(args.get("id") or "").strip()
    from xlii.workbench import QUICK_LAUNCH_META

    if pid not in QUICK_LAUNCH_META:
        ids = ", ".join(sorted(QUICK_LAUNCH_META))
        return ToolResult(f"unknown pane id {pid!r}. valid: {ids}", is_error=True)
    _label, action = QUICK_LAUNCH_META[pid]
    emit = getattr(ctx, "emit_door", None)
    if callable(emit):
        emit({"action": action})
        return ToolResult(f"opened {action}")
    return ToolResult(f"no panes here — /{pid}")


def _arg_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def t_memory_set(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    sitting = getattr(ctx, "sitting", None)
    if sitting is None:
        return ToolResult("no sitting to change", is_error=True)
    # Omitted args mean UNCHANGED (not default True).
    if "sync" in args:
        sync = _arg_bool(args["sync"])
        if sync and bool(getattr(sitting, "scratch", False)):
            return ToolResult(
                "refused: scratch sitting is never-sync",
                is_error=True,
            )
        sitting.no_sync = not sync
    if "journal" in args:
        sitting.journal_mute = not _arg_bool(args["journal"])
    sync_on = not bool(getattr(sitting, "no_sync", False))
    journal_on = not bool(getattr(sitting, "journal_mute", False))
    return ToolResult(
        f"memory: sync={'on' if sync_on else 'off'} · journal={'on' if journal_on else 'off'}"
    )


def t_post_job(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """Post an advisory farm ad to a pool. Always confirms; names pool + budget."""
    task = str(args.get("task") or "").strip()
    if not task:
        return ToolResult("need a task", is_error=True)
    job = str(args.get("job") or "explore").strip() or "explore"
    pool_arg = str(args.get("pool") or args.get("where") or "").strip()
    if not pool_arg:
        return ToolResult("need a pool (jobs room name)", is_error=True)
    usd = args.get("budget")
    try:
        max_usd = float(usd) if usd is not None and usd != "" else None
    except (TypeError, ValueError):
        max_usd = None
    try:
        iters = int(args.get("max_iters") or 8)
    except (TypeError, ValueError):
        iters = 8
    cfg = getattr(ctx, "cfg", None)
    if cfg is None:
        from xlii.config import GlobalConfig
        cfg = GlobalConfig.load()
    try:
        from xlii.farm import Budget, JobError
        from xlii.farm_post import post_to_pool, resolve_where

        pool = resolve_where(cfg, pool_arg)
        if not pool:
            return ToolResult("where=this body is a local hire, not a pool post", is_error=True)
        ticket = post_to_pool(
            cfg,
            job=job,
            task=task,
            pool=pool,
            budget=Budget(max_usd=max_usd, max_iters=iters),
            context=str(args.get("context") or ""),
        )
    except JobError as e:
        return ToolResult(str(e), is_error=True)
    except Exception as e:
        return ToolResult(f"post_job failed: {type(e).__name__}: {e}", is_error=True)
    usd_s = "none" if max_usd is None else f"{max_usd:g}"
    return ToolResult(
        f"posted {ticket.id} to pool {pool} "
        f"(job={job} budget max_usd={usd_s} max_iters={iters})"
    )


def t_explain_xlii(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    q = str(args.get("question") or "").strip()
    if not q:
        return ToolResult("need a question", is_error=True)
    try:
        from xlii.selfwiki import selfwiki_root
        from xlii.wiki_retrieval import search_self_wiki, wiki_context_block

        root = selfwiki_root()
        if root is None:
            return ToolResult("shipped self-doc is not available")
        hits = [
            h for h in search_self_wiki(root, q, limit=3, scheme="xwiki")
            if h.score >= 1
        ]
        block = wiki_context_block(root, hits, limit=3) if hits else ""
        return ToolResult(block or "no self-doc match")
    except Exception as e:
        return ToolResult(f"explain_xlii failed: {type(e).__name__}: {e}", is_error=True)


def _tool(name, description, parameters, handler, *, parallel: bool = False) -> AgentTool:
    return AgentTool(
        name=name,
        description=description,
        parameters=parameters,
        handler=handler,
        parallel_safe=parallel,
        source="door",
        category="doors",
        plan_mode_safe=False,
        worker_safe=False,
    )


_TOOLS = (
    _tool(
        "desk_where",
        "Where this mouth is standing: bearings (body, surface, desk, reach, "
        "hire, last-turn delta) plus projects registered on this body.",
        {"type": "object", "properties": {}},
        t_desk_where,
        parallel=True,
    ),
    _tool(
        "desk_switch",
        "Switch the live session onto a registered project (same as /project switch). "
        "Refused if the project is not on this body.",
        {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Registered project name"},
            },
            "required": ["project"],
        },
        t_desk_switch,
    ),
    _tool(
        "desk_new",
        "Create a folder, run xlii init --local, register it, and switch into it. "
        "Writes; confirm names the path. Default path is $HOME/<name> unless path is given. "
        "Never assume ~/Projects.",
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute folder to create"},
                "name": {"type": "string", "description": "Project name (defaults to folder name)"},
                "local": {"type": "boolean", "description": "Always true — local-only init"},
            },
        },
        t_desk_new,
    ),
    _tool(
        "pane_open",
        "Open a Face pane by quick-launch id (git, projects, tasks, …). "
        "On REPL, returns the slash equivalent instead.",
        {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "QUICK_LAUNCH_META id, e.g. git"},
            },
            "required": ["id"],
        },
        t_pane_open,
    ),
    _tool(
        "memory_set",
        "Sitting memory knob: turn sync and/or journal on or off. "
        "Omitted args leave that knob unchanged. Scratch sittings refuse sync=true.",
        {
            "type": "object",
            "properties": {
                "sync": {
                    "type": "boolean",
                    "description": "False mutes end-of-turn sync; omit to leave unchanged",
                },
                "journal": {
                    "type": "boolean",
                    "description": "False mutes observe_turn journal; omit to leave unchanged",
                },
            },
        },
        t_memory_set,
    ),
    _tool(
        "explain_xlii",
        "Answer a product question from shipped self-doc (xwiki). Read-only.",
        {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
            },
            "required": ["question"],
        },
        t_explain_xlii,
        parallel=True,
    ),
    _tool(
        "post_job",
        "Post an advisory farm job to a pool (a jobs room). Always confirms; "
        "the prompt names the pool and budget. auto_deny / a no means no ad. "
        "Not a local hire — for that, dispatch_subagent without where=.",
        {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "What the node should answer"},
                "pool": {
                    "type": "string",
                    "description": "Pool / jobs room name (localpart or full JID)",
                },
                "job": {
                    "type": "string",
                    "description": "Named job (v0: explore)",
                },
                "budget": {
                    "type": "number",
                    "description": "Soft USD cap (max_usd)",
                },
                "max_iters": {
                    "type": "integer",
                    "description": "Iteration cap (default 8)",
                },
                "context": {"type": "string"},
            },
            "required": ["task", "pool"],
        },
        t_post_job,
    ),
)
