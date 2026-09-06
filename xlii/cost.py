"""Per-turn cost estimation.

xAI pricing varies by model and changes over time, so XLI does not ship
hardcoded rates. Instead, fill in the per-model rates in your config:

    {
      "pricing": {
        "grok-4-1-fast-reasoning":     {
            "input_per_million":         0.50,
            "cached_input_per_million":  0.05,
            "output_per_million":        2.00,
        },
        "grok-4-1-fast-non-reasoning": {
            "input_per_million":         0.10,
            "cached_input_per_million":  0.01,
            "output_per_million":        0.40,
        }
      }
    }

(Values above are placeholders — verify against your xAI dashboard.)

`cached_input_per_million` is the rate for tokens served from xAI's
prompt cache. If omitted, defaults to 10% of `input_per_million` —
matching the standard OpenAI-compatible prompt-cache discount and a
reasonable approximation for xAI. Long agent turns frequently hit
80-95% cache, so leaving this off can over-state real cost by 4×+.

`estimate_cost` returns USD as a float, or None if no pricing data is
configured for the model in question. Callers should treat None as
"unknown — don't display a number."
"""

from __future__ import annotations

from typing import Optional


def estimate_cost(
    pricing_table: dict,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
) -> Optional[float]:
    """Compute USD cost for a single completion call, accounting for prompt
    caching.

    `cached_tokens` is the subset of `prompt_tokens` that the provider
    reported as served from prompt cache (xAI: usage.prompt_tokens_details
    .cached_tokens). It is billed at the lower `cached_input_per_million`
    rate; the remainder is billed at full `input_per_million`. If
    `cached_input_per_million` is missing from pricing_table, we default to
    10% of `input_per_million` (a sensible approximation that matches the
    OpenAI-compatible prompt-cache discount convention).

    Returns None when the model isn't in pricing_table — that's the signal
    to the caller that we don't know the price and shouldn't fabricate one.
    """
    entry = pricing_table.get(model)
    if not entry:
        return None
    in_rate = float(entry.get("input_per_million", 0.0))
    out_rate = float(entry.get("output_per_million", 0.0))
    # Default cached rate to 10% of the input rate when not configured. The
    # explicit value should always win when present, including 0.0 (some
    # providers offer free cache reads).
    if "cached_input_per_million" in entry:
        cached_rate = float(entry["cached_input_per_million"])
    else:
        cached_rate = in_rate * 0.1

    # Defensive clamps — provider could in theory report cached > prompt;
    # treat that as "all cached" rather than negative uncached billing.
    cached_tokens = max(0, min(int(cached_tokens), int(prompt_tokens)))
    fresh_tokens = int(prompt_tokens) - cached_tokens

    return (
        (fresh_tokens / 1_000_000.0) * in_rate
        + (cached_tokens / 1_000_000.0) * cached_rate
        + (completion_tokens / 1_000_000.0) * out_rate
    )


def format_cost(usd: Optional[float]) -> str:
    """Compact USD string. None when pricing unknown; '<$0.001' for tiny."""
    if usd is None:
        return "?"
    if usd == 0:
        return "$0"
    if usd < 0.001:
        return "<$0.001"
    return f"${usd:.3f}"


def format_tokens(n: int) -> str:
    """Compact token count: '847 tok', '2.1k tok', '15.4k tok'."""
    if n < 1000:
        return f"{n} tok"
    return f"{n / 1000:.1f}k tok"
