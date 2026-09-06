"""Mojo-keeper Phase B — shipped self-doc fused into mojo ambient."""

from __future__ import annotations

from types import SimpleNamespace

from xlii.repl_cmds.mojo import build_mojo_ambient


def _home_state():
    return SimpleNamespace(
        project=SimpleNamespace(name="scratch/home", xli_dir=None, project_root=None),
        journal=None,
        workbench=None,
    )


def test_product_question_fuses_shipped_selfdoc():
    out = build_mojo_ambient(_home_state(), "how do I start a project")
    assert "shipped self-doc" in out
    assert "command-surface" in out or "desktop-face" in out


def test_non_product_question_has_no_selfdoc_shard():
    out = build_mojo_ambient(_home_state(), "what's the weather")
    assert "shipped self-doc" not in out


def test_missing_selfwiki_leaves_ambient_unchanged(monkeypatch):
    monkeypatch.setattr("xlii.selfwiki.selfwiki_root", lambda: None)
    state = _home_state()
    # Desk banner may fill ambient when selfwiki is gone; still no selfwiki shards.
    out = build_mojo_ambient(state, "how do I start a project")
    assert "shipped self-doc" not in out
    assert "command-surface" not in out
    assert "desktop-face" not in out
    journal = SimpleNamespace(
        recall_context=lambda q, wiki_context="": f"JOURNAL[{q}]+{wiki_context}")
    state.journal = journal
    out = build_mojo_ambient(state, "how do I start a project")
    assert "JOURNAL[how do I start a project]+" in out
    assert "shipped self-doc" not in out
