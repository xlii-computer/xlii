"""Context compaction — summarize-and-continue for long sessions (Phase 9).

Distills older conversation turns via ``secondary_ai`` into a structured summary,
rebuilds in-memory history as ``[system, summary, recent-N]``, and leaves
on-disk turn files untouched. Full-fidelity chat is recoverable from
``.xlii/turns/`` on the next session re-seed — not via ``/rewind`` (git files only).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

from xlii.agent_stats import TurnStats

COMPACT_SUMMARY_HEADER = "[Compacted session summary]"

_DEFAULT_RECENT_TURNS = 4
_AUTO_COMPACT_RATIO = 0.85

# Caps live in session_meter (live /v1/models + baked fallback).

_COMPACT_SYSTEM = """You compress long coding-agent conversations for continuity.
Output markdown with exactly these sections (use the headings verbatim):
## Plan
## Work done
## Files touched
## Next steps

Be specific: file paths, decisions, commands, errors, and open tasks.
Omit tool-call JSON and repetitive investigation noise."""


@dataclass
class CompactResult:
    compacted: bool
    before_messages: int = 0
    after_messages: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    summarized_turns: int = 0
    kept_turns: int = 0
    reason: str = ""


def context_window_for_model(model: str) -> Optional[int]:
    from xlii.session_meter import context_window

    return context_window(model or "")


def context_cap_for_agent(agent: Any) -> Optional[int]:
    cfg = getattr(agent, "cfg", None)
    session = getattr(agent, "session", None)
    try:
        from xlii.agent import resolve_orchestrator_model

        model, _ = resolve_orchestrator_model(
            cfg=cfg,
            model_override=getattr(session, "model_override", None),
            conversational=getattr(session, "conversational", False),
            howto_mode=getattr(session, "howto_mode", False),
        )
    except Exception:
        model = ""
    return context_window_for_model(model or "")


def init_compact_auto_from_env(session: Any) -> None:
    """Seed session.compact_auto from XLII_COMPACT_AUTO once at session start."""
    if getattr(session, "compact_auto_env_cleared", False):
        return
    if getattr(session, "compact_auto", False):
        return
    raw = os.environ.get("XLII_COMPACT_AUTO", "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        session.compact_auto = True


def prior_compacted_summary(history: list[dict]) -> str:
    """Body of the most recent compacted-summary message, if any."""
    for m in history:
        if m.get("role") != "user":
            continue
        content = m.get("content")
        if not isinstance(content, str):
            continue
        text = content.strip()
        if text.startswith(COMPACT_SUMMARY_HEADER):
            body = text[len(COMPACT_SUMMARY_HEADER) :].strip()
            return body
    return ""


def conversation_messages(history: list[dict]) -> list[dict]:
    """Plain user/assistant string messages (skip system, tools, and prior summary)."""
    out: list[dict] = []
    for m in history:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        if content.strip().startswith(COMPACT_SUMMARY_HEADER):
            continue
        out.append({"role": m["role"], "content": content.strip()})
    return out


def split_for_compact(
    conv: list[dict], recent_turns: int
) -> tuple[list[dict], list[dict]]:
    """Return (to_summarize, to_keep) as user/assistant message lists."""
    if recent_turns <= 0:
        if not conv:
            return [], []
        return conv, []
    keep_msgs = recent_turns * 2
    if len(conv) <= keep_msgs:
        return [], conv
    return conv[:-keep_msgs], conv[-keep_msgs:]


def estimate_history_tokens(history: list[dict]) -> int:
    """Rough token estimate for the meter after compaction (chars / 4)."""
    total = 0
    for m in history:
        content = m.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += len(str(part.get("text", "")))
    return max(0, total // 4)


def _summarize_messages(messages: list[dict], prior_summary: str = "") -> Any:
    from xlii.secondary_ai import query_with_profile

    system = _COMPACT_SYSTEM
    if prior_summary:
        system += (
            "\n\nAn earlier compacted summary already exists — merge its facts "
            "into the new summary (do not drop prior plan, files, or open tasks):\n\n"
            + prior_summary
        )
    return query_with_profile(
        messages,
        "Compress the conversation above into the required sections.",
        system=system,
    )


def _summary_text_from_response(resp: Any) -> str:
    text = (getattr(resp, "text", None) or "").strip()
    if text == "(empty response from secondary model)":
        return ""
    return text


def _record_summarizer_cost(agent: Any, resp: Any, state: Any = None) -> None:
    session = getattr(agent, "session", None)
    if session is None:
        return
    cost = getattr(resp, "cost_usd", None)
    if cost is not None and cost > 0:
        session.session_cost = float(getattr(session, "session_cost", 0.0) or 0.0) + cost
    tokens = int(getattr(resp, "prompt_tokens", 0) or 0) + int(
        getattr(resp, "completion_tokens", 0) or 0
    )
    if tokens:
        session.session_tokens = int(getattr(session, "session_tokens", 0) or 0) + tokens
    if state is None or cost is None or cost <= 0:
        return
    ctrl = getattr(state, "loop", None)
    if ctrl is not None and getattr(ctrl, "is_active", False):
        ctrl.state.cost["judges_usd"] = ctrl.state.cost.get("judges_usd", 0.0) + cost


def _patch_context_meter(agent: Any, history: list[dict]) -> None:
    session = getattr(agent, "session", None)
    if session is None:
        return
    est = estimate_history_tokens(history)
    stats = getattr(session, "last_turn_stats", None)
    if stats is None:
        stats = TurnStats()
        session.last_turn_stats = stats
    stats.context_tokens = est


def compact_agent_history(
    agent: Any,
    *,
    recent_turns: Optional[int] = None,
    dry_run: bool = False,
    state: Any = None,
) -> CompactResult:
    """Summarize older turns and rebuild agent.history. Disk turns are untouched."""
    session = getattr(agent, "session", None)
    if recent_turns is None:
        recent_turns = getattr(session, "compact_recent", _DEFAULT_RECENT_TURNS)
    recent_turns = max(0, int(recent_turns))

    history = list(getattr(agent, "history", []) or [])
    if not history:
        return CompactResult(compacted=False, reason="no history")

    prior = prior_compacted_summary(history)
    conv = conversation_messages(history)
    to_summarize, to_keep = split_for_compact(conv, recent_turns)
    if not to_summarize:
        return CompactResult(
            compacted=False,
            before_messages=len(conv),
            after_messages=len(conv),
            reason="nothing to compact (conversation shorter than recent window)",
        )

    before_tokens = estimate_history_tokens(history)
    summarized_turns = len(to_summarize) // 2

    if dry_run:
        kept_hist = [
            history[0],
            {"role": "user", "content": f"{COMPACT_SUMMARY_HEADER}\n\n…"},
            *to_keep,
        ]
        after_tokens = estimate_history_tokens(kept_hist)
        return CompactResult(
            compacted=False,
            before_messages=len(conv),
            after_messages=1 + len(to_keep),
            before_tokens=before_tokens,
            after_tokens=after_tokens,
            summarized_turns=summarized_turns,
            kept_turns=len(to_keep) // 2,
            reason="dry run",
        )

    try:
        resp = _summarize_messages(to_summarize, prior_summary=prior)
    except Exception as exc:
        return CompactResult(
            compacted=False,
            before_messages=len(conv),
            reason=f"summarizer failed: {exc}",
        )

    summary = _summary_text_from_response(resp)
    if not summary:
        return CompactResult(
            compacted=False,
            before_messages=len(conv),
            reason="summarizer returned empty",
        )

    _record_summarizer_cost(agent, resp, state)

    system = {"role": "system", "content": agent._effective_system_prompt()}
    summary_msg = {
        "role": "user",
        "content": f"{COMPACT_SUMMARY_HEADER}\n\n{summary}",
    }
    agent.history = [system, summary_msg, *to_keep]
    after_tokens = estimate_history_tokens(agent.history)
    _patch_context_meter(agent, agent.history)

    return CompactResult(
        compacted=True,
        before_messages=len(conv),
        after_messages=len(agent.history) - 1,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        summarized_turns=summarized_turns,
        kept_turns=len(to_keep) // 2,
    )


def should_auto_compact(used_tokens: int, cap: Optional[int]) -> bool:
    if not used_tokens or not cap:
        return False
    return used_tokens >= int(cap * _AUTO_COMPACT_RATIO)


def maybe_auto_compact(state: Any) -> bool:
    """Run compaction when auto mode is on and context is near the cap."""
    agent = getattr(state, "agent", None)
    session = getattr(agent, "session", None) if agent is not None else None
    if agent is None or session is None or not getattr(session, "compact_auto", False):
        return False
    stats = getattr(session, "last_turn_stats", None)
    used = getattr(stats, "context_tokens", 0) or 0
    cap = context_cap_for_agent(agent)
    if not should_auto_compact(used, cap):
        return False
    result = compact_agent_history(agent, recent_turns=session.compact_recent, state=state)
    if result.compacted:
        console = getattr(state, "console", None)
        if console is not None:
            console.print(
                f"[dim][compact] auto — ~{_ktok(result.before_tokens)} → "
                f"~{_ktok(result.after_tokens)} context "
                f"({result.summarized_turns} turn(s) summarized, "
                f"{result.kept_turns} kept)[/dim]"
            )
    return result.compacted


def _ktok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)
