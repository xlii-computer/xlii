"""Tests for the view pane — client #2 of the pane contract, a leaf reader over vfs_read."""

from __future__ import annotations

import pytest

from xlii.panes import ENQUEUE_TURN, Pane, Rendered
from xlii.panes.view import ViewPane


def _addr(p) -> str:
    return f"file://{p}"


def test_viewpane_satisfies_pane_protocol(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("hi")
    assert isinstance(ViewPane(_addr(f)), Pane)


def test_render_decodes_lines(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("alpha\nbeta\ngamma\n")
    pane = ViewPane(_addr(f))
    r = pane.render()
    assert isinstance(r, Rendered)
    assert [row.text for row in r.rows] == ["alpha", "beta", "gamma"]
    assert all(row.kind == "line" for row in r.rows)
    assert r.title == _addr(f)


def test_empty_file_is_empty_projection(tmp_path):
    f = tmp_path / "empty.txt"
    f.write_text("")
    pane = ViewPane(_addr(f))
    assert pane.render().empty is True
    assert pane.render().rows == ()


def test_mount_container_raises(tmp_path):
    with pytest.raises(IsADirectoryError):
        ViewPane(_addr(tmp_path))


def test_selection_is_the_leaf(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    pane = ViewPane(_addr(f))
    assert pane.selection().node.kind == "leaf"
    assert pane.selection().address == _addr(f)


def test_actions_offer_summarize(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("x")
    pane = ViewPane(_addr(f))
    acts = pane.actions()
    assert [a.name for a in acts] == ["summarize"]
    assert acts[0].outcome.kind == ENQUEUE_TURN
    assert acts[0].outcome.address == _addr(f)


def test_handle_is_all_fallthrough(tmp_path):
    f = tmp_path / "f.txt"
    f.write_text("a\nb")
    pane = ViewPane(_addr(f))
    for key in ("up", "down", "enter", "back", "pagedown"):
        assert pane.handle(key) is False


def test_binary_bytes_do_not_crash(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\xff\xfe\x00ok")
    pane = ViewPane(_addr(f))
    # decoded with errors="replace" — renders something, does not raise
    assert pane.render().empty is False
