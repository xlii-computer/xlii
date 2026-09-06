"""Vault env alias + path contract (grades plan Phase 2 / docs/LEGACY.md)."""

from __future__ import annotations



from xlii import vault
from xlii.config import GLOBAL_CONFIG_DIR


def test_vault_paths_live_under_xlii_config_dir():
    assert vault.VAULT_FILE == GLOBAL_CONFIG_DIR / "vault.enc"
    assert vault.KEY_FILE == GLOBAL_CONFIG_DIR / ".vault-key"
    # Product config dir is xlii — never ~/.config/xli
    assert GLOBAL_CONFIG_DIR.name == "xlii" or "xlii" in str(GLOBAL_CONFIG_DIR)


def test_keyring_service_stays_xli_for_compat():
    assert vault.KEYRING_SERVICE == "xli"
    assert vault.KEYRING_USERNAME == "vault-master"


def test_canonical_env_is_xlii_vault_key():
    assert vault.ENV_VAR == "XLII_VAULT_KEY"
    assert vault.ENV_VAR_LEGACY == "XLI_VAULT_KEY"


def test_keyring_read_does_not_block_when_backend_hangs(monkeypatch):
    """import keyring / get_password can hang on D-Bus NoReply. Face boot
    must return None in ~KEYRING_WAIT_S, not join the worker."""
    import sys
    import threading
    import time
    import types

    monkeypatch.setattr(vault, "KEYRING_WAIT_S", 0.2)
    # A throwaway latch: monkeypatch puts the real one back even if this test
    # fails partway, so a tripped probe can never mute the keyring for the
    # rest of the session.
    monkeypatch.setattr(vault, "_KEYRING_DEAD", threading.Event())

    fake = types.ModuleType("keyring")

    def get_password(*_a, **_k):
        time.sleep(5)
        return "should-not-return"

    fake.get_password = get_password
    monkeypatch.setitem(sys.modules, "keyring", fake)

    t0 = time.monotonic()
    assert vault._read_keyring_key() is None
    assert time.monotonic() - t0 < 1.0
    assert vault._KEYRING_DEAD.is_set()
    t1 = time.monotonic()
    assert vault._read_keyring_key() is None
    assert time.monotonic() - t1 < 0.05


def test_read_env_key_accepts_legacy_alias(monkeypatch):
    monkeypatch.delenv("XLII_VAULT_KEY", raising=False)
    monkeypatch.delenv("XLI_VAULT_KEY", raising=False)
    assert vault._read_env_key() is None

    # 44-char-ish placeholder — resolution only checks presence, not Fernet parse here
    monkeypatch.setenv("XLI_VAULT_KEY", "legacy-key-value")
    assert vault._read_env_key() == b"legacy-key-value"

    monkeypatch.setenv("XLII_VAULT_KEY", "canonical-wins")
    assert vault._read_env_key() == b"canonical-wins"
