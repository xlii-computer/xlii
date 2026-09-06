"""``xlii map`` — the repo map from the shell (doctor-shaped: offline, no key, no session).

The scriptable client of :func:`xlii.repo_map.build_map`, printing the exact bytes
the engine gives every other surface (agent tool ``map``, ``/map``, ``map://``).
This is the surface *other* agents consume — vector briefs for fanned-out workers
can embed ``xlii map xlii/tui`` output, and shell users get a pipeable shape dump.

PATH is a scope inside the cwd (``xlii map xlii/tui``); a path *outside* the cwd
(absolute, or climbing ``..``) is mapped as its own root instead, so
``xlii map /some/other/tree`` also works.
"""

from __future__ import annotations

import sys


def cmd_map(args) -> int:
    from pathlib import Path

    from xlii.repo_map import build_map

    root = Path.cwd()
    scope = (args.path or "").strip() or None
    kwargs = {"depth": args.depth, "detail": args.detail}
    try:
        try:
            text = build_map(root, scope=scope, **kwargs)
        except ValueError:
            # Outside the cwd: map that tree as its own root (dirs only —
            # a bogus path must still fail loudly below).
            target = Path(scope).expanduser() if scope else None
            if target is None or not target.is_dir():
                raise
            text = build_map(target.resolve(), **kwargs)
    except ValueError as e:
        print(f"map: {e}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


def register(sub) -> None:
    p = sub.add_parser(
        "map",
        help="Repo map: file tree + Python class/function signatures — orientation, "
             "offline, deterministic (same bytes as /map and the map tool).",
    )
    p.add_argument("path", nargs="?", default="",
                   help="Scope: a subtree inside the cwd (or a directory outside it, mapped as its own root)")
    p.add_argument("--depth", type=int, default=None,
                   help="Tree depth cap (1 = top level only; default full)")
    p.add_argument("--detail", choices=["files", "symbols"], default="symbols",
                   help="'files' = tree only; 'symbols' (default) adds class/function signatures")
    p.set_defaults(func=cmd_map)
