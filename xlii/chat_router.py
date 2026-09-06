"""Chat router — resolve `auto` to a concrete tier + the escalation contract
(chat-tiers Vector B).

`auto` is resolved here (this replaces :func:`xlii.chat_tiers._route_auto`'s
placeholder). Two layers:

  L1  deterministic, zero-latency heuristics on the message → a confident tier
      (``fast`` | ``expert`` | ``heavy``) or ``None`` (ambiguous).
  L2  the ambiguous tail defaults to ``expert``, which carries the
      ``request_deep_search`` escape hatch: if the model calls it *instead of*
      answering, the turn escalates one hop to ``heavy``, warm-started from any
      sub-queries it emitted. Escalation beats a pre-turn classifier — the
      decision is made with full context and tool awareness, at no pre-turn
      latency cost.

This module is pure (no I/O, no model calls) so it is trivially testable. The
escalation *activation* — putting ``request_deep_search`` in the chat palette and
re-running as heavy — is wired at the turn layer once Vector C's
``run_deep_search`` exists (until then heavy and expert are the same reasoning
model, so escalating buys nothing). The primitives + injectable
:func:`maybe_escalate` driver here make that hookup a one-liner.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# --------------------------------------------------------------------------- #
#  Layer 1 — deterministic heuristics
# --------------------------------------------------------------------------- #

# High-precision "needs fresh / external / broad data" signals → heavy. This set
# is deliberately high-precision, low-recall: a missed heavy routes to expert and
# self-escalates, so we only fire on strong signals and never jump to heavy on a
# weak one (the locked router rule).
_URL_RE = re.compile(r"https?://|\bwww\.\w", re.I)
_HEAVY_RE = re.compile(
    r"\b(?:latest|most recent|up[- ]?to[- ]?date|breaking(?: news)?|"
    r"who won|who is winning|"
    r"current (?:price|score|weather|events|version|state of)|"
    r"price of|stock price|release date|as of (?:today|now|this)|"
    r"right now|today'?s|this week|this month|in the news|news (?:about|on)|"
    r"search (?:for|the web)|look up|on (?:x|twitter)|"
    r"tweets? (?:about|from|by|on)|search(?:ing)? tweets?|"
    r"score of|weather (?:in|for|today))\b",
    re.I,
)

# Deeper-reasoning signals → expert.
_EXPERT_RE = re.compile(
    r"\b(?:prove|derive|step[- ]by[- ]step|why (?:does|is|are|do|would)|explain why|"
    r"design (?:a|an|the)|architect|debug|trace through|walk through|"
    r"solve|optimi[sz]e|time complexity|big[- ]?o|algorithm|"
    r"reason (?:through|about)|think through|analy[sz]e|compare and contrast|"
    r"trade[- ]?offs?)\b",
    re.I,
)

# Casual / quick-edit signals → fast.
_FAST_RE = re.compile(
    r"\b(?:thanks?|thank you|thx|hello|hey|yo|lol|okay|cool|nice|got it|"
    r"tl;?dr|summari[sz]e this|rewrite|rephrase|reword|make it shorter|"
    r"shorter|fix (?:the )?grammar|proofread)\b",
    re.I,
)

_CODE_FENCE = "```"
_LONG_MESSAGE = 400   # chars — a substantial question leans expert
_SHORT_MESSAGE = 32   # chars — a terse aside leans fast


def classify(message: Optional[str]) -> Optional[str]:
    """Confident heuristic tier for *message*, or ``None`` when ambiguous.

    Order = precedence: a fresh-data need (heavy) beats reasoning depth (expert)
    beats casual (fast) — you can't reason over data you don't have yet.
    """
    if not message:
        return None
    text = message.strip()
    if not text:
        return None

    # heavy — strong external / fresh-data signals.
    if _URL_RE.search(text) or _HEAVY_RE.search(text):
        return "heavy"

    # expert — explicit reasoning asks, code to reason over, or a long question.
    if _EXPERT_RE.search(text) or _CODE_FENCE in text or len(text) > _LONG_MESSAGE:
        return "expert"

    # fast — greetings / quick edits, or a very short aside.
    if _FAST_RE.search(text) or len(text) <= _SHORT_MESSAGE:
        return "fast"

    return None


def route(message: Optional[str], *, cfg: Any = None) -> str:
    """Resolve `auto` to a concrete tier name — the L1 + L2 router.

    A confident heuristic wins; the ambiguous tail defaults to ``expert`` (never
    auto-spend on heavy without a signal — expert self-escalates instead).
    """
    return classify(message) or "expert"


# --------------------------------------------------------------------------- #
#  Layer 2 — the escalation contract
# --------------------------------------------------------------------------- #

ESCALATE_TOOL = "request_deep_search"


@dataclass
class EscalationRequest:
    """A model's request to escalate the current turn to a heavy deep search."""

    reason: str = ""
    subqueries: list[str] = field(default_factory=list)


def request_deep_search_schema() -> dict:
    """Tool schema for the escape hatch offered to auto/expert chat turns.

    Calling it (instead of answering) signals "I can't answer well without a
    coordinated deep search." The turn escalates one hop to heavy; any
    ``subqueries`` warm-start the coordinator's first wave (Vector C).
    """
    return {
        "type": "function",
        "function": {
            "name": ESCALATE_TOOL,
            "description": (
                "Escalate THIS turn to a coordinated deep search (the 'heavy' "
                "tier) when you cannot answer well from what you already know — "
                "because the answer needs current, real-time, or broad external "
                "information (recent events, live data, wide web/X coverage). "
                "Call this EARLY, before drafting a partial answer, INSTEAD of "
                "answering. Do NOT call it for questions you can answer from your "
                "own knowledge — answer those directly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": (
                            "One line: what fresh/external information the answer needs."
                        ),
                    },
                    "subqueries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional 2–6 focused sub-questions to search in "
                            "parallel — these warm-start the deep search."
                        ),
                    },
                    "gaggle": {
                        "type": "string",
                        "description": (
                            "Optional: run investigate sub-queries as a named "
                            "gaggle (stock: second-opinion) instead of one "
                            "home worker. Foreign members must be on "
                            "gigwork.defaults.allow. Do not pass with gig=."
                        ),
                    },
                    "gig": {
                        "type": "string",
                        "description": (
                            "Optional: hire a configured non-xAI provider as "
                            "the investigate worker's brain (gigwork). Only "
                            "names in gigwork.defaults.allow. Explore kit, "
                            "read-only. Do not pass with gaggle=."
                        ),
                    },
                },
                "required": ["reason"],
            },
        },
    }


def _tool_call_field(tc: Any, attr: str) -> Any:
    """Read ``tc.function.<attr>`` whether *tc* is an object or a dict."""
    fn = getattr(tc, "function", None)
    if fn is None and isinstance(tc, dict):
        fn = tc.get("function")
    if fn is None:
        return None
    val = getattr(fn, attr, None)
    if val is None and isinstance(fn, dict):
        val = fn.get(attr)
    return val


def detect_escalation(tool_calls: Any) -> Optional[EscalationRequest]:
    """Return an :class:`EscalationRequest` if *tool_calls* contains a
    ``request_deep_search`` call, else ``None``.

    Tolerant of malformed / missing JSON arguments (``reason`` falls back to
    ``""`` and ``subqueries`` to ``[]``) so a sloppy call never crashes the turn.
    """
    for tc in tool_calls or []:
        if _tool_call_field(tc, "name") != ESCALATE_TOOL:
            continue
        raw = _tool_call_field(tc, "arguments") or ""
        try:
            data = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        subs = data.get("subqueries")
        subs = subs if isinstance(subs, list) else []
        subs = [str(s).strip() for s in subs if str(s).strip()]
        return EscalationRequest(reason=str(data.get("reason") or ""), subqueries=subs)
    return None


def maybe_escalate(
    *,
    tool_calls: Any,
    heavy_executor: Callable[[list[str], str], Any],
    already_heavy: bool = False,
) -> Optional[Any]:
    """One-hop escalation driver (injectable — the turn layer's seam).

    If *tool_calls* asks for a deep search and we are not already on heavy, run
    ``heavy_executor(subqueries, reason)`` and return its result; else ``None``
    (no escalation). Never chains — *already_heavy* guards a heavy turn from
    re-escalating, so escalation is strictly one hop.
    """
    if already_heavy:
        return None
    esc = detect_escalation(tool_calls)
    if esc is None:
        return None
    return heavy_executor(esc.subqueries, esc.reason)
