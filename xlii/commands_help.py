"""Help text generation for the REPL slash-command registry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from xlii.commands import REPLCommand

_HELP_PAD = 22  # usage column width before the description

# Progressive disclosure tiers (grades plan Phase 1). Names are primary command
# names only — aliases never appear in /help anyway.
HELP_TIERS = ("daily", "compose", "power", "all")

# Daily: one-screen survival kit (shell section added separately).
_DAILY_NAMES = frozenset({
    "help",
    "status",
    "plan",
    "execute",
    "cancel",
    "attach",
    "detach",
    "attachments",
    "persona",
    "howto",
    "describe",
    "code",
    "chat",
    "yolo",
    "safe",
    "model",
    "project",
})

# Compose: curation / knowledge packing (includes daily).
_COMPOSE_NAMES = _DAILY_NAMES | frozenset({
    "plugin",
    "get",
    "skill",
    "loadout",
    "locker",
    "workspace",
    "wiki",
    "journal",
    "mark",
    "bookmarks",
    "recall",
    "role",
    "context",
    "clear-attachments",
    "edit",
    "forget",
    "upload",
})

# Power: gates, automation, admin (includes daily; compose names also allowed so
# /help power is useful alone without forcing /help compose first).
_POWER_EXTRA = frozenset({
    "rail",
    "loop",
    "swarm",
    "debug",
    "discovery",
    "ops",
    "peer",
    "verify",
    "consult",
    "delegate",
    "cursor",
    "claude",
    "grok-build",
    "admin",
    "budget",
    "account",
    "cost",
    "jobs",
    "tasks",
    "map",
    "diff",
    "checkpoint",
    "rewind",
    "compact",
    "iterations",
    "hook-control",
    "inspect",
    "tools",
    "tools-reload",
    "sync",
    "reset",
    "image",
    "imagine",
    "tui",
    "scratch",
    "terminal",
    "interactive",
    "browse",
    "git",
    "remote",
    "panel",
    "file-view",
    "file-tab",
    "send",
    "replay",
    "approve",
    "os",
    "nfo",
    "sh",
    "mojo",
    "temp",
    "cwd",
    "theme",
    "reload",
    "commands",
})

_POWER_NAMES = _DAILY_NAMES | _COMPOSE_NAMES | _POWER_EXTRA

# Categories that always count as "power" when tier=power (belt + suspenders).
_POWER_CATEGORIES = frozenset({"mode", "admin"})


def normalize_help_tier(raw: Optional[str]) -> str:
    """Map user token → daily|compose|power|all. Empty/None → daily."""
    if raw is None or not str(raw).strip():
        return "daily"
    t = str(raw).strip().lower()
    if t in ("daily", "day", "d"):
        return "daily"
    if t in ("compose", "comp", "c"):
        return "compose"
    if t in ("power", "p"):
        return "power"
    if t in ("all", "full", "a", "*"):
        return "all"
    return ""  # unknown — caller prints usage


def command_in_help_tier(cmd: "REPLCommand", tier: str) -> bool:
    """Whether *cmd* appears in progressive help *tier* (not including shell rows)."""
    tier = tier or "all"
    if tier == "all":
        return True
    name = cmd.name
    if tier == "daily":
        return name in _DAILY_NAMES
    if tier == "compose":
        return name in _COMPOSE_NAMES
    if tier == "power":
        if name in _POWER_NAMES:
            return True
        if cmd.category in _POWER_CATEGORIES:
            return True
        # Project-local power tools still show under power when tier is power.
        return cmd.source == "project"
    return True


def _help_row(usage: str, desc: str) -> list[str]:
    """One or two plain-text lines for a command in the help listing.

    Short usages align in a fixed column; long ones get the description on a
    following indented line so nothing runs off into a ragged single row.
    """
    usage = usage or ""
    desc = desc or ""
    if len(usage) <= _HELP_PAD:
        return [f"  {usage:<{_HELP_PAD}} {desc}"]
    rows = [f"  {usage}"]
    if desc:
        rows.append(f"  {'':<{_HELP_PAD}} {desc}")
    return rows


def _help_sections(
    repl: str = "code",
    include_shell: bool = True,
    tier: str = "all",
) -> list[tuple[str, list[tuple[str, str]]]]:
    """Ordered ``(section-title, [(usage, description), ...])`` for one REPL.

    The single structured builder behind both the plain-text help
    (:func:`get_repl_help`, for docs / tests / non-tty) and the colored
    interactive help (:func:`render_repl_help`). One builder means the two
    presentations can never drift in which commands appear or how they group.

    *tier*: ``daily`` | ``compose`` | ``power`` | ``all`` (default ``all`` so
    docs/tests that expect the full surface keep working).
    """
    from collections import defaultdict

    from xlii.commands import iter_repl_commands

    tier = tier or "all"
    groups: dict[str, list] = defaultdict(list)
    for cmd in iter_repl_commands():
        if repl not in cmd.repls:
            continue
        if not command_in_help_tier(cmd, tier):
            continue
        groups[cmd.category].append(cmd)

    def _rows(cmds: list) -> list[tuple[str, str]]:
        return [
            (cmd.usage or f"/{cmd.name}", cmd.description or "")
            for cmd in sorted(cmds, key=lambda c: c.name)
        ]

    sections: list[tuple[str, list[tuple[str, str]]]] = []
    order = ["session", "mode", "knowledge", "admin", "general", "project"]

    # Normal categories (project-sourced commands get their own section below).
    for cat in order:
        if cat not in groups:
            continue
        normal = [c for c in groups[cat] if c.source != "project"]
        if not normal:
            continue
        sections.append((cat.upper(), _rows(normal)))

    # Dedicated PROJECT COMMANDS section (much nicer UX).
    project_cmds = [
        c
        for c in iter_repl_commands()
        if c.source == "project" and repl in c.repls and command_in_help_tier(c, tier)
    ]
    if project_cmds:
        sections.append(("PROJECT COMMANDS", _rows(project_cmds)))

    if include_shell:
        if repl == "code":
            shell_rows = [
                ("<command>", "Run as a live shell command in the tracked cwd (cd moves it)"),
                ("!<command>", "Force a shell command at the project root (ungated)"),
                ("?<text>", "Send to the AI / agent"),
            ]
        else:
            shell_rows = [
                ("!<command>", "Run a local shell command (no model turn)"),
                ("?<text>", "Send to the AI / agent (alias for bare input)"),
            ]
        sections.append(("SHELL", shell_rows))

    return sections


def get_repl_help(
    repl: str = "code",
    include_shell: bool = True,
    tier: str = "all",
) -> str:
    """Generate grouped slash-command help from the registry (plain text).

    This is the single source of truth for /help — there are no hand-maintained
    help strings to drift out of sync (every command's usage/description comes
    from its REPLCommand registration). The interactive REPL renders the same
    sections in color via :func:`render_repl_help`; this string form feeds docs,
    tests, and any non-terminal output.

    Default *tier* is ``all`` (full registry) so doc generators and broad tests
    stay complete; the interactive ``/help`` command passes ``daily`` by default.
    """
    lines: list[str] = []
    for title, rows in _help_sections(repl, include_shell, tier=tier):
        lines.append(f"\n{title}")
        for usage, desc in rows:
            lines.extend(_help_row(usage, desc))
    return "\n".join(lines).strip()


def render_repl_help(
    repl: str = "code",
    include_shell: bool = True,
    tier: str = "all",
):
    """Rich renderable for /help: the same sections as :func:`get_repl_help`,
    but colored and with descriptions that wrap cleanly in a narrow terminal.

    The layout mirrors the plain-text help's adaptive behavior so nothing
    regresses on long usages:

    * short usages (<= _HELP_PAD) share an aligned two-column table — the cyan
      usage on the left, the description on the right, wrapping *within its own
      column* (so a wrapped line stays indented under the description instead of
      restarting at column 0 against the next command);
    * long usages get their own colored line with the description wrapped on the
      following indented line(s) — the same shape the flat text already used,
      just colored.

    The cyan usage column makes each command pop out from its grey description.
    :func:`get_repl_help` stays the plain-text source of truth for docs / tests /
    non-tty surfaces.
    """
    from rich.console import Group
    from rich.padding import Padding
    from rich.table import Table
    from rich.text import Text

    from xlii.theme import THEME

    usage_style = f"bold {THEME.shell}"        # cyan — matches /describe + shell blocks
    header_style = f"bold {THEME.meta_color}"  # magenta — the slash / meta color

    indent = 2                       # rows sit two columns in from the header
    desc_col = indent + _HELP_PAD + 1  # where short-row descriptions begin

    blocks: list[Any] = []
    for title, rows in _help_sections(repl, include_shell, tier=tier):
        if blocks:
            blocks.append(Text(""))  # blank line between sections
        blocks.append(Text(title, style=header_style))

        # Buffer runs of consecutive short rows into one aligned table so the
        # usage columns line up; flush it whenever a long usage interrupts the
        # run (this preserves the registry's alphabetical ordering).
        short_buf: list[tuple[str, str]] = []

        def _flush() -> None:
            if not short_buf:
                return
            table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 1, 0, 0))
            table.add_column(width=_HELP_PAD, no_wrap=True)
            table.add_column(overflow="fold")
            for u, d in short_buf:
                # Style the Text (not the column) so the inter-column gap stays
                # unstyled, and wrap in Text so literal brackets in usages like
                # "/rail [next|back]" render verbatim, not as Rich markup tags.
                table.add_row(Text(u, style=usage_style), Text(d))
            blocks.append(Padding(table, (0, 0, 0, indent)))
            short_buf.clear()

        for usage, desc in rows:
            if len(usage) <= _HELP_PAD:
                short_buf.append((usage, desc))
                continue
            _flush()
            blocks.append(Padding(Text(usage, style=usage_style), (0, 0, 0, indent)))
            if desc:
                blocks.append(Padding(Text(desc), (0, 0, 0, desc_col)))
        _flush()

    return Group(*blocks)
