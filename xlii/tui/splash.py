"""Startup splash for the Textual TUI.

Not a loading screen — just something to fill the empty transcript while the
frame mounts. Stays in the log until /clear (or a bare `clear`) wipes it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from rich.align import Align
from rich.console import Group, RenderableType
from rich.padding import Padding
from rich.text import Text

# The XLII wordmark (slant block letters). XLII = 42 in Roman numerals; the
# trailing "·" is a quiet wink at that.
_XLII = (
    "██╗  ██╗  ██╗       ██╗  ██╗",
    "╚██╗██╔╝  ██║       ██║  ██║",
    " ╚███╔╝   ██║       ██║  ██║",
    " ██╔██╗   ██║       ██║  ██║",
    "██╔╝ ██╗  ██████╗   ██║  ██║",
    "╚═╝  ╚═╝  ╚═════╝   ╚═╝  ╚═╝ ·",
)

# Subtitle under the wordmark. "the answer" nods to 42 without spelling it out.
_TAGLINE = "the answer, at the command line"
_TOP_PAD_LINES = 4
# Commands get an accent color so the get-started invite stands out from the dim hint.
_CMD_STYLE = "bold green"

# The old-school `.nfo` splash override. Drop a file at one of these spots and it becomes the whole
# splash, verbatim — the "make it your own" hack. Caps keep a stray huge/binary file from flooding
# the transcript; over the cap we silently fall back to the built-in wordmark.
_NFO_FILENAME = "splash.nfo"       # in a config dir (project .xlii/ or global ~/.config/xlii/)
_NFO_ROOT_FILENAME = "xlii.nfo"    # bare at the project root, "release .nfo" flavored
_NFO_MAX_BYTES = 64 * 1024
_NFO_MAX_LINES = 200


def resolve_splash_nfo(
    *,
    project_root: Optional[Path] = None,
    xli_dir: Optional[Path] = None,
) -> Optional[Path]:
    """First existing `.nfo` override, most-specific first, else ``None``.

    Precedence (first hit wins): per-project ``.xlii/splash.nfo`` → repo-root ``xlii.nfo`` →
    global ``~/.config/xlii/splash.nfo`` (the "my splash everywhere" spot, next to config.json).
    Best-effort — a filesystem hiccup just means "no override".
    """
    from xlii.config import global_config_dir

    candidates: list[Optional[Path]] = [
        (Path(xli_dir) / _NFO_FILENAME) if xli_dir is not None else None,
        (Path(project_root) / _NFO_ROOT_FILENAME) if project_root is not None else None,
        global_config_dir() / _NFO_FILENAME,
    ]
    for cand in candidates:
        if cand is None:
            continue
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


def read_nfo(path: Path) -> Optional[str]:
    """Read a `.nfo` override, or ``None`` if missing/unreadable/over the size caps.

    Returning ``None`` (rather than raising) lets the caller fall back to the wordmark for a stray
    huge or binary file instead of dumping it into the transcript.
    """
    try:
        if path.stat().st_size > _NFO_MAX_BYTES:
            return None
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if raw.count("\n") + 1 > _NFO_MAX_LINES:
        return None
    return raw.rstrip("\n")


def default_splash_text() -> Optional[str]:
    """The bundled "standard" splash shipped with xlii (``xlii/tui/splash.nfo``) — the auto-loading
    default when no user override exists. Read via ``importlib.resources`` so it works identically in
    editable and wheel installs. ``None`` only if the asset is somehow missing (then the caller falls
    back to the in-code wordmark)."""
    try:
        from importlib.resources import files

        raw = (files("xlii.tui") / _NFO_FILENAME).read_text(encoding="utf-8")
    except (OSError, ModuleNotFoundError, FileNotFoundError):
        return None
    return raw.rstrip("\n")


def ensure_setup_splash() -> None:
    """Seed the setup folder (``~/.config/xlii/splash.nfo``) with the shipped default so there's a
    hackable splash file sitting next to ``config.json``. No-op if it already exists or the config dir
    isn't there yet (we never create the dir just for this). Best-effort — never raises into startup."""
    try:
        from xlii.config import global_config_dir

        cfg_dir = global_config_dir()
        if not cfg_dir.is_dir():
            return
        target = cfg_dir / _NFO_FILENAME
        if target.exists():
            return
        default = default_splash_text()
        if default is not None:
            target.write_text(default + "\n", encoding="utf-8")
    except OSError:
        return


def _help_line() -> Text:
    """`type /help or /howto to get started`, with the two commands highlighted."""
    hint = Text()  # neutral base so the dim segments don't bleed onto the commands
    hint.append("type ", style="dim")
    hint.append("/help", style=_CMD_STYLE)
    hint.append(" or ", style="dim")
    hint.append("/howto", style=_CMD_STYLE)
    hint.append(" to get started", style="dim")
    return hint


def splash_renderable(
    *,
    project: Optional[str] = None,
    nfo_text: Optional[str] = None,
) -> RenderableType:
    """Centered logo + hint lines for the empty transcript.

    When ``nfo_text`` is given (a user's `.nfo` override), it *replaces* the whole splash — art plus
    whatever tagline/hint the author baked in — rendered verbatim, no added chrome. Otherwise the
    built-in XLII wordmark is shown.
    """
    if nfo_text is not None:
        # Verbatim: plain Text (not from_markup, so `[` in art stays literal), no wrap so wide art
        # crops rather than reflowing, centered as one block. No forced color — respect the terminal.
        return Align.center(Text(nfo_text, no_wrap=True))
    logo = Text(("\n" * _TOP_PAD_LINES) + "\n".join(_XLII), style="bold cyan")
    hint = _help_line()
    tagline = Text(_TAGLINE, style="italic dim")
    parts: list[RenderableType] = [
        Padding(Align.center(logo), (0, 0, 1, 0)),
        Align.center(hint),
        Align.center(tagline),
    ]
    if project:
        parts.append(Align.center(Text(project, style="bold")))
    return Group(*parts)
