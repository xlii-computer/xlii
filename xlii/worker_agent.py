"""Read-only (or writer-swarm) subagent dispatched by the main agent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from xlii.agent_stats import CallStats, _cache_headers
from xlii.client import Clients
from xlii.config import GlobalConfig, ProjectConfig
from xlii.tools import (
    WORKER_REGISTRY,
    WRITER_REGISTRY,
    ToolContext,
    worker_tool_schemas,
)
from xlii.turn_prompt import WORKER_SYSTEM_PROMPT, WRITER_SYSTEM_PROMPT

# Returned verbatim as the worker's whole answer when the iteration allowance
# runs out mid-task. loop_verdict.parse_verdict keys on this exact string to
# emit an honest "inconclusive" verdict instead of a FAIL (the 2026-07-10 loop
# postmortem: a starved verifier read as a code failure and killed the loop).
STARVED_SENTINEL = "(worker hit max_worker_iterations without finishing)"
CANCELLED_SENTINEL = "(job cancelled)"


@dataclass
class WorkerAgent:
    clients: Clients
    project: ProjectConfig
    cfg: GlobalConfig
    # Inherits parent's plugin subscriptions so workers can plugin_search too.
    subscribed_plugins: list[str] = field(default_factory=list)
    # Writer-swarm: write palette jailed to project_root (typically a worktree).
    worker_writes: bool = False
    loop_lock_tests: bool = False
    # Subagent role (A3): "explore" (no shell) | "bash" (read+shell) | "general".
    # Narrows the read-only worker's tool palette; ignored for writer workers.
    role: str = "general"
    yolo: bool = False
    # Optional model override (e.g. loop judges resolving a non-worker config role).
    model: Optional[str] = None
    # Gigwork (G0): a swappable chat brain for this pass. None = today's home
    # path (clients.chat), byte-identical. A backend without the xAI server
    # capability has search_project / web_search / x_search stripped from its
    # schemas — capability flags, not prompt theater (proposals/gigwork.md §4).
    # ``xai_docs`` stays: outbound HTTP to docs.x.ai, not the Responses plane.
    chat_backend: Optional[Any] = None

    def run(self, task: str, context: Optional[str] = None,
            system_prompt_override: Optional[str] = None,
            max_iterations: Optional[int] = None,
            should_stop: Optional[Callable[[], bool]] = None) -> tuple[str, CallStats]:
        """*max_iterations* overrides ``cfg.max_worker_iterations`` for callers
        whose task can't fit the default allowance (loop judges walking a real
        multi-file diff starve at the default and return :data:`STARVED_SENTINEL`).

        *should_stop*, if given, is checked at the start of each iteration
        (farm cancel hook). Returns a cancelled sentinel so the caller can
        distinguish abort from a finished answer.
        """
        call = CallStats()
        if system_prompt_override is None and self.worker_writes:
            system_prompt_override = WRITER_SYSTEM_PROMPT
        system_prompt = system_prompt_override or WORKER_SYSTEM_PROMPT
        files_hint = ""
        try:
            from xlii.desk_files import files_mount_prompt_addendum

            files_hint = files_mount_prompt_addendum(self.project)
        except Exception:
            # Optional [FILES] enrichment — a pointer-desk hint must not
            # abort the worker if desk_files cannot be imported or resolved.
            files_hint = ""
        if files_hint and files_hint not in system_prompt:
            system_prompt = system_prompt.rstrip() + "\n\n" + files_hint
        history: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]
        user_msg = f"Task:\n{task}"
        if context:
            user_msg += f"\n\nContext supplied by parent:\n{context}"
        history.append({"role": "user", "content": user_msg})

        ctx = ToolContext(
            project=self.project,
            clients=self.clients,
            cfg=self.cfg,
            pool=None,    # workers don't get a pool — no nested dispatch
            is_worker=True,
            worker_writes=self.worker_writes,
            loop_lock_tests=self.loop_lock_tests,
            yolo=self.yolo,
            subscribed_plugins=list(self.subscribed_plugins),
        )
        schemas = worker_tool_schemas(writer=self.worker_writes, role=self.role)
        registry = WRITER_REGISTRY if self.worker_writes else WORKER_REGISTRY
        if not self.subscribed_plugins:
            schemas = [
                s for s in schemas
                if s["function"]["name"] not in {"plugin_search", "plugin_get"}
            ]
        from xlii.tool_schemas import apply_email_account_gate, apply_xai_docs_gate
        schemas = apply_email_account_gate(schemas, self.cfg)
        schemas = apply_xai_docs_gate(schemas, self.cfg)
        backend = self.chat_backend
        if backend is not None:
            schemas = [
                s for s in schemas if backend.allows_tool(s["function"]["name"])
            ]
        # Tool-layer enforcement of the role: anything not advertised for this role
        # is refused even if the registry could run it (e.g. bash for an explore
        # worker), so the role is a real boundary, not just a prompt hint.
        allowed_tool_names = {s["function"]["name"] for s in schemas}

        get_worker_model = getattr(self.cfg, "get_model_for_worker_role", None)
        if self.model:
            model = self.model
        elif backend is not None and getattr(backend, "model", ""):
            model = backend.model
        elif callable(get_worker_model):
            model = get_worker_model(self.role)
        else:
            model = self.cfg.get_model_for_role("worker")
        call.model = model
        cache_hdrs = _cache_headers(self.project.conversation_id, suffix="workers")
        temperature = self.cfg.worker_temp()
        for _ in range(max_iterations or self.cfg.max_worker_iterations):
            if should_stop is not None and should_stop():
                return (CANCELLED_SENTINEL, call)
            call.iterations += 1
            kwargs = dict(
                model=model,
                messages=history,
                tools=schemas,
                tool_choice="auto",
                temperature=temperature,
            )
            if cache_hdrs:
                kwargs["extra_headers"] = cache_hdrs
            if backend is not None:
                resp = backend.create(**kwargs)
            else:
                resp = self.clients.chat.chat.completions.create(**kwargs)
            call.absorb_usage(resp.usage, model, self.cfg.pricing)
            msg = resp.choices[0].message
            entry: dict[str, Any] = {"role": "assistant"}
            if msg.content:
                entry["content"] = msg.content
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            history.append(entry)

            if not msg.tool_calls:
                return (msg.content or "", call)

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError as e:
                    result_text = f"invalid JSON arguments: {e}"
                else:
                    if name not in allowed_tool_names:
                        result_text = (
                            f"tool '{name}' is not available to a '{self.role}' subagent "
                            "(role-restricted); re-dispatch with role 'bash' or 'general' "
                            "if the task needs it"
                        )
                    else:
                        fn = registry.get(name)
                        if fn is None:
                            from xlii.tools import _PROJECT_TOOLS
                            t = _PROJECT_TOOLS.get(name)
                            if t is not None and (t.worker_safe or (self.worker_writes and name in {"write_file", "edit_file"})):
                                fn = t.handler
                        if fn is None:
                            result_text = f"tool not available to workers: {name}"
                        else:
                            try:
                                r = fn(ctx, args)
                                result_text = r.content
                            except Exception as e:
                                result_text = f"tool raised: {type(e).__name__}: {e}"
                history.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": result_text}
                )

            # Drain server-tool sub-call usage from this iteration's tools.
            in_t, out_t, cost, _ = ctx.drain_server_usage()
            if in_t or out_t or cost:
                call.absorb_server_tool(in_t, out_t, cost)

        return (STARVED_SENTINEL, call)
