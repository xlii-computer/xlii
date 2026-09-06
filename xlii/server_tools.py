"""Wrappers around xAI's Responses API server-side tools.

The main agent loop runs on Chat Completions. xAI moved Live Search and
code execution to the Responses API only — Chat Completions Live Search
is deprecated. Rather than rewriting the loop, we expose web_search and
code_execute as ordinary local function tools whose implementations fire
a one-shot Responses-API sub-call.

Endpoint: POST <api host>/v1/responses
The OpenAI Python SDK (>=1.50) routes `client.responses.create()` there via
Clients.chat's base_url — the global api.x.ai/v1 or a regional edge when
cfg.region / XAI_REGION is set, with no extra wiring here.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol

from xlii.client import Clients
from xlii.config import GlobalConfig

# Server tools work on reasoning models. If the user has an older orchestrator
# configured, fall back to a known-good model rather than fail mysteriously.
SERVER_TOOL_FALLBACK_MODEL = "grok-4.20-reasoning"


class _UsageSink(Protocol):
    """Anything that can record sub-call usage. ToolContext implements this."""
    def record_server_usage(self, model: str, prompt_tokens: int, completion_tokens: int) -> None: ...


def _pick_model(cfg: GlobalConfig) -> str:
    m = cfg.get_model_for_role("orchestrator")
    return m if "reasoning" in m else SERVER_TOOL_FALLBACK_MODEL


def _extract_text(resp: Any) -> str:
    """Pull the final answer out of a Responses-API response.

    SDK exposes `output_text` as a convenience; fall back to walking
    `output[].content[].text` if that's missing on older SDKs.
    """
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text:
        return text
    parts: list[str] = []
    for item in getattr(resp, "output", None) or []:
        for c in getattr(item, "content", None) or []:
            t = getattr(c, "text", None)
            if isinstance(t, str) and t:
                parts.append(t)
    return "\n".join(parts)


def _extract_citations(resp: Any) -> list[str]:
    cites = getattr(resp, "citations", None)
    if not cites:
        return []
    out: list[str] = []
    for i, c in enumerate(cites, 1):
        if isinstance(c, dict):
            url = c.get("url") or ""
            title = c.get("title") or ""
        else:
            url = getattr(c, "url", "") or ""
            title = getattr(c, "title", "") or ""
        if url:
            out.append(f"[{i}] {title or url}\n    {url}")
    return out


def _record_usage(resp: Any, model: str, sink: Optional[_UsageSink]) -> None:
    if sink is None:
        return
    usage = getattr(resp, "usage", None)
    if usage is None:
        return
    # Responses API surfaces input_tokens / output_tokens; tolerate either.
    in_t = (
        getattr(usage, "input_tokens", None)
        or getattr(usage, "prompt_tokens", None)
        or 0
    )
    out_t = (
        getattr(usage, "output_tokens", None)
        or getattr(usage, "completion_tokens", None)
        or 0
    )
    sink.record_server_usage(model, int(in_t), int(out_t))


def _server_tool_call(
    clients: Clients,
    cfg: GlobalConfig,
    *,
    user_input: str,
    tool: dict,
    sink: Optional[_UsageSink] = None,
) -> str:
    model = _pick_model(cfg)
    resp = clients.chat.responses.create(
        model=model,
        input=[{"role": "user", "content": user_input}],
        tools=[tool],
    )
    _record_usage(resp, model, sink)
    text = _extract_text(resp) or "(empty response)"
    cites = _extract_citations(resp)
    if cites:
        return text + "\n\n--- citations ---\n" + "\n".join(cites)
    return text


def web_search(
    clients: Clients,
    cfg: GlobalConfig,
    query: str,
    *,
    allowed_domains: Optional[list[str]] = None,
    excluded_domains: Optional[list[str]] = None,
    sink: Optional[_UsageSink] = None,
) -> str:
    tool: dict[str, Any] = {"type": "web_search"}
    filters: dict[str, Any] = {}
    if allowed_domains:
        filters["allowed_domains"] = allowed_domains
    if excluded_domains:
        filters["excluded_domains"] = excluded_domains
    if filters:
        tool["filters"] = filters
    return _server_tool_call(clients, cfg, user_input=query, tool=tool, sink=sink)


def x_search(
    clients: Clients,
    cfg: GlobalConfig,
    query: str,
    *,
    allowed_x_handles: Optional[list[str]] = None,
    excluded_x_handles: Optional[list[str]] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    enable_image_understanding: Optional[bool] = None,
    enable_video_understanding: Optional[bool] = None,
    sink: Optional[_UsageSink] = None,
) -> str:
    tool: dict[str, Any] = {"type": "x_search"}
    if allowed_x_handles:
        tool["allowed_x_handles"] = allowed_x_handles[:10]
    if excluded_x_handles:
        tool["excluded_x_handles"] = excluded_x_handles[:10]
    if from_date:
        tool["from_date"] = from_date
    if to_date:
        tool["to_date"] = to_date
    if enable_image_understanding is not None:
        tool["enable_image_understanding"] = enable_image_understanding
    if enable_video_understanding is not None:
        tool["enable_video_understanding"] = enable_video_understanding
    return _server_tool_call(clients, cfg, user_input=query, tool=tool, sink=sink)


def code_execute(
    clients: Clients,
    cfg: GlobalConfig,
    task: str,
    *,
    sink: Optional[_UsageSink] = None,
) -> str:
    """Run Python in xAI's sandbox. `task` may be a description or actual code —
    the model decides how to interpret it. NumPy/Pandas/Matplotlib/SciPy preinstalled."""
    return _server_tool_call(
        clients, cfg,
        user_input=task,
        tool={"type": "code_interpreter"},
        sink=sink,
    )
