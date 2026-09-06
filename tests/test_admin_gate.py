"""Vector E — capability & security gate, unit-tested.

Covers the three mechanisms that hang off one elevation axis:
  1. the `REPLCommand.capability` field + centralized refusal in
     `dispatch_repl_command` (merge seam #1);
  2. the vault admin secret (`set/verify/clear/is_set`) that backs `/admin
     unlock` and the keyed daemon launch; and
  3. the role/plugin risk-gate (`role.gate_loadout_plugins` +
     `plugin_manifest` classifiers, seam #3 activation side),
plus the `/admin` command that ties them together.

Hermetic: the vault file + master key are pinned to tmp_path (the env backend),
and PLUGINS_DIR is redirected, so the real ~/.config/xlii is never touched.

Run directly:  ./venv/bin/python -m pytest tests/test_admin_gate.py
"""

from types import SimpleNamespace

import pytest

from xlii.commands import (
    REPLCommand,
    dispatch_repl_command,
    register_repl_command,
    session_is_elevated,
    unregister_repl_command,
)


class FakeConsole:
    """Records rich-markup lines so tests can assert on output."""

    def __init__(self):
        self.lines: list[str] = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(str(a) for a in args))

    def text(self) -> str:
        return "\n".join(self.lines)


# --------------------------------------------------------------------------- #
#  Seam #1 — capability field + centralized dispatch refusal
# --------------------------------------------------------------------------- #

def test_capability_field_defaults_to_none():
    # Every existing command stays ungated unless it opts in.
    cmd = REPLCommand(name="whatever", handler=lambda line, ctx: True)
    assert cmd.capability is None


def test_session_is_elevated_reads_state_or_context():
    assert session_is_elevated({}) is False                                   # fail closed
    assert session_is_elevated({"elevated": True}) is True                    # explicit ctx key
    assert session_is_elevated({"state": SimpleNamespace(elevated=True)}) is True
    assert session_is_elevated({"state": SimpleNamespace(elevated=False)}) is False
    assert session_is_elevated({"state": SimpleNamespace()}) is False         # attr unset


@pytest.fixture
def gated_command():
    """Register a throwaway capability-gated command; record handler calls."""
    calls: list[int] = []
    cmd = REPLCommand(
        name="__etest_gated",
        handler=lambda line, ctx: (calls.append(1), True)[1],
        capability="admin",
        repls=["code"],
    )
    register_repl_command(cmd)
    try:
        yield calls
    finally:
        unregister_repl_command("__etest_gated")


def test_gated_command_refused_when_not_elevated(gated_command):
    console = FakeConsole()
    ctx = {"command_scope": "code", "console": console, "state": SimpleNamespace()}
    handled = dispatch_repl_command("/__etest_gated", ctx)
    assert handled is True                # consumed (never falls through to the model)
    assert gated_command == []            # handler did NOT run
    out = console.text().lower()
    assert "admin" in out and "/admin unlock" in console.text()


def test_gated_command_runs_when_elevated(gated_command):
    console = FakeConsole()
    ctx = {"command_scope": "code", "console": console, "state": SimpleNamespace(elevated=True)}
    handled = dispatch_repl_command("/__etest_gated", ctx)
    assert handled is True
    assert gated_command == [1]           # handler ran exactly once


def test_gated_command_runs_with_explicit_context_elevation(gated_command):
    # The headless / non-REPLState path: elevation passed directly in the ctx.
    ctx = {"command_scope": "code", "console": FakeConsole(), "elevated": True}
    dispatch_repl_command("/__etest_gated", ctx)
    assert gated_command == [1]


def test_ungated_command_is_unaffected():
    calls: list[int] = []
    cmd = REPLCommand(
        name="__etest_open",
        handler=lambda line, ctx: (calls.append(1), True)[1],
        repls=["code"],
    )
    register_repl_command(cmd)
    try:
        ctx = {"command_scope": "code", "console": FakeConsole(), "state": SimpleNamespace()}
        assert dispatch_repl_command("/__etest_open", ctx) is True
        assert calls == [1]               # ran despite no elevation
    finally:
        unregister_repl_command("__etest_open")


# --------------------------------------------------------------------------- #
#  Vault admin secret (the elevation key)
# --------------------------------------------------------------------------- #

@pytest.fixture
def vault(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    import xlii.vault as vault_mod

    monkeypatch.setattr(vault_mod, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(vault_mod, "KEY_FILE", tmp_path / ".vault-key")
    monkeypatch.setenv(vault_mod.ENV_VAR, Fernet.generate_key().decode())
    return vault_mod


def test_admin_secret_set_then_verify_round_trips(vault):
    assert vault.admin_secret_is_set() is False        # nothing yet
    vault.set_admin_secret("correct horse battery staple")
    assert vault.admin_secret_is_set() is True
    assert vault.verify_admin_secret("correct horse battery staple") is True
    assert vault.verify_admin_secret("wrong") is False


def test_verify_fails_closed_when_unset(vault):
    assert vault.verify_admin_secret("anything") is False
    assert vault.verify_admin_secret("") is False


def test_set_empty_secret_raises(vault):
    with pytest.raises(ValueError):
        vault.set_admin_secret("")


def test_clear_admin_secret(vault):
    vault.set_admin_secret("s")
    assert vault.clear_admin_secret() is True
    assert vault.admin_secret_is_set() is False
    assert vault.clear_admin_secret() is False         # idempotent


def test_secret_is_hashed_not_stored_plaintext(vault):
    secret = "PLAINTEXT-SHOULD-NOT-APPEAR-12345"
    vault.set_admin_secret(secret)
    raw = vault.VAULT_FILE.read_bytes()
    assert secret.encode() not in raw                  # encrypted AND only a hash stored


def test_rotated_secret_invalidates_old(vault):
    vault.set_admin_secret("old")
    vault.set_admin_secret("new")
    assert vault.verify_admin_secret("old") is False
    assert vault.verify_admin_secret("new") is True


# --------------------------------------------------------------------------- #
#  Plugin risk classification (plugin_manifest) + role gate (role.py, seam #3)
# --------------------------------------------------------------------------- #

def test_effect_trust_high_risk_classifier():
    from xlii.plugin_manifest import (
        EFFECT_DESTRUCTIVE,
        EFFECT_EXTERNAL_WRITE,
        EFFECT_LOCAL_SYSTEM,
        EFFECT_READ_ONLY,
        TRUST_ALWAYS_CONFIRM,
        TRUST_SUBSCRIPTION,
        effect_trust_is_high_risk,
    )

    assert effect_trust_is_high_risk(EFFECT_READ_ONLY, TRUST_SUBSCRIPTION) is False
    assert effect_trust_is_high_risk(EFFECT_EXTERNAL_WRITE, TRUST_SUBSCRIPTION) is False
    assert effect_trust_is_high_risk(EFFECT_EXTERNAL_WRITE, TRUST_ALWAYS_CONFIRM) is True
    assert effect_trust_is_high_risk(EFFECT_LOCAL_SYSTEM, TRUST_SUBSCRIPTION) is True
    assert effect_trust_is_high_risk(EFFECT_DESTRUCTIVE, TRUST_SUBSCRIPTION) is True
    assert effect_trust_is_high_risk("bogus", TRUST_SUBSCRIPTION) is True        # fail closed


@pytest.fixture
def plugins(tmp_path, monkeypatch):
    import xlii.plugin as plugin_mod

    d = tmp_path / "plugins"
    d.mkdir()
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", d)

    (d / "etest_low.md").write_text(
        "---\n"
        "id: etest_low\n"
        "name: Low\n"
        "effect: read-only\n"
        "trust: subscription\n"
        "actions:\n"
        "  - id: ping\n"
        "    method: GET\n"
        "    url: https://example.com/\n"
        "---\nbody\n"
    )
    (d / "etest_high.md").write_text(
        "---\n"
        "id: etest_high\n"
        "name: High\n"
        "effect: destructive\n"
        "trust: always-confirm\n"
        "actions:\n"
        "  - id: boom\n"
        "    method: GET\n"
        "    url: https://example.com/\n"
        "---\nbody\n"
    )
    # A structured-manifest plugin: effect drives the classification, not `risk:`.
    (d / "etest_destruct.md").write_text(
        "---\n"
        "id: etest_destruct\n"
        "name: Destruct\n"
        "effect: destructive\n"
        "trust: subscription\n"
        "actions:\n"
        "  - id: nuke\n"
        "    description: dangerous\n"
        "    method: exec\n"
        "    command: 'echo hi'\n"
        "---\nbody\n"
    )
    return d


def test_gate_splits_declared_plugins_by_risk(plugins):
    from xlii.role import gate_loadout_plugins

    allowed, gated = gate_loadout_plugins(
        ["etest_low", "etest_high", "etest_destruct"], elevated=False
    )
    assert allowed == ["etest_low"]
    assert set(gated) == {"etest_high", "etest_destruct"}


def test_gate_elevated_allows_high_risk(plugins):
    from xlii.role import gate_loadout_plugins

    allowed, gated = gate_loadout_plugins(["etest_low", "etest_high"], elevated=True)
    assert set(allowed) == {"etest_low", "etest_high"}
    assert gated == []


def test_gate_missing_plugin_fails_closed(plugins):
    from xlii.role import gate_loadout_plugins, plugin_needs_elevation

    assert plugin_needs_elevation("not_installed_anywhere") is True
    allowed, gated = gate_loadout_plugins(["not_installed_anywhere"], elevated=False)
    assert allowed == []
    assert gated == ["not_installed_anywhere"]


def test_gate_handles_empty_and_none():
    from xlii.role import gate_loadout_plugins

    assert gate_loadout_plugins(None, elevated=False) == ([], [])
    assert gate_loadout_plugins([], elevated=True) == ([], [])


def test_persona_loadout_applies_gate(plugins, tmp_path, monkeypatch):
    """High-risk declared plugins must not auto-subscribe without elevation."""
    from types import SimpleNamespace

    import xlii.cmds.sessions as sessions

    subs: list[str] = []
    lines: list[str] = []

    monkeypatch.setattr(
        "xlii.plugin.add_subscription",
        lambda xli_dir, pid: subs.append(pid),
    )

    class _Console:
        def print(self, *a, **k):
            lines.append(" ".join(str(x) for x in a))

    monkeypatch.setattr("xlii.cmds.sessions.loadout.console", _Console())

    xli = tmp_path / ".xlii"
    xli.mkdir()
    state = SimpleNamespace(elevated=False, save=lambda: None)
    persona = SimpleNamespace(
        loadout=lambda: {"plugins": ["etest_low", "etest_high", "etest_destruct"]}
    )
    project = SimpleNamespace(xli_dir=xli)

    sessions._apply_persona_loadout(state, persona, project)

    assert subs == ["etest_low"]
    assert any("high-risk" in line for line in lines)


def test_persona_loadout_elevated_allows_high_risk(plugins, tmp_path, monkeypatch):
    from types import SimpleNamespace

    import xlii.cmds.sessions as sessions

    subs: list[str] = []
    monkeypatch.setattr(
        "xlii.plugin.add_subscription",
        lambda xli_dir, pid: subs.append(pid),
    )
    monkeypatch.setattr(
        "xlii.cmds.sessions.loadout.console",
        SimpleNamespace(print=lambda *a, **k: None),
    )

    xli = tmp_path / ".xlii"
    xli.mkdir()
    state = SimpleNamespace(elevated=True, save=lambda: None)
    persona = SimpleNamespace(loadout=lambda: {"plugins": ["etest_low", "etest_high"]})
    project = SimpleNamespace(xli_dir=xli)

    sessions._apply_persona_loadout(state, persona, project)

    assert set(subs) == {"etest_low", "etest_high"}


# --------------------------------------------------------------------------- #
#  /admin command — unlock / lock / set-key / status
# --------------------------------------------------------------------------- #

def _run_admin(line: str, state) -> FakeConsole:
    from xlii.repl_cmds.admin import h_admin

    console = FakeConsole()
    h_admin(line, {"console": console, "state": state})
    return console


def test_admin_set_key_bootstrap_elevates(vault):
    state = SimpleNamespace()
    console = _run_admin("/admin set-key hunter2", state)
    assert getattr(state, "elevated", False) is True
    assert vault.admin_secret_is_set() is True
    assert "set" in console.text().lower()


def test_admin_unlock_correct_secret_elevates(vault):
    vault.set_admin_secret("opensesame")
    state = SimpleNamespace()
    _run_admin("/admin unlock opensesame", state)
    assert state.elevated is True


def test_admin_unlock_wrong_secret_does_not_elevate(vault):
    vault.set_admin_secret("opensesame")
    state = SimpleNamespace()
    console = _run_admin("/admin unlock nope", state)
    assert getattr(state, "elevated", False) is False
    assert "incorrect" in console.text().lower()


def test_admin_unlock_without_key_set_nudges(vault):
    state = SimpleNamespace()
    console = _run_admin("/admin unlock whatever", state)
    assert getattr(state, "elevated", False) is False
    assert "set-key" in console.text()


def test_admin_lock_drops_elevation(vault):
    state = SimpleNamespace(elevated=True)
    _run_admin("/admin lock", state)
    assert state.elevated is False


def test_admin_rotate_requires_elevation(vault):
    vault.set_admin_secret("first")

    locked = SimpleNamespace()                       # not elevated
    console = _run_admin("/admin set-key second", locked)
    assert "already exists" in console.text().lower()
    assert vault.verify_admin_secret("first") is True   # unchanged
    assert vault.verify_admin_secret("second") is False

    elevated = SimpleNamespace(elevated=True)
    _run_admin("/admin set-key second", elevated)
    assert vault.verify_admin_secret("second") is True  # rotation allowed


def test_admin_status_reports_key_state(vault):
    state = SimpleNamespace()
    before = _run_admin("/admin status", state)
    assert "not set" in before.text().lower()
    vault.set_admin_secret("x")
    after = _run_admin("/admin status", state)
    assert "not set" not in after.text().lower()


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
