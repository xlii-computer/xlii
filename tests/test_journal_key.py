"""The dedicated journal key — isolated, auditable spend.

A key labeled `journal` (provisioned via `xlii journal key`) is kept out of the
orchestrator + worker rotation (pool.primary/acquire) and handed only to the
journal, so its LLM spend shows up separately on the xAI dashboard.
"""

from __future__ import annotations

import argparse
import json
from types import SimpleNamespace

import pytest

import xlii.config as C
from xlii.config import JOURNAL_KEY_LABEL
from xlii.journal import ProjectJournal
from xlii.pool import ClientPool


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch, tmp_path):
    """Pin the config path to a per-test tmp dir at the MODULE-ATTR level.

    tests/conftest.py already redirects XLII_CONFIG_DIR/HOME for pytest runs,
    but this file's helpers write the config path directly — executed under any
    other runner (bare python, an agent replaying a test's logic), a frozen
    from-import of GLOBAL_CONFIG_FILE points at the operator's REAL
    ~/.config/xlii/config.json and clobbers it (2026-07-20 incident: a 12-byte
    ``{"keys": []}`` over the live config). Never freeze the constant; always
    resolve through ``xlii.config`` at call time, pinned here per test."""
    cdir = tmp_path / "xlii-config"
    monkeypatch.setattr(C, "GLOBAL_CONFIG_DIR", cdir)
    monkeypatch.setattr(C, "GLOBAL_CONFIG_FILE", cdir / "config.json")
    # Consumers that froze their own copy of the constant at import time —
    # each must be re-pointed or their reads/writes land on a different file
    # than the one this file asserts against (xlii.bootstrap's
    # provision_labeled_key writes GLOBAL_CONFIG_FILE directly).
    import xlii.bootstrap as B
    import xlii.cmds.journal as J
    import xlii.vault as V
    from cryptography.fernet import Fernet

    monkeypatch.setattr(B, "GLOBAL_CONFIG_FILE", cdir / "config.json", raising=False)
    monkeypatch.setattr(J, "GLOBAL_CONFIG_DIR", cdir, raising=False)
    monkeypatch.setattr(V, "VAULT_FILE", cdir / "vault.enc")
    monkeypatch.setattr(V, "KEY_FILE", cdir / ".vault-key")
    monkeypatch.setenv(V.ENV_VAR, Fernet.generate_key().decode())
    yield


def _c(label):
    return SimpleNamespace(label=label)


# --------------------------------------------------------------------------- #
#  pool: the journal key is reserved
# --------------------------------------------------------------------------- #

def test_journal_client_is_resolved_by_label():
    pool = ClientPool(clients=[_c("primary"), _c("worker-1"), _c(JOURNAL_KEY_LABEL)])
    assert pool.journal_client().label == JOURNAL_KEY_LABEL


def test_no_journal_key_means_none():
    pool = ClientPool(clients=[_c("primary"), _c("worker-1")])
    assert pool.journal_client() is None


def test_primary_never_returns_the_journal_key():
    # Even with the journal key first (unusual ordering), the orchestrator/sync
    # must not spend it.
    pool = ClientPool(clients=[_c(JOURNAL_KEY_LABEL), _c("primary")])
    assert pool.primary().label == "primary"


def test_acquire_excludes_the_journal_key():
    pool = ClientPool(clients=[_c("primary"), _c("worker-1"), _c("worker-2"),
                               _c(JOURNAL_KEY_LABEL)])
    handed = {pool.acquire().label for _ in range(30)}
    assert JOURNAL_KEY_LABEL not in handed
    assert handed <= {"worker-1", "worker-2"}   # workers only; primary reserved


def test_acquire_falls_back_to_primary_when_only_journal_besides_primary():
    pool = ClientPool(clients=[_c("primary"), _c(JOURNAL_KEY_LABEL)])
    handed = {pool.acquire().label for _ in range(10)}
    assert handed == {"primary"}                 # never the journal key


# --------------------------------------------------------------------------- #
#  journal prefers the dedicated key, falls back to primary
# --------------------------------------------------------------------------- #

def test_journal_uses_dedicated_key_when_present():
    primary, jc = _c("primary"), _c(JOURNAL_KEY_LABEL)
    j = ProjectJournal(project=SimpleNamespace(), pool=ClientPool(clients=[primary, jc]))
    assert j._clients() is jc
    assert j.using_dedicated_key() is True


def test_journal_falls_back_to_primary_without_dedicated_key():
    primary = _c("primary")
    j = ProjectJournal(project=SimpleNamespace(), pool=ClientPool(clients=[primary]))
    assert j._clients() is primary
    assert j.using_dedicated_key() is False


# --------------------------------------------------------------------------- #
#  provisioning: `xlii journal key`
# --------------------------------------------------------------------------- #

def _write_config(keys):
    # Resolve through the module at call time — a frozen from-import here is
    # exactly what wrote an empty config over the operator's real file.
    C.GLOBAL_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    C.GLOBAL_CONFIG_FILE.write_text(json.dumps({"keys": keys}))


def test_journal_key_provisions_a_labeled_key(monkeypatch):
    import xlii.bootstrap as B
    from xlii.cmds.journal import cmd_journal_key

    _write_config([])
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "mgmt-secret")
    monkeypatch.setattr(B, "discover_team_id", lambda cfg: "team-1")
    monkeypatch.setattr(B, "create_api_key",
                        lambda *a, **k: {"api_key": "sk-journal", "apiKeyId": "kid-1"})

    rc = cmd_journal_key(argparse.Namespace(force=False, expire_days=None))
    assert rc == 0
    keys = json.loads(C.GLOBAL_CONFIG_FILE.read_text())["keys"]
    journal_keys = [e for e in keys if e.get("label") == JOURNAL_KEY_LABEL]
    assert len(journal_keys) == 1
    assert "api_key" not in journal_keys[0]
    assert journal_keys[0]["vault_ref"]
    assert "sk-journal" not in C.GLOBAL_CONFIG_FILE.read_text()


def test_journal_key_is_idempotent(monkeypatch):
    import xlii.bootstrap as B
    from xlii.cmds.journal import cmd_journal_key

    _write_config([{"api_key": "sk-existing", "label": JOURNAL_KEY_LABEL}])
    monkeypatch.setenv("XAI_MANAGEMENT_API_KEY", "mgmt-secret")

    def _boom(*a, **k):
        raise AssertionError("create_api_key must NOT be called when one exists")

    monkeypatch.setattr(B, "create_api_key", _boom)
    rc = cmd_journal_key(argparse.Namespace(force=False, expire_days=None))
    assert rc == 0   # skipped cleanly, no second key created


def test_journal_key_requires_management_key(monkeypatch):
    from xlii.cmds.journal import cmd_journal_key
    _write_config([])
    monkeypatch.delenv("XAI_MANAGEMENT_API_KEY", raising=False)
    assert cmd_journal_key(argparse.Namespace(force=False, expire_days=None)) == 1


# --------------------------------------------------------------------------- #
#  JRN-2 — opt-in bash-wide capture. Full behavior (install/uninstall/serve,
#  the bash hook, feed tailing) is covered in test_journal_daemon.py; here we
#  just assert the CLI surface exists and the safe control paths never block.
# --------------------------------------------------------------------------- #

def test_jrn2_subcommands_are_registered():
    from xlii.cli import build_parser
    sub = next(a for a in build_parser()._actions
               if a.__class__.__name__ == "_SubParsersAction")
    journal_sub = sub.choices["journal"]
    actions = next(a for a in journal_sub._actions
                   if a.__class__.__name__ == "_SubParsersAction")
    for name in ("key", "install", "uninstall", "serve"):
        assert name in actions.choices


def test_jrn2_serve_stop_is_noop_when_not_running(tmp_path, monkeypatch):
    import argparse as _ap
    from xlii.cmds.journal import cmd_journal_serve
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    # --stop must return cleanly (and never block) when no daemon is running.
    assert cmd_journal_serve(_ap.Namespace(stop=True, once=False)) == 0
