"""xlii CLI entrypoint: argument parsing + dispatch.

Subcommand implementations live in xlii/cmds/ — each module owns its commands
and their argparse wiring via register(sub). Interactive REPL slash commands
live in xlii/commands.py + xlii/repl_cmds/. This file is intentionally thin:
build the root parser, let each command module register itself, dispatch.
"""

from __future__ import annotations

import argparse
import sys

from xlii import __version__


class _SuggestingParser(argparse.ArgumentParser):
    """Root parser that turns an unknown command into a did-you-mean hint.

    argparse's stock ``invalid choice`` error dumps all ~40 command names with no
    guidance and points nowhere. On a command typo we instead suggest the closest
    command(s) and point at ``xlii help`` / ``xlii <command> --help``. Every other
    parse error (missing arg, bad flag, a typo *inside* a valid subcommand) falls
    through to argparse's default behaviour unchanged.
    """

    def error(self, message: str):  # noqa: D102 — argparse hook
        import difflib
        import re

        m = re.search(r"argument command: invalid choice: '([^']+)'", message)
        if m:
            bad = m.group(1)
            choices: list[str] = []
            for act in self._actions:
                if isinstance(act, argparse._SubParsersAction):
                    choices = list(act.choices)
                    break
            near = difflib.get_close_matches(bad, choices, n=3, cutoff=0.5)
            head = f"xlii: '{bad}' is not a command."
            if len(near) == 1:
                head += f" Did you mean '{near[0]}'?"
            elif near:
                head += " Did you mean one of: " + ", ".join(f"'{c}'" for c in near) + "?"
            hint = "Run 'xlii help' for the full list, or 'xlii <command> --help' for one command."
            print(f"{head}\n{hint}", file=sys.stderr)
            self.exit(2)
        super().error(message)


def build_parser() -> argparse.ArgumentParser:
    """Assemble the full argument parser (no dispatch).

    Separated from main() so tooling — notably xlii/docgen.py — can introspect
    the complete subcommand/flag tree without running anything.
    """
    from xlii.cmds import (
        account, artifact, content, daemon, destroy, diag, email, fabric, jid, jobs, journal, make, mcp,
        notify, nodes, pair, pr,
        project, provision, remote, roles, serve, serve_web, sessions, sweep, vfs, wiki,
    )
    from xlii.cmds import map as map_cmd  # own line, append-only ("map_cmd" keeps the builtin clear)
    from xlii.cmds import skin as skin_cmd

    p = _SuggestingParser(
        prog="xlii", description="Personal AI substrate — Grok + xAI Collections."
    )
    from xlii import version_season_line

    _season = version_season_line()
    p.add_argument("--version", action="version",
                   version=f"xlii {__version__}" + (f" {_season}" if _season else ""))
    sub = p.add_subparsers(dest="command", required=True)

    # Order controls `xlii --help` subcommand grouping (now domain-grouped).
    for mod in (project, sessions, make, provision, account, artifact, content, email, vfs,
                map_cmd, remote, roles, serve, serve_web, skin_cmd, diag, mcp, daemon, pair, fabric,
                jid, jobs, nodes, notify, journal, wiki, sweep, pr, destroy):
        mod.register(sub)

    return p


def main() -> int:
    from xlii.tracelog import log_crash, log_exit, log_invocation, setup_tracelog

    setup_tracelog()
    log_invocation(sys.argv)
    args = build_parser().parse_args()
    try:
        code = args.func(args)
    except BaseException as exc:  # noqa: BLE001 — log then re-raise unchanged
        log_crash(exc)
        raise
    log_exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
