"""Approximate context-weight summary for the active loadout."""

from __future__ import annotations

from typing import Any

from xlii.multimodal import estimate_live_cost


def estimate_loadout_budget(state: Any) -> dict[str, Any]:
    """Return a structured, explicitly-approximate budget for what's attached.

    Docs are counted by inlined bytes; refs by count (RAG cost varies); locker
    files reuse ``estimate_live_cost`` for enabled paths.
    """
    docs = getattr(state, "attached_docs", None) or []
    refs = getattr(state, "attached_refs", None) or []
    files = getattr(state, "attached_files", None) or []

    doc_bytes = sum(len(c) for _, c in docs if isinstance(c, str))
    doc_tokens = doc_bytes // 4

    enabled_paths = []
    if hasattr(state, "live_attachment_paths"):
        enabled_paths = state.live_attachment_paths()
    elif files:
        enabled_paths = [e["path"] for e in files if e.get("enabled") and e.get("path")]

    locker_summary = estimate_live_cost(enabled_paths) if enabled_paths else "none enabled"

    total_tokens = doc_tokens
    if enabled_paths:
        # Parse ~N from estimate_live_cost when present
        if "~" in locker_summary and "tokens" in locker_summary:
            try:
                chunk = locker_summary.split("~")[1].split(" tokens")[0].replace(",", "")
                total_tokens += int(chunk)
            except (ValueError, IndexError):
                # An unparseable summary simply contributes nothing to the estimate.
                pass

    return {
        "doc_count": len(docs),
        "doc_bytes": doc_bytes,
        "doc_tokens_approx": doc_tokens,
        "ref_count": len(refs),
        "locker_enabled": len(enabled_paths),
        "locker_summary": locker_summary,
        "total_tokens_approx": total_tokens,
    }


def format_loadout_budget_line(state: Any) -> str:
    """One-line human summary for /loadout and status."""
    b = estimate_loadout_budget(state)
    parts = []
    if b["doc_count"]:
        parts.append(f"docs {b['doc_count']} (~{b['doc_tokens_approx']:,} tok)")
    if b["ref_count"]:
        parts.append(f"refs {b['ref_count']}")
    if b["locker_enabled"] or getattr(state, "attached_files", None):
        parts.append(f"locker {b['locker_summary']}")
    if not parts:
        return "context budget: empty"
    total = b["total_tokens_approx"]
    return f"context budget (approx): {' · '.join(parts)} · ~{total:,} tokens total"
