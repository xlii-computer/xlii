"""Kernel-side curation export/import (B8)."""

from __future__ import annotations

import json

import pytest

import xlii.curation as curation


@pytest.fixture()
def fake_config(tmp_path, monkeypatch):
    """Point curation's config-dir constants at a throwaway tree."""
    cfg = tmp_path / "cfg"
    personas = cfg / "personas"
    chat_state = cfg / "chat-state"
    personas.mkdir(parents=True)
    chat_state.mkdir(parents=True)
    monkeypatch.setattr(curation, "GLOBAL_CONFIG_DIR", cfg)
    monkeypatch.setattr(curation, "PERSONAS_DIR", personas)
    monkeypatch.setattr(curation, "CHAT_STATE_DIR", chat_state)
    return cfg


# --- export_curation ---------------------------------------------------------


def test_export_curation_layout_and_manifest(fake_config, tmp_path):
    (fake_config / "personas" / "sam.md").write_text("persona")
    (fake_config / "chat-state" / "sam").mkdir()
    (fake_config / "chat-state" / "sam" / "turn-1.json").write_text("{}")
    (fake_config / "docs").mkdir()
    (fake_config / "docs" / "ref.md").write_text("doc")
    (fake_config / "plugins").mkdir()
    (fake_config / "plugins" / "weather.md").write_text("plugin")
    (fake_config / "projects.json").write_text("{}")

    dest = tmp_path / "backup"
    counts = curation.export_curation(dest)

    assert counts == {"personas": 1, "persona-memory": 1, "docs": 1,
                      "plugins": 1, "registry": 1}
    manifest = json.loads((dest / "export.json").read_text())
    assert manifest["format"] == 1
    assert manifest["tool"].startswith("xlii ")
    assert manifest["counts"] == counts
    assert "config.json (API keys)" in manifest["excluded"]
    assert (dest / "personas" / "sam.md").read_text() == "persona"
    assert (dest / "registry" / "projects.json").exists()


def test_export_curation_refuses_non_empty_dest(fake_config, tmp_path):
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "stale.txt").write_text("x")
    with pytest.raises(ValueError, match="not empty"):
        curation.export_curation(dest)


def test_export_curation_empty_config_exports_nothing(fake_config, tmp_path):
    # No personas/chat-state dirs on disk at all → nothing to copy.
    (fake_config / "personas").rmdir()
    (fake_config / "chat-state").rmdir()
    counts = curation.export_curation(tmp_path / "empty-backup")
    assert counts == {}


# --- import_curation ---------------------------------------------------------


def test_import_curation_round_trip(fake_config, tmp_path):
    (fake_config / "docs").mkdir()
    (fake_config / "docs" / "ref.md").write_text("doc v1")
    (fake_config / "projects.json").write_text('{"a": 1}')
    src = tmp_path / "backup"
    curation.export_curation(src)

    # Wipe the live config, then restore into it.
    (fake_config / "docs" / "ref.md").unlink()
    restored, skipped, registry_ref = curation.import_curation(src)
    assert (restored, skipped) == (1, 0)
    assert (fake_config / "docs" / "ref.md").read_text() == "doc v1"
    # The old machine's registry lands as a reference copy, never live.
    assert registry_ref == fake_config / "projects.imported.json"
    assert json.loads(registry_ref.read_text()) == {"a": 1}


def test_import_curation_keeps_existing_unless_force(fake_config, tmp_path):
    (fake_config / "docs").mkdir()
    (fake_config / "docs" / "ref.md").write_text("doc v1")
    src = tmp_path / "backup"
    curation.export_curation(src)

    (fake_config / "docs" / "ref.md").write_text("doc v2 — edited")
    restored, skipped, _ = curation.import_curation(src)
    assert (restored, skipped) == (0, 1)
    assert (fake_config / "docs" / "ref.md").read_text() == "doc v2 — edited"

    restored, skipped, _ = curation.import_curation(src, force=True)
    assert (restored, skipped) == (1, 0)
    assert (fake_config / "docs" / "ref.md").read_text() == "doc v1"


def test_import_curation_rejects_non_export(tmp_path):
    with pytest.raises(ValueError, match="not an xlii export"):
        curation.import_curation(tmp_path)
