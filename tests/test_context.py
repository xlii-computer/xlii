import json

from xlii import context as deep_context


def test_mcp_sync_context_writes_back_to_xlii_session(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    xli_dir = project_root / ".xlii"
    xli_dir.mkdir(parents=True)
    (xli_dir / "session.json").write_text(json.dumps({
        "version": 2,
        "current_workspace": "main",
        "workspaces": {
            "main": {
                "attached_refs": [["old-ref", "old-cid"]],
                "attached_docs": [["old-doc", "old body"]],
            }
        },
    }))
    monkeypatch.setattr(deep_context, "GLOBAL_CONTEXTS_DIR", tmp_path / "contexts")

    ctx = deep_context.DeepContext(
        id="ctx-1",
        name="repo-main",
        source_project_path=str(project_root),
        source_workspace_name="main",
    )
    deep_context.save_deep_context(ctx)

    deep_context.mcp_sync_context("repo-main", {
        "attached_refs": [["new-ref", "new-cid"]],
        "attached_docs": [["new-doc", "new body"]],
    })

    session = json.loads((xli_dir / "session.json").read_text())
    workspace = session["workspaces"]["main"]
    assert workspace["attached_refs"] == [["new-ref", "new-cid"]]
    assert workspace["attached_docs"] == [["new-doc", "new body"]]
    assert workspace["last_synced_from_deep_context"]
    assert not (project_root / ".xli").exists()


def test_mcp_sync_context_auto_migrates_legacy_xli_session(tmp_path, monkeypatch):
    # A pre-rename `.xli` project state dir is auto-migrated to `.xlii` when the
    # MCP bridge resolves it (_project_xli_dir), so write-back lands in the
    # canonical dir and the legacy dir is gone — no permanent legacy fallback.
    project_root = tmp_path / "repo"
    legacy_dir = project_root / ".xli"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "session.json").write_text(json.dumps({
        "version": 2,
        "current_workspace": "main",
        "workspaces": {
            "main": {
                "attached_refs": [["old-ref", "old-cid"]],
                "attached_docs": [["old-doc", "old body"]],
            }
        },
    }))
    monkeypatch.setattr(deep_context, "GLOBAL_CONTEXTS_DIR", tmp_path / "contexts")

    ctx = deep_context.DeepContext(
        id="ctx-1",
        name="repo-main",
        source_project_path=str(project_root),
        source_workspace_name="main",
    )
    deep_context.save_deep_context(ctx)

    deep_context.mcp_sync_context("repo-main", {
        "attached_refs": [["new-ref", "new-cid"]],
        "attached_docs": [["new-doc", "new body"]],
    })

    # Write-back targets the canonical .xlii/ dir; the legacy .xli/ is migrated away.
    canonical = project_root / ".xlii"
    session = json.loads((canonical / "session.json").read_text())
    workspace = session["workspaces"]["main"]
    assert workspace["attached_refs"] == [["new-ref", "new-cid"]]
    assert workspace["attached_docs"] == [["new-doc", "new body"]]
    assert workspace["last_synced_from_deep_context"]
    assert not legacy_dir.exists()


def test_sync_attachments_logs_warning_on_writeback_failure(tmp_path, monkeypatch):
    project_root = tmp_path / "repo"
    xli_dir = project_root / ".xlii"
    xli_dir.mkdir(parents=True)
    (xli_dir / "session.json").write_text(json.dumps({"workspaces": {"main": {}}}))

    ctx = deep_context.DeepContext(
        id="ctx-1",
        name="repo-main",
        source_project_path=str(project_root),
        source_workspace_name="main",
    )
    msgs = []
    monkeypatch.setattr(
        deep_context, "write_text_atomic", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    monkeypatch.setattr(deep_context.logger, "warning", lambda *a, **k: msgs.append(a))

    deep_context._sync_attachments_back_to_source_workspace(ctx)
    assert msgs
    assert "Failed to write DeepContext changes back to source workspace" in msgs[0][0]
