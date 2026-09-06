"""Provisioning + account subcommands: config, setup, bootstrap, keys, models, auth.

Moved out of the former monolithic cli.py (see proposals/done/cli-refactor.md).
"""

from __future__ import annotations

# Re-export kept for import-path stability; the implementation moved to the
# kernel with the B6 provision consolidation (godzilla-mothra).
from xlii.bootstrap import select_prune_candidates as _select_prune_candidates

from .auth import cmd_auth
from .bootstrap import cmd_bootstrap
from .keys import cmd_keys
from .migrate import cmd_keys_migrate
from .models import cmd_models
from .register import register
from .setup import cmd_setup
from .shell import cmd_config, cmd_shell_init

__all__ = [
    "_select_prune_candidates",
    "cmd_auth",
    "cmd_bootstrap",
    "cmd_config",
    "cmd_keys",
    "cmd_keys_migrate",
    "cmd_models",
    "cmd_setup",
    "cmd_shell_init",
    "register",
]
