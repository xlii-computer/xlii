"""Subagent roles (cursor-workflows.md A3): explore / bash / general palettes
and tool-layer enforcement of the role boundary."""

from types import SimpleNamespace

import xlii.agent as A
from xlii.tools import (
    WORKER_ROLES,
    ToolResult,
    dispatch_subagent_schema,
    worker_tool_schemas,
)
from tests.helpers import make_agent, make_msg


def _names(role):
    return {s["function"]["name"] for s in worker_tool_schemas(role=role)}


# --------------------------------------------------------------------------- #
#  palettes
# --------------------------------------------------------------------------- #

def test_roles_are_known():
    assert WORKER_ROLES == {"explore", "bash", "general", "lab"}


def test_explore_role_has_no_shell():
    n = _names("explore")
    assert "bash" not in n and "code_execute" not in n
    assert {"read_file", "grep", "glob", "list_dir"} <= n  # investigation tools present


def test_bash_role_is_read_plus_shell():
    assert _names("bash") == {"read_file", "bash"}


def test_general_role_keeps_full_palette():
    n = _names("general")
    assert "bash" in n
    assert {"read_file", "grep", "glob", "list_dir"} <= n


def test_dispatch_schema_exposes_role_enum():
    props = dispatch_subagent_schema()["function"]["parameters"]["properties"]
    assert "role" in props
    assert set(props["role"]["enum"]) == {"explore", "bash", "general", "lab"}


# --------------------------------------------------------------------------- #
#  tool-layer enforcement (exit gate)
# --------------------------------------------------------------------------- #

def _resp(msg):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0),
    )


def _fake_clients(scripted):
    it = iter(scripted)
    completions = SimpleNamespace(create=lambda **kw: next(it))
    return SimpleNamespace(chat=SimpleNamespace(chat=SimpleNamespace(completions=completions)))


def test_explore_rejects_bash_but_general_runs_it(tmp_path, monkeypatch):
    calls: list[str] = []
    monkeypatch.setitem(
        A.WORKER_REGISTRY, "bash",
        lambda ctx, args: calls.append(args.get("command")) or ToolResult("ran"),
    )
    agent = make_agent(tmp_path)
    cfg = agent.cfg
    cfg.max_worker_iterations = 4

    def run(role):
        scripted = [
            _resp(make_msg("", [("bash", {"command": "echo hi", "intent": "read-only"})])),
            _resp(make_msg("done", None)),
        ]
        w = A.WorkerAgent(clients=_fake_clients(scripted), project=agent.project, cfg=cfg, role=role)
        return w.run("investigate")[0]

    assert run("explore") == "done"
    assert calls == []            # bash refused at the tool layer for an explore worker

    assert run("general") == "done"
    assert calls == ["echo hi"]   # the identical call runs for a general worker


def test_worker_models_map_selects_per_role(tmp_path):
    from xlii.config import GlobalConfig

    agent = make_agent(tmp_path)
    cfg = GlobalConfig()
    cfg.worker_models = {"explore": "explore-model", "general": "general-model"}
    cfg.max_worker_iterations = 1
    scripted = [_resp(make_msg("done", None))]

    _, explore_call = A.WorkerAgent(
        clients=_fake_clients(scripted),
        project=agent.project,
        cfg=cfg,
        role="explore",
    ).run("look around")
    assert explore_call.model == "explore-model"

    _, general_call = A.WorkerAgent(
        clients=_fake_clients(scripted),
        project=agent.project,
        cfg=cfg,
        role="general",
    ).run("look around")
    assert general_call.model == "general-model"
