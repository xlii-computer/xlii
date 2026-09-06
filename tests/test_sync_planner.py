import json

import pytest

from xlii import sync as sync_mod
from xlii.config import GlobalConfig, ProjectConfig
from xlii.sync import sync_project


@pytest.fixture
def project(tmp_path):
    d = tmp_path / ".xlii"
    d.mkdir()
    (d / "project.json").write_text(json.dumps({
        "name": "t", "root": str(tmp_path.resolve()), "collection_id": "c1",
        "created_at": "2026-01-01", "conversation_id": "x", "local_only": False,
    }))
    return ProjectConfig.load(tmp_path)


def _remote(paths):
    return {p: {"file_id": f"id-{p}", "sha256": "0" * 64, "name": p} for p in paths}


def test_sha_skip_path(project, tmp_path, monkeypatch):
    from xlii.sync import hash_file
    (tmp_path / "a.py").write_text("print(1)")
    sha = hash_file(tmp_path / "a.py")
    remote = {"a.py": {"file_id": "id-a", "sha256": sha, "name": "a.py"}}
    monkeypatch.setattr(sync_mod, "fetch_collection_state", lambda c, cid: remote)
    stats = sync_project(None, project, GlobalConfig())
    assert stats.unchanged == 1
    assert stats.uploaded == stats.updated == stats.deleted == 0


def test_dry_run_lists_exact_paths(project, tmp_path, monkeypatch):
    (tmp_path / "new.py").write_text("x")
    monkeypatch.setattr(sync_mod, "fetch_collection_state",
                        lambda c, cid: _remote(["gone.py"]))
    stats = sync_project(None, project, GlobalConfig(), dry_run=True)
    assert stats.planned["upload"] == ["new.py"]
    assert stats.planned["delete"] == ["gone.py"]


def test_delete_guard_blocks_empty_local_scan(project, monkeypatch):
    # No local files at all, remote has docs → must refuse to delete.
    monkeypatch.setattr(sync_mod, "fetch_collection_state",
                        lambda c, cid: _remote([f"f{i}.py" for i in range(3)]))
    stats = sync_project(None, project, GlobalConfig())
    assert stats.deleted == 0
    assert any("delete-guard" in e for e in stats.errors)


def test_delete_guard_threshold_requires_confirmation(project, tmp_path, monkeypatch):
    (tmp_path / "keep.py").write_text("x")
    remote = _remote([f"f{i}.py" for i in range(15)])
    monkeypatch.setattr(sync_mod, "fetch_collection_state", lambda c, cid: remote)
    # keep.py would upload — stub the network ops so no real calls happen
    monkeypatch.setattr(sync_mod, "_do_upload", lambda *a, **k: "fake-id")
    deleted_ids = []
    monkeypatch.setattr(sync_mod, "_do_delete", lambda c, cid, fid: deleted_ids.append(fid))

    # Without a confirmer: skipped
    stats = sync_project(None, project, GlobalConfig())
    assert stats.deleted == 0
    assert deleted_ids == []
    assert any("delete-guard" in e for e in stats.errors)

    # Confirmer declines: skipped
    stats = sync_project(None, project, GlobalConfig(), confirm_deletes=lambda paths: False)
    assert stats.deleted == 0
    assert deleted_ids == []

    # Confirmer approves: deletions run
    stats = sync_project(None, project, GlobalConfig(), confirm_deletes=lambda paths: True)
    assert stats.deleted == 15
    assert len(deleted_ids) == 15


def test_small_deletions_pass_without_confirmation(project, tmp_path, monkeypatch):
    (tmp_path / "keep.py").write_text("x")
    monkeypatch.setattr(sync_mod, "fetch_collection_state",
                        lambda c, cid: _remote(["old.py"]))
    monkeypatch.setattr(sync_mod, "_do_upload", lambda *a, **k: "fake-id")
    monkeypatch.setattr(sync_mod, "_do_delete", lambda *a, **k: None)
    stats = sync_project(None, project, GlobalConfig())
    assert stats.deleted == 1
    assert not stats.errors


def test_rate_limit_retry_uses_injectable_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(sync_mod, "_sleep", sleeps.append)
    monkeypatch.setattr(sync_mod, "_is_rate_limited", lambda e: True)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429")
        return "ok"

    assert sync_mod._with_rate_limit_retry(flaky) == "ok"
    assert sleeps == [2, 3]  # 2**0+1, 2**1+1 — no real time elapsed


def test_transient_internal_errors_are_retried(monkeypatch):
    sleeps = []
    monkeypatch.setattr(sync_mod, "_sleep", sleeps.append)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("INTERNAL: Failed to create storage ledger file (update_document)")
        return "ok"

    assert sync_mod._with_rate_limit_retry(flaky) == "ok"
    assert sleeps == [2]


def test_non_transient_errors_are_not_retried(monkeypatch):
    sleeps = []
    monkeypatch.setattr(sync_mod, "_sleep", sleeps.append)

    def fatal():
        raise RuntimeError("PERMISSION_DENIED: key expired")

    with pytest.raises(RuntimeError):
        sync_mod._with_rate_limit_retry(fatal)
    assert sleeps == []
