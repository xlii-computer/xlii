"""Coverage for the B6 provision consolidation in xlii/bootstrap.py: the
primitives that deleted the duplicated face copies (D2–D6, D15), the divergent
label-allocator pin (D3), and the headless-callable verbs. No network —
create/list/delete are monkeypatched."""

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from cryptography.fernet import Fernet

import xlii.bootstrap as bootstrap
import xlii.config as config
import xlii.vault as vault
from xlii.bootstrap import (
    BootstrapError,
    extract_api_key_id,
    has_key_labeled,
    is_provisioned_name,
    next_free_label,
    parse_create_response,
    parse_xai_timestamp,
    provision_labeled_key,
    provisioned_key_names,
    reconcile_local_keys,
    require_management_key,
    team_id_of,
)
from xlii.config import GlobalConfig


# --------------------------------------------------------------------------- #
#  D4 — one id-extraction chain, strict
# --------------------------------------------------------------------------- #

def test_extract_api_key_id_fallback_chain():
    assert extract_api_key_id({"apiKeyId": "a"}) == "a"
    assert extract_api_key_id({"api_key_id": "b"}) == "b"
    assert extract_api_key_id({"id": "c"}) == "c"
    assert extract_api_key_id({"apiKeyId": "a", "id": "c"}) == "a"
    assert extract_api_key_id({}) is None
    assert extract_api_key_id("nope") is None


def test_extract_api_key_id_rejects_non_string_values():
    # the strict kernel guard the deleted face copies lacked
    assert extract_api_key_id({"apiKeyId": 42}) is None
    assert extract_api_key_id({"apiKeyId": ""}) is None


# --------------------------------------------------------------------------- #
#  D2 — one create-response → entry composition
# --------------------------------------------------------------------------- #

def test_parse_create_response_full_entry():
    resp = {
        "api_key": "sk-secret",
        "apiKeyId": "kid-1",
        "expireTime": "2026-12-31T00:00:00Z",
    }
    assert parse_create_response(resp, "worker-1") == {
        "api_key": "sk-secret",
        "label": "worker-1",
        "api_key_id": "kid-1",
        "expire_time": "2026-12-31T00:00:00Z",
    }


def test_parse_create_response_snake_expire_and_minimal():
    resp = {"key": "sk-secret", "expire_time": "2026-06-01T00:00:00Z"}
    assert parse_create_response(resp, "primary-1") == {
        "api_key": "sk-secret",
        "label": "primary-1",
        "expire_time": "2026-06-01T00:00:00Z",
    }


def test_parse_create_response_missing_secret_returns_none():
    assert parse_create_response({"apiKeyId": "kid"}, "w-1") is None
    assert parse_create_response(None, "w-1") is None


# --------------------------------------------------------------------------- #
#  D3 — one allocator; the divergent case is pinned
# --------------------------------------------------------------------------- #

def test_next_free_label_bumps_numeric_suffix():
    assert next_free_label([{"label": "worker-1"}], "worker-1") == "worker-2"
    assert next_free_label([], "worker-1") == "worker-1"
    assert next_free_label([{"label": "journal"}], "journal") == "journal-1"
    # non-dict entries are ignored, not crashes
    assert next_free_label(["sk-bare"], "worker-1") == "worker-1"


def test_next_free_label_continues_from_requested_suffix_not_gap_fill():
    """The two deleted allocators diverged here: with worker-10 taken, the
    suffix-parsing allocator continues (worker-11) while the index-counting
    one gap-filled (worker-1). Continuation is the kept semantics — a taken
    number may still exist server-side after local entries were pruned."""
    assert next_free_label([{"label": "worker-10"}], "worker-10") == "worker-11"
    # ...and the hole at worker-1 is NOT filled when we asked for worker-10
    existing = [{"label": "worker-1"}, {"label": "worker-10"}]
    assert next_free_label(existing, "worker-10") == "worker-11"


# --------------------------------------------------------------------------- #
#  D6 — one server-name convention
# --------------------------------------------------------------------------- #

def test_provisioned_key_names_current_and_legacy():
    assert provisioned_key_names("worker-1") == ("xlii-worker-1", "xli-worker-1")


def test_is_provisioned_name():
    assert is_provisioned_name("xlii-worker-1")
    assert is_provisioned_name("xli-worker-1")
    assert not is_provisioned_name("VSCode Agent")
    assert not is_provisioned_name("xlii")  # prefix needs the dash


# --------------------------------------------------------------------------- #
#  D15 — one management-key gate
# --------------------------------------------------------------------------- #

def test_require_management_key_returns_key_when_set():
    assert require_management_key(SimpleNamespace(management_api_key="mgmt")) == "mgmt"


def test_require_management_key_raises_canonical_remediation():
    with pytest.raises(BootstrapError, match="XAI_MANAGEMENT_API_KEY not set"):
        require_management_key(SimpleNamespace(management_api_key=None))


# --------------------------------------------------------------------------- #
#  D5 + small primitives
# --------------------------------------------------------------------------- #

def test_parse_xai_timestamp_trailing_z_and_garbage():
    ts = parse_xai_timestamp("2026-06-14T12:00:00Z")
    assert ts == datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    assert parse_xai_timestamp("not-a-date") is None
    assert parse_xai_timestamp(None) is None
    assert parse_xai_timestamp("") is None


def test_has_key_labeled():
    cfg = SimpleNamespace(keys=[{"label": "journal"}, "sk-bare"])
    assert has_key_labeled(cfg, "journal")
    assert not has_key_labeled(cfg, "worker-1")


def test_team_id_of_shapes():
    assert team_id_of({"teamId": "a"}) == "a"
    assert team_id_of({"team_id": "b"}) == "b"
    assert team_id_of({"id": "c"}) == "c"
    assert team_id_of({}) == ""


# --------------------------------------------------------------------------- #
#  verbs — provision_labeled_key
# --------------------------------------------------------------------------- #

@pytest.fixture
def pinned_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(config, "GLOBAL_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(bootstrap, "GLOBAL_CONFIG_FILE", cfg_file)
    monkeypatch.setattr(vault, "VAULT_FILE", tmp_path / "vault.enc")
    monkeypatch.setattr(vault, "KEY_FILE", tmp_path / ".vault-key")
    monkeypatch.setenv(vault.ENV_VAR, Fernet.generate_key().decode())
    cfg_file.write_text(json.dumps({"keys": []}))
    return cfg_file


def _cfg():
    cfg = GlobalConfig.load()
    cfg.management_api_key = "mgmt"
    return cfg


def test_provision_labeled_key_creates_and_appends(pinned_config, monkeypatch):
    created = {}

    def _create(mgmt, team_id, name, **kw):
        created["name"] = name
        return {"api_key": "sk-new", "apiKeyId": "kid-1", "expireTime": "2027-01-01T00:00:00Z"}

    monkeypatch.setattr(bootstrap, "create_api_key", _create)
    result = provision_labeled_key(_cfg(), "team-1", label="worker-1", expire_days=30)

    assert result.ok
    assert created["name"] == "xlii-worker-1"
    entry = json.loads(pinned_config.read_text())["keys"][0]
    assert "api_key" not in entry
    assert entry["vault_ref"]
    assert entry["api_key_id"] == "kid-1"
    assert entry["expire_time"] == "2027-01-01T00:00:00Z"
    assert "sk-new" not in pinned_config.read_text()
    pairs = GlobalConfig.load().key_pairs()
    assert pairs[0].api_key == "sk-new"


def test_provision_labeled_key_bumps_on_collision(pinned_config, monkeypatch):
    pinned_config.write_text(json.dumps({"keys": [{"label": "worker-1", "api_key": "sk-old"}]}))
    created = {}

    def _create(mgmt, team_id, name, **kw):
        created["name"] = name
        return {"api_key": "sk-n"}

    monkeypatch.setattr(bootstrap, "create_api_key", _create)
    result = provision_labeled_key(_cfg(), "team-1", label="worker-1", expire_days=30)

    assert result.ok
    assert result.label == "worker-2"
    assert created["name"] == "xlii-worker-2"


def test_provision_labeled_key_exact_label_when_bump_false(pinned_config, monkeypatch):
    pinned_config.write_text(json.dumps({"keys": [{"label": "journal", "api_key": "sk-old"}]}))
    created = {}

    def _create(mgmt, team_id, name, **kw):
        created["name"] = name
        return {"api_key": "sk-n"}

    monkeypatch.setattr(bootstrap, "create_api_key", _create)
    result = provision_labeled_key(_cfg(), "team-1", label="journal", expire_days=30, bump=False)

    assert result.ok
    assert result.label == "journal"  # dedicated keys keep their fixed label
    assert created["name"] == "xlii-journal"


def test_provision_labeled_key_api_error_is_structured(pinned_config, monkeypatch):
    def _boom(*a, **k):
        raise BootstrapError("POST … → 500")

    monkeypatch.setattr(bootstrap, "create_api_key", _boom)
    result = provision_labeled_key(_cfg(), "team-1", label="worker-1", expire_days=30)

    assert not result.ok
    assert result.error_kind == "api"
    assert "500" in result.error
    assert json.loads(pinned_config.read_text())["keys"] == []


def test_provision_labeled_key_missing_secret(pinned_config, monkeypatch):
    monkeypatch.setattr(bootstrap, "create_api_key", lambda *a, **k: {"nope": True})
    result = provision_labeled_key(_cfg(), "team-1", label="worker-1", expire_days=30)

    assert not result.ok
    assert result.error_kind == "missing_secret"
    assert result.raw == {"nope": True}
    assert json.loads(pinned_config.read_text())["keys"] == []


# --------------------------------------------------------------------------- #
#  verbs — reconcile_local_keys
# --------------------------------------------------------------------------- #

NOW = datetime(2026, 6, 14, tzinfo=timezone.utc)


def _remote(name, kid, expire=None, disabled=False):
    k = {"name": name, "apiKeyId": kid, "disabled": disabled}
    if expire:
        k["expireTime"] = expire
    return k


def test_reconcile_matches_by_id_and_computes_days_left():
    local = [{"label": "worker-1", "api_key_id": "id-1"}]
    remote = [_remote("xlii-worker-1", "id-1", expire="2026-06-21T00:00:00Z")]
    (st,) = reconcile_local_keys(local, remote, NOW)
    assert st.found and st.name == "xlii-worker-1"
    assert st.days_left == 7
    assert not st.parse_failed


def test_reconcile_name_fallback_including_legacy():
    local = [{"label": "old-1"}, {"label": "older-2"}]
    remote = [_remote("xlii-old-1", "id-a"), _remote("xli-older-2", "id-b")]
    st_new, st_legacy = reconcile_local_keys(local, remote, NOW)
    assert st_new.found and st_new.name == "xlii-old-1"
    assert st_legacy.found and st_legacy.name == "xli-older-2"


def test_reconcile_not_found_and_expired():
    local = [{"label": "ghost", "api_key_id": "id-x"}, {"label": "dead", "api_key_id": "id-y"}]
    remote = [_remote("xlii-dead", "id-y", expire="2026-06-01T00:00:00Z")]
    st_ghost, st_dead = reconcile_local_keys(local, remote, NOW)
    assert not st_ghost.found
    assert st_dead.days_left < 0


def test_reconcile_stored_id_never_falls_back_to_name():
    # a stored id that misses on the server means "not found" — the name
    # fallback exists only for entries with no stored id
    local = [{"label": "worker-1", "api_key_id": "id-stale"}]
    remote = [_remote("xlii-worker-1", "id-live")]
    (st,) = reconcile_local_keys(local, remote, NOW)
    assert not st.found


# --------------------------------------------------------------------------- #
#  verbs — run_setup (state machine, no network)
# --------------------------------------------------------------------------- #

def test_run_setup_skips_when_pool_sufficient(pinned_config, monkeypatch):
    pinned_config.write_text(json.dumps({
        "team_id": "team-1",
        "keys": [{"label": f"k{i}", "api_key": f"sk-{i}"} for i in range(9)],
    }))
    monkeypatch.setattr(
        bootstrap, "create_api_key",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not create")),
    )
    events = []
    rc = bootstrap.run_setup(
        _cfg(), workers=8, expire_days=180, force=False,
        on_event=lambda kind, **p: events.append(kind),
    )
    assert rc == 0
    assert "pool_sufficient" in events
    assert "creating_primary" not in events


def test_run_setup_creates_primary_then_workers(pinned_config, monkeypatch):
    pinned_config.write_text(json.dumps({"team_id": "team-1", "keys": []}))
    names = []
    monkeypatch.setattr(
        bootstrap, "create_api_key",
        lambda mgmt, team_id, name, **kw: names.append(name) or {"api_key": f"sk-{name}"},
    )
    monkeypatch.setattr(bootstrap, "INTER_CREATE_DELAY_SEC", 0)
    monkeypatch.setattr(bootstrap, "discover_models", lambda *a, **k: [])
    events = []
    rc = bootstrap.run_setup(
        _cfg(), workers=2, expire_days=180, force=False,
        on_event=lambda kind, **p: events.append(kind),
    )
    assert rc == 0
    assert names == ["xlii-primary-1", "xlii-worker-1", "xlii-worker-2"]
    labels = [e["label"] for e in json.loads(pinned_config.read_text())["keys"]]
    assert labels == ["primary-1", "worker-1", "worker-2"]
    assert events[:2] == ["team_already_cached", "proceeding"]
    assert "creating_primary" in events and "creating_workers" in events
    assert "models_unavailable" in events  # discovery returned []


def test_run_setup_aborts_on_create_failure(pinned_config, monkeypatch):
    pinned_config.write_text(json.dumps({"team_id": "team-1", "keys": []}))

    def _boom(*a, **k):
        raise BootstrapError("POST … → 429")

    monkeypatch.setattr(bootstrap, "create_api_key", _boom)
    events = []
    rc = bootstrap.run_setup(
        _cfg(), workers=2, expire_days=180, force=False,
        on_event=lambda kind, **p: events.append(kind),
    )
    assert rc == 1
    assert "key_failed" in events
    assert json.loads(pinned_config.read_text())["keys"] == []


def test_provision_worker_keys_saves_prior_keys_when_parse_fails(pinned_config, monkeypatch):
    """A later malformed create response must not orphan earlier secrets."""
    pinned_config.write_text(json.dumps({"keys": []}))
    responses = [
        {"api_key": "sk-worker-1", "apiKeyId": "id-worker-1"},
        {"apiKeyId": "id-worker-2"},
    ]

    def _create(mgmt, team_id, name, **kw):
        return responses.pop(0)

    monkeypatch.setattr(bootstrap, "create_api_key", _create)
    monkeypatch.setattr(bootstrap, "INTER_CREATE_DELAY_SEC", 0)
    events = []

    rc = bootstrap.provision_worker_keys(
        _cfg(),
        "team-1",
        prefix="worker",
        count=2,
        expire_days=180,
        force=False,
        on_event=lambda kind, **p: events.append((kind, p)),
    )

    assert rc == 1
    keys = json.loads(pinned_config.read_text())["keys"]
    assert len(keys) == 1
    assert keys[0]["label"] == "worker-1"
    assert keys[0]["api_key_id"] == "id-worker-1"
    assert "api_key" not in keys[0]
    assert keys[0]["vault_ref"]
    assert "sk-worker-1" not in pinned_config.read_text()
    assert ("aborted", {"saved": 1}) in events
