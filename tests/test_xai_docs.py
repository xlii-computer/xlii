"""xai_docs — DNA read of the hosted xAI docs MCP.

The house brain looks up how Grok and the API actually run. Not a
marketplace, not xlii's wiki. All mocked HTTP; no live docs.x.ai in CI.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from tests.helpers import make_agent, make_tool_ctx
from xlii.chat_backend import XAI_SERVER_TOOL_NAMES
from xlii.mcp.doc_client import MCPError
from xlii.mode_contract import CHAT_BLIND_TOOLS, READ_ONLY_TOOLS
from xlii.tool_handlers import XAI_DOCS_MCP_URL, t_xai_docs
from xlii.tool_schemas import (
    BUILTIN_TOOLS,
    apply_xai_docs_gate,
    tool_schemas,
    worker_tool_schemas,
)
from xlii.tools import t_xai_docs as tools_t_xai_docs


def test_reexport_is_the_handler():
    assert tools_t_xai_docs is t_xai_docs


def test_handler_imports_doc_client_not_package():
    src = inspect.getsource(t_xai_docs)
    assert "xlii.mcp.doc_client" in src
    assert "from xlii.mcp import" not in src
    assert "context_server" not in src


def test_registered_as_read_palette():
    tool = next(t for t in BUILTIN_TOOLS if t.name == "xai_docs")
    assert tool.plan_mode_safe and tool.worker_safe and tool.parallel_safe
    assert tool.source == "builtin"
    assert "xai_docs" in READ_ONLY_TOOLS
    assert "xai_docs" in CHAT_BLIND_TOOLS
    assert "xai_docs" not in XAI_SERVER_TOOL_NAMES
    advertised = {s["function"]["name"] for s in tool_schemas()}
    assert "xai_docs" in advertised
    explore = {s["function"]["name"] for s in worker_tool_schemas(role="explore")}
    assert "xai_docs" in explore
    bash_role = {s["function"]["name"] for s in worker_tool_schemas(role="bash")}
    assert "xai_docs" not in bash_role


def _call(tmp_path, monkeypatch, args, *, result="HITS", error=None, cfg=None):
    seen: dict = {}

    def fake(url, name, arguments, timeout=30):
        seen.update(url=url, name=name, arguments=arguments, timeout=timeout)
        if error is not None:
            raise error
        return result

    monkeypatch.setattr("xlii.mcp.doc_client.call_tool", fake)
    ctx = make_tool_ctx(tmp_path)
    if cfg is not None:
        ctx.cfg = cfg
    return t_xai_docs(ctx, args), seen


def test_search_maps_to_search_docs(tmp_path, monkeypatch):
    r, seen = _call(
        tmp_path, monkeypatch,
        {"action": "search", "query": "responses api", "max_results": 3},
    )
    assert not r.is_error and r.content == "HITS"
    assert seen["url"] == XAI_DOCS_MCP_URL
    assert seen["name"] == "search_docs"
    assert seen["arguments"] == {"query": "responses api", "max_results": 3}


def test_search_inferred_from_query(tmp_path, monkeypatch):
    r, seen = _call(tmp_path, monkeypatch, {"query": "imagine"})
    assert not r.is_error
    assert seen["name"] == "search_docs"
    assert seen["arguments"] == {"query": "imagine"}


def test_get_maps_to_get_doc_page(tmp_path, monkeypatch):
    r, seen = _call(
        tmp_path, monkeypatch,
        {"action": "get", "slug": "/developers/models"},
        result="# Models",
    )
    assert not r.is_error and r.content == "# Models"
    assert seen["name"] == "get_doc_page"
    assert seen["arguments"] == {"slug": "developers/models"}


def test_list_maps_to_list_doc_pages(tmp_path, monkeypatch):
    r, seen = _call(tmp_path, monkeypatch, {"action": "list"}, result="developers/…")
    assert not r.is_error
    assert seen["name"] == "list_doc_pages"
    assert seen["arguments"] == {}


def test_missing_query_and_slug_and_unknown_action(tmp_path, monkeypatch):
    r, seen = _call(tmp_path, monkeypatch, {"action": "search"})
    assert r.is_error and "query" in r.content
    assert seen == {}
    r, _ = _call(tmp_path, monkeypatch, {"action": "get"})
    assert r.is_error and "slug" in r.content
    r, _ = _call(tmp_path, monkeypatch, {"action": "crawl"})
    assert r.is_error and "search, get, or list" in r.content
    r, _ = _call(tmp_path, monkeypatch, {})
    assert r.is_error and "action required" in r.content


def test_mcp_error_is_tool_error(tmp_path, monkeypatch):
    r, _ = _call(
        tmp_path, monkeypatch,
        {"action": "search", "query": "x"},
        error=MCPError("HTTP 406 from https://docs.x.ai/api/mcp: nope"),
    )
    assert r.is_error
    assert "xai_docs failed:" in r.content
    assert "HTTP 406" in r.content


def test_kill_switch_handler_and_gate(tmp_path, monkeypatch):
    r, seen = _call(
        tmp_path, monkeypatch,
        {"action": "search", "query": "x"},
        cfg=SimpleNamespace(xai_docs=False),
    )
    assert r.is_error and "xai_docs is off" in r.content
    assert seen == {}

    schemas = [
        {"function": {"name": "web_search"}},
        {"function": {"name": "xai_docs"}},
    ]
    kept = apply_xai_docs_gate(schemas, SimpleNamespace(xai_docs=True))
    assert {s["function"]["name"] for s in kept} == {"web_search", "xai_docs"}
    stripped = apply_xai_docs_gate(schemas, SimpleNamespace(xai_docs=False))
    assert {s["function"]["name"] for s in stripped} == {"web_search"}
    # missing field / None cfg = default on
    assert apply_xai_docs_gate(schemas, None) == schemas


def test_chat_palette_offers_xai_docs(tmp_path):
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    captured = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)
        from tests.helpers import make_msg
        return (make_msg("ok", None), None, False)

    agent._stream_orchestrator_iteration = fake
    agent.run_turn("hello")
    names = {s["function"]["name"] for s in captured["schemas"]}
    assert "xai_docs" in names

    agent.cfg.xai_docs = False
    captured.clear()
    agent.run_turn("hello")
    names_off = {s["function"]["name"] for s in captured["schemas"]}
    assert "xai_docs" not in names_off
    assert "web_search" in names_off
