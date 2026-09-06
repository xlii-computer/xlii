"""XliiApp shell CSS — theme-derived layout chrome for the transcript + input."""

from __future__ import annotations

from xlii.tui.theme import THEME


def _tui_app_css() -> str:
    """App CSS with theme-derived input scroll chrome."""
    scroll_border = THEME.tui_input_scroll_border
    scrollbar = THEME.tui_input_scrollbar
    return f"""
    /* Track C: transcript paper — independent of trim theme tokens. */
    Screen.-canvas-dark {{ background: #0a0a0a; }}
    Screen.-canvas-light {{ background: #fafafa; }}
    #log.-canvas-dark {{ background: #0a0a0a; }}
    #log.-canvas-light {{ background: #fafafa; }}
    /* The completion popups float on their own layer so revealing them never
       reflows the base layout — an in-flow reveal resized the transcript and
       cost a full-screen relayout per keystroke (~185ms on a long session:
       the 2026-07-10 slash-latency diagnosis). */
    Screen {{ layers: base overlay; }}
    #status {{ height: 1; padding: 0 1; color: $text-muted; }}
    /* #fkey-bar is styled by _FKeyBar.DEFAULT_CSS (its own distinct strip), like _MenuBar. */
    /* Pinned query bar (pinned-query-bar-style): same chrome family as the
       input / completions — round accent frame, transparent fill. Capped at
       3 text rows (+2 border); the rest scrolls inside the bar. Inset matches
       #log-row, with a 1-row gutter below so the frame never kisses the
       transcript. The scrollbar rides inside the frame (slim, 1 col). */
    #question-scroll {{
        display: none;
        height: auto;
        max-height: 5;
        border: round $accent;
        margin: 0 1 1 1;
        padding: 0 1;
        background: transparent;
        scrollbar-size-vertical: 1;
    }}
    #question {{
        display: none;
        height: auto;
        text-style: bold;
    }}
    /* Vector P (J1): the transcript shares its row with an optional dockable
       side panel. With no panel open the panel is display:none (zero width),
       so #log fills the row. The-fold chrome: the ROW carries the 1-char outer
       margin (left AND right) so the content region never butts the terminal
       edge, and #log/#panel sit flush inside it — a tight 0-col gutter between
       the transcript and an open side panel (each has its own 1-col text pad). */
    #log-row {{ height: 1fr; margin: 0 1 1 1; }}
    /* scrollbar-gutter: stable reserves the scrollbar its own column so the
       right padding becomes real space to its LEFT (between text and bar); the
       1-col right margin is the space to its RIGHT (bar ↔ panel/edge) — a
       1-column gutter on both sides of the transcript scrollbar. */
    #log {{ height: 1fr; width: 1fr; padding: 0 1; margin: 0 1 0 0; scrollbar-gutter: stable; scrollbar-size-vertical: 1; }}
    #panel {{
        display: none;
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }}
    #heartbeat {{ display: none; height: 1; padding: 0 6; color: $accent; }}
    /* Slash / palette / @ / recall popup. Spacing matches #input-box: same outer
       gutters as the typed frame (refined at show-time by _align_completions_popup
       so left/right borders line up with the input's round frame). Frame is accent
       only — no solid panel fill; transcript shows through; row highlight tints text. */
    #completions {{
        display: none;
        layer: overlay;
        dock: bottom;
        height: auto;
        max-height: 8;
        border: round $accent;
        padding: 0 1;
        background: transparent;
        margin: 0 1 6 1;
    }}
    #shortcode-popup {{
        display: none;
        layer: overlay;
        dock: bottom;
        height: auto;
        max-height: 8;
        border: round $accent;
        padding: 0 1;
        background: transparent;
        margin: 0 1 6 1;
    }}
    #input-chips {{ height: 1; padding: 0; margin: 0 1; }}
    #input-row {{
        height: auto;
        margin: 0 1;
        align: left top;
    }}
    #input-prefix {{
        width: auto;
        min-width: 3;
        height: 1;
        padding: 0 0 0 1;
        margin-top: 1;
        margin-right: 0;
        content-align: right middle;
    }}
    #input-box {{
        width: 1fr;
        height: auto;
        min-height: 3;
        border: round;
        margin: 0 1 0 0;
        padding: 0;
        background: $panel;
    }}
    #input-box:focus-within {{
        border: round;
    }}
    #input-box.-scroll-mode {{
        border: round;
    }}
    #input-box.-scroll-mode:focus-within {{
        border: round;
    }}
    #input {{
        height: auto;
        min-height: 1;
        border: none;
        margin: 0;
        padding: 0 1;
        background: transparent;
        width: 1fr;
    }}
    #input Scrollbar {{
        background: $panel;
        color: {scrollbar};
    }}
    #input Scrollbar:hover {{
        color: {scroll_border};
    }}
    #input-action {{
        width: 8;
        min-width: 8;
        border: round;
        background: $panel;
        color: #808080;
        padding: 0;
        content-align: center middle;
    }}
    #input-action:hover {{
        background: $panel;
    }}
    #input-action.-scroll-mode {{
        border: round;
    }}
    """
