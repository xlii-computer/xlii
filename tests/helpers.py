"""Shared fakes for the test suite (see proposals/test-suite.md).

Lightweight, hermetic construction helpers so tool/agent/REPL tests don't each
re-derive project + context plumbing. No network, no real config.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


class FakeConsole:
    """Captures print() calls; status() is a no-op context manager."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **kwargs) -> None:
        self.lines.append(" ".join(str(a) for a in args))

    def status(self, *args, **kwargs):
        class _CM:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return _CM()

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


def make_project(root, *, local_only=False, extra_ignores=None):
    """A minimal project stand-in with the attributes the tools touch."""
    root = Path(root)
    return SimpleNamespace(
        project_root=root,
        xli_dir=root / ".xlii",
        extra_ignores=list(extra_ignores or []),
        local_only=local_only,
        name="testproj",
        collection_id=None,
        conversation_id=None,
    )


def make_tool_ctx(root, *, yolo=False, is_worker=False, console=None,
                  subscribed_plugins=None, extra_ignores=None, auto_approve=None):
    """A ToolContext over a temp project. clients/cfg are None — fine for the
    filesystem + bash tools; pass real ones for server/plugin tools."""
    from xlii.tool_context import DEFAULT_AUTO_APPROVE
    from xlii.tools import ToolContext

    aa = DEFAULT_AUTO_APPROVE if auto_approve is None else frozenset(auto_approve)

    return ToolContext(
        project=make_project(root, extra_ignores=extra_ignores),
        clients=None,
        cfg=None,
        yolo=yolo,
        auto_approve=aa,
        is_worker=is_worker,
        console=console,
        subscribed_plugins=list(subscribed_plugins or []),
    )


# --------------------------------------------------------------------------- #
#  Agent harness — drive run_turn with a scripted LLM (no network)
# --------------------------------------------------------------------------- #

def make_cfg(*, max_tool_iterations=8, max_chat_tool_iterations=8,
             orchestrator="orch-model", worker="worker-model",
             chat="chat-model", help="help-model", temp=0.5, max_parallel_workers=4,
             pricing=None):
    """A GlobalConfig stand-in with the accessors run_turn / batch exec touch."""

    def get_model_for_role(role="orchestrator"):
        if role == "worker":
            return worker
        if role == "chat":
            return chat
        if role == "help":
            return help
        return orchestrator

    return SimpleNamespace(
        max_tool_iterations=max_tool_iterations,
        max_chat_tool_iterations=max_chat_tool_iterations,
        max_parallel_workers=max_parallel_workers,
        pricing=pricing or {},
        get_model_for_role=get_model_for_role,
        orchestrator=lambda: orchestrator,
        worker=lambda: worker,
        chat=lambda: chat,
        help=lambda: help,
        orchestrator_temp=lambda: temp,
        worker_temp=lambda: temp,
        chat_temp=lambda: temp,
    )


def make_agent(root, *, cfg=None, console=None, history=None, model_override=None):
    """A real Agent wired with fakes, built via object.__new__ so __post_init__
    side effects are skipped (the proven _bare_agent pattern, fuller)."""
    from xlii.agent import Agent, SessionState

    a = object.__new__(Agent)
    a.session = SessionState()
    if model_override:
        a.session.model_override = model_override
    a.base_system_prompt = "SYSTEM"
    a.active_mode = None
    a.project = make_project(root)
    a.cfg = cfg or make_cfg()
    a.console = console or FakeConsole()
    a.history = history if history is not None else [{"role": "system", "content": "SYSTEM"}]
    a.pool = SimpleNamespace(
        primary=lambda: None, acquire=lambda: None,
        report_success=lambda c: None, report_auth_failure=lambda c: None,
    )
    return a


def make_msg(content=None, tool_calls=None):
    """An LLM message stand-in: .content + .tool_calls (each .id/.function.name/.arguments)."""
    tcs = []
    for i, (name, args) in enumerate(tool_calls or []):
        tcs.append(SimpleNamespace(
            id=f"call_{i}",
            function=SimpleNamespace(name=name, arguments=json.dumps(args)),
        ))
    return SimpleNamespace(content=content, tool_calls=tcs or None)


def script_iterations(agent, *steps):
    """Make agent.run_turn replay scripted model responses.

    Each step is `(content, tool_calls)` where tool_calls is a list of
    `(name, args_dict)`. Returns nothing; mutates the agent so its
    `_stream_orchestrator_iteration` pops the next scripted message.
    """
    it = iter(steps)

    def fake(*args, **kwargs):
        content, tool_calls = next(it)
        return (make_msg(content, tool_calls), None, False)  # (msg, usage, streamed)

    agent._stream_orchestrator_iteration = fake
