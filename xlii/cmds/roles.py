"""`xlii role` — list/show role descriptors (proposals/roles.md R1).

Headless catalog management; activation is a session act (R2 / `xlii chat --id`).
Thin wrapper over `xlii.role` — resolves the cwd project so project-local roles
(`.xlii/roles/`) are included alongside global ones.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from xlii.config import ProjectConfig
from xlii.role import format_role_summary, load_role, load_roles


def _resolve_root() -> Optional[Path]:
    proj = ProjectConfig.load(Path.cwd())
    return proj.project_root if proj else None


def cmd_role_list(args: argparse.Namespace) -> int:
    roles = load_roles(_resolve_root())
    if not roles:
        print("(no roles — add a descriptor to .xlii/roles/ or ~/.config/xlii/roles/)")
        return 0
    for name in sorted(roles):
        r = roles[name]
        scope = "" if r.scope == "global" else " (project)"
        warn = "  [malformed]" if r.validate() else ""
        print(f"{name}{scope}: {r.description()}{warn}")
    return 0


def cmd_role_show(args: argparse.Namespace) -> int:
    role = load_role(args.name, _resolve_root())
    if role is None:
        print(f"role: no such role: {args.name}", file=sys.stderr)
        return 1
    for ln in format_role_summary(role):
        print(ln)
    return 0


def register(sub) -> None:
    p_role = sub.add_parser(
        "role", help="List/show role descriptors (named specialist bundles)"
    )
    role_sub = p_role.add_subparsers(dest="role_command", required=True)

    p_list = role_sub.add_parser("list", help="List available roles")
    p_list.set_defaults(func=cmd_role_list)

    p_show = role_sub.add_parser("show", help="Show a role's loadout + identity")
    p_show.add_argument("name", help="Role name")
    p_show.set_defaults(func=cmd_role_show)
