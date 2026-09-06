"""Project lifecycle subcommands: init, new, scratch, sync, status, projects, gc.

Moved out of the former monolithic cli.py (see proposals/done/cli-refactor.md).
"""

from __future__ import annotations

from xlii.cmds.project._guards import (
    _confirm_risky_init,
    _count_tracked_files,
    _sensitive_init_target,
)
from xlii.cmds.project.gc import cmd_gc
from xlii.cmds.project.init import cmd_init, cmd_new
from xlii.cmds.project.lifecycle import cmd_find, cmd_projects, cmd_status, cmd_sync
from xlii.cmds.project.register import register
from xlii.cmds.project.rm import _resolve_rm_target, _run_project_rm, cmd_project_rm
from xlii.cmds.project.scratch import cmd_scratch

__all__ = [
    "cmd_init",
    "_sensitive_init_target",
    "_count_tracked_files",
    "_confirm_risky_init",
    "_resolve_rm_target",
    "_run_project_rm",
    "cmd_new",
    "cmd_projects",
    "cmd_find",
    "cmd_sync",
    "cmd_status",
    "cmd_gc",
    "cmd_project_rm",
    "cmd_scratch",
    "register",
]
