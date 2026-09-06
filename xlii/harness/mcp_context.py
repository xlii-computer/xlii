"""Register xlii DeepContext MCP servers for external harnesses."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from xlii.acp_client import (
    XLII_MCP_SERVER_NAME,
    enable_cursor_mcp,
    ensure_cursor_mcp_config,
    xlii_mcp_server_spec,
)

GROK_MCP_DIR = ".grok"
GROK_MCP_FILE = "mcp.json"


def ensure_grok_mcp_config(
    project_root: str | Path,
    *,
    name: str = XLII_MCP_SERVER_NAME,
    spec: dict[str, Any] | None = None,
) -> str:
    """Merge xlii DeepContext into ``<project_root>/.grok/mcp.json``."""
    spec = spec or xlii_mcp_server_spec()
    gdir = Path(project_root) / GROK_MCP_DIR
    cfg_path = gdir / GROK_MCP_FILE
    data: dict[str, Any] = {}
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(errors="replace"))
        except (json.JSONDecodeError, OSError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        servers = data["mcpServers"] = {}
    if servers.get(name) == spec:
        return "present"
    existed = name in servers
    servers[name] = spec
    gdir.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(data, indent=2) + "\n")
    return "updated" if existed else "added"


def build_handoff_context(state: Any, *, max_doc_chars: int = 2000, max_turns: int = 6) -> str:
    """Summarize xlii's live tab manifest into a prompt block (Vector C, Tier 2).

    The `--context` flag historically only registered the DeepContext MCP server
    (cursor/grok). This *deepens* it: it forwards the live manifest — the equipped
    role, attached docs/refs, and the recent conversation — as a text block to
    prepend to the delegate/session task, so the harness starts *where xlii is*
    even when it can't reach the MCP (claude/codex). Returns '' when there is
    nothing to hand off (a fresh, attachment-free session). Pure over REPLState
    (defensive getattr), so a partial/fake state never raises."""
    if state is None:
        return ""
    parts: list[str] = []

    role = getattr(state, "active_role", None)
    if role:
        parts.append(f"Active role: {role}")

    docs = getattr(state, "attached_docs", None) or []
    doc_blocks: list[str] = []
    for entry in docs:
        try:
            name, content = entry
        except (TypeError, ValueError):
            continue
        # Skills ride attached_docs under a `skill:` prefix; they are xlii's own
        # loadout, not handoff material — skip them (mirrors seam #7's split).
        if str(name).startswith("skill:"):
            continue
        body = content or ""
        if len(body) > max_doc_chars:
            body = body[:max_doc_chars].rstrip() + "\n…(truncated)"
        doc_blocks.append(f"### {name}\n{body}")
    if doc_blocks:
        parts.append("Attached reference docs:\n\n" + "\n\n".join(doc_blocks))

    refs = getattr(state, "attached_refs", None) or []
    ref_names = [str(n) for n, *_ in refs] if refs else []
    if ref_names:
        parts.append("Attached memory refs: " + ", ".join(ref_names))

    agent = getattr(state, "agent", None)
    history = getattr(agent, "history", None) or []
    convo: list[str] = []
    for msg in history:
        if not isinstance(msg, dict):
            continue
        who = msg.get("role")
        if who not in ("user", "assistant"):
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            snippet = content.strip()
            if len(snippet) > 800:
                snippet = snippet[:800].rstrip() + "…"
            convo.append(f"[{who}] {snippet}")
    if convo:
        parts.append("Recent xlii conversation:\n" + "\n".join(convo[-max_turns:]))

    if not parts:
        return ""
    return "## Context handed off from xlii\n\n" + "\n\n".join(parts)


def ensure_deep_context(
    project_root: str | Path,
    harness: str,
    *,
    server_name: str = XLII_MCP_SERVER_NAME,
    spec: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """Register (and when possible approve) DeepContext for ``harness``.

    Returns ``(action, approval_ok)`` where ``action`` is ``present|added|updated|skipped``.
    """
    root = Path(project_root)
    if harness == "cursor":
        action = ensure_cursor_mcp_config(root, name=server_name, spec=spec)
        approved = enable_cursor_mcp(name=server_name, cwd=root)
        return action, approved
    if harness == "grok":
        action = ensure_grok_mcp_config(root, name=server_name, spec=spec)
        # Grok Build picks up .grok/mcp.json on launch; no separate enable step.
        return action, True
    return "skipped", True
