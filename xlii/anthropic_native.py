"""Anthropic Messages API backend for gigwork (native cache_control).

Translates the worker loop's OpenAI-shaped chat-completions request to
``/v1/messages`` and adapts the response back so WorkerAgent and
``/plan --with`` need no edits. Judges stay on the OpenAI-compat path (D18).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from types import SimpleNamespace
from typing import Any, Optional

from xlii.chat_backend import ChatBackend, GIG_CAPABILITIES, GigError

_ANTHROPIC_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 8192
_CACHE_EPHEMERAL = {"type": "ephemeral"}


class AnthropicNativeBackend(ChatBackend):
    """Gig brain that speaks Anthropic's native Messages API."""

    capabilities = GIG_CAPABILITIES

    def __init__(
        self,
        *,
        label: str,
        model: str,
        api_key: str,
        base_url: str,
        temperature: Optional[float] = None,
        cache_marks: bool = True,
    ) -> None:
        self.label = label
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.cache_marks = cache_marks

    def create(self, **kwargs: Any) -> Any:
        kwargs.pop("extra_headers", None)
        kwargs.pop("temperature", None)
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if kwargs.pop("stream", False):
            raise GigError(
                "anthropic_native does not stream yet — plan turns need a compat provider"
            )
        kwargs.pop("stream_options", None)

        model = kwargs.pop("model", None) or self.model
        messages = kwargs.pop("messages", None) or []
        tools = kwargs.pop("tools", None)
        tool_choice = kwargs.pop("tool_choice", None)
        max_tokens = int(kwargs.pop("max_tokens", _DEFAULT_MAX_TOKENS))

        system, anthropic_messages = openai_messages_to_anthropic(
            messages, cache_marks=self.cache_marks
        )
        anthropic_tools = openai_tools_to_anthropic(tools, cache_marks=self.cache_marks)

        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": anthropic_messages,
        }
        if system is not None:
            payload["system"] = system
        if anthropic_tools:
            payload["tools"] = anthropic_tools
            if tool_choice is not None:
                payload["tool_choice"] = _openai_tool_choice_to_anthropic(tool_choice)

        if "temperature" in kwargs:
            payload["temperature"] = kwargs.pop("temperature")

        url = f"{self.base_url}/messages"
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            raise GigError(
                f"anthropic_native HTTP {e.code} from {self.label}/{model}: "
                f"{err_body[:400]}"
            ) from e
        except urllib.error.URLError as e:
            raise GigError(f"anthropic_native network error to {self.label}: {e.reason}") from e

        return anthropic_response_to_openai(body)


def openai_tools_to_anthropic(
    tools: Any, *, cache_marks: bool = False
) -> list[dict[str, Any]]:
    """OpenAI ``tools`` list → Anthropic tool definitions."""
    if not tools:
        return []
    out: list[dict[str, Any]] = []
    for entry in tools:
        if not isinstance(entry, dict):
            continue
        fn = entry.get("function") if entry.get("type") == "function" else entry
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not name:
            continue
        tool: dict[str, Any] = {
            "name": name,
            "description": fn.get("description") or "",
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        }
        out.append(tool)
    if cache_marks and out:
        out[-1] = {**out[-1], "cache_control": _CACHE_EPHEMERAL}
    return out


def _text_block(text: str, *, cache: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "text", "text": text}
    if cache:
        block["cache_control"] = _CACHE_EPHEMERAL
    return block


def _openai_content_to_blocks(content: Any, *, cache: bool = False) -> list[dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [_text_block(content, cache=cache)] if content else []
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text", "")
                if text:
                    blocks.append(
                        _text_block(
                            text,
                            cache=part.get("cache_control") is not None,
                        )
                    )
            elif isinstance(part, dict) and "text" in part:
                text = part.get("text", "")
                if text:
                    use_cache = part.get("cache_control") is not None
                    blocks.append(_text_block(text, cache=use_cache))
        if cache and blocks:
            blocks[-1] = {**blocks[-1], "cache_control": _CACHE_EPHEMERAL}
        return blocks
    return [_text_block(str(content), cache=cache)]


def openai_messages_to_anthropic(
    messages: list[Any], *, cache_marks: bool = False
) -> tuple[Any, list[dict[str, Any]]]:
    """Split OpenAI-shaped ``messages`` into Anthropic ``system`` + ``messages``."""
    system_blocks: list[dict[str, Any]] = []
    anthropic_messages: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    last_user_idx = -1
    if cache_marks:
        for i, m in enumerate(messages):
            if isinstance(m, dict) and m.get("role") == "user":
                last_user_idx = i

    def _flush_tool_results() -> None:
        nonlocal pending_tool_results
        if not pending_tool_results:
            return
        anthropic_messages.append(
            {"role": "user", "content": list(pending_tool_results)}
        )
        pending_tool_results = []

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "system":
            system_blocks.extend(_openai_content_to_blocks(msg.get("content")))
            continue

        if role == "tool":
            pending_tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": msg.get("tool_call_id", ""),
                    "content": str(msg.get("content", "")),
                }
            )
            continue

        _flush_tool_results()

        if role == "user":
            cache = cache_marks and i == last_user_idx
            blocks = _openai_content_to_blocks(msg.get("content"), cache=cache)
            if blocks:
                anthropic_messages.append({"role": "user", "content": blocks})
            continue

        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            blocks.extend(_openai_content_to_blocks(msg.get("content")))
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") or {}
                raw_args = fn.get("arguments") or "{}"
                try:
                    inputs = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    inputs = {}
                if not isinstance(inputs, dict):
                    inputs = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": inputs,
                    }
                )
            if blocks:
                anthropic_messages.append({"role": "assistant", "content": blocks})
            continue

    _flush_tool_results()

    system: Any = None
    if system_blocks:
        if cache_marks:
            system_blocks[-1] = {
                **system_blocks[-1],
                "cache_control": _CACHE_EPHEMERAL,
            }
            system = system_blocks
        elif len(system_blocks) == 1:
            system = system_blocks[0]["text"]
        else:
            system = system_blocks

    while anthropic_messages and anthropic_messages[0].get("role") != "user":
        anthropic_messages.pop(0)

    return system, anthropic_messages


def _openai_tool_choice_to_anthropic(tool_choice: Any) -> dict[str, Any]:
    if tool_choice == "auto":
        return {"type": "auto"}
    if tool_choice == "none":
        return {"type": "none"}
    if tool_choice == "required":
        return {"type": "any"}
    if isinstance(tool_choice, dict):
        fn = tool_choice.get("function") or {}
        name = fn.get("name")
        if name:
            return {"type": "tool", "name": name}
    return {"type": "auto"}


def anthropic_response_to_openai(resp: dict[str, Any]) -> SimpleNamespace:
    """Anthropic Messages response → OpenAI chat-completions shape."""
    content_parts: list[str] = []
    tool_calls: list[SimpleNamespace] = []
    for block in resp.get("content") or []:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            content_parts.append(block.get("text", ""))
        elif btype == "tool_use":
            tool_calls.append(
                SimpleNamespace(
                    id=block.get("id", ""),
                    type="function",
                    function=SimpleNamespace(
                        name=block.get("name", ""),
                        arguments=json.dumps(block.get("input") or {}),
                    ),
                )
            )

    usage_raw = resp.get("usage") or {}
    input_tokens = int(usage_raw.get("input_tokens", 0))
    output_tokens = int(usage_raw.get("output_tokens", 0))
    cached = int(usage_raw.get("cache_read_input_tokens", 0))
    usage = SimpleNamespace(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
    )

    message = SimpleNamespace(
        content="".join(content_parts) if content_parts else None,
        tool_calls=tool_calls or None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=usage,
    )
