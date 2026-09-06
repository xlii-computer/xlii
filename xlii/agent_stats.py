"""Per-turn and per-call token/cost accounting for the agent loop."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from xlii.cost import estimate_cost


@dataclass
class CallStats:
    """Stats for one model's contribution to a turn.

    For the orchestrator: aggregated across all main-loop iterations.
    For workers: aggregated across every dispatched worker in the turn,
    or (when stored on a single WorkerAgent.run) the single worker's call.
    """
    model: str = ""
    iterations: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Optional[float] = None  # None when no pricing configured

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def absorb_usage(self, usage, model: str, pricing: dict) -> None:
        self.prompt_tokens += usage.prompt_tokens
        self.completion_tokens += usage.completion_tokens
        # Cache-served prompt tokens bill at the discounted rate — without
        # this, cost is overstated on every cache hit.
        cached = getattr(
            getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0
        ) or 0
        c = estimate_cost(
            pricing, model, usage.prompt_tokens, usage.completion_tokens,
            cached_tokens=int(cached),
        )
        if c is not None:
            self.cost_usd = (self.cost_usd or 0.0) + c

    def absorb_server_tool(
        self, prompt_tokens: int, completion_tokens: int, cost: float
    ) -> None:
        """Absorb server-tool sub-call usage. Cost is pre-computed by ToolContext
        because the server-tool model may differ from this CallStats's model."""
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        if cost:
            self.cost_usd = (self.cost_usd or 0.0) + cost

    def absorb(self, other: "CallStats") -> None:
        """Merge another CallStats's totals into this one (for worker aggregation)."""
        self.iterations += other.iterations
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        if other.cost_usd is not None:
            self.cost_usd = (self.cost_usd or 0.0) + other.cost_usd


@dataclass
class TurnStats:
    orch: CallStats = field(default_factory=CallStats)
    workers: CallStats = field(default_factory=CallStats)
    judges: CallStats = field(default_factory=CallStats)
    tool_calls: int = 0
    workers_dispatched: int = 0
    server_tool_calls: int = 0  # web_search / x_search / code_execute sub-calls
    warnings: list[str] = field(default_factory=list)
    # Live context occupancy (info-only; NOT summed like CallStats). The
    # orchestrator's LAST call this turn carried the whole conversation, so its
    # prompt_tokens ≈ how full the context is; cached_tokens is the cache-served
    # portion of that one call. The TUI context meter reads these; cost/totals
    # ignore them (they'd double-count against the summed CallStats figures).
    context_tokens: int = 0
    cached_tokens: int = 0

    @property
    def model(self) -> str:
        return self.orch.model

    @property
    def total_tokens(self) -> int:
        return self.orch.total_tokens + self.workers.total_tokens

    @property
    def total_cost(self) -> Optional[float]:
        parts = [self.orch.cost_usd, self.workers.cost_usd, self.judges.cost_usd]
        if all(c is None for c in parts):
            return None
        return sum(c or 0.0 for c in parts)


# Past-tense action verbs that imply work was completed. If the orchestrator
# uses one of these but called zero tools, it's claiming work it did not do —
# the system prompt forbids this but models violate it. We surface a yellow
# warning under the turn line so the user knows to verify before trusting.
_CLAIM_PATTERN = re.compile(
    r"\b("
    r"verified|created|wrote|added|installed|downloaded|uploaded|"
    r"tested|ran|executed|launched|"
    r"deleted|removed|"
    r"fixed|patched|repaired|"
    r"completed|implemented|built|generated|saved|persisted"
    r")\b",
    re.IGNORECASE,
)

# The subset that asserts the tree (or system) was *mutated*. A turn whose
# tools were all read-only leaves dirty_paths empty — write tools add real
# paths and any successful bash adds "__rescan__" — so an empty set plus one
# of these verbs means the model claims an edit its tools cannot have made.
# Verify-flavored verbs (tested, ran, …) stay out: they belong to a future
# verify tier, not this write gate (turn-receipts P0).
_EDIT_CLAIM_PATTERN = re.compile(
    r"\b("
    r"created|wrote|added|installed|downloaded|uploaded|"
    r"deleted|removed|"
    r"fixed|patched|repaired|"
    r"implemented|built|generated|saved|persisted|committed"
    r")\b",
    re.IGNORECASE,
)


def _detect_unsupported_claim(
    text: str, stats: "TurnStats", dirty_paths: Optional[set] = None,
) -> Optional[str]:
    """Return a warning line when the final text claims work the turn's tools
    cannot substantiate; None when the claim is supported (or absent).
    False positives are tolerable — this is a nudge, not a wall.

    Two lanes:
    - zero tool calls + any action claim (the original hallucination guard);
    - tools ran but none could have modified the tree (dirty_paths empty,
      i.e. read-only tools only) + an edit claim — the read-only loophole.
    """
    if not text:
        return None
    if stats.tool_calls == 0:
        m = _CLAIM_PATTERN.search(text)
        if m:
            return (
                f'model said "{m.group(1).lower()}" but called 0 tools'
                " — verify before trusting"
            )
        return None
    if not dirty_paths:
        m = _EDIT_CLAIM_PATTERN.search(text)
        if m:
            return (
                f'model said "{m.group(1).lower()}" but no files were modified'
                " this turn — verify before trusting"
            )
    return None


def _cache_headers(conversation_id: Optional[str], suffix: str = "") -> Optional[dict]:
    """Build the xAI prompt-cache header.

    `x-grok-conv-id` is xAI's convention for tagging a stable conversation so
    repeated prefixes (system prompt + tool schemas + early context) hit cache.
    The orchestrator and workers use distinct IDs (`<id>` vs `<id>:workers`)
    because they have different system prompts — sharing one ID would give us
    cache misses anyway.

    Returns None when no conversation_id is set, so the call falls through
    with no extra headers and behaves identically to the un-cached path.
    """
    if not conversation_id:
        return None
    cid = f"{conversation_id}:{suffix}" if suffix else conversation_id
    return {"x-grok-conv-id": cid}
