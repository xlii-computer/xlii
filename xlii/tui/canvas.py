"""Transcript canvas vs trim chrome (Track C).

The **canvas** is the Screen + ``#log`` paper (dark or light). **Trim** stays on the
existing Textual ``tui_theme`` — input, panels, menus, status. Themes are filtered
to the canvas polarity so trim ink stays readable on the paper.
"""

from __future__ import annotations

from typing import Iterable

CANVAS_MODES = ("dark", "light")

# Paper colors — fixed literals, not theme tokens (the canvas is independent of trim).
CANVAS_PAPER: dict[str, str] = {
    "dark": "#0a0a0a",
    "light": "#fafafa",
}

# Name heuristics for built-in Textual themes (option A contrast policy).
_LIGHT_THEME_HINTS = ("-light", "latte", "dawn", "solarized-light")


def normalize_canvas(mode: str | None) -> str:
    """Return ``dark`` or ``light``; unknown values fall back to ``dark``."""
    m = (mode or "dark").strip().lower()
    return m if m in CANVAS_MODES else "dark"


def canvas_paper_color(mode: str | None) -> str:
    return CANVAS_PAPER[normalize_canvas(mode)]


def theme_polarity(name: str) -> str:
    """Classify a Textual theme name as ``dark`` or ``light`` (name-based)."""
    n = (name or "").strip().lower()
    if not n:
        return "dark"
    if any(h in n for h in _LIGHT_THEME_HINTS) or n.endswith("light"):
        return "light"
    return "dark"


def themes_matching_canvas(
    names: Iterable[str], canvas: str | None
) -> list[str]:
    """Themes whose polarity matches the canvas (sorted)."""
    want = normalize_canvas(canvas)
    return sorted(n for n in names if theme_polarity(n) == want)


def resolve_theme_for_canvas(
    saved: str | None,
    canvas: str | None,
    available: Iterable[str],
) -> str:
    """Pick a theme compatible with ``canvas`` — prefer ``saved`` when it matches."""
    names = list(available)
    if not names:
        return (saved or "").strip()
    want = normalize_canvas(canvas)
    saved = (saved or "").strip()
    if saved and saved in names and theme_polarity(saved) == want:
        return saved
    default = "textual-light" if want == "light" else "textual-dark"
    if default in names:
        return default
    matched = themes_matching_canvas(names, want)
    return matched[0] if matched else saved
