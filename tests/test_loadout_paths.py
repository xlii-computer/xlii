"""Global loadout path helpers."""

from xlii.loadout_paths import migrate_global_loadouts


def test_migrate_global_loadouts_dry_run(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "shared.json").write_text('{"version": 1}')
    canon = tmp_path / "canon"
    monkeypatch.setattr("xlii.loadout_paths.LEGACY_GLOBAL_LOADOUTS_DIR", legacy)
    monkeypatch.setattr("xlii.loadout_paths.GLOBAL_LOADOUTS_DIR", canon)

    lines = migrate_global_loadouts(dry_run=True)
    assert any("would migrate" in ln for ln in lines)
    assert not canon.exists()
