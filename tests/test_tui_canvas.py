"""Track C — transcript canvas vs trim chrome."""

from __future__ import annotations

from xlii.tui.canvas import (
    canvas_paper_color,
    normalize_canvas,
    resolve_theme_for_canvas,
    theme_polarity,
    themes_matching_canvas,
)


def test_normalize_canvas_defaults_unknown_to_dark():
    assert normalize_canvas("dark") == "dark"
    assert normalize_canvas("light") == "light"
    assert normalize_canvas("paper") == "dark"
    assert normalize_canvas(None) == "dark"


def test_canvas_paper_colors():
    assert canvas_paper_color("dark") == "#0a0a0a"
    assert canvas_paper_color("light") == "#fafafa"


def test_theme_polarity_name_heuristics():
    assert theme_polarity("textual-dark") == "dark"
    assert theme_polarity("textual-light") == "light"
    assert theme_polarity("catppuccin-latte") == "light"
    assert theme_polarity("nord") == "dark"
    assert theme_polarity("rose-pine-dawn") == "light"


def test_themes_matching_canvas_filters_opposite_polarity():
    all_names = ["textual-dark", "textual-light", "nord", "solarized-light"]
    assert themes_matching_canvas(all_names, "dark") == [
        "nord",
        "textual-dark",
    ]
    assert themes_matching_canvas(all_names, "light") == [
        "solarized-light",
        "textual-light",
    ]


def test_resolve_theme_for_canvas_prefers_matching_saved():
    avail = ["textual-dark", "textual-light", "nord"]
    assert resolve_theme_for_canvas("nord", "dark", avail) == "nord"
    assert resolve_theme_for_canvas("nord", "light", avail) == "textual-light"
    assert resolve_theme_for_canvas("", "light", avail) == "textual-light"
    assert resolve_theme_for_canvas("", "dark", avail) == "textual-dark"
