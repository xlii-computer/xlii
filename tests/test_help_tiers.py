"""Progressive /help tiers (grades plan Phase 1)."""

from __future__ import annotations

from xlii.commands_help import (
    HELP_TIERS,
    _DAILY_NAMES,
    command_in_help_tier,
    get_repl_help,
    normalize_help_tier,
)
from xlii.commands import iter_repl_commands
from xlii.repl_cmds import register_all

register_all()


def test_normalize_help_tier():
    assert normalize_help_tier(None) == "daily"
    assert normalize_help_tier("") == "daily"
    assert normalize_help_tier("compose") == "compose"
    assert normalize_help_tier("POWER") == "power"
    assert normalize_help_tier("all") == "all"
    assert normalize_help_tier("nope") == ""


def test_daily_help_is_small():
    text = get_repl_help("code", tier="daily")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    # One terminal screen budget (~40 lines); allow shell section + headers.
    assert len(lines) <= 40, f"daily help too long: {len(lines)} lines\n{text}"
    # Core verbs present
    assert "/attach" in text or "attach" in text.lower()
    assert "/plan" in text or "plan" in text.lower()
    assert "/help" in text or "help" in text.lower()
    # Power-only verbs absent
    assert "/rail" not in text
    assert "/loop" not in text
    assert "/admin" not in text


def test_compose_includes_plugin_not_rail():
    text = get_repl_help("code", tier="compose")
    assert "/plugin" in text or "plugin" in text.lower()
    assert "/rail" not in text


def test_power_includes_rail_and_loop():
    text = get_repl_help("code", tier="power")
    assert "/rail" in text or "rail" in text.lower()
    assert "/loop" in text or "loop" in text.lower()


def test_all_is_superset_of_daily():
    daily = get_repl_help("code", tier="daily")
    full = get_repl_help("code", tier="all")
    assert len(full) > len(daily)
    # Full registry still lists rail
    assert "rail" in full.lower()


def test_daily_names_resolve_in_registry():
    registered = {c.name for c in iter_repl_commands()}
    # Every daily name that is code-or-chat scoped should exist (persona is chat-only — OK if missing from code).
    missing = sorted(n for n in _DAILY_NAMES if n not in registered)
    # persona is chat-only — still registered
    assert not missing, f"daily names not registered: {missing}"


def test_command_in_help_tier_all_true():
    for cmd in iter_repl_commands():
        assert command_in_help_tier(cmd, "all")


def test_help_tiers_constant():
    assert HELP_TIERS == ("daily", "compose", "power", "all")
