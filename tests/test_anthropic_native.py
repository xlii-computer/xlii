"""anthropic_native backend — offline translation pins and HTTP fakes."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest import mock

import pytest

import xlii.config as C
from xlii.anthropic_native import (
    AnthropicNativeBackend,
    anthropic_response_to_openai,
    openai_messages_to_anthropic,
    openai_tools_to_anthropic,
)
from xlii.chat_backend import GigError, gig_providers, resolve_gig_backend
from xlii.worker_agent import WorkerAgent
from tests.helpers import make_agent, make_msg


def _openai_tools():
    return [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    ]


def test_openai_tools_to_anthropic_with_cache_on_last_tool():
    tools = openai_tools_to_anthropic(_openai_tools(), cache_marks=True)
    assert tools[0]["name"] == "read_file"
    assert tools[0]["input_schema"]["required"] == ["path"]
    assert tools[0]["cache_control"] == {"type": "ephemeral"}


def test_openai_messages_splits_system_tools_and_user_cache():
    messages = [
        {"role": "system", "content": "HARNESS"},
        {"role": "user", "content": "TASK"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "tc_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"a.py"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "tc_1", "content": "file body"},
    ]
    system, out = openai_messages_to_anthropic(messages, cache_marks=True)
    assert system == [
        {"type": "text", "text": "HARNESS", "cache_control": {"type": "ephemeral"}}
    ]
    assert out[0] == {
        "role": "user",
        "content": [{"type": "text", "text": "TASK", "cache_control": {"type": "ephemeral"}}],
    }
    assert out[1]["role"] == "assistant"
    assert out[1]["content"][0]["type"] == "tool_use"
    assert out[1]["content"][0]["input"] == {"path": "a.py"}
    assert out[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "tc_1", "content": "file body"}],
    }


def test_multipart_last_user_message_gets_exactly_one_cache_mark():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "part one"},
                {"type": "text", "text": "part two"},
                {"type": "text", "text": "part three"},
            ],
        },
    ]
    _system, out = openai_messages_to_anthropic(messages, cache_marks=True)
    blocks = out[-1]["content"]
    marked = [b for b in blocks if "cache_control" in b]
    assert marked == [blocks[-1]]


def test_anthropic_response_maps_text_tools_and_cached_usage():
    resp = anthropic_response_to_openai(
        {
            "content": [
                {"type": "text", "text": "done"},
                {
                    "type": "tool_use",
                    "id": "tc_9",
                    "name": "grep",
                    "input": {"pattern": "foo"},
                },
            ],
            "usage": {
                "input_tokens": 1200,
                "output_tokens": 40,
                "cache_read_input_tokens": 900,
            },
        }
    )
    msg = resp.choices[0].message
    assert msg.content == "done"
    assert msg.tool_calls[0].function.name == "grep"
    assert json.loads(msg.tool_calls[0].function.arguments) == {"pattern": "foo"}
    assert resp.usage.prompt_tokens == 1200
    assert resp.usage.completion_tokens == 40
    assert resp.usage.prompt_tokens_details.cached_tokens == 900


def test_create_posts_native_payload_and_adapts_response(monkeypatch):
    captured: dict = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        body = json.dumps(
            {
                "content": [{"type": "text", "text": "worker says ok"}],
                "usage": {
                    "input_tokens": 50,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 30,
                },
            }
        ).encode("utf-8")
        return mock.Mock(
            read=lambda: body,
            __enter__=lambda s: s,
            __exit__=mock.Mock(return_value=False),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    backend = AnthropicNativeBackend(
        label="haiku",
        model="claude-haiku-4-5",
        api_key="sk-ant",
        base_url="https://api.anthropic.com/v1",
        cache_marks=True,
    )
    resp = backend.create(
        model="claude-haiku-4-5",
        messages=[
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "go"},
        ],
        tools=_openai_tools(),
        tool_choice="auto",
        temperature=0.2,
        extra_headers={"x-grok-conv-id": "drop-me"},
    )
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-ant"
    payload = captured["payload"]
    assert payload["model"] == "claude-haiku-4-5"
    assert payload["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert payload["tools"][-1]["cache_control"] == {"type": "ephemeral"}
    assert payload["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "temperature" not in payload
    assert resp.choices[0].message.content == "worker says ok"
    assert resp.usage.prompt_tokens_details.cached_tokens == 30


def test_create_refuses_streaming():
    backend = AnthropicNativeBackend(
        label="haiku",
        model="m",
        api_key="k",
        base_url="https://api.anthropic.com/v1",
    )
    with pytest.raises(GigError, match="does not stream yet"):
        backend.create(messages=[], stream=True)


def test_resolve_gig_backend_mints_anthropic_native(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "sk-ant")
    cfg = C.GlobalConfig()
    cfg.gigwork = {
        "providers": {
            "claude": {
                "kind": "anthropic_native",
                "base_url": "https://api.anthropic.com/v1",
                "api_key_env": "ANTHROPIC_TEST_KEY",
                "model": "claude-sonnet-5",
            }
        }
    }
    assert gig_providers(cfg)["claude"].kind == "anthropic_native"
    backend = resolve_gig_backend(cfg, "claude")
    assert isinstance(backend, AnthropicNativeBackend)
    assert backend.label == "claude" and backend.cache_marks is True


def test_worker_pass_absorbs_cached_tokens(tmp_path, monkeypatch):
    def fake_urlopen(req, timeout=0):
        body = json.dumps(
            {
                "content": [{"type": "text", "text": "reviewed"}],
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 10,
                    "cache_read_input_tokens": 80,
                },
            }
        ).encode("utf-8")
        return mock.Mock(
            read=lambda: body,
            __enter__=lambda s: s,
            __exit__=mock.Mock(return_value=False),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    agent = make_agent(tmp_path)
    agent.cfg.max_worker_iterations = 1
    agent.cfg.pricing = {
        "claude-sonnet-5": {
            "input_per_million": 3.0,
            "output_per_million": 15.0,
            "cached_input_per_million": 0.3,
        }
    }
    monkeypatch.setenv("ANTHROPIC_TEST_KEY", "sk-ant")
    cfg = C.GlobalConfig()
    cfg.gigwork = {
        "providers": {
            "claude": {
                "kind": "anthropic_native",
                "base_url": "https://api.anthropic.com/v1",
                "api_key_env": "ANTHROPIC_TEST_KEY",
                "model": "claude-sonnet-5",
            }
        }
    }
    cfg.pricing = agent.cfg.pricing
    backend = resolve_gig_backend(cfg, "claude")
    w = WorkerAgent(
        clients=SimpleNamespace(chat=SimpleNamespace(chat=SimpleNamespace())),
        project=agent.project,
        cfg=agent.cfg,
        role="explore",
        chat_backend=backend,
    )
    text, call = w.run("scan imports")
    assert text == "reviewed"
    assert call.prompt_tokens == 100
    assert call.cost_usd is not None


def _resp(msg):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0),
    )


def test_worker_uses_backend_not_home_clients(tmp_path):
    agent = make_agent(tmp_path)
    agent.cfg.max_worker_iterations = 1
    backend = AnthropicNativeBackend(
        label="claude",
        model="claude-sonnet-5",
        api_key="k",
        base_url="https://api.anthropic.com/v1",
    )
    backend.create = lambda **kw: _resp(make_msg("via native", None))  # type: ignore[method-assign]
    clients = SimpleNamespace(
        chat=SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kw: (_ for _ in ()).throw(
                        AssertionError("home client touched")
                    )
                )
            )
        )
    )
    w = WorkerAgent(
        clients=clients,
        project=agent.project,
        cfg=agent.cfg,
        role="explore",
        chat_backend=backend,
    )
    text, _call = w.run("task")
    assert text == "via native"
