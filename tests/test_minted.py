"""Vector M — minted.json ownership manifest at mint chokepoints."""

from __future__ import annotations

import json
import stat
from types import SimpleNamespace

import pytest

import xlii.bootstrap as bootstrap
import xlii.config as config
from xlii.minted import (
    load,
    minted_path,
    record_collection,
    record_key,
)
from xlii.storage_backend import CollectionsBackend


@pytest.fixture
def minted_file(tmp_path, monkeypatch):
    path = tmp_path / "minted.json"
    monkeypatch.setenv("XLII_MINTED_PATH", str(path))
    return path


def test_record_key_round_trip_and_path_override(minted_file):
    record_key(key_id="kid-1", team_id="team-a", label="worker-1")
    assert minted_path() == minted_file
    m = load()
    assert len(m.keys) == 1
    assert m.keys[0].key_id == "kid-1"
    assert m.keys[0].team_id == "team-a"
    assert m.keys[0].label == "worker-1"
    assert m.keys[0].minted_at
    mode = minted_file.stat().st_mode
    assert stat.S_IMODE(mode) == 0o600
    raw = json.loads(minted_file.read_text())
    assert raw["version"] == 1
    assert raw["collections"] == []


def test_duplicate_record_key_is_idempotent(minted_file):
    record_key(key_id="kid-1", team_id="team-a", label="worker-1")
    record_key(key_id="kid-1", team_id="team-a", label="worker-1")
    assert len(load().keys) == 1


def test_json_never_persists_secrets(minted_file):
    """Only declared manifest fields are written — never caller-side secret blobs."""
    record_key(key_id="kid-x", team_id="team-x", label="main")
    text = minted_file.read_text()
    assert "sk-" not in text
    assert "api_key" not in text
    assert "secret" not in text
    raw = json.loads(text)
    assert set(raw["keys"][0].keys()) == {"key_id", "team_id", "label", "minted_at"}


@pytest.fixture
def pinned_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "GLOBAL_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(bootstrap, "GLOBAL_CONFIG_FILE", cfg_file)
    cfg_file.write_text(json.dumps({"keys": []}))
    return cfg_file


def _cfg():
    cfg = config.GlobalConfig.load()
    cfg.management_api_key = "mgmt"
    return cfg


def test_provision_labeled_key_records_minted_row(pinned_config, minted_file, monkeypatch):
    def _create(mgmt, team_id, name, **kw):
        return {
            "api_key": "sk-new",
            "apiKeyId": "kid-prov-1",
            "expireTime": "2027-01-01T00:00:00Z",
        }

    monkeypatch.setattr(bootstrap, "create_api_key", _create)
    result = bootstrap.provision_labeled_key(_cfg(), "team-1", label="worker-1", expire_days=30)

    assert result.ok
    m = load()
    assert len(m.keys) == 1
    assert m.keys[0].key_id == "kid-prov-1"
    assert m.keys[0].team_id == "team-1"
    assert m.keys[0].label == "worker-1"
    assert "sk-new" not in minted_file.read_text()


class _FakeColls:
    def __init__(self):
        self.created = []

    def create(self, name, field_definitions=None):
        cid = f"coll-{len(self.created)}"
        self.created.append((name, field_definitions))
        return SimpleNamespace(collection_id=cid)


def test_create_collection_records_minted_row(minted_file):
    colls = _FakeColls()
    clients = SimpleNamespace(xai=SimpleNamespace(collections=colls), team_id="team-z")
    resp = CollectionsBackend.create_collection(clients, "xlii/demo", None)
    assert resp.collection_id == "coll-0"
    m = load()
    assert len(m.collections) == 1
    assert m.collections[0].collection_id == "coll-0"
    assert m.collections[0].team_id == "team-z"
    assert m.collections[0].name == "xlii/demo"
    assert m.collections[0].kind == ""


def test_create_collection_infers_journal_kind(minted_file):
    colls = _FakeColls()
    clients = SimpleNamespace(xai=SimpleNamespace(collections=colls), team_id="team-z")
    journal_name = f"{config.JOURNAL_COLLECTION_PREFIX}lab"
    CollectionsBackend.create_collection(clients, journal_name, None)
    assert load().collections[0].kind == "journal"


def test_record_failure_does_not_break_provision(pinned_config, minted_file, monkeypatch):
    def _create(mgmt, team_id, name, **kw):
        return {"api_key": "sk-new", "apiKeyId": "kid-1"}

    monkeypatch.setattr(bootstrap, "create_api_key", _create)
    monkeypatch.setattr(
        "xlii.minted.record_key",
        lambda **kw: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = bootstrap.provision_labeled_key(_cfg(), "team-1", label="worker-1", expire_days=30)
    assert result.ok
    assert json.loads(pinned_config.read_text())["keys"]


def test_record_failure_does_not_break_create_collection(minted_file, monkeypatch):
    colls = _FakeColls()
    clients = SimpleNamespace(xai=SimpleNamespace(collections=colls), team_id="team-z")

    def _boom(**kw):
        raise OSError("disk full")

    monkeypatch.setattr("xlii.minted.record_collection", _boom)
    resp = CollectionsBackend.create_collection(clients, "xlii/demo", None)
    assert resp.collection_id == "coll-0"
    assert load().collections == []


def test_provision_worker_keys_records_each_key(pinned_config, minted_file, monkeypatch):
    pinned_config.write_text(json.dumps({"keys": []}))
    responses = [
        {"api_key": "sk-1", "apiKeyId": "kid-1"},
        {"api_key": "sk-2", "apiKeyId": "kid-2"},
    ]

    def _create(mgmt, team_id, name, **kw):
        return responses.pop(0)

    monkeypatch.setattr(bootstrap, "create_api_key", _create)
    monkeypatch.setattr(bootstrap, "INTER_CREATE_DELAY_SEC", 0)
    rc = bootstrap.provision_worker_keys(
        _cfg(), "team-batch", prefix="worker", count=2, expire_days=180, force=False
    )
    assert rc == 0
    m = load()
    assert {k.key_id for k in m.keys} == {"kid-1", "kid-2"}
    assert all(k.team_id == "team-batch" for k in m.keys)


def test_corrupt_minted_json_returns_empty(minted_file):
    minted_file.write_text("{not json")
    assert load().keys == [] and load().collections == []


def test_record_collection_round_trip(minted_file):
    record_collection(
        collection_id="cid-1",
        team_id="team-a",
        name="xlii/proj",
        kind="main",
    )
    m = load()
    assert len(m.collections) == 1
    assert m.collections[0].collection_id == "cid-1"
    record_collection(collection_id="cid-1", team_id="team-a", name="xlii/proj")
    assert len(load().collections) == 1
