"""Golden first-hour spine exists and stays discoverable (grades plan Phase 3)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_golden_path_md_exists_and_has_spine():
    p = ROOT / "docs" / "GOLDEN-PATH.md"
    assert p.is_file(), "docs/GOLDEN-PATH.md missing"
    text = p.read_text(encoding="utf-8")
    for needle in (
        "xlii doctor",
        "xlii setup",
        "/help",
        "/plan",
        "/execute",
        "open-meteo",
        "xlii chat",
        "xlii export",
        "first hour",
    ):
        assert needle in text, f"missing {needle!r} in GOLDEN-PATH.md"


def test_howto_points_at_golden_path():
    howto = (ROOT / "docs" / "HOWTO.md").read_text(encoding="utf-8")
    assert "GOLDEN-PATH.md" in howto
    assert "The first hour" in howto


def test_readme_points_at_golden_path():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "GOLDEN-PATH.md" in readme
