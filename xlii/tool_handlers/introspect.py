"""Registry introspection handlers — live command facts, on demand.

``/howto`` used to fence the WHOLE ``get_repl_help`` block into the system
prompt (17 KB of a 26 KB attachment, re-sent on every howto turn — the
time-to-first-token the operator felt as sluggishness). The attachment now
carries command NAMES only; this is the pull side of that trade: the model asks
for the one command it needs and gets usage/description/aliases from the same
live registry ``/describe`` reads, so the answer is accurate for THIS build
instead of recited from training data.
"""

from __future__ import annotations

from typing import Any, Optional

from xlii.tool_context import ToolContext, ToolResult


def _lookup(token: str):
    """The command claiming *token* as a name or alias, any REPL surface.

    Deliberately surface-blind (unlike ``find_repl_command``): the model is
    asking "what is /x", not "may I run /x here" — and the answer names the
    surfaces, so a chat-only command still gets a real reply instead of a miss.
    Names win over aliases so ``/get`` resolves to /get, not to something
    aliasing it.
    """
    from xlii.commands import iter_repl_commands

    key = token.strip().lstrip("/").strip().lower()
    if not key:
        return None
    alias_hit = None
    for cmd in iter_repl_commands():
        if cmd.name == key:
            return cmd
        if alias_hit is None and key in cmd.aliases:
            alias_hit = cmd
    return alias_hit


def _suggestion(token: str) -> Optional[str]:
    import difflib

    from xlii.commands import iter_repl_commands

    key = token.strip().lstrip("/").strip().lower()
    names = sorted({cmd.name for cmd in iter_repl_commands()})
    hit = difflib.get_close_matches(key, names, n=1, cutoff=0.6)
    return hit[0] if hit else None


def t_command_help(ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
    """One slash command's live registry facts (howto-fast T1).

    Plain text, not JSON: this lands in a howto answer, and the model quotes it
    back. A miss is an explicit refusal with the nearest name — never an empty
    result the model could paper over with a guess."""
    raw = args.get("name")
    if not isinstance(raw, str) or not raw.strip():
        return ToolResult(
            "command_help needs `name` — a slash command name without the "
            "slash, e.g. \"plan\"",
            is_error=True,
        )
    cmd = _lookup(raw)
    if cmd is None:
        hint = _suggestion(raw)
        tail = f" — did you mean /{hint}?" if hint else ""
        return ToolResult(
            f"no slash command named {raw.strip()!r} in this build{tail} "
            "(the howto guide's command index lists every name; do not invent one)",
            is_error=True,
        )

    lines = [f"/{cmd.name} — {cmd.description or '(no description registered)'}"]
    lines.append(f"usage: {cmd.usage or f'/{cmd.name}'}")
    if cmd.aliases:
        lines.append("aliases: " + ", ".join(f"/{a}" for a in cmd.aliases))
    lines.append(f"available in: {', '.join(sorted(cmd.repls))} REPL")
    lines.append(f"category: {cmd.category}")
    if cmd.role:
        lines.append(f"role-scoped: address it /{cmd.role}:{cmd.name} while that role is active")
    if cmd.capability:
        lines.append(f"gated: needs the {cmd.capability!r} capability (/admin unlock)")
    if cmd.source != "builtin":
        lines.append(f"source: {cmd.source}")
    lines.append(f"deeper: /describe {cmd.name} fuses this with the expansive GitHub doc")
    return ToolResult("\n".join(lines))
