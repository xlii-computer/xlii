"""Source hygiene — catch accidental stego / paste artifacts in product code.

LLM text watermarks (when they exist) often show up as zero-width characters,
unusual Unicode spaces, or lookalike punctuation that does not belong in a
Python/JS source tree. Intentional ZWJ appears only in shortcode / grapheme
tests and docs about emoji — those paths are allowlisted.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Zero-width + uncommon space codepoints that should not appear in product code.
_BAD = {
    0x200B,  # ZERO WIDTH SPACE
    0x200C,  # ZERO WIDTH NON-JOINER
    0x200D,  # ZERO WIDTH JOINER — only ok in allowlisted emoji docs/tests
    0xFEFF,  # BOM / ZWNBSP
    0x2060,  # WORD JOINER
    0x00AD,  # SOFT HYPHEN
    0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006,
    0x2007, 0x2008, 0x2009, 0x200A, 0x202F, 0x205F, 0x3000,
}

_SCAN_ROOTS = ("xlii", "tests", "scripts")
_SUFFIXES = {".py", ".js", ".html", ".css", ".toml", ".json"}

# Paths where ZWJ (and only ZWJ) is intentional (emoji grapheme talk).
_ZWJ_OK_PARTS = (
    "shortcodes",
    "input_completions",
    "input-completions",
    "test_input_completions",
    "vendor",
)


def _allowed(path: Path, codepoint: int) -> bool:
    if codepoint != 0x200D:
        return False
    s = str(path).replace("\\", "/")
    return any(part in s for part in _ZWJ_OK_PARTS)


def test_product_source_has_no_zero_width_stego():
    hits: list[str] = []
    for root_name in _SCAN_ROOTS:
        root = ROOT / root_name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in _SUFFIXES:
                continue
            if "vendor" in path.parts or "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for i, ch in enumerate(text):
                o = ord(ch)
                if o not in _BAD:
                    continue
                if _allowed(path, o):
                    continue
                line = text.count("\n", 0, i) + 1
                rel = path.relative_to(ROOT)
                hits.append(f"{rel}:{line} U+{o:04X}")
                if len(hits) >= 20:
                    break
            if len(hits) >= 20:
                break
        if len(hits) >= 20:
            break
    assert hits == [], "unexpected zero-width / exotic space in source:\n  " + "\n  ".join(hits)


def test_quick_launch_product_labels_are_plain():
    """Switch / pack labels stay human-readable ASCII words (no stego spaces)."""
    from xlii.workbench import BUILTIN_WORKBENCHES, QUICK_LAUNCH_META, quick_launch_buttons

    assert set(BUILTIN_WORKBENCHES) == {"home", "chat", "code"}
    for qid, (label, _action) in QUICK_LAUNCH_META.items():
        assert label.isascii() and label.strip() == label, qid
        assert "\u200b" not in label and "\u00a0" not in label, qid
    home = quick_launch_buttons(BUILTIN_WORKBENCHES["home"])
    assert home[0]["id"] == "switch" and home[0]["label"] == "switch"
