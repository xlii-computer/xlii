"""Fuzzy subsequence matching — pure string scoring shared by the help corpus
(xlii.help_corpus) and the TUI discoverability layer (palette, tool drawer,
shortcodes). Extracted verbatim from xlii/tui/discover.py (godzilla-mothra
V1ab): the matcher is kernel-shaped; the palette/@-pinning machinery stays in
the face.

Leaf module: stdlib only.
"""

from __future__ import annotations


def fuzzy_score(needle: str, haystack: str) -> int:
    """Lower is better; -1 means no match. Subsequence match with gap penalty."""
    if not needle:
        return 0
    n = needle.lower()
    h = haystack.lower()
    i = 0
    score = 0
    for ch in n:
        j = h.find(ch, i)
        if j < 0:
            return -1
        score += j - i
        i = j + 1
    return score


def _best_fuzzy(needle: str, *parts: str) -> int:
    scores = [fuzzy_score(needle, p) for p in parts if p]
    good = [s for s in scores if s >= 0]
    return min(good) if good else -1
