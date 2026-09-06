"""Coverage for pool.is_auth_failure — the single classifier deciding whether
an exception is auth-shaped (quarantine the key) vs transient (keep it). This
heuristic was previously duplicated verbatim in agent._run_worker and
review._spawn_reviewer; a security boundary, so it gets its own lock."""

import pytest

from xlii.pool import is_auth_failure


@pytest.mark.parametrize("msg", [
    "HTTP 401 Unauthorized",
    "403 Forbidden",
    "unauthorized",
    "permission denied",
    "API key expired",
    "unauthenticated request",
    "RuntimeError: 401",
    # xAI/OpenAI reject a bad key as INVALID_ARGUMENT, not 401 — observed live
    # in a startup-sync failure. Must still be recognized as auth-shaped.
    ("INVALID_ARGUMENT: Incorrect API key provided: xa***zD. "
     "You can obtain an API key from https://console.x.ai."),
    "Invalid API key",
])
def test_auth_shaped_failures_quarantine(msg):
    assert is_auth_failure(msg) is True
    assert is_auth_failure(RuntimeError(msg)) is True  # exception object, not just str


@pytest.mark.parametrize("msg", [
    "connection reset by peer",
    "read timed out",
    "500 Internal Server Error",
    "503 Service Unavailable",
    "name resolution failed",
    # a non-auth INVALID_ARGUMENT must stay non-auth (the new markers are
    # specific to the "incorrect/invalid api key" wording, not INVALID_ARGUMENT)
    "INVALID_ARGUMENT: collection_id must be a valid UUID",
    "",
])
def test_transient_failures_do_not_quarantine(msg):
    assert is_auth_failure(msg) is False
    assert is_auth_failure(RuntimeError(msg)) is False


def test_case_insensitive():
    assert is_auth_failure("UNAUTHORIZED") is True
    assert is_auth_failure("Expired Token") is True


def test_rebuild_from_config_swaps_clients_in_place(monkeypatch):
    from xlii.config import GlobalConfig
    from xlii.pool import ClientPool

    monkeypatch.delenv("XAI_REGION", raising=False)
    cfg = GlobalConfig(keys=["xai-test-key"])
    pool = ClientPool.from_config(cfg, require_management=False)
    original_list = pool.clients
    assert str(pool.clients[0].chat.base_url).startswith("https://api.x.ai/v1")

    cfg.region = "us-west-2"
    pool.rebuild_from_config(cfg)
    # same list object (everyone holding the pool sees the swap), new edge
    assert pool.clients is original_list
    assert str(pool.clients[0].chat.base_url).startswith("https://us-west-2.api.x.ai/v1")
    assert pool._next == 0


def test_rebuild_from_config_preserves_quarantine_and_gates_zero_keys(monkeypatch):
    from xlii.config import GlobalConfig
    from xlii.pool import ClientPool, MissingCredentials

    monkeypatch.delenv("XAI_REGION", raising=False)
    cfg = GlobalConfig(keys=["xai-test-key"])
    pool = ClientPool.from_config(cfg, require_management=False)
    # quarantine records KEY-badness — a region flip must not clear it
    pool._failures["primary"] = 3
    cfg.region = "eu-west-1"
    pool.rebuild_from_config(cfg)
    assert pool._failures == {"primary": 3}

    # zero keys raise BEFORE the swap — the live pool stays usable
    before = list(pool.clients)
    empty = GlobalConfig(keys=[])
    with pytest.raises(MissingCredentials):
        pool.rebuild_from_config(empty)
    assert pool.clients == before


def test_from_config_vault_failure_is_missing_credentials_not_traceback(monkeypatch):
    from xlii.client import MissingCredentials
    from xlii.config import GlobalConfig
    from xlii.pool import ClientPool

    cfg = GlobalConfig(keys=[{"vault_ref": "primary", "label": "primary"}])

    def _boom():
        raise RuntimeError(
            "a chat key is vault-backed but the vault could not be opened: no master key"
        )

    monkeypatch.setattr(cfg, "key_pairs", _boom)
    with pytest.raises(MissingCredentials, match="vault"):
        ClientPool.from_config(cfg, require_management=False)
