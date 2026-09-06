#!/usr/bin/env python
"""Doc-truth check (CI ratchet for the documentation reset; ROADMAP 6.1).

Two guarantees, so the docs can never quietly drift from the code:

  1. The generated reference regions in docs/GUIDE.md are fresh (`xlii.docgen --check`).
  2. Every command the docs *name in an inline-code span* exists:
       - `` `xlii <cmd>` ``  resolves to a real subcommand, and
       - `` `/<slash>` ``    resolves to a registered slash command (either REPL).

Only inline-code (single-backtick) spans are authoritative. Prose ("attach xlii
as memory") and fenced example output ("[RAIL 4/5 …]", "write_file/edit_file")
are illustrative, not command references, and would false-positive.

Run:  python scripts/check_docs.py   (exit 1 on any problem)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from xlii import docgen  # noqa: E402
from xlii.cli import build_parser  # noqa: E402
from xlii.commands import find_repl_command  # noqa: E402
from xlii.repl_cmds import register_all  # noqa: E402

# Slash tokens handled specially by the loop, not via the registry — the inline
# REPL's exit/quit, plus the TUI front-end's own exit/quit/clear.
# exit/quit/clear are REPL meta-commands; whoami/node are the daemon's XMPP
# identity commands (fabric) — valid slash tokens, not REPL-registry commands.
SPECIAL_SLASH = {"exit", "quit", "clear", "whoami", "node", "kill", "xsu"}

INLINE_CODE = re.compile(r"`([^`\n]+)`")
XLII_CMD = re.compile(r"^xlii\s+([a-z][a-z-]*)")
# A slash command reference is an inline span that *starts* with /word. The
# negative lookahead rejects URL/version paths (`/v1/models`, `/api/users`):
# a slash command's word is not immediately followed by a digit or another /.
SLASH_CMD = re.compile(r"^/([a-z][a-z-]*)(?![a-z0-9/])")


def _real_subcommands() -> set[str]:
    sub = next(a for a in build_parser()._actions
               if a.__class__.__name__ == "_SubParsersAction")
    return set(sub.choices)


# Prose docs whose inline-code `xlii <cmd>` / `/<slash>` references are validated.
CHECKED_DOCS = ["README.md", "docs/GUIDE.md", "docs/HOWTO.md"]


def _command_ref_errors(text: str, rel_path: str, subs: set[str]) -> list[str]:
    """Validate that every inline-code `xlii <cmd>` / `/<slash>` resolves."""
    errors: list[str] = []
    seen: set[str] = set()
    for span in INLINE_CODE.findall(text):
        s = span.strip()
        m = XLII_CMD.match(s)
        if m and (cmd := m.group(1)) not in seen:
            seen.add(cmd)
            if cmd not in subs:
                errors.append(f"{rel_path}: `xlii {cmd}` — not a real subcommand")
        sm = SLASH_CMD.match(s)
        if sm and (slash := sm.group(1)) not in seen:
            seen.add(slash)
            registered = (find_repl_command(f"/{slash}", "code")
                          or find_repl_command(f"/{slash}", "chat"))
            if not registered and slash not in SPECIAL_SLASH:
                errors.append(f"{rel_path}: `/{slash}` — not a registered slash command")
    return errors


def check_doc(rel_path: str) -> list[str]:
    return _command_ref_errors((ROOT / rel_path).read_text(), rel_path, _real_subcommands())


# A fenced example line that *starts* with `xlii <sub>` is an unambiguous command
# (paths/output never start with "xlii "), so we validate those too — help topics
# are example-heavy and inline-code alone misses `xlii bogus` shown in a ``` block.
FENCED_XLII = re.compile(r"^\s*xlii\s+([a-z][a-z-]*)")


def _fenced_xlii_errors(text: str, rel_path: str, subs: set[str]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    in_fence = False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            continue
        m = FENCED_XLII.match(ln)
        if m and (cmd := m.group(1)) not in seen:
            seen.add(cmd)
            if cmd not in subs:
                errors.append(f"{rel_path}: `xlii {cmd}` (in example) — not a real subcommand")
    return errors


def check_help_corpus() -> list[str]:
    """Every command named in docs/help/**/*.md must exist — the guardrail so help
    topics can't drift to commands the build doesn't have. Checks inline-code
    spans everywhere plus `xlii <sub>` lines inside fenced examples."""
    help_dir = ROOT / "docs" / "help"
    if not help_dir.is_dir():
        return []
    subs = _real_subcommands()
    errors: list[str] = []
    for md in sorted(help_dir.rglob("*.md")):
        rel = md.relative_to(ROOT).as_posix()
        text = md.read_text()
        errors += _command_ref_errors(text, rel, subs)
        errors += _fenced_xlii_errors(text, rel, subs)
    return errors


# --------------------------------------------------------------------------- #
#  Coverage ratchet: "no fake commands" → "no undocumented commands"
# --------------------------------------------------------------------------- #
# Every top-level subcommand and every registered slash command must be named in
# at least one *hand-written* doc or help-corpus page. Generated regions are
# stripped before scanning — an auto-generated table row mirrors the code, it is
# not documentation. A command still genuinely undocumented is either written up
# or (temporarily) grandfathered on the allowlist below; the ratchet flips fully
# hard once the allowlist drains to empty (see the plan's Decision log).

# Broader than CHECKED_DOCS on purpose: REFERENCE is the deep command catalog, so
# it counts as coverage even though its prose is not (yet) fake-ref validated.
COVERAGE_DOCS = ["README.md", "docs/GUIDE.md", "docs/HOWTO.md", "docs/REFERENCE.md"]

# Grandfathered gaps: names known-undocumented when the ratchet landed. Shrink to
# empty as prose lands — a stale entry (now covered, or no longer a command) is
# reported, so the list can only move toward zero. Currently empty: the 2026-07
# sweep documented every command, so the ratchet ships fully hard.
COVERAGE_ALLOWLIST_SUBS: set[str] = set()
COVERAGE_ALLOWLIST_SLASH: set[str] = set()

_GEN_REGION = re.compile(
    r"<!-- BEGIN GENERATED: .*? -->.*?<!-- END GENERATED: .*? -->", re.DOTALL)


def _strip_generated(text: str) -> str:
    """Drop generated regions — a generated table row is not documentation."""
    return _GEN_REGION.sub("", text)


def _referenced_commands(text: str) -> tuple[set[str], set[str]]:
    """Collect (subcommands, slash names) a doc references.

    Same authoritative-reference rule as the no-fake checker: inline-code
    `xlii <sub>` / `/slash` anywhere, plus `xlii <sub>` lines inside fenced
    examples — proof a command is *mentioned* somewhere a human reads."""
    subs: set[str] = set()
    slashes: set[str] = set()
    for span in INLINE_CODE.findall(text):
        s = span.strip()
        if m := XLII_CMD.match(s):
            subs.add(m.group(1))
        if sm := SLASH_CMD.match(s):
            slashes.add(sm.group(1))
    in_fence = False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence and (m := FENCED_XLII.match(ln)):
            subs.add(m.group(1))
    return subs, slashes


def _all_slash_names() -> set[str]:
    """Every registered slash command's primary name (aliases are optional)."""
    from xlii.commands import iter_repl_commands
    return {c.name for c in iter_repl_commands()}


def _coverage_errors(ref_subs: set[str], ref_slash: set[str],
                     all_subs: set[str], all_slash: set[str]) -> list[str]:
    """Pure coverage diff (testable): undocumented commands + stale allowlist."""
    errors: list[str] = []
    for name in sorted(all_subs - ref_subs - COVERAGE_ALLOWLIST_SUBS):
        errors.append(f"coverage: `xlii {name}` is documented nowhere — add prose to "
                      "a README/GUIDE/HOWTO/REFERENCE or a help page (or allowlist it)")
    for name in sorted(all_slash - ref_slash - COVERAGE_ALLOWLIST_SLASH):
        errors.append(f"coverage: `/{name}` is documented nowhere — add prose to a "
                      "README/GUIDE/HOWTO/REFERENCE or a help page (or allowlist it)")
    # Keep the allowlist honest so it can only shrink.
    for name in sorted(COVERAGE_ALLOWLIST_SUBS):
        if name in ref_subs:
            errors.append(f"coverage: `xlii {name}` is allowlisted but now documented "
                          "— drop it from COVERAGE_ALLOWLIST_SUBS")
        elif name not in all_subs:
            errors.append(f"coverage: `xlii {name}` is allowlisted but no longer a "
                          "subcommand — drop it from COVERAGE_ALLOWLIST_SUBS")
    for name in sorted(COVERAGE_ALLOWLIST_SLASH):
        if name in ref_slash:
            errors.append(f"coverage: `/{name}` is allowlisted but now documented "
                          "— drop it from COVERAGE_ALLOWLIST_SLASH")
        elif name not in all_slash:
            errors.append(f"coverage: `/{name}` is allowlisted but no longer a slash "
                          "command — drop it from COVERAGE_ALLOWLIST_SLASH")
    return errors


def check_command_coverage() -> list[str]:
    """Every subcommand + slash command must be named in some hand-written doc."""
    ref_subs: set[str] = set()
    ref_slash: set[str] = set()
    for rel in COVERAGE_DOCS:
        s, sl = _referenced_commands(_strip_generated((ROOT / rel).read_text()))
        ref_subs |= s
        ref_slash |= sl
    help_dir = ROOT / "docs" / "help"
    if help_dir.is_dir():
        for md in help_dir.rglob("*.md"):
            s, sl = _referenced_commands(md.read_text())
            ref_subs |= s
            ref_slash |= sl
    return _coverage_errors(ref_subs, ref_slash, _real_subcommands(), _all_slash_names())


def check_help_menu() -> list[str]:
    """Every howto topic (except index) must declare a short Help-bar label.

    The bar is 36 cells / 280px. Long titles (em-dashes, "covers X") overflow
    the TUI and scroll the Face. A new shard without ``menu:`` / ``group:``
    fails here so docgen/bundle cannot ship a blown dropdown.
    """
    from xlii.help_corpus import HELP_MENU_GROUPS, HELP_MENU_MAX, load_manifest

    try:
        manifest = load_manifest(bundled=False)
    except Exception as exc:
        return [f"help menu: cannot load docs/help/manifest.yaml ({exc})"]
    errors: list[str] = []
    allowed = set(HELP_MENU_GROUPS)
    for tid, topic in manifest.topics.items():
        if tid == "index":
            continue
        menu = (topic.menu or "").strip()
        group = (topic.group or "").strip()
        if not menu:
            errors.append(
                f"help menu: `{tid}` missing menu: (short Help-bar label, "
                f"≤{HELP_MENU_MAX} chars)"
            )
        elif len(menu) > HELP_MENU_MAX:
            errors.append(
                f"help menu: `{tid}` menu: {menu!r} is {len(menu)} chars "
                f"(max {HELP_MENU_MAX})"
            )
        if group not in allowed:
            errors.append(
                f"help menu: `{tid}` group: must be one of "
                + "/".join(HELP_MENU_GROUPS)
            )
    return errors


def check_help_bundle() -> list[str]:
    """Ensure xlii/help/ matches docs/help/ core tier."""
    from bundle_help import check as help_bundle_check  # noqa: PLC0415

    stale = help_bundle_check()
    if stale:
        return ["stale help bundle: " + ", ".join(stale)
                + " — run `python scripts/bundle_help.py`"]
    return []


def main() -> int:
    register_all()
    problems: list[str] = []
    stale = docgen.check()
    if stale:
        problems.append(
            "stale generated regions in " + ", ".join(stale)
            + " — run `python -m xlii.docgen`")
    problems += check_help_bundle()
    problems += check_help_menu()
    problems += check_help_corpus()
    for rel_path in CHECKED_DOCS:
        problems += check_doc(rel_path)
    problems += check_command_coverage()

    if problems:
        for p in problems:
            print(f"✗ {p}")
        return 1
    print("✓ docs name only real commands; every command is documented; "
          "generated regions are fresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
