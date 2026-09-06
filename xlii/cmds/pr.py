"""``xlii pr sweep`` / ``xlii pr watch`` — PR events become inbox goals (pr-watch)."""

from __future__ import annotations

import argparse
import sys


def _project(args: argparse.Namespace):
    from xlii.cmds.sessions.resolve import _resolve_project_target
    from xlii.config import ProjectConfig

    target = _resolve_project_target(getattr(args, "workspace", None))
    if target is None:
        print("pr: could not resolve workspace", file=sys.stderr)
        return None
    project = ProjectConfig.load(target.resolve())
    if not project:
        print(f"pr: not an xlii project: {target}", file=sys.stderr)
        return None
    return project


def cmd_pr_sweep(args: argparse.Namespace) -> int:
    from xlii.pr_watch import sweep

    project = _project(args)
    if project is None:
        return 1
    result = sweep(
        cwd=project.project_root,
        xli_dir=project.xli_dir,
        pr=getattr(args, "pr", None),
        marker=getattr(args, "marker", "@xlii"),
        all_comments=bool(getattr(args, "all_comments", False)),
    )
    if result.error:
        print(f"pr sweep: {result.error}", file=sys.stderr)
        return 1
    if result.message:
        print(result.message)
    for p in result.enqueued:
        print(f"  queued {p.name}")
    flushed = result.flush
    if flushed is not None:
        if flushed.error or flushed.skipped:
            print(f"pr sweep: flush {flushed.error or flushed.skipped}", file=sys.stderr)
        elif flushed.replied:
            print(f"pr sweep: replied {len(flushed.replied)}")
    return 0


def cmd_pr_watch(args: argparse.Namespace) -> int:
    from xlii.pr_watch import watch

    project = _project(args)
    if project is None:
        return 1
    drain = bool(getattr(args, "drain", True))
    return watch(
        cwd=project.project_root,
        xli_dir=project.xli_dir,
        pr=getattr(args, "pr", None),
        marker=getattr(args, "marker", "@xlii"),
        all_comments=bool(getattr(args, "all_comments", False)),
        interval=int(getattr(args, "interval", 45) or 45),
        drain=drain,
    )


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "pr", nargs="?", default=None,
        help="PR number, URL, or omitted for the current branch's open PR.",
    )
    p.add_argument(
        "--marker", default="@xlii",
        help="Comment summon string (default: @xlii). Not a GitHub account.",
    )
    p.add_argument(
        "--all-comments", action="store_true",
        help="Treat every conversation/review comment as actionable (not just --marker).",
    )
    p.add_argument(
        "--workspace", metavar="PATH",
        help="Project directory (default: cwd).",
    )


def register(sub) -> None:
    p = sub.add_parser(
        "pr",
        help="Turn GitHub PR events into inbox goals (poll via gh; drain with xlii loop).",
    )
    actions = p.add_subparsers(dest="pr_action", required=True)

    s = actions.add_parser(
        "sweep",
        help="One poll pass: new PR events → .xlii/inbox files (never drains).",
    )
    _add_common(s)
    s.set_defaults(func=cmd_pr_sweep)

    w = actions.add_parser(
        "watch",
        help="Foreground loop over sweep. Drains by default after a pass that queued work.",
    )
    _add_common(w)
    w.add_argument(
        "--interval", type=int, default=45, metavar="SECS",
        help="Poll interval (default 45; floor 30).",
    )
    w.add_argument(
        "--drain", dest="drain", action="store_true", default=True,
        help="After a pass that enqueued, run `xlii loop --drain-inbox` as a subprocess (default).",
    )
    w.add_argument(
        "--no-drain", dest="drain", action="store_false",
        help="Queue only — never spawn the drain subprocess.",
    )
    w.set_defaults(func=cmd_pr_watch)
