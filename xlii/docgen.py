"""Generate the command + slash-command reference from the live code.

Single source of truth for the docs' reference tables: the argparse tree
(`xlii.cli.build_parser`) and the slash-command registry
(`xlii.commands.get_repl_help`). Hand-maintained tables drift; these don't.

Usage:
    python -m xlii.docgen          # rewrite the GENERATED regions in the docs
    python -m xlii.docgen --check  # exit 1 if any region is stale (for CI)

Generated regions are delimited in the target files by:
    <!-- BEGIN GENERATED: <id> -->
    …
    <!-- END GENERATED: <id> -->
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------- #
#  argparse introspection
# --------------------------------------------------------------------------- #

def _iter_leaf_commands(parser: argparse.ArgumentParser, prog: str, help_text: str = ""):
    """Yield (full_command, help, leaf_parser) for every dispatchable command.

    Recurses through nested subparsers (e.g. `models set`, `keys revoke`,
    `auth list`, `mcp deep-contexts`); a leaf is a parser with no subparsers.

    A parser can be BOTH runnable and have sub-actions — `xlii daemon` runs the
    daemon while `xlii daemon trust` pins a device — so a parent that sets its own
    `func` default is emitted too, not just its leaves.
    """
    subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    if not subs:
        yield prog, help_text, parser
        return
    if parser.get_default("func") is not None:
        yield prog, help_text, parser
    sa = subs[0]
    helps = {pa.dest: (pa.help or "") for pa in sa._choices_actions}
    seen: set[int] = set()
    for name, sub in sa.choices.items():
        # argparse aliases map extra names onto the SAME subparser object (e.g.
        # `remote` + its hidden legacy alias `ftp`) — emit each parser once,
        # under its first (canonical) name, or every alias becomes a duplicate
        # command family in the generated reference.
        if id(sub) in seen:
            continue
        seen.add(id(sub))
        yield from _iter_leaf_commands(sub, f"{prog} {name}", helps.get(name, ""))


def _positional_token(action) -> str:
    """Render a positional as `<name>` (required) or `[name]` (optional/repeated)."""
    label = (action.metavar or action.dest)
    if isinstance(label, tuple):
        label = label[0]
    if action.nargs in ("?", "*"):
        return f"[{label.lower()}]"
    if action.nargs == "+":
        return f"<{label.lower()}...>"
    return f"<{label.lower()}>"


def _flags_and_positionals(parser: argparse.ArgumentParser):
    flags: list[str] = []
    positionals: list[str] = []
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            continue
        if a.option_strings:
            if "-h" in a.option_strings or "--help" in a.option_strings:
                continue
            if a.help is argparse.SUPPRESS:
                continue
            long = next((o for o in a.option_strings if o.startswith("--")),
                        a.option_strings[0])
            flags.append(long)
        elif a.dest != "help":
            positionals.append(_positional_token(a))
    return flags, positionals


def render_subcommands() -> str:
    """A `| Command | Flags | What it does |` table over every dispatchable command."""
    from xlii.cli import build_parser

    rows = ["| Command | Flags | What it does |", "| --- | --- | --- |"]
    for prog, help_text, leaf in _iter_leaf_commands(build_parser(), "xlii"):
        flags, positionals = _flags_and_positionals(leaf)
        cmd = " ".join([prog, *positionals])
        flag_cell = " ".join(f"`{f}`" for f in flags) if flags else "—"
        rows.append(f"| `{cmd}` | {flag_cell} | {help_text} |")
    return "\n".join(rows)


def _flag_spec(action) -> str:
    """Render one option as `--long <metavar>` (value flag) or `--long` (boolean).

    Prefers the long form (matching `_flags_and_positionals`); a value-taking flag
    shows its metavar / choices so the reference is usable without `--help`.
    """
    long = next((o for o in action.option_strings if o.startswith("--")),
                action.option_strings[0])
    if action.nargs == 0:                       # store_true / store_false / store_const
        return f"`{long}`"
    if action.choices:
        return f"`{long} {{{','.join(str(c) for c in action.choices)}}}`"
    metavar = action.metavar
    if isinstance(metavar, tuple):
        metavar = " ".join(metavar)
    metavar = metavar or action.dest.upper()
    return f"`{long} <{str(metavar).lower()}>`"


def _flag_details(parser: argparse.ArgumentParser):
    """Yield (rendered_flag, help_text) for every real option on a leaf parser."""
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            continue
        if not a.option_strings:
            continue                            # positional — rendered separately
        if "-h" in a.option_strings or "--help" in a.option_strings:
            continue
        if a.help is argparse.SUPPRESS:
            continue
        yield _flag_spec(a), (a.help or "")


def render_cli_reference() -> str:
    """Flag-level reference: every leaf command, its help, and each flag's help.

    The `subcommands` table (GUIDE) is the compact map — command + bare flag
    *names*. This region is the deep end: every flag rendered with its metavar
    and its argparse help string, grouped by top-level command. The parser is the
    single source of truth, so a new flag documents itself on the next regen.
    """
    from xlii.cli import build_parser

    lines: list[str] = []
    current_top = None
    for prog, help_text, leaf in _iter_leaf_commands(build_parser(), "xlii"):
        parts = prog.split()
        top = parts[1] if len(parts) > 1 else prog
        if top != current_top:
            current_top = top
            lines.append(f"\n#### `xlii {top}`\n")
        _, positionals = _flags_and_positionals(leaf)
        sig = " ".join([prog, *positionals])
        # The deep reference prefers the parser's `description` (the paragraph
        # `xlii <cmd> --help` shows) when a command carries one; the compact GUIDE
        # table keeps the short `help=` one-liner as the map. Same source of truth,
        # so a `description=` added for depth documents itself here on the next regen.
        body = (getattr(leaf, "description", None) or "").strip() or help_text
        lines.append(f"- **`{sig}`** — {body}" if body else f"- **`{sig}`**")
        for flag, fhelp in _flag_details(leaf):
            lines.append(f"    - {flag} — {fhelp}" if fhelp else f"    - {flag}")
    return "\n".join(lines).strip()


# --------------------------------------------------------------------------- #
#  slash-command introspection
# --------------------------------------------------------------------------- #

def render_slash(repl: str) -> str:
    """Fenced block of the registry-generated slash help for one REPL."""
    from xlii.commands import get_repl_help
    from xlii.repl_cmds import register_all

    register_all()
    return "```\n" + get_repl_help(repl) + "\n```"


# --------------------------------------------------------------------------- #
#  region replacement
# --------------------------------------------------------------------------- #

# (region id, target file relative to repo root, renderer)
# The compact command map lives in docs/GUIDE.md; the flag-level deep reference
# lives in docs/REFERENCE.md. The README is the storefront and carries no
# generated regions, so editing it can never break this ratchet.
REGIONS = [
    ("subcommands", "docs/GUIDE.md", render_subcommands),
    ("slash-code", "docs/GUIDE.md", lambda: render_slash("code")),
    ("slash-chat", "docs/GUIDE.md", lambda: render_slash("chat")),
    ("cli-reference", "docs/REFERENCE.md", render_cli_reference),
]


def _replace_region(text: str, region_id: str, new_body: str) -> str:
    begin = f"<!-- BEGIN GENERATED: {region_id} -->"
    end = f"<!-- END GENERATED: {region_id} -->"
    i = text.find(begin)
    j = text.find(end)
    if i == -1 or j == -1:
        raise SystemExit(f"docgen: region markers for {region_id!r} not found in target file")
    if j < i:
        raise SystemExit(f"docgen: END before BEGIN for region {region_id!r}")
    return text[:i] + begin + "\n" + new_body + "\n" + text[j:]


def _render_file(rel_path: str) -> str:
    """Return the target file's content with all its regions refreshed."""
    path = REPO_ROOT / rel_path
    text = path.read_text()
    for region_id, target, render in REGIONS:
        if target != rel_path:
            continue
        text = _replace_region(text, region_id, render())
    return text


def write() -> list[str]:
    """Refresh every region in place. Returns the files that changed."""
    changed = []
    for rel_path in sorted({target for _, target, _ in REGIONS}):
        path = REPO_ROOT / rel_path
        new = _render_file(rel_path)
        if new != path.read_text():
            path.write_text(new)
            changed.append(rel_path)
    return changed


def check() -> list[str]:
    """Return the list of files whose regions are stale (empty = all fresh)."""
    stale = []
    for rel_path in sorted({target for _, target, _ in REGIONS}):
        path = REPO_ROOT / rel_path
        if _render_file(rel_path) != path.read_text():
            stale.append(rel_path)
    return stale


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m xlii.docgen", description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="Exit 1 if any generated region is stale (does not write).")
    args = ap.parse_args(argv)

    if args.check:
        stale = check()
        if stale:
            print("docgen: stale generated regions in: " + ", ".join(stale))
            print("        run `python -m xlii.docgen` to refresh.")
            return 1
        print("docgen: all generated regions up to date.")
        return 0

    changed = write()
    if changed:
        print("docgen: refreshed " + ", ".join(changed))
    else:
        print("docgen: nothing to do (already up to date).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
