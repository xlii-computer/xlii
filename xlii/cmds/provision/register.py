"""Argparse registration for provision subcommands."""

from __future__ import annotations

from xlii.bootstrap import DEFAULT_EXPIRE_DAYS

from .auth import cmd_auth
from .bootstrap import cmd_bootstrap
from .keys import cmd_keys
from .migrate import cmd_keys_migrate
from .models import cmd_models
from .setup import cmd_setup
from .shell import cmd_config, cmd_shell_init


def register(sub) -> None:
        p_config = sub.add_parser(
            "config",
            help="Create the config template at ~/.config/xlii/config.json (only if absent; never clobbers keys).",
            description=(
                "Create a config template at ~/.config/xlii/config.json, then edit it to "
                "paste your management_api_key and add chat API keys to the keys[] list. "
                "Safe to re-run: it writes the template only when no config exists — it "
                "never clobbers real keys — and otherwise just repairs the file's "
                "permissions. `xlii setup` does this and also provisions worker keys."
            ),
        )
        p_config.set_defaults(func=cmd_config)

        p_shell_init = sub.add_parser(
            "shell-init",
            help="Print a shell wrapper so `cd` inside `xlii code` follows you out on exit "
                 "(opt-in; add `eval \"$(xlii shell-init)\"` to your rc file).",
        )
        p_shell_init.add_argument(
            "shell", nargs="?", choices=["bash", "zsh", "fish"],
            help="Target shell syntax (default: detect from $SHELL).",
        )
        p_shell_init.set_defaults(func=cmd_shell_init)

        p_setup = sub.add_parser(
            "setup",
            help="One-shot first-time setup: writes config, checks env mgmt key, provisions primary + workers.",
        )
        p_setup.add_argument("--workers", type=int, default=8, help="Number of worker keys to create (default: 8)")
        p_setup.add_argument("--expire-days", type=int, default=DEFAULT_EXPIRE_DAYS,
                             help=f"Key expiration in days (default: {DEFAULT_EXPIRE_DAYS}; 0 = no expiry)")
        p_setup.add_argument("--force", action="store_true", help="Re-run bootstrap even if pool already populated")
        p_setup.add_argument(
            "--journal", default="",
            help="Mojo nickname for this body (limb: the throne's /name). "
                 "Default: $XLII_MOJO_NAME, else mojo. Daemon cannot /name.",
        )
        p_setup.set_defaults(func=cmd_setup)

        p_bootstrap = sub.add_parser(
            "bootstrap",
            help="Provision worker API keys via the management API (lower-level than `setup`).",
        )
        p_bootstrap.add_argument("--count", type=int, default=8, help="How many worker keys to create (default: 8)")
        p_bootstrap.add_argument("--prefix", default="worker", help="Label prefix for created keys (default: 'worker')")
        p_bootstrap.add_argument("--expire-days", type=int, default=DEFAULT_EXPIRE_DAYS,
                                 help=f"Key expiration in days (default: {DEFAULT_EXPIRE_DAYS}; 0 = no expiry)")
        p_bootstrap.add_argument("--force", action="store_true", help="Add new keys even if matching prefix already exists")
        p_bootstrap.add_argument("--revoke", action="store_true", help="Revoke (delete) all keys with matching prefix")
        p_bootstrap.add_argument("--yes", action="store_true", help="Skip confirmation when revoking")
        p_bootstrap.set_defaults(func=cmd_bootstrap)

        p_models = sub.add_parser(
            "models", help="Inspect or set orchestrator/worker/chat/help models."
        )
        p_models_sub = p_models.add_subparsers(dest="action", required=True)

        p_models_list = p_models_sub.add_parser("list", help="List models the team has access to.")
        p_models_list.set_defaults(func=cmd_models)

        p_models_rec = p_models_sub.add_parser("recommended", help="Show heuristic best-of-class picks.")
        p_models_rec.set_defaults(func=cmd_models)

        p_models_set = p_models_sub.add_parser(
            "set", help="Pin orchestrator, worker, chat, and/or help model(s)."
        )
        p_models_set.add_argument("--orchestrator", help="Model id for the main code agent")
        p_models_set.add_argument("--worker", help="Model id for dispatched workers")
        p_models_set.add_argument("--chat", help="Model id for persona / conversational chat")
        p_models_set.add_argument(
            "--help-model",
            dest="help_model",
            help="Model id for /howto (help role)",
        )
        p_models_set.set_defaults(func=cmd_models)

        p_models_profile = p_models_sub.add_parser(
            "profile", help="List or apply named model profiles (build, reason, vision, …)."
        )
        p_models_prof_sub = p_models_profile.add_subparsers(dest="profile_action", required=True)
        p_models_prof_list = p_models_prof_sub.add_parser("list", help="Show built-in and custom profiles.")
        p_models_prof_list.set_defaults(func=cmd_models)
        p_models_prof_set = p_models_prof_sub.add_parser(
            "set", help="Apply a profile to orchestrator, worker, chat, and help (persisted)."
        )
        p_models_prof_set.add_argument("name", help="Profile name (e.g. vision, build, reason)")
        p_models_prof_set.set_defaults(func=cmd_models)

        p_keys = sub.add_parser("keys", help="Manage chat keys (list / rotate / expire / revoke / prune).")
        p_keys_sub = p_keys.add_subparsers(dest="action", required=True)

        p_keys_list = p_keys_sub.add_parser("list", help="List local chat keys with their server-side expiration.")
        p_keys_list.set_defaults(func=cmd_keys)

        p_keys_rot = p_keys_sub.add_parser("rotate", help="Rotate the secret of one or all keys (same key_id, new value).")
        p_keys_rot.add_argument("--label", help="Rotate only this label (otherwise: all)")
        p_keys_rot.set_defaults(func=cmd_keys)

        p_keys_exp = p_keys_sub.add_parser("expire", help="Update expireTime on existing key(s).")
        p_keys_exp.add_argument("--days", type=int, required=True, help="Days from now (0 = remove expiry)")
        p_keys_exp.add_argument("--label", help="Apply to a single label (otherwise: all)")
        p_keys_exp.set_defaults(func=cmd_keys)

        p_keys_rev = p_keys_sub.add_parser("revoke", help="Delete keys by label prefix (server-side + local).")
        p_keys_rev.add_argument("--prefix", default="worker", help="Label prefix to revoke (default: worker)")
        p_keys_rev.add_argument("--yes", action="store_true", help="Skip confirmation")
        p_keys_rev.set_defaults(func=cmd_keys)

        p_keys_prune = p_keys_sub.add_parser(
            "prune",
            help="Delete orphaned xlii-provisioned keys not in this machine's pool.",
        )
        p_keys_prune.add_argument("--name", help="Only keys whose xAI name matches this glob (e.g. '*test*'). Overrides the xlii-only default.")
        p_keys_prune.add_argument("--any-name", action="store_true",
                                  help="Consider keys of ANY name, not just xlii-provisioned (use with care — reaches other tools' keys).")
        p_keys_prune.add_argument("--older-than", type=int, metavar="DAYS", help="Only keys created more than DAYS ago.")
        p_keys_prune.add_argument("--include-active", action="store_true",
                                  help="Also consider your live pool keys (dangerous).")
        p_keys_prune.add_argument("--dry-run", action="store_true", help="Show what would be deleted, then stop.")
        p_keys_prune.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
        p_keys_prune.set_defaults(func=cmd_keys)

        p_keys_mig = p_keys_sub.add_parser(
            "migrate",
            help="Move plaintext chat-key secrets from config.json into the encrypted vault (local, no network).",
        )
        p_keys_mig.add_argument("--dry-run", action="store_true", help="Show what would move, then stop.")
        p_keys_mig.add_argument("--no-backup", action="store_true", help="Skip the timestamped config.json backup.")
        p_keys_mig.set_defaults(func=cmd_keys_migrate)

        p_auth = sub.add_parser("auth", help="Manage plugin credentials in the encrypted vault.")
        p_auth_sub = p_auth.add_subparsers(dest="auth_action", required=True)
        p_auth_set = p_auth_sub.add_parser("set", help="Store a credential: xlii auth set <plugin-id> <ENV_VAR> (value prompted, not echoed).")
        p_auth_set.add_argument("plugin_id")
        p_auth_set.add_argument("env_var")
        p_auth_set.set_defaults(func=cmd_auth)
        p_auth_list = p_auth_sub.add_parser("list", help="List plugins + env var names in the vault (never values).")
        p_auth_list.set_defaults(func=cmd_auth)
        p_auth_clear = p_auth_sub.add_parser("clear", help="Remove a credential or a plugin's whole entry.")
        p_auth_clear.add_argument("plugin_id")
        p_auth_clear.add_argument("env_var", nargs="?", default=None)
        p_auth_clear.set_defaults(func=cmd_auth)
