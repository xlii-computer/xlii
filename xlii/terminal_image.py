"""Inline terminal image preview — graphics → blocks → path cascade (model-routing R3).

Delegates protocol detection to chafa or timg when available; never raises to
callers — always returns a DisplayResult so generation can proceed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional

Tier = Literal["graphics", "blocks", "path"]
Backend = Literal["auto", "graphics", "blocks", "path", "chafa", "timg"]

# Env values that mean "don't render inline". Shared by the preview gate and the
# renderer so the two agree on what counts as off.
_OFF_LIKE = frozenset({"off", "0", "never", "path", "false", "no"})

# Some front-ends own the screen and can't show the stdout cascade: a subprocess
# writing raw escape codes to stdout is painted over by the compositor (the
# Textual TUI — see tui_textual.py). Such a host installs a sink here that takes
# a Rich renderable and places it in its transcript instead. launch() sets this
# while the TUI runs and restores it on exit, mirroring the xlii.tools._confirm
# swap. None (the default) means "write to stdout" — the inline REPL and CLI.
_RENDERABLE_SINK: Optional[Callable[[Any], None]] = None


def set_renderable_sink(sink: Optional[Callable[[Any], None]]) -> Optional[Callable[[Any], None]]:
    """Install (or clear, with None) the renderable sink; return the previous one.

    When set, maybe_preview emits a Rich renderable through the sink instead of
    shelling a renderer out to stdout. The caller is responsible for restoring
    the previous value on teardown (launch() does this in a finally).
    """
    global _RENDERABLE_SINK
    prev = _RENDERABLE_SINK
    _RENDERABLE_SINK = sink
    return prev


@dataclass
class DisplayResult:
    path: Path
    tier: Tier
    backend: str
    message: str = ""


@dataclass(frozen=True)
class ImageRef:
    """A neutral 'render this image as richly as the host can' marker.

    Emitted to the renderable sink when textual-image is available so the host
    (the Textual transcript) can mount a real graphics widget (sixel/kitty/iterm)
    instead of the chafa-symbols fallback. Carries no Textual/Rich types so this
    module stays import-light; the transcript layer interprets it.
    """

    path: Path
    max_width: int = 60


def has_local_preview() -> bool:
    """True when there is somewhere LOCAL to show an image: a renderable sink
    (the Textual TUI / browser face) or an interactive stdout. False in a
    headless subprocess — `xlii ask`'s stdout IS the parsed reply (the daemon
    reads it as the message text), so emitting a preview there would corrupt
    phone replies. The guard tool-side unconditional previews key on."""
    return _RENDERABLE_SINK is not None or _is_tty()


def preview_mode() -> str:
    """Env override for preview tier/tool. Default ``auto`` (ambient preview —
    a made image is never invisible; tui-media-delivery P1): graphics → blocks
    → path line by capability. Off-like values downgrade to the path line."""
    return os.environ.get("XLII_IMAGE_PREVIEW", "auto").strip().lower()


def _is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _term_cols() -> int:
    try:
        return max(20, os.get_terminal_size().columns)
    except OSError:
        return 80


def _max_width(config_width: int = 60) -> int:
    return max(20, min(_term_cols() - 4, config_width))


def _run(cmd: list[str]) -> bool:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            stdout=sys.stdout,
            stderr=subprocess.DEVNULL,
        )
        return proc.returncode == 0
    except OSError:
        return False


def _try_chafa(path: Path, *, blocks: bool, width: int) -> bool:
    if not shutil.which("chafa"):
        return False
    # chafa's --format takes one of {iterm, kitty, sixels, symbols} — there is NO
    # "auto" value (passing it exits 2 and renders nothing). For the high-quality
    # tier, OMIT -f so chafa auto-detects the best protocol (the same thing bare
    # `chafa <img>` does); for the blocks tier, force unicode symbols.
    # --size takes WIDTH or WIDTHxHEIGHT; a zero dimension (the old "{w}x0" for
    # "auto height") is rejected with exit 2 and renders nothing. Width-only
    # constrains the width and scales height proportionally — what we want.
    cmd = ["chafa"]
    if blocks:
        cmd += ["-f", "symbols"]
    cmd += ["--size", str(width), str(path)]
    return _run(cmd)


def _try_timg(path: Path, *, pixelation: str, width: int) -> bool:
    if not shutil.which("timg"):
        return False
    return _run(["timg", f"-p{pixelation}", "-w", str(width), "-C", str(path)])


def _try_kitty_icat(path: Path, width: int) -> bool:
    kitty = shutil.which("kitty")
    if not kitty:
        return False
    return _run([kitty, "+kitten", "icat", "--align", "left", "--width", str(width), str(path)])


def display_image(
    path: Path | str,
    *,
    backend: str = "auto",
    max_width: int = 60,
    force: bool = False,
) -> DisplayResult:
    """Show an image inline when possible; always return status.

    ``force`` means the caller already decided to preview (image mode / --inline /
    an explicit `/image latest`), so the env default ``XLII_IMAGE_PREVIEW=off``
    must not silently veto an ``auto`` request back to path-only. The env can
    still *pick a renderer* (graphics/blocks/chafa/timg), and an explicit
    ``backend="path"``/``"off"`` from the caller is always honored.
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return DisplayResult(p, "path", "path", message=f"[image missing: {p}]")

    mode = (backend or "auto").strip().lower()
    env_mode = preview_mode()
    if mode == "auto" and env_mode not in ("", "auto"):
        # Forced previews ignore an off-like env default (keep the auto cascade);
        # otherwise the env wins, including its renderer choice.
        mode = "auto" if (force and env_mode in _OFF_LIKE) else env_mode
    if mode in _OFF_LIKE:
        return DisplayResult(p, "path", "path", message=str(p))

    if not _is_tty():
        return DisplayResult(p, "path", "path", message=str(p))

    w = _max_width(max_width)

    if mode == "blocks":
        for fn in (_try_chafa,):
            if fn(p, blocks=True, width=w):
                return DisplayResult(p, "blocks", "chafa")
        if _try_timg(p, pixelation="h", width=w):
            return DisplayResult(p, "blocks", "timg")
        return DisplayResult(p, "path", "path", message=str(p))

    if mode == "graphics":
        if _try_chafa(p, blocks=False, width=w):
            return DisplayResult(p, "graphics", "chafa")
        if _try_timg(p, pixelation="k", width=w):
            return DisplayResult(p, "graphics", "timg")
        if _try_kitty_icat(p, w):
            return DisplayResult(p, "graphics", "kitty")
        return DisplayResult(p, "path", "path", message=str(p))

    if mode == "chafa":
        tier: Tier = "blocks" if _try_chafa(p, blocks=True, width=w) else "path"
        if tier == "path" and _try_chafa(p, blocks=False, width=w):
            tier = "graphics"
        return DisplayResult(p, tier, "chafa", message=str(p) if tier == "path" else "")

    if mode == "timg":
        if _try_timg(p, pixelation="k", width=w):
            return DisplayResult(p, "graphics", "timg")
        if _try_timg(p, pixelation="h", width=w):
            return DisplayResult(p, "blocks", "timg")
        return DisplayResult(p, "path", "path", message=str(p))

    # auto: graphics first, then blocks, then path
    if _try_chafa(p, blocks=False, width=w):
        return DisplayResult(p, "graphics", "chafa")
    if _try_timg(p, pixelation="k", width=w):
        return DisplayResult(p, "graphics", "timg")
    if _try_kitty_icat(p, w):
        return DisplayResult(p, "graphics", "kitty")
    if _try_chafa(p, blocks=True, width=w):
        return DisplayResult(p, "blocks", "chafa")
    if _try_timg(p, pixelation="h", width=w):
        return DisplayResult(p, "blocks", "timg")
    return DisplayResult(p, "path", "path", message=str(p))


def _chafa_capture(path: Path, *, width: int) -> str:
    """Run chafa in symbols mode and return its ANSI output (``""`` on failure).

    Symbols (unicode block art with SGR color) is the only tier that round-trips
    through ``rich.Text.from_ansi`` — the graphics protocols (sixel/kitty/iterm)
    emit binary control payloads, not color escapes, so they'd land as garbage in
    a RichLog. Width-only ``--size`` for the same reason a zero height breaks the
    stdout path (see _try_chafa).
    """
    if not shutil.which("chafa"):
        return ""
    try:
        proc = subprocess.run(
            ["chafa", "-f", "symbols", "--size", str(width), str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except OSError:
        return ""
    return proc.stdout if proc.returncode == 0 else ""


def image_renderable(
    path: Path | str,
    *,
    max_width: int = 60,
    backend: str = "auto",
) -> Optional[Any]:
    """Build a Rich renderable previewing the image, or None if unavailable.

    For hosts that can't accept raw escape codes on stdout (the Textual TUI owns
    the screen), the stdout cascade in display_image is invisible. This renders
    chafa to unicode symbols, captures the ANSI, and wraps it as ``rich.Text`` —
    a renderable the transcript can host. Block fidelity only; high-res graphics
    protocols can't survive Textual's compositor (the textual-image widget path).
    An explicit path/off backend returns None so the caller falls back to a path
    line.
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file() or (backend or "auto").strip().lower() in _OFF_LIKE:
        return None
    ansi = _chafa_capture(p, width=_max_width(max_width))
    if not ansi:
        return None
    from rich.text import Text

    return Text.from_ansi(ansi.rstrip("\n"))


_tui_graphics_cache: Optional[bool] = None


def tui_graphics_available(*, refresh: bool = False) -> bool:
    """Whether the TUI can render TRUE graphics (sixel / kitty-TGP) via textual-image.

    textual-image picks its protocol at import time by querying the terminal —
    which only works *before* Textual takes the screen — so launch() primes this
    (refresh=True) while the real TTY is still queryable, and later calls read the
    cache. We count only the genuine graphics protocols: textual-image's
    halfcell/unicode text fallbacks are no better than the chafa-symbols tier (and
    are currently broken on Pillow 11.x), so for those we prefer chafa symbols.
    """
    global _tui_graphics_cache
    if _tui_graphics_cache is not None and not refresh:
        return _tui_graphics_cache
    try:
        from textual_image.renderable import Image as _Auto
        from textual_image.renderable.sixel import Image as _Sixel
        from textual_image.renderable.tgp import Image as _TGP

        _tui_graphics_cache = _Auto in (_Sixel, _TGP)
    except Exception:
        _tui_graphics_cache = False
    return _tui_graphics_cache


def image_block(
    path: Path | str,
    *,
    max_width: int = 60,
    backend: str = "auto",
):
    """Best transcript payload for an image on a renderable-hosting host.

    Returns an ``ImageRef`` when the TUI has a true graphics protocol (the host
    can mount a sixel/kitty image widget), else the chafa-symbols ``Text``
    fallback (image_renderable), else None. An explicit path/off backend returns
    None so the caller falls back to a path line.
    """
    mode = (backend or "auto").strip().lower()
    if mode in _OFF_LIKE:
        return None
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return None
    w = _max_width(max_width)

    if mode == "blocks":
        return image_renderable(p, max_width=max_width, backend=backend)

    if mode == "graphics":
        return ImageRef(p, w) if tui_graphics_available() else None

    if mode == "chafa":
        rend = image_renderable(p, max_width=max_width, backend=backend)
        if rend is not None:
            return rend
        return ImageRef(p, w) if tui_graphics_available() else None

    # timg / auto: graphics first, then blocks (matches display_image)
    if tui_graphics_available():
        return ImageRef(p, w)
    return image_renderable(p, max_width=max_width, backend=backend)


def maybe_preview(
    path: Path | str,
    *,
    enabled: bool,
    backend: str = "auto",
    console=None,
    force: bool = False,
) -> DisplayResult:
    """Preview when enabled; print path line when falling back to path tier.

    ``force`` propagates the caller's "I already decided to show this" intent to
    display_image so an off-like ``XLII_IMAGE_PREVIEW`` default can't downgrade
    image-mode / --inline / `/image latest` to path-only.

    When a renderable sink is installed (the Textual TUI), emit a payload through
    it instead of the stdout cascade, which the compositor would clobber: a true
    graphics widget (ImageRef) when textual-image is present, else chafa symbols.
    """
    if enabled and _RENDERABLE_SINK is not None:
        block = image_block(path, backend=backend)
        if block is not None:
            if isinstance(block, ImageRef):
                from xlii.tui.transcript import image_display_tier

                tier = image_display_tier(block)
            else:
                tier = "blocks"
            _RENDERABLE_SINK(block)
            return DisplayResult(Path(path).expanduser().resolve(), tier, "renderable")
        result = DisplayResult(Path(path).expanduser().resolve(), "path", "path", message=str(path))
    elif enabled:
        result = display_image(path, backend=backend, force=force)
    else:
        result = DisplayResult(Path(path), "path", "path", message=str(path))
    if console is not None and result.tier == "path" and result.message:
        console.print(f"[dim]{result.message}[/dim]")
    return result
