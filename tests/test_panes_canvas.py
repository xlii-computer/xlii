"""canvas:// — one make on the work, over the artifacts store."""

from __future__ import annotations

import os
from types import SimpleNamespace

from xlii.addressing import Address
from xlii.addressing.builtins.canvas import CanvasProvider
from xlii.panes.canvas import CanvasPane
from xlii.panes.dock import Dock


_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
)


def _store(tmp_path, monkeypatch, files=()):
    art = tmp_path / ".xlii" / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    import time
    now = time.time()
    for i, name in enumerate(files):
        p = art / name
        p.write_bytes(_PNG if name.endswith(".png") else b"data-" + name.encode())
        os.utime(p, (now - i * 60, now - i * 60))
    sess = SimpleNamespace(project=SimpleNamespace(project_root=tmp_path))
    monkeypatch.setattr("xlii.active_session.active_session", lambda: sess)
    return art


def test_provider_lists_same_store_under_canvas_scheme(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("new.png", "old.pdf"))
    nodes = CanvasProvider().list(Address(scheme="canvas"))
    assert [n.name for n in nodes] == ["new.png", "old.pdf"]
    assert all(n.address.startswith("canvas://") for n in nodes)


def test_pane_shows_latest_as_media(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("shot.png", "older.png"))
    pane = CanvasPane("canvas://")
    r = pane.render()
    assert not r.empty
    assert r.media is not None and r.media.kind == "image"
    assert r.media.b64
    assert "shot.png" in r.title
    assert pane.selection().node.name == "shot.png"


def test_pad_shows_a_text_note(tmp_path, monkeypatch):
    art = tmp_path / ".xlii" / "artifacts"
    art.mkdir(parents=True)
    (art / "note.txt").write_text("hello pad\n", encoding="utf-8")
    sess = SimpleNamespace(
        project=SimpleNamespace(project_root=tmp_path),
        attached_files=[{
            "name": "note.txt", "path": str(art / "note.txt"),
            "kind": "text", "enabled": True,
        }],
    )
    monkeypatch.setattr("xlii.active_session.active_session", lambda: sess)
    pane = CanvasPane("canvas://note.txt")
    r = pane.render()
    assert not r.empty
    assert any("hello pad" in row.text for row in r.rows)


def test_pane_empty_is_honest(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=())
    r = CanvasPane("canvas://").render()
    assert r.empty
    assert "the pad" in r.rows[0].text
    assert r.title == "Canvas"


def test_dock_routes_canvas_root(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("a.png",))
    dock = Dock()
    pane = dock.open_address("canvas://", slot="A")
    assert type(pane).__name__ == "CanvasPane"


def test_image_actions_include_edit(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("shot.png",))
    acts = {a.name: a for a in CanvasPane("canvas://").actions()}
    assert "edit" in acts
    assert acts["edit"].outcome.text == '/image edit "…" --ref shot.png'
    assert "focus" not in acts
    assert "describe" not in acts
    assert "attach" not in acts


def test_keyed_address_puts_that_make_on_the_work(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("new.png", "old.png"))
    pane = CanvasPane("canvas://old.png")
    assert pane.selection().node.name == "old.png"
    assert "old.png" in pane.render().title
    assert pane.render().media is not None
    assert pane.handle("down") is False
    assert pane.select_index(1) is False


def test_dock_routes_canvas_leaf(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("new.png", "old.png"))
    dock = Dock()
    pane = dock.open_address("canvas://old.png", slot="A", focus=True)
    assert type(pane).__name__ == "CanvasPane"
    assert pane.selection().node.name == "old.png"


def test_artifacts_open_puts_that_make_on_canvas(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("new.png", "old.png"))
    dock = Dock()
    arts = dock.open_address("artifacts://", slot="A", focus=True)
    assert arts.handle("down")
    act = next(a for a in arts.actions() if a.name == "open")
    dock.dispatch(act.outcome, from_slot="A")
    canvas = dock.pane("B")
    assert type(canvas).__name__ == "CanvasPane"
    assert canvas.selection().node.name == "old.png"
    assert dock.focused == "B"


def test_write_artifact_notifies_session(tmp_path, monkeypatch):
    from xlii.artifacts import write_artifact

    seen = []
    sess = SimpleNamespace(
        project=SimpleNamespace(project_root=tmp_path),
        on_artifact_written=seen.append,
    )
    monkeypatch.setattr("xlii.active_session.active_session", lambda: sess)
    rel = write_artifact(tmp_path, _PNG, ext="png")
    assert seen and seen[0].name.endswith(".png")
    assert rel.endswith(".png")
