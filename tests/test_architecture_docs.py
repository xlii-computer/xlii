"""Phase 6: architecture discoverable from the repo root."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_architecture_md_exists_and_covers_core():
    text = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    for needle in (
        "SessionState",
        "ModeController",
        "control flow",
        "GOLDEN-PATH",
        "LEGACY",
        "dispatch_subagent",
        "ROADMAP",
    ):
        assert needle in text, f"ARCHITECTURE.md missing {needle!r}"


def test_readme_and_overview_link_architecture():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    overview = (ROOT / "docs" / "OVERVIEW.md").read_text(encoding="utf-8")
    assert "ARCHITECTURE.md" in readme
    assert "ARCHITECTURE.md" in overview
    assert "ROADMAP.md" in readme or "ROADMAP.md" in overview


def test_roadmap_banner_points_at_architecture():
    rm = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    assert "ARCHITECTURE.md" in rm
    assert "GOLDEN-PATH.md" in rm
