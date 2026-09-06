"""Round-trip test for `xlii keys migrate`: plaintext chat keys move into the
encrypted vault, config.json keeps only refs, and key_pairs() resolves them back.

Hermetic: config.json + vault.enc are pinned to tmp_path and the vault master
key is supplied via $XLI_VAULT_KEY (env backend), so the real OS keyring and
~/.config/xlii are never touched.
"""

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("cryptography")
from cryptography.fernet import Fernet  # noqa: E402

import xlii.bootstrap as bootstrap  # noqa: E402
import xlii.config as config  # noqa: E402
import xlii.vault as vault  # noqa: E402
import xlii.cmds.provision as provision  # noqa: E402
from xlii.cmds.provision import keys as provision_keys  # noqa: E402
from xlii.config import GlobalConfig  # noqa: E402


SECRET_PRIMARY = "xai-SECRET-primary-0123456789abcdef"
SECRET_BARE = "xai-SECRET-bare-fedcba9876543210"


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "GLOBAL_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(vault, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(vault, "KEY_FILE", tmp_path / ".vault-key")
    monkeypatch.setenv(vault.ENV_VAR, Fernet.generate_key().decode())
    return cfg_file


def _write_config(cfg_file, keys):
    cfg_file.write_text(json.dumps({"_comment": "keep me", "keys": keys}, indent=2))


def test_migrate_moves_secrets_to_vault_and_round_trips(pinned):
    cfg_file = pinned
    _write_config(cfg_file, [
        {"api_key": SECRET_PRIMARY, "label": "primary-1", "api_key_id": "id-1",
         "expire_time": "2026-12-11T00:00:00Z"},
        SECRET_BARE,  # bare-string plaintext entry
    ])

    rc = provision.cmd_keys_migrate(SimpleNamespace(dry_run=False, no_backup=False))
    assert rc == 0

    raw_text = cfg_file.read_text()
    raw = json.loads(raw_text)

    # Secrets are gone from config.json (both the structured and bare entry).
    assert SECRET_PRIMARY not in raw_text
    assert SECRET_BARE not in raw_text
    # Entries now reference the vault; non-secret metadata is preserved.
    assert raw["_comment"] == "keep me"          # surgical rewrite kept other keys
    assert "api_key" not in raw["keys"][0]
    assert raw["keys"][0]["vault_ref"]
    assert raw["keys"][0]["label"] == "primary-1"
    assert raw["keys"][0]["api_key_id"] == "id-1"   # metadata untouched
    assert raw["keys"][1]["vault_ref"]

    # The vault file is encrypted: the plaintext secret is not in its bytes.
    assert vault.VAULT_FILE.exists()
    assert SECRET_PRIMARY.encode() not in vault.VAULT_FILE.read_bytes()

    # key_pairs() resolves the refs back to the original secrets, in order.
    pairs = GlobalConfig.load().key_pairs()
    assert [p.api_key for p in pairs] == [SECRET_PRIMARY, SECRET_BARE]
    assert pairs[0].label == "primary-1"


def test_rotate_updates_vault_backed_secret_without_plaintext(pinned, monkeypatch):
    cfg_file = pinned
    rotated_secret = "xai-SECRET-rotated-0011223344556677"
    _write_config(cfg_file, [
        {"api_key": SECRET_PRIMARY, "label": "primary-1", "api_key_id": "id-1"},
    ])

    assert provision.cmd_keys_migrate(SimpleNamespace(dry_run=False, no_backup=True)) == 0
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "mgmt-secret")
    monkeypatch.setattr(
        bootstrap,
        "rotate_api_key",
        lambda mgmt_key, api_key_id: {"api_key": rotated_secret},
    )

    rc = provision_keys._keys_rotate(
        GlobalConfig.load(),
        "team-1",
        SimpleNamespace(label="primary-1"),
    )
    assert rc == 0

    raw_text = cfg_file.read_text()
    raw = json.loads(raw_text)
    assert rotated_secret not in raw_text
    assert "api_key" not in raw["keys"][0]
    assert raw["keys"][0]["vault_ref"] == "primary-1"
    assert GlobalConfig.load().key_pairs()[0].api_key == rotated_secret


def test_migrate_is_idempotent(pinned):
    cfg_file = pinned
    _write_config(cfg_file, [{"api_key": SECRET_PRIMARY, "label": "primary-1"}])

    assert provision.cmd_keys_migrate(SimpleNamespace(dry_run=False, no_backup=True)) == 0
    assert GlobalConfig.load().plaintext_key_count() == 0

    # Second run finds nothing to do and leaves the file unchanged.
    before = cfg_file.read_text()
    assert provision.cmd_keys_migrate(SimpleNamespace(dry_run=False, no_backup=True)) == 0
    assert cfg_file.read_text() == before


def test_dry_run_changes_nothing(pinned):
    cfg_file = pinned
    _write_config(cfg_file, [{"api_key": SECRET_PRIMARY, "label": "primary-1"}])
    before = cfg_file.read_text()

    assert provision.cmd_keys_migrate(SimpleNamespace(dry_run=True, no_backup=False)) == 0
    assert cfg_file.read_text() == before          # untouched
    assert not vault.VAULT_FILE.exists()           # vault never opened
    assert GlobalConfig.load().plaintext_key_count() == 1


def test_backup_retains_old_plaintext(pinned):
    cfg_file = pinned
    _write_config(cfg_file, [{"api_key": SECRET_PRIMARY, "label": "primary-1"}])

    assert provision.cmd_keys_migrate(SimpleNamespace(dry_run=False, no_backup=False)) == 0
    backups = list(cfg_file.parent.glob("config.json.bak-*"))
    assert len(backups) == 1
    assert SECRET_PRIMARY in backups[0].read_text()   # backup is the pre-migration copy
