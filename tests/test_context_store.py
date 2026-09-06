"""Global DeepContext store — canonical (~/.config/xlii/contexts), mirroring
xlii/loadout_paths.py.

A legacy ~/.xli/contexts tree is DRAINED into canonical on read (moved, canonical
wins on a clash); there is no permanent fallback. Writes target canonical; delete
clears canonical (the legacy shadow is already drained, so it can't resurface).
The explicit `migrate_global_contexts` (used by `xlii diag`) copies, not moves.
All disk-only / no network.
"""

import json

from xlii import context as C


def _patch_dirs(monkeypatch, tmp_path):
    canon = tmp_path / "canon"
    legacy = tmp_path / "legacy"
    monkeypatch.setattr(C, "GLOBAL_CONTEXTS_DIR", canon)
    monkeypatch.setattr(C, "LEGACY_GLOBAL_CONTEXTS_DIR", legacy)
    return canon, legacy


def _write_ctx(d, name, doc):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps({
        "id": name,
        "name": name,
        "source_project_path": "/tmp/none",
        "source_workspace_name": "main",
        "attached_docs": [[doc, "BODY"]],
        "version": 2,
    }))


def test_save_writes_to_canonical_only(monkeypatch, tmp_path):
    canon, legacy = _patch_dirs(monkeypatch, tmp_path)
    ctx = C.DeepContext(id="a", name="a",
                        source_project_path="/tmp/none", source_workspace_name="main")
    C.save_deep_context(ctx)
    assert (canon / "a.json").exists()
    assert not legacy.exists()


def test_list_includes_drained_legacy_contexts(monkeypatch, tmp_path):
    # Legacy-only contexts are drained into canonical on read, so they list.
    canon, legacy = _patch_dirs(monkeypatch, tmp_path)
    _write_ctx(legacy, "old", "legacy-doc")
    _write_ctx(canon, "new", "canon-doc")
    assert C.list_deep_contexts() == ["new", "old"]


def test_load_finds_legacy_only_name(monkeypatch, tmp_path):
    canon, legacy = _patch_dirs(monkeypatch, tmp_path)
    _write_ctx(legacy, "old", "legacy-doc")
    ctx = C.load_deep_context("old")
    assert ctx is not None
    assert [n for n, _ in ctx.attached_docs] == ["legacy-doc"]


def test_canonical_wins_over_legacy_shadow_on_drain(monkeypatch, tmp_path):
    # Same name in both: the drain keeps canonical and drops the legacy shadow.
    canon, legacy = _patch_dirs(monkeypatch, tmp_path)
    _write_ctx(legacy, "dup", "legacy-doc")
    _write_ctx(canon, "dup", "canon-doc")
    ctx = C.load_deep_context("dup")
    assert ctx is not None
    assert [n for n, _ in ctx.attached_docs] == ["canon-doc"]


def test_delete_clears_canonical_and_drained_legacy(monkeypatch, tmp_path):
    # The legacy shadow is drained on read, so one canonical delete fully removes
    # the name — it can't resurface from a lingering legacy file.
    canon, legacy = _patch_dirs(monkeypatch, tmp_path)
    _write_ctx(legacy, "dup", "legacy-doc")
    _write_ctx(canon, "dup", "canon-doc")
    assert C.list_deep_contexts() == ["dup"]

    assert C.delete_deep_context("dup") is True
    assert not (canon / "dup.json").exists()
    assert not (legacy / "dup.json").exists()
    assert C.list_deep_contexts() == []

    assert C.delete_deep_context("dup") is False


def test_migrate_copies_legacy_to_canonical_skipping_existing(monkeypatch, tmp_path):
    canon, legacy = _patch_dirs(monkeypatch, tmp_path)
    _write_ctx(legacy, "old", "legacy-doc")
    _write_ctx(canon, "keep", "canon-doc")
    _write_ctx(legacy, "keep", "legacy-shadow")   # same name already canonical → skip

    log = C.migrate_global_contexts()

    assert (canon / "old.json").exists()                       # migrated
    assert (legacy / "old.json").exists()                      # copy, not move
    assert any("old.json" in ln and "migrated" in ln for ln in log)
    assert any("keep.json" in ln and "skip" in ln for ln in log)
    # canonical 'keep' untouched — the legacy shadow did not overwrite it
    kept = json.loads((canon / "keep.json").read_text())
    assert [d[0] for d in kept["attached_docs"]] == ["canon-doc"]


def test_migrate_dry_run_writes_nothing(monkeypatch, tmp_path):
    canon, legacy = _patch_dirs(monkeypatch, tmp_path)
    _write_ctx(legacy, "old", "legacy-doc")
    log = C.migrate_global_contexts(dry_run=True)
    assert not (canon / "old.json").exists()
    assert any("would migrate" in ln for ln in log)


def test_migrate_noop_when_no_legacy(monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    assert C.migrate_global_contexts() == []


def test_legacy_migrate_all_includes_contexts(monkeypatch, tmp_path):
    # The aggregator wires the contexts migration alongside loadouts.
    import xlii.legacy_migrate as LM
    canon, legacy = _patch_dirs(monkeypatch, tmp_path)
    _write_ctx(legacy, "old", "legacy-doc")
    log = LM.migrate_all(dry_run=True)
    assert any("old.json" in ln for ln in log)
