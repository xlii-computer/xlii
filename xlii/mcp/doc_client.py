"""Minimal MCP (Model Context Protocol) client over HTTP.

Implements just enough of the MCP spec for /doc --mcp sources to work:
  - initialize handshake (sent once per call to keep things stateless)
  - tools/list (used by xli doc mcp-import; not yet shipped)
  - tools/call (the read path used by doc_query)

JSON-RPC 2.0 over HTTP POST. urllib only — no SDK dependencies. Mirrors
the request/error pattern in xli.tools.plugins.plugin_call so timeouts,
HTTPError handling, and redaction look familiar.

SSE transport is deferred. If a server only speaks SSE, doc_query will
surface a clear error pointing at the limitation.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class MCPError(RuntimeError):
    """Raised on protocol or transport errors. Caller turns it into a tool
    error that the agent can see."""
    pass


def _post(url: str, payload: dict, timeout: int = 30) -> dict:
    """POST a JSON-RPC payload, return the parsed response object.

    The MCP Streamable HTTP transport requires clients to advertise BOTH
    application/json AND text/event-stream in Accept; servers respond with
    whichever they choose. Sending just one (or sending */*) gets HTTP 406
    from spec-strict servers like xAI's docs endpoint.

    Raises MCPError on transport failure or non-2xx response."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-03-26",
            "User-Agent": "xlii-mcp/0.1",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            content_type = (resp.headers.get("Content-Type") or "").lower()
    except urllib.error.HTTPError as e:
        err_body = ""
        if e.fp:
            err_body = e.read().decode("utf-8", errors="replace")
        raise MCPError(f"HTTP {e.code} from {url}: {err_body[:400]}") from e
    except TimeoutError as e:
        raise MCPError(f"timeout talking to {url}") from e
    except urllib.error.URLError as e:
        raise MCPError(f"network error to {url}: {e.reason}") from e

    # Server may respond with JSON OR SSE depending on its choice. SSE is
    # used when the server wants to stream; for our request shape (one-shot
    # initialize / tools/list / tools/call) the meaningful payload is the
    # final `data:` line carrying the JSON-RPC envelope.
    if "text/event-stream" in content_type:
        return _parse_sse_response(body, url)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise MCPError(f"invalid JSON from {url}: {body[:400]}") from e


def _parse_sse_response(body: str, url: str) -> dict:
    """Extract the last JSON-RPC envelope from an SSE response body.

    SSE format is `event: <name>` / `data: <payload>` / blank-line-terminated.
    For one-shot RPC we want the last `data:` JSON payload — that's the
    response. Multi-line `data:` are concatenated per spec."""
    last_payload = None
    current_data: list[str] = []
    for raw in body.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("data:"):
            current_data.append(line[5:].lstrip())
        elif line == "":
            if current_data:
                joined = "\n".join(current_data)
                try:
                    last_payload = json.loads(joined)
                except json.JSONDecodeError:
                    # A non-JSON SSE data block leaves last_payload as the previous good one.
                    pass
                current_data = []
    # Catch a final event with no trailing blank line.
    if current_data:
        joined = "\n".join(current_data)
        try:
            last_payload = json.loads(joined)
        except json.JSONDecodeError:
            # Same for the trailing block -- last_payload keeps the last good value.
            pass
    if last_payload is None:
        raise MCPError(f"SSE response from {url} contained no parseable JSON-RPC payload: {body[:400]}")
    return last_payload


def _check_jsonrpc(resp: dict) -> dict:
    """Validate JSON-RPC envelope. Returns the `result` dict or raises MCPError
    if the response carries an `error` field."""
    if not isinstance(resp, dict):
        raise MCPError(f"unexpected response shape: {resp!r}")
    if "error" in resp:
        err = resp["error"] or {}
        code = err.get("code", "?")
        msg = err.get("message", "<no message>")
        raise MCPError(f"MCP error {code}: {msg}")
    if "result" not in resp:
        raise MCPError(f"response missing 'result': {resp!r}")
    return resp["result"]


def _initialize(url: str, timeout: int = 30) -> None:
    """Send the MCP initialize handshake. Servers that don't require a
    persistent session will accept this and we proceed; servers that do
    require sessions over plain HTTP would surface that as an error here."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "xli", "version": "0.1"},
        },
    }
    resp = _post(url, payload, timeout=timeout)
    _check_jsonrpc(resp)


def list_tools(url: str, timeout: int = 30) -> list[dict]:
    """Return the server's advertised tool list. Each entry is the raw MCP
    tool descriptor (includes name, description, inputSchema)."""
    _initialize(url, timeout=timeout)
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    resp = _post(url, payload, timeout=timeout)
    result = _check_jsonrpc(resp)
    tools = result.get("tools", [])
    if not isinstance(tools, list):
        raise MCPError(f"tools/list returned non-list: {tools!r}")
    return tools


def call_tool(url: str, tool_name: str, arguments: dict, timeout: int = 30) -> str:
    """Invoke a tool. Returns the response content collapsed to text.

    Skips the initialize handshake — for stateless HTTP servers like xAI's
    docs endpoint, initialize is unnecessary per-call and adds a full
    network roundtrip. If a server requires init, it will respond with a
    JSON-RPC error and the caller can decide to retry with init via list_tools.

    MCP responses use a content blocks model — concatenate all text blocks
    and surface non-text blocks as a one-line note. Most servers (including
    docs servers) return only text blocks for read operations."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    resp = _post(url, payload, timeout=timeout)
    result = _check_jsonrpc(resp)
    if result.get("isError"):
        # The server reported a tool-level error; surface it.
        return _collapse_content(result.get("content", []), error=True)
    return _collapse_content(result.get("content", []))


def _collapse_content(blocks: list, *, error: bool = False) -> str:
    """Turn MCP content blocks into a flat string the agent can read."""
    if not isinstance(blocks, list):
        return f"(unexpected content shape: {blocks!r})"
    out = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        btype = b.get("type")
        if btype == "text":
            out.append(b.get("text", ""))
        else:
            out.append(f"(non-text block: {btype})")
    text = "\n".join(out).strip() or "(empty response)"
    if error:
        text = "TOOL ERROR: " + text
    return text
