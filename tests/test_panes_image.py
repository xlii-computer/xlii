"""ImageViewPane (client #3) — an image leaf renders as an image, never as decoded text."""

from __future__ import annotations

import io

import pytest

from xlii.panes import Rendered, RenderedMedia
from xlii.panes.dock import Dock
from xlii.panes.image import ImageViewPane, _human_size

# A real 1x1 PNG — classify() keys off the extension, but valid bytes keep the renderer honest.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
)


def _img(tmp_path, name="pic.png"):
    p = tmp_path / name
    p.write_bytes(_PNG)
    return p


def test_render_produces_image_media_not_text(tmp_path):
    p = _img(tmp_path)
    r = ImageViewPane(f"file://{p}").render()
    assert isinstance(r, Rendered)
    assert r.media is not None and r.media.kind == "image"
    assert r.media.path == str(p)  # a real filesystem path for the renderer
    assert "pic.png" in r.media.caption
    assert not r.empty  # an image leaf is never a blank slot


def test_selection_and_focus_action(tmp_path):
    from xlii.panes import ATTACH

    pane = ImageViewPane(f"file://{_img(tmp_path)}")
    assert pane.selection().node is not None
    acts = pane.actions()
    assert acts and acts[0].name == "focus"
    assert acts[0].outcome.kind == ATTACH


def test_refuses_a_container(tmp_path):
    (tmp_path / "d").mkdir()
    with pytest.raises(IsADirectoryError):
        ImageViewPane(f"file://{tmp_path}/d")


def test_dock_routes_image_leaf_to_image_pane_text_leaf_to_view(tmp_path):
    p = _img(tmp_path)
    (tmp_path / "notes.txt").write_text("hi")
    dock = Dock()
    assert type(dock.open_address(f"file://{p}", slot="A")).__name__ == "ImageViewPane"
    assert type(dock.open_address(f"file://{tmp_path}/notes.txt", slot="B")).__name__ == "ViewPane"


def test_human_size():
    assert _human_size(None) == "?"
    assert _human_size(512) == "512 B"
    assert _human_size(2048) == "2.0 KB"
    assert _human_size(5 * 1024 * 1024) == "5.0 MB"


def test_slot_renderable_shows_caption_for_image_media():
    from rich.console import Console

    from xlii.tui.dock_surface import slot_renderable

    r = Rendered(
        title="file://x/pic.png",
        media=RenderedMedia(kind="image", address="file://x/pic.png", path="", caption="img pic.png 1 KB"),
    )
    out = io.StringIO()
    Console(file=out, width=50).print(slot_renderable(r))
    s = out.getvalue()
    assert "pic.png" in s  # caption shown, not blank — even with no graphics path
