"""Vault.unlock: try every master-key backend until vault.enc decrypts.

A stale OS-keyring entry must not strand a still-valid .vault-key, and an
existing vault.enc must never be re-keyed by create_if_missing.
"""

from __future__ import annotations

import json
import threading

import pytest

pytest.importorskip("cryptography")
from cryptography.fernet import Fernet  # noqa: E402

import xlii.vault as vault  # noqa: E402


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    monkeypatch.setattr(vault, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(vault, "KEY_FILE", tmp_path / ".vault-key")
    monkeypatch.delenv(vault.ENV_VAR, raising=False)
    monkeypatch.delenv(vault.ENV_VAR_LEGACY, raising=False)
    monkeypatch.setattr(vault, "_KEYRING_DEAD", threading.Event())
    monkeypatch.setattr(vault, "_read_keyring_key", lambda: None)
    return tmp_path


def _seal(key: bytes, payload: dict) -> bytes:
    return Fernet(key).encrypt(json.dumps(payload).encode())


def test_unlock_falls_through_mismatched_keyring_to_file(pinned, monkeypatch):
    good = Fernet.generate_key()
    bad = Fernet.generate_key()
    vault.KEY_FILE.write_bytes(good)
    vault.VAULT_FILE.write_bytes(_seal(good, {"xlii:chat-keys": {"primary-2": "s"}}))
    monkeypatch.setattr(vault, "_read_keyring_key", lambda: bad)

    opened = vault.Vault.unlock(create_if_missing=False)
    assert opened.backend == vault.BACKEND_FILE
    assert opened.get("xlii:chat-keys")["primary-2"] == "s"


def test_unlock_does_not_provision_over_existing_vault(pinned, monkeypatch):
    writes: list[bytes] = []
    monkeypatch.setattr(vault, "_write_keyring_key", lambda k: writes.append(k) or True)
    vault.VAULT_FILE.write_bytes(_seal(Fernet.generate_key(), {}))

    with pytest.raises(vault.VaultError, match="sealed"):
        vault.Vault.unlock(create_if_missing=True)
    assert writes == []
    assert not vault.KEY_FILE.exists()


def test_unlock_provisions_only_when_vault_is_absent(pinned, monkeypatch):
    monkeypatch.setattr(vault, "_write_keyring_key", lambda _k: False)
    opened = vault.Vault.unlock(create_if_missing=True)
    assert opened.backend == vault.BACKEND_FILE
    assert opened.list_plugins() == []
    assert vault.KEY_FILE.exists()
