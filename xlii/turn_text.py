"""Plain-text turn summaries — the pure (rich-free) half of tui/blocks.py,
split out in godzilla-mothra V1ab: ``tool_summary``'s 1-3 line tool-result
gists and ``turn_footer``'s compact turn-cost line are *data*, the strings the
W2 wire protocol and any body can serialize without a terminal. The rich
``*_block`` renderers stay face-side in ``xlii.tui.blocks`` and import these.

Imports only xlii.cost (a leaf) — never rich, textual, or xlii.tui, so any
kernel module can sit on top of it without a cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from xlii.cost import format_cost, format_tokens

if TYPE_CHECKING:
    from xlii.agent_stats import TurnStats

PREVIEW_LINE_LIMIT = 120  # max chars per tool-preview line


def _strip_line_number_prefix(line: str) -> str:
    """t_read_file emits '     1\\tcontent'. Trim the prefix for previews."""
    if "\t" in line:
        head, _, rest = line.partition("\t")
        if head.strip().isdigit():
            return rest
    return line


def _trunc(s: str, n: int = PREVIEW_LINE_LIMIT) -> str:
    s = s.rstrip()
    return s if len(s) <= n else s[: n - 1] + "…"


def tool_summary(name: str, content: str, is_error: bool) -> list[str]:
    """The gist of a tool result as 1-3 plain lines (no markup, no gutter).

    Single source for the agent's `⎿` previews AND the styled ToolBlock body —
    each tool's shape is tailored so the user sees head/tail/count without the
    full output flooding the terminal. The full content still goes to the model;
    this is purely visual. Renderers add their own gutter to the first line.
    """
    if is_error:
        first = (content or "").split("\n", 1)[0]
        return [_trunc(first)] if first else []

    text = (content or "").rstrip()
    if not text:
        return []
    lines = text.splitlines()
    if not lines:
        return []

    if name == "read_file":
        n = len(lines)
        first = _strip_line_number_prefix(lines[0])
        return [f"{n} line{'s' if n != 1 else ''} · {_trunc(first, 80)}"]

    if name == "list_dir":
        if text == "(empty)":
            return ["(empty)"]
        sample = "  ".join(lines[:5])
        return [f"{len(lines)} entries · {_trunc(sample)}"]

    if name == "glob":
        if text == "(no matches)":
            return ["no matches"]
        sample = ", ".join(lines[:3])
        return [f"{len(lines)} match{'es' if len(lines) != 1 else ''} · {_trunc(sample)}"]

    if name == "grep":
        if text == "(no matches)":
            return ["no matches"]
        out = [f"{len(lines)} match{'es' if len(lines) != 1 else ''}"]
        out += [_trunc(ln) for ln in lines[:2]]
        return out

    if name == "bash":
        # Drop the synthetic "--- exit N ---" trailer; show last 1-3 lines of
        # real output. Tests / build commands put the verdict at the end.
        meaningful = [ln for ln in lines if not ln.startswith("--- exit ")]
        if not meaningful:
            return [_trunc(lines[-1])]
        if len(meaningful) <= 3:
            return [_trunc(ln) for ln in meaningful]
        return [f"… {_trunc(meaningful[-3])}"] + [_trunc(ln) for ln in meaningful[-2:]]

    if name == "search_project":
        for ln in lines:
            if ln.startswith("[1]"):
                return [_trunc(ln)]
        return []

    if name in ("web_search", "x_search", "xai_docs"):
        for ln in lines:
            s = ln.strip()
            if s and not s.startswith("---"):
                return [_trunc(s)]
        return []

    if name == "code_execute":
        if len(lines) <= 2:
            return [_trunc(ln) for ln in lines if ln.strip()]
        return [_trunc(lines[0]), f"… {_trunc(lines[-1])}"]

    if name == "dispatch_subagent":
        for ln in lines:
            if ln.startswith("---"):  # skip the worker[...] header
                continue
            s = ln.strip()
            if s:
                return [_trunc(s)]
        return []

    # write_file / edit_file self-narrate ("wrote foo.py (123 bytes)") — no preview.
    return []


def turn_footer(ts: "TurnStats") -> str:
    """Compact, scannable turn summary (Rich markup string). Moved verbatim from
    ui.format_turn_line via tui.blocks — xlii.ui re-exports this as
    ``format_turn_line`` so call sites don't change."""
    parts: list[str] = [
        ts.orch.model,
        f"{ts.orch.iterations} iter",
        f"{ts.tool_calls} tools",
    ]
    orch_part = f"orch {format_tokens(ts.orch.total_tokens)}"
    if ts.orch.cost_usd is not None:
        orch_part += f" ({format_cost(ts.orch.cost_usd)})"
    parts.append(orch_part)

    if ts.workers_dispatched > 0:
        w = f"{ts.workers_dispatched} workers {format_tokens(ts.workers.total_tokens)}"
        if ts.workers.cost_usd is not None:
            w += f" ({format_cost(ts.workers.cost_usd)})"
        parts.append(w)

    judges = getattr(ts, "judges", None)
    if judges is not None and (getattr(judges, "total_tokens", 0) or getattr(judges, "cost_usd", None)):
        j = f"judges {format_tokens(judges.total_tokens)}"
        if judges.cost_usd is not None:
            j += f" ({format_cost(judges.cost_usd)})"
        parts.append(j)

    if ts.total_cost is not None and (
        ts.workers_dispatched > 0 or (judges is not None and judges.cost_usd)
    ):
        parts.append(f"total {format_cost(ts.total_cost)}")

    return "[dim]" + " · ".join(parts) + "[/dim]"
