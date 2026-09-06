"""Tests for xlii/terminal_image.py."""

from __future__ import annotations

import base64

from pathlib import Path

from types import SimpleNamespace

from xlii.terminal_image import (
    _try_chafa,
    display_image,
    image_renderable,
    maybe_preview,
    set_renderable_sink,
)


_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_display_image_path_backend(tmp_path):
    p = tmp_path / "shot.png"
    p.write_bytes(_TINY_PNG)
    result = display_image(p, backend="path")
    assert result.tier == "path"
    assert result.path == p.resolve()


def test_display_image_missing_file(tmp_path):
    p = tmp_path / "nope.png"
    result = display_image(p, backend="auto")
    assert result.tier == "path"
    assert "missing" in result.message


def test_force_overrides_off_env(tmp_path, monkeypatch):
    # image mode / --inline / `/image latest` already decided to show — an
    # off-like XLII_IMAGE_PREVIEW default must not downgrade an `auto` request to
    # path-only. (Bugbot PR#75: "Image mode preview stays path-only")
    monkeypatch.setenv("XLII_IMAGE_PREVIEW", "off")
    monkeypatch.setattr("xlii.terminal_image._is_tty", lambda: True)
    monkeypatch.setattr("xlii.terminal_image._try_chafa", lambda p, *, blocks, width: True)
    p = tmp_path / "shot.png"
    p.write_bytes(_TINY_PNG)

    assert display_image(p, backend="auto", force=True).tier != "path"   # rendered
    assert display_image(p, backend="auto", force=False).tier == "path"  # env vetoes


def test_chafa_graphics_does_not_pass_invalid_format(monkeypatch):
    # chafa --format is one of {iterm,kitty,sixels,symbols} — there is NO "auto".
    # `chafa -f auto` exits 2 and renders nothing; the high-quality tier must omit
    # -f (auto-detect, like bare `chafa <img>`). The blocks tier forces symbols.
    cmds = []
    monkeypatch.setattr("xlii.terminal_image.shutil.which", lambda _: "/usr/bin/chafa")
    monkeypatch.setattr("xlii.terminal_image._run", lambda cmd: cmds.append(cmd) or True)

    _try_chafa(Path("x.jpg"), blocks=False, width=60)
    _try_chafa(Path("x.jpg"), blocks=True, width=60)

    graphics, blocks = cmds
    assert "auto" not in graphics, f"graphics tier still passes invalid format: {graphics}"
    assert "-f" not in graphics, "graphics tier should omit -f and let chafa auto-detect"
    assert blocks[1:3] == ["-f", "symbols"], f"blocks tier must force symbols: {blocks}"
    # --size must be width-only; a zero dimension ("60x0") makes chafa exit 2.
    for cmd in (graphics, blocks):
        size = cmd[cmd.index("--size") + 1]
        assert size == "60", f"--size must be width-only, got {size!r}"
        assert "x0" not in size and "x" not in size, f"zero/explicit height breaks chafa: {size!r}"


def test_force_still_honors_explicit_path_backend(tmp_path, monkeypatch):
    # `force` only neutralizes the env-derived downgrade; an explicit /image path
    # request is still honored.
    monkeypatch.setattr("xlii.terminal_image._is_tty", lambda: True)
    monkeypatch.setattr("xlii.terminal_image._try_chafa", lambda p, *, blocks, width: True)
    p = tmp_path / "shot.png"
    p.write_bytes(_TINY_PNG)
    assert display_image(p, backend="path", force=True).tier == "path"


# -- renderable path (Textual TUI) -----------------------------------------
#
# The stdout cascade is invisible under Textual (a subprocess writing escape
# codes to stdout is painted over by the compositor). For a renderable-hosting
# host we build a Rich renderable from chafa's *symbols* output instead.

_FAKE_ANSI = "\x1b[38;2;255;0;0m██\x1b[0m\n██\n"


def test_image_renderable_captures_chafa_symbols(tmp_path, monkeypatch):
    cmds = []

    def fake_run(cmd, **kw):
        cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout=_FAKE_ANSI)

    monkeypatch.setattr("xlii.terminal_image.shutil.which", lambda _: "/usr/bin/chafa")
    monkeypatch.setattr("xlii.terminal_image.subprocess.run", fake_run)
    p = tmp_path / "shot.png"
    p.write_bytes(_TINY_PNG)

    from rich.text import Text

    rend = image_renderable(p, max_width=40)
    assert isinstance(rend, Text)
    assert "█" in rend.plain  # the block glyphs survived ANSI parsing
    # symbols tier, width-only size (a zero height makes chafa exit 2)
    (cmd,) = cmds
    assert cmd[1:3] == ["-f", "symbols"]
    size = cmd[cmd.index("--size") + 1]
    assert "x" not in size, f"--size must be width-only, got {size!r}"


def test_image_renderable_none_without_chafa(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.terminal_image.shutil.which", lambda _: None)
    p = tmp_path / "shot.png"
    p.write_bytes(_TINY_PNG)
    assert image_renderable(p) is None


def test_image_renderable_none_for_path_backend(tmp_path, monkeypatch):
    # An explicit path/off backend must not render — caller falls back to a line.
    monkeypatch.setattr("xlii.terminal_image.shutil.which", lambda _: "/usr/bin/chafa")
    monkeypatch.setattr(
        "xlii.terminal_image.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=_FAKE_ANSI),
    )
    p = tmp_path / "shot.png"
    p.write_bytes(_TINY_PNG)
    assert image_renderable(p, backend="path") is None


def test_maybe_preview_routes_through_sink(tmp_path, monkeypatch):
    # With a sink installed (the TUI), maybe_preview emits the image_block payload
    # through it and never touches the stdout cascade. A non-ImageRef block (the
    # chafa-symbols fallback) reports tier=blocks.
    sentinel = object()
    received = []
    monkeypatch.setattr("xlii.terminal_image.image_block", lambda *a, **k: sentinel)
    monkeypatch.setattr(
        "xlii.terminal_image.display_image",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("stdout cascade must not run")),
    )
    prev = set_renderable_sink(received.append)
    try:
        result = maybe_preview(tmp_path / "shot.png", enabled=True, console=None)
    finally:
        set_renderable_sink(prev)

    assert received == [sentinel]
    assert result.tier == "blocks"
    assert result.backend == "renderable"


def test_maybe_preview_imageref_reports_graphics_tier(tmp_path, monkeypatch):
    # With a true graphics protocol available, the sink gets an ImageRef and the
    # result reports the graphics tier.
    from xlii.terminal_image import ImageRef

    monkeypatch.setattr("xlii.terminal_image.tui_graphics_available", lambda: True)
    received = []
    p = tmp_path / "shot.png"
    p.write_bytes(_TINY_PNG)
    prev = set_renderable_sink(received.append)
    try:
        result = maybe_preview(p, enabled=True, console=None)
    finally:
        set_renderable_sink(prev)

    assert len(received) == 1 and isinstance(received[0], ImageRef)
    assert received[0].path == p.resolve()
    assert result.tier == "graphics"
    assert result.backend == "renderable"


def test_maybe_preview_sink_falls_back_to_path_line(tmp_path, monkeypatch):
    # Sink installed but no block available → path line, no crash.
    from tests.helpers import FakeConsole

    monkeypatch.setattr("xlii.terminal_image.image_block", lambda *a, **k: None)
    con = FakeConsole()
    p = tmp_path / "shot.png"
    prev = set_renderable_sink(lambda r: None)
    try:
        result = maybe_preview(p, enabled=True, console=con)
    finally:
        set_renderable_sink(prev)

    assert result.tier == "path"
    assert str(p) in con.text


# -- image_block fidelity decision (A symbols vs B widget) ------------------


def test_image_block_prefers_widget_when_graphics_available(tmp_path, monkeypatch):
    from xlii.terminal_image import ImageRef, image_block

    monkeypatch.setattr("xlii.terminal_image.tui_graphics_available", lambda: True)
    p = tmp_path / "shot.png"
    p.write_bytes(_TINY_PNG)
    block = image_block(p, max_width=44)
    assert isinstance(block, ImageRef)
    assert block.path == p.resolve()
    assert block.max_width == 44


def test_image_block_falls_back_to_symbols_without_graphics(tmp_path, monkeypatch):
    from rich.text import Text
    from xlii.terminal_image import image_block

    monkeypatch.setattr("xlii.terminal_image.tui_graphics_available", lambda: False)
    monkeypatch.setattr("xlii.terminal_image.shutil.which", lambda _: "/usr/bin/chafa")
    monkeypatch.setattr(
        "xlii.terminal_image.subprocess.run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=_FAKE_ANSI),
    )
    p = tmp_path / "shot.png"
    p.write_bytes(_TINY_PNG)
    assert isinstance(image_block(p), Text)


def test_image_block_none_for_missing_or_off(tmp_path, monkeypatch):
    from xlii.terminal_image import image_block

    monkeypatch.setattr("xlii.terminal_image.tui_graphics_available", lambda: True)
    assert image_block(tmp_path / "nope.png") is None  # missing
    p = tmp_path / "shot.png"
    p.write_bytes(_TINY_PNG)
    assert image_block(p, backend="path") is None  # explicit off-like backend


def test_tui_graphics_available_counts_only_real_protocols(monkeypatch):
    # Only sixel / kitty-TGP count; the halfcell/unicode text fallbacks don't
    # (they're no better than chafa symbols and are broken on Pillow 11.x).
    import xlii.terminal_image as ti
    import textual_image.renderable as r
    from textual_image.renderable.sixel import Image as Sixel
    from textual_image.renderable.unicode import Image as Unicode

    monkeypatch.setattr(ti, "_tui_graphics_cache", None, raising=False)
    monkeypatch.setattr(r, "Image", Sixel, raising=False)
    assert ti.tui_graphics_available(refresh=True) is True

    monkeypatch.setattr(ti, "_tui_graphics_cache", None, raising=False)
    monkeypatch.setattr(r, "Image", Unicode, raising=False)
    assert ti.tui_graphics_available(refresh=True) is False


def test_set_renderable_sink_returns_previous():
    a, b = (lambda r: None), (lambda r: None)
    prev0 = set_renderable_sink(a)
    try:
        assert set_renderable_sink(b) is a
    finally:
        set_renderable_sink(prev0)
    assert set_renderable_sink(prev0) is None  # restored to default
