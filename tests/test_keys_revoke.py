"""`keys revoke` / `bootstrap --revoke` partial-failure policy, at the kernel
seam (revoke_keys_by_prefix, consolidated from cmds/provision/_keyops.py)."""

import json

import xlii.bootstrap as bootstrap
import xlii.config as config
from xlii.bootstrap import BootstrapError
from xlii.config import GlobalConfig


def test_revoke_preserves_local_secret_when_server_delete_fails(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "GLOBAL_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(bootstrap, "GLOBAL_CONFIG_FILE", cfg_file)
    cfg_file.write_text(json.dumps({
        "keys": [
            {"label": "worker-1", "api_key": "sk-deleted", "api_key_id": "id-deleted"},
            {"label": "worker-2", "api_key": "sk-still-live", "api_key_id": "id-live"},
            {"label": "primary-1", "api_key": "sk-primary", "api_key_id": "id-primary"},
        ]
    }))

    monkeypatch.setattr(bootstrap, "list_api_keys", lambda mgmt_key, team_id: [
        {"name": "xlii-worker-1", "apiKeyId": "id-deleted"},
        {"name": "xlii-worker-2", "apiKeyId": "id-live"},
    ])

    def _delete_api_key(mgmt_key, team_id, key_id):
        if key_id == "id-live":
            raise BootstrapError("temporary delete failure")

    monkeypatch.setattr(bootstrap, "delete_api_key", _delete_api_key)

    cfg = GlobalConfig.load()
    cfg.management_api_key = "mgmt-secret"
    rc = bootstrap.revoke_keys_by_prefix(
        cfg,
        "team-1",
        "worker",
        confirm_cb=lambda _matches: True,
    )

    assert rc == 1
    keys = json.loads(cfg_file.read_text())["keys"]
    by_label = {entry["label"]: entry for entry in keys}
    assert "worker-1" not in by_label
    assert by_label["worker-2"]["api_key"] == "sk-still-live"
    assert by_label["primary-1"]["api_key"] == "sk-primary"


def test_revoke_without_confirm_callback_cancels(tmp_path, monkeypatch):
    """confirm_cb=None (a headless body that cannot confirm) must delete
    nothing server-side and leave the local config untouched."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "GLOBAL_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(bootstrap, "GLOBAL_CONFIG_FILE", cfg_file)
    cfg_file.write_text(json.dumps({
        "keys": [{"label": "worker-1", "api_key": "sk-1", "api_key_id": "id-1"}]
    }))

    monkeypatch.setattr(bootstrap, "list_api_keys", lambda mgmt_key, team_id: [
        {"name": "xlii-worker-1", "apiKeyId": "id-1"},
    ])

    def _boom(*a, **k):
        raise AssertionError("delete_api_key must NOT run without confirmation")

    monkeypatch.setattr(bootstrap, "delete_api_key", _boom)

    cfg = GlobalConfig.load()
    cfg.management_api_key = "mgmt-secret"
    events = []
    rc = bootstrap.revoke_keys_by_prefix(
        cfg, "team-1", "worker", on_event=lambda kind, **p: events.append(kind)
    )

    assert rc == 0
    assert "cancelled" in events
    assert "deleted" not in events


def test_revoke_with_no_server_matches_preserves_local_keys(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "GLOBAL_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(bootstrap, "GLOBAL_CONFIG_FILE", cfg_file)
    cfg_file.write_text(json.dumps({
        "keys": [
            {"label": "worker-1", "api_key": "sk-local", "api_key_id": "id-local"},
            {"label": "primary-1", "api_key": "sk-primary", "api_key_id": "id-primary"},
        ]
    }))

    monkeypatch.setattr(bootstrap, "list_api_keys", lambda mgmt_key, team_id: [])

    def _delete_api_key(*a, **k):
        raise AssertionError("delete_api_key must not run when no server key matched")

    monkeypatch.setattr(bootstrap, "delete_api_key", _delete_api_key)

    cfg = GlobalConfig.load()
    cfg.management_api_key = "mgmt-secret"
    events = []
    rc = bootstrap.revoke_keys_by_prefix(
        cfg,
        "team-1",
        "worker",
        confirm_cb=lambda _matches: True,
        on_event=lambda kind, **p: events.append((kind, p)),
    )

    assert rc == 0
    assert json.loads(cfg_file.read_text())["keys"][0]["api_key"] == "sk-local"
    assert any(kind == "none_found" for kind, _ in events)
    assert any(kind == "config_removed" and payload["count"] == 0 for kind, payload in events)
