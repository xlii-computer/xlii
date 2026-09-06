"""Vector D — destroy L0+L1 gate matrix and scope guards."""

from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@dataclass
class _MintedKey:
    key_id: str
    team_id: str
    label: str = ""
    minted_at: str = ""


@dataclass
class _MintedCollection:
    collection_id: str
    team_id: str
    name: str = ""
    kind: str = ""
    minted_at: str = ""


@dataclass
class _Manifest:
    keys: list[_MintedKey] = field(default_factory=list)
    collections: list[_MintedCollection] = field(default_factory=list)


def _install_gm(monkeypatch, *, phrase_ok: bool = True, admin_ok: bool = True):
    hg = types.ModuleType("xlii.human_gate")

    class HumanGateError(Exception):
        pass

    def require_human(context):
        console = context.get("console")
        if getattr(console, "xlii_foreground", None) is False:
            raise HumanGateError("not foreground")

    hg.HumanGateError = HumanGateError
    hg.require_human = require_human
    hg.confirm_typed_phrase = lambda console, phrase, *, prompt=None: phrase_ok
    hg.verify_fresh_admin = lambda console: admin_ok
    hg.is_foreground_console = lambda console: getattr(console, "xlii_foreground", None) is not False
    monkeypatch.setitem(sys.modules, "xlii.human_gate", hg)

    minted = types.ModuleType("xlii.minted")
    manifest = _Manifest(
        keys=[_MintedKey("kid-1", "team-a", label="worker")],
        collections=[_MintedCollection("cid-1", "team-a", name="xlii/demo", kind="main")],
    )
    minted.Manifest = _Manifest
    minted.MintedKey = _MintedKey
    minted.MintedCollection = _MintedCollection
    minted.load = lambda: manifest
    minted.record_key_safe = lambda **k: None
    minted.record_collection_safe = lambda **k: None
    minted.record_key = lambda **k: None
    minted.record_collection = lambda **k: None
    monkeypatch.setitem(sys.modules, "xlii.minted", minted)

    if "xlii.destroy" in sys.modules:
        importlib.reload(sys.modules["xlii.destroy"])


@pytest.fixture
def destroy_mod(monkeypatch, tmp_path):
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("XLII_DESTROY_JOURNAL", str(tmp_path / "destroy-journal.jsonl"))
    monkeypatch.delenv("XAI_MANAGEMENT_API_KEY", raising=False)
    _install_gm(monkeypatch)
    import xlii.destroy as mod

    return importlib.reload(mod)


def test_l0_dry_run_no_deletes(destroy_mod, monkeypatch):
    deleted_keys: list[str] = []
    deleted_colls: list[str] = []
    rmtrees: list[str] = []

    monkeypatch.setattr(
        "xlii.bootstrap.delete_api_key",
        lambda *a, **k: deleted_keys.append(a[2]),
    )
    monkeypatch.setattr(
        "xlii.storage_backend.CollectionsBackend.delete_collection",
        lambda *a, **k: deleted_colls.append(a[1]),
    )
    monkeypatch.setattr("shutil.rmtree", lambda p, *a, **k: rmtrees.append(str(p)))

    report = destroy_mod.run_destroy("all", level=0, console=MagicMock())
    assert report.dry_run is True
    assert report.planned
    assert not deleted_keys
    assert not deleted_colls
    assert not rmtrees


def test_l1_refuses_without_console(destroy_mod):
    report = destroy_mod.run_destroy("all", level=1, dry_run=False, console=None)
    assert report.failed
    assert any("console" in f.get("error", "") for f in report.failed)


def test_l1_refuses_wrong_phrase(destroy_mod, monkeypatch):
    _install_gm(monkeypatch, phrase_ok=False)
    report = destroy_mod.run_destroy("all", level=1, dry_run=False, console=MagicMock())
    assert report.failed
    assert any("phrase" in f.get("error", "") for f in report.failed)


def test_l1_refuses_no_admin(destroy_mod, monkeypatch):
    _install_gm(monkeypatch, admin_ok=False)
    report = destroy_mod.run_destroy("all", level=1, dry_run=False, console=MagicMock())
    assert report.failed


def test_l1_refuses_non_foreground(destroy_mod, monkeypatch):
    _install_gm(monkeypatch)

    class CapConsole:
        xlii_foreground = False

    report = destroy_mod.run_destroy("all", level=1, dry_run=False, console=CapConsole())
    assert report.failed


def test_prefix_id_not_in_minted_not_deleted(destroy_mod, monkeypatch):
    calls: list[str] = []

    def _list_keys(mgmt, team):
        return [
            {"name": "xlii-worker-1", "apiKeyId": "kid-1"},
            {"name": "xlii-sibling", "apiKeyId": "kid-other"},
        ]

    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "mgmt")
    monkeypatch.setattr("xlii.bootstrap.list_api_keys", _list_keys)
    monkeypatch.setattr(
        "xlii.bootstrap.delete_api_key",
        lambda mgmt, team, kid: calls.append(kid),
    )
    monkeypatch.setattr("xlii.xai_mgmt.list_teams", lambda mgmt: [{"teamId": "team-a"}])
    monkeypatch.setattr(
        "xlii.storage_backend.CollectionsBackend.delete_collection",
        lambda *a, **k: None,
    )
    monkeypatch.setattr("xlii.client.Clients.from_config", lambda cfg: MagicMock())

    report = destroy_mod.run_destroy(
        "all",
        level=1,
        dry_run=False,
        local_only=True,
        console=MagicMock(),
    )
    assert "kid-other" not in calls
    assert any("not on minted paper" in r for r in report.residuals)


def test_local_only_journals_surviving_ids(destroy_mod, tmp_path):
    journal = Path(tmp_path / "destroy-journal.jsonl")
    destroy_mod.run_destroy(
        "all",
        level=1,
        dry_run=False,
        local_only=True,
        console=MagicMock(),
        journal_path=journal,
    )
    text = journal.read_text()
    assert "surviving_cloud_ids" in text
    assert " unqualified destroyed" not in text.lower()


def test_vault_wipe_honest_on_keyring_failure(monkeypatch, tmp_path):
    import xlii.vault as vault

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    monkeypatch.setattr(vault, "GLOBAL_CONFIG_DIR", cfg)
    monkeypatch.setattr(vault, "VAULT_FILE", cfg / "vault.enc")
    monkeypatch.setattr(vault, "KEY_FILE", cfg / ".vault-key")
    (cfg / "vault.enc").write_text("cipher")

    kr = MagicMock()
    kr.get_password.return_value = "still-there"
    monkeypatch.setitem(sys.modules, "keyring", kr)

    result = vault.wipe_vault_verified()
    assert result.wiped is False
    assert result.residuals


def test_node_not_on_paper(destroy_mod):
    report = destroy_mod.run_destroy(
        "node",
        target="missing-node",
        level=1,
        dry_run=False,
        console=MagicMock(),
    )
    assert report.residuals == ["not on paper"]
    assert not report.done


def test_help_tier_omission_from_power(monkeypatch):
    _install_gm(monkeypatch)
    from xlii.commands import iter_repl_commands
    from xlii.commands_help import command_in_help_tier
    from xlii.repl_cmds import register_all

    register_all()
    cmd = next(c for c in iter_repl_commands() if c.name == "destroy-all")
    assert cmd.category == "danger"
    assert command_in_help_tier(cmd, "power") is False
    assert command_in_help_tier(cmd, "all") is True


def test_cli_default_is_dry_run(monkeypatch):
    import argparse

    from xlii.cmds import destroy as destroy_cli

    seen: dict = {}

    def _capture(aim, **kwargs):
        seen.update(kwargs)
        seen["aim"] = aim

        class R:
            aim = "all"
            level = kwargs.get("level", 0)
            dry_run = kwargs.get("dry_run")
            local_only = kwargs.get("local_only")
            planned: list = []
            done: list = []
            failed: list = []
            residuals: list = []
            journal_path = None

        return R()

    monkeypatch.setattr("xlii.destroy.run_destroy", _capture)
    args = argparse.Namespace(
        dry_run=False, keys_and_local=False, local_only=False, aim="all", target=""
    )
    assert destroy_cli.cmd_destroy_all(args) == 0
    assert seen["level"] == 0
    assert seen["dry_run"] is True

    args.keys_and_local = True
    destroy_cli.cmd_destroy_all(args)
    assert seen["level"] == 1
    assert seen["dry_run"] is False

    args.dry_run = True
    destroy_cli.cmd_destroy_all(args)
    assert seen["level"] == 0
    assert seen["dry_run"] is True


def test_factory_reset_is_level_1(monkeypatch):
    from xlii.repl_cmds import destroy as destroy_repl

    seen: dict = {}

    def _capture(aim, **kwargs):
        seen.update(kwargs)
        seen["aim"] = aim
        return SimpleNamespace(
            aim=aim,
            level=kwargs.get("level", 0),
            dry_run=kwargs.get("dry_run"),
            local_only=kwargs.get("local_only", False),
            planned=[],
            done=[],
            failed=[],
            residuals=[],
            journal_path=None,
        )

    monkeypatch.setattr("xlii.repl_cmds.destroy.run_destroy", _capture)
    console = type("C", (), {"print": lambda *a, **k: None})()
    destroy_repl.h_destroy_all("/factory-reset", {"console": console})
    assert seen["level"] == 1
    assert seen["dry_run"] is False
    destroy_repl.h_destroy_all("/destroy-all", {"console": console})
    assert seen["level"] == 0
    assert seen["dry_run"] is True


def test_human_only_dispatch_if_field_present(monkeypatch):
    import dataclasses

    from xlii.commands import REPLCommand, dispatch_repl_command, register_repl_command

    if "human_only" not in {f.name for f in dataclasses.fields(REPLCommand)}:
        pytest.skip("human_only dispatch owned by Vector G — not on this branch yet")

    class CapConsole:
        xlii_foreground = False

        def print(self, *a, **k):
            pass

    register_repl_command(
        REPLCommand(
            name="destroy-all-test-hook",
            handler=lambda line, ctx: True,
            capability="admin",
            human_only=True,
            category="danger",
            repls=["code"],
        )
    )
    ctx = {"console": CapConsole(), "command_scope": "code", "elevated": True}
    assert dispatch_repl_command("/destroy-all-test-hook", ctx) is True
