""":shortcode: completion — an in-memory symbol table + a capped popup.

The-fold Vector F (input completions v1). THE LAW that governs this module:
**no model and no I/O in the input loop.** Per-keystroke work is a subsequence
scan (:func:`xlii.tui.discover.fuzzy_score`, the same matcher behind the Ctrl+K
palette and the slash menu) over a static in-memory table — nothing else.

Scope is **symbols and width-safe emoji**. Symbols (box-drawing, arrows, math,
typographic marks) are single-width glyphs with no render hazard. Emoji (v2) are
admitted only through :func:`_is_width_safe`, a structural pass over the codepoint
sequence: a single wide scalar (optionally + VS16) that measures exactly two cells
— e.g. ``👍`` ``🔥`` ``❤️``. It **rejects the terminal-divergent sequence machinery
by construction**: ZWJ sequences (``👨‍👩‍👧‍👦`` — a non-ligature terminal renders the
four faces separately and blows the width up to eight cells), regional-indicator
flag pairs, combining keycaps, and skin-tone modifiers. ``rich``'s ``cell_len``
collapses every one of those to 2, but real terminals disagree; admitting only
single-scalar (+VS16) emoji keeps what xlii *reserves* equal to what the terminal
*draws*, so a frame never tears. As a belt-and-suspenders guard, :data:`SHORTCODES`
is filtered through :func:`_is_width_safe` at import, so no width-hazard glyph can
reach the transcript even if one is added to :data:`_RAW`/:data:`_EMOJI` by mistake.

Shape (Slack/GitHub convention, keyboard-first): type ``:box``, a small capped
popup offers ``:box-tl:`` ╭ etc.; Tab (or Enter) accepts, Esc dismisses, ↑/↓
walk — you never leave the keyboard. The popup opens only on ``:`` + at least two
characters, and only when the ``:`` starts the token (start-of-line or after
whitespace) — an ordinary colon (``12:30``, ``http://``, ``key: value``) never
triggers it.
"""

from __future__ import annotations

from typing import Optional

from rich.cells import cell_len
from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

# Popup only opens on ``:`` + this many characters (no flicker on a lone colon).
SHORTCODE_MIN_CHARS = 2
# Overlay row cap — render cost stays constant regardless of table size.
POPUP_CAP = 8

# Characters that may appear in a shortcode token after the ``:``. Alnum plus the
# symbolic-arrow punctuation (``->``, ``<-``, ``<->``, ``=>``) and the ``-``/``_``
# word joiners used by names (``box-tl``). ``:`` and whitespace are delimiters.
_LEGAL = set(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_<>=+"
)

# --------------------------------------------------------------------------- #
#  The symbol table. Single-width glyphs only. Grouped for maintenance;
#  deliberately redundant (symbolic + named arrow codes) so discovery is
#  forgiving. Emoji live in _EMOJI below, gated by _is_width_safe.
# --------------------------------------------------------------------------- #
_RAW: dict[str, str] = {
    # arrows — symbolic
    "->": "→", "<-": "←", "<->": "↔", "=>": "⇒", "<=": "⇐", "<=>": "⇔",
    "^|": "↑", "v|": "↓",
    # arrows — named (aid `:arr…` discovery)
    "arrow-r": "→", "arrow-l": "←", "arrow-u": "↑", "arrow-d": "↓",
    "arrow-lr": "↔", "arrow-ud": "↕",
    "arrow-ne": "↗", "arrow-nw": "↖", "arrow-se": "↘", "arrow-sw": "↙",
    "return": "↵",
    # box-drawing — round corners (the frame-drawing program's bread and butter)
    "box-tl": "╭", "box-tr": "╮", "box-bl": "╰", "box-br": "╯",
    # box-drawing — sharp corners
    "corner-tl": "┌", "corner-tr": "┐", "corner-bl": "└", "corner-br": "┘",
    # box-drawing — lines, tees, cross
    "hline": "─", "vline": "│", "cross": "┼",
    "tee-l": "├", "tee-r": "┤", "tee-up": "┴", "tee-down": "┬",
    # box-drawing — heavy + double
    "heavy-h": "━", "heavy-v": "┃",
    "dbl-h": "═", "dbl-v": "║", "dbl-tl": "╔", "dbl-tr": "╗", "dbl-bl": "╚", "dbl-br": "╝",
    # blocks + shades
    "block": "█", "block-l": "▌", "block-r": "▐", "block-u": "▀", "block-d": "▄",
    "shade-light": "░", "shade-med": "▒", "shade-dark": "▓",
    # math + logic
    "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "neq": "≠", "leq": "≤", "geq": "≥", "approx": "≈", "equiv": "≡",
    "inf": "∞", "deg": "°", "sqrt": "√", "sum": "∑", "prod": "∏",
    "int": "∫", "partial": "∂", "nabla": "∇", "prop": "∝",
    "in": "∈", "notin": "∉", "subset": "⊂", "superset": "⊃",
    "union": "∪", "inter": "∩", "empty": "∅",
    "forall": "∀", "exists": "∃", "and": "∧", "or": "∨", "not": "¬", "xor": "⊕",
    # greek (common in notes/frames)
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "Delta": "Δ",
    "theta": "θ", "lambda": "λ", "mu": "µ", "pi": "π", "sigma": "σ", "Sigma": "Σ",
    "phi": "φ", "omega": "ω", "Omega": "Ω",
    # typographic marks
    "ellipsis": "…", "mdash": "—", "ndash": "–", "bullet": "•", "middot": "·",
    "dagger": "†", "ddagger": "‡", "section": "§", "para": "¶",
    "lsquo": "‘", "rsquo": "’", "ldquo": "“", "rdquo": "”", "prime": "′", "dprime": "″",
    # currency + legal
    "euro": "€", "pound": "£", "yen": "¥", "cent": "¢",
    "copy": "©", "reg": "®", "tm": "™",
}

# --------------------------------------------------------------------------- #
#  The emoji table (v2). Every glyph is a single wide scalar, optionally + VS16
#  (``️``) to force emoji presentation — the ONLY shapes _is_width_safe
#  admits. NO ZWJ sequences, flag pairs, keycaps, or skin-tone modifiers: those
#  render at terminal-dependent widths and would tear a frame (see the module
#  docstring). Codes follow the Slack/GitHub convention (hyphen-joined, house
#  style); a few classic aliases (`+1`, `-1`, `check`, `warn`) are included since
#  the fuzzy matcher rewards short distinctive stems (`:fire`, `:rocket`, `:heart`).
# --------------------------------------------------------------------------- #
_EMOJI: dict[str, str] = {
    # hands & gestures
    "thumbsup": "👍", "+1": "👍", "thumbsdown": "👎", "-1": "👎",
    "ok-hand": "👌", "wave": "👋", "clap": "👏", "raised-hands": "🙌",
    "pray": "🙏", "muscle": "💪", "handshake": "🤝", "point-up": "👆",
    "point-down": "👇", "point-left": "👈", "point-right": "👉",
    "fist": "✊", "punch": "👊", "eyes": "👀", "wave-hand": "👋",
    # faces
    "smile": "😄", "grin": "😁", "joy": "😂", "rofl": "🤣", "wink": "😉",
    "blush": "😊", "thinking": "🤔", "neutral": "😐", "sweat-smile": "😅",
    "sob": "😭", "cry": "😢", "rage": "😡", "angry": "😠", "scream": "😱",
    "flushed": "😳", "cool": "😎", "nerd": "🤓", "party": "🥳",
    "exploding-head": "🤯", "shrug": "🤷", "facepalm": "🤦", "yawn": "🥱",
    "sleepy": "😴", "zany": "🤪", "smirk": "😏", "unamused": "😒",
    # creatures & symbols-as-faces
    "skull": "💀", "ghost": "👻", "alien": "👽", "robot": "🤖", "poop": "💩",
    "clown": "🤡", "see-no-evil": "🙈", "unicorn": "🦄", "snake": "🐍",
    # hearts
    "heart": "❤️", "orange-heart": "🧡", "yellow-heart": "💛",
    "green-heart": "💚", "blue-heart": "💙", "purple-heart": "💜",
    "black-heart": "🖤", "white-heart": "🤍", "broken-heart": "💔",
    "sparkling-heart": "💖", "two-hearts": "💕", "100": "💯",
    # status / check / warn
    "white-check-mark": "✅", "check": "✅", "heavy-check": "✔️",
    "x-mark": "❌", "cross-mark": "❌", "warning": "⚠️", "warn": "⚠️",
    "no-entry": "⛔", "question": "❓", "grey-question": "❔",
    "exclamation": "❗", "bangbang": "‼️", "recycle": "♻️",
    "sos": "🆘", "new": "🆕", "ok-button": "🆗", "up-button": "🆙",
    "prohibited": "🚫", "radioactive": "☢️", "biohazard": "☣️",
    # dev, tools & objects
    "fire": "🔥", "rocket": "🚀", "tada": "🎉", "sparkles": "✨",
    "star": "⭐", "star2": "🌟", "boom": "💥", "zap": "⚡", "bulb": "💡",
    "bug": "🐛", "ant": "🐜", "wrench": "🔧", "hammer": "🔨",
    "hammer-and-wrench": "🛠️", "gear": "⚙️", "nut-and-bolt": "🔩",
    "package": "📦", "memo": "📝", "pencil": "✏️", "pushpin": "📌",
    "paperclip": "📎", "lock": "🔒", "unlock": "🔓", "key": "🔑",
    "mag": "🔍", "book": "📖", "books": "📚", "clipboard": "📋",
    "calendar": "📅", "chart-up": "📈", "chart-down": "📉", "bar-chart": "📊",
    "moneybag": "💰", "gem": "💎", "gift": "🎁", "bell": "🔔",
    "hourglass": "⌛", "watch": "⌚", "alarm": "⏰", "stopwatch": "⏱️",
    "computer": "💻", "phone": "📱", "envelope": "✉️", "inbox": "📥",
    "outbox": "📤", "floppy": "💾", "camera": "📷", "battery": "🔋",
    "electric-plug": "🔌", "flashlight": "🔦", "bomb": "💣", "pill": "💊",
    "test-tube": "🧪", "telescope": "🔭", "microscope": "🔬", "magnet": "🧲",
    "link": "🔗", "scissors": "✂️", "art": "🎨", "wave-box": "📨",
    # nature, weather, food
    "sunny": "☀️", "cloud": "☁️", "snowflake": "❄️",
    "umbrella": "☔", "rainbow": "🌈", "ocean": "🌊", "droplet": "💧",
    "snowman": "⛄", "coffee": "☕", "pizza": "🍕", "beer": "🍺",
    "cake": "🎂", "apple": "🍎", "seedling": "🌱", "leaves": "🍃",
    "cactus": "🌵", "sun": "🌞", "moon": "🌙", "earth": "🌍",
    # markers & flags (single-glyph only — NOT regional-indicator pairs)
    "checkered-flag": "🏁", "triangular-flag": "🚩", "round-pushpin": "📍",
    "trophy": "🏆", "medal": "🏅", "crown": "👑", "dart": "🎯",
    "hourglass-flowing": "⏳", "wastebasket": "🗑️",
}


def _is_width_safe(glyph: str) -> bool:
    """The v2 width-sanity pass. ``True`` for a glyph xlii can insert without any
    risk of a torn frame — i.e. one whose reserved width (``cell_len``) is what a
    terminal actually draws.

    Admits two shapes:
      * a single-width symbol (``cell_len == 1``) — the v1 invariant, unchanged;
      * a single wide *scalar* (optionally followed by VS16 ``\\ufe0f``) that
        measures exactly two cells — the safe emoji shape.

    Rejects, **structurally** (not by ``cell_len``, which collapses them all to 2),
    every terminal-divergent sequence: ZWJ joins (``\\u200d``), regional-indicator
    flag pairs (``U+1F1E6..U+1F1FF``), combining enclosing keycap (``\\u20e3``),
    and skin-tone modifiers (``U+1F3FB..U+1F3FF``). A non-conforming terminal
    renders those at a different width than ``cell_len`` reserves, so they can't be
    admitted safely — even though a modern, ligature-aware terminal would show them
    fine."""
    if not glyph:
        return False
    width = cell_len(glyph)
    if width == 1 and len(glyph) == 1:
        return True                      # single-width symbol (v1 shape)
    if width != 2:
        return False                     # 0-wide, or a >2 blowup — never safe
    for ch in glyph:
        cp = ord(ch)
        if ch in ("‍", "⃣"):   # ZWJ / combining enclosing keycap
            return False
        if 0x1F1E6 <= cp <= 0x1F1FF:     # regional indicator (flag pair)
            return False
        if 0x1F3FB <= cp <= 0x1F3FF:     # skin-tone modifier
            return False
    # What survives must be exactly one base scalar (+ optional VS16) — reject any
    # other multi-scalar shape we didn't explicitly enumerate above (defensive).
    return len(glyph.replace("️", "")) == 1


def _load_table(*sources: dict[str, str]) -> dict[str, str]:
    """Merge the source tables into the served map, keeping only width-safe glyphs
    (:func:`_is_width_safe`). This is the belt-and-suspenders gate: a hazardous
    glyph (double-width blowup, ZWJ sequence, flag pair, …) is dropped before it
    can reach the transcript, even if one slips into :data:`_RAW`/:data:`_EMOJI`.
    Later sources win on a code collision (emoji never shadow a symbol here — the
    two code namespaces are disjoint, asserted in tests)."""
    out: dict[str, str] = {}
    for src in sources:
        for code, g in src.items():
            if _is_width_safe(g):
                out[code] = g
    return out


SHORTCODES: dict[str, str] = _load_table(_RAW, _EMOJI)


def shortcode_query(line: str, col: int) -> Optional[tuple[int, str]]:
    """Pure. Detect an open ``:shortcode`` token whose partial ends at ``col``
    (a column within a single input ``line``). Returns ``(colon_index, partial)``
    or ``None``.

    Fires only when: the ``:`` is at the start of the line or preceded by
    whitespace (kills ``12:30``, ``http://``, ``key: value``); the partial is
    ``>= SHORTCODE_MIN_CHARS`` legal characters; and no closing ``:`` intervenes
    (walking back stops at the first non-legal char). No model, no I/O — a bounded
    string walk."""
    if col <= 0 or col > len(line):
        return None
    i = col
    while i > 0 and line[i - 1] in _LEGAL:
        i -= 1
    if i == 0 or line[i - 1] != ":":
        return None
    colon = i - 1
    partial = line[i:col]
    if len(partial) < SHORTCODE_MIN_CHARS:
        return None
    # The ':' must open the token — start-of-line or after whitespace.
    if colon > 0 and not line[colon - 1].isspace():
        return None
    return colon, partial


def shortcode_matches(partial: str, *, limit: int = POPUP_CAP) -> list[tuple[str, str]]:
    """Pure. ``[(code, glyph), …]`` for ``partial``, ranked by the house fuzzy
    matcher (lower is better), then shorter code, then alpha; capped at ``limit``.
    A subsequence scan over the static table — same cost profile as the ``/`` popup."""
    from xlii.tui.discover import fuzzy_score

    scored: list[tuple[int, int, str, str]] = []
    for code, glyph in SHORTCODES.items():
        s = fuzzy_score(partial, code)
        if s >= 0:
            scored.append((s, len(code), code, glyph))
    scored.sort(key=lambda t: (t[0], t[1], t[2]))
    return [(code, glyph) for _s, _n, code, glyph in scored[:limit]]


def apply_shortcode(line: str, start: int, end: int, glyph: str) -> str:
    """Pure. Replace ``line[start:end]`` (the ``:partial``) with ``glyph`` and
    return the new line. The caller places the cursor at ``start + len(glyph)``."""
    return line[:start] + glyph + line[end:]


class _ShortcodePopup(OptionList):
    """The capped ``:shortcode`` overlay — mirrors the ``#completions`` slash menu
    (an ``OptionList`` shown above the input), but owned by the input surface. Its
    selection is driven by :class:`_PromptInput` (index tracked there); this widget
    only renders the rows. Hidden until a query matches."""

    DEFAULT_CSS = """
    #shortcode-popup {
        display: none;
        layer: overlay;
        dock: bottom;
        height: auto;
        max-height: 8;
        border: round $accent;
        padding: 0 1;
        background: transparent;
        margin: 0 1 6 1;
    }
    """

    def set_matches(self, matches: list[tuple[str, str]]) -> None:
        """Repaint the rows from ``[(code, glyph), …]`` and highlight the first."""
        self.clear_options()
        self.add_options([
            Option(Text.assemble((f"{glyph} ", "bold"), (f":{code}:", "cyan")))
            for code, glyph in matches
        ])
        if matches:
            self.highlighted = 0
