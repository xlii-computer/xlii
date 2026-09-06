"""The durable-state writers must go through atomicio.write_text_atomic so a
crash mid-write can't corrupt state. test_atomicio.py pins the helper's
contract; these pin the WIRING — patch os.replace to fail and confirm each
writer leaves the prior file intact and drops no temp. A regression back to a
raw write_text would fail these."""

import os

import pytest


def _replace_fails(monkeypatch):
    def boom(src, dst):
        raise OSError("simulated crash during rename")
    monkeypatch.setattr(os, "replace", boom)


def test_registry_save_is_crash_safe(tmp_path, monkeypatch):
    import xlii.registry as reg

    f = tmp_path / "projects.json"
    monkeypatch.setattr(reg, "REGISTRY_FILE", f)
    f.write_text('{"entries": []}')  # good prior state
    r = reg.Registry(entries=[reg.RegistryEntry(
        path="/p", collection_id="c", name="n", created_at="t")])

    _replace_fails(monkeypatch)
    with pytest.raises(OSError):
        r.save()
    assert f.read_text() == '{"entries": []}'
    assert not (tmp_path / "projects.json.tmp").exists()


def test_global_config_save_is_crash_safe(tmp_path, monkeypatch):
    import xlii.config as cfgmod

    f = tmp_path / "config.json"
    monkeypatch.setattr(cfgmod, "GLOBAL_CONFIG_FILE", f)
    f.write_text('{"keys": ["GOOD"]}')
    cfg = cfgmod.GlobalConfig(keys=["NEW"])

    _replace_fails(monkeypatch)
    with pytest.raises(OSError):
        cfg.save()
    assert f.read_text() == '{"keys": ["GOOD"]}'
    assert not (tmp_path / "config.json.tmp").exists()


def test_vault_save_is_crash_safe(tmp_path, monkeypatch):
    pytest.importorskip("cryptography")
    from cryptography.fernet import Fernet

    import xlii.vault as vaultmod

    monkeypatch.setattr(vaultmod, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(vaultmod, "KEY_FILE", tmp_path / ".vault-key")
    monkeypatch.setenv(vaultmod.ENV_VAR, Fernet.generate_key().decode())
    v = vaultmod.Vault.unlock()
    v.set("demo", "TOKEN", "prior-secret")
    prior = vaultmod.VAULT_FILE.read_bytes()

    _replace_fails(monkeypatch)
    v._data["demo"]["TOKEN"] = "new-secret"
    with pytest.raises(OSError):
        v.save()
    assert vaultmod.VAULT_FILE.read_bytes() == prior


def test_manifest_save_is_crash_safe(tmp_path, monkeypatch):
    import xlii.manifest as man

    f = tmp_path / "manifest.json"
    f.write_text('{"entries": {}}')
    m = man.Manifest(path=f, entries={"a.py": man.FileEntry(sha256="x", size=1, mtime=1.0)})

    _replace_fails(monkeypatch)
    with pytest.raises(OSError):
        m.save()
    assert f.read_text() == '{"entries": {}}'
    assert not (tmp_path / "manifest.json.tmp").exists()
