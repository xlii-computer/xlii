"""artifacts:// + ArtifactsPane — the gallery door (tui-media-delivery P2).

The Tray/Artifacts split kept crisp: locker = files YOU staged to send
(session state); artifacts = things XLII made (.xlii/artifacts/ on disk).
Provider is newest-first, media-only, containment-checked, and degrades to an
empty container on a bare seat.
"""

from __future__ import annotations

import os
import time
from types import SimpleNamespace

from xlii.addressing import Address
from xlii.addressing.builtins.artifacts import ArtifactsProvider
from xlii.panes import PREFILL, RETARGET_SLOT, SHOW_MEDIA
from xlii.panes.artifacts import ArtifactsPane


def _store(tmp_path, monkeypatch, files=()):
    """A fake ambient session whose project owns tmp_path; seed artifact files."""
    art = tmp_path / ".xlii" / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for i, name in enumerate(files):
        p = art / name
        p.write_bytes(b"data-" + name.encode())
        os.utime(p, (now - i * 60, now - i * 60))   # earlier in the list = newer
    sess = SimpleNamespace(project=SimpleNamespace(project_root=tmp_path))
    monkeypatch.setattr("xlii.active_session.active_session", lambda: sess)
    return art


def test_provider_lists_media_newest_first_excluding_bookkeeping(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch,
           files=("new.png", "older.jpg", "oldest.webp"))
    (tmp_path / ".xlii" / "artifacts" / "last-session.json").write_text("{}")
    (tmp_path / ".xlii" / "artifacts" / "video-abc.json").write_text("{}")

    nodes = ArtifactsProvider().list(Address(scheme="artifacts"))
    assert [n.name for n in nodes] == ["new.png", "older.jpg", "oldest.webp"]
    assert all(n.extra.get("path") for n in nodes)
    assert nodes[0].extra["type"] == "image"


def test_provider_read_and_containment(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("a.png",))
    prov = ArtifactsProvider()
    assert prov.read(Address.parse("artifacts://a.png")) == b"data-a.png"
    assert prov.exists(Address.parse("artifacts://a.png")) is True
    # Escapes resolve to nothing — never to a file outside the store.
    assert prov.exists(Address.parse("artifacts://../secret.png")) is False
    assert prov.resolve(Address.parse("artifacts://../secret.png")).ok is False


def test_provider_bare_seat_degrades_to_empty(monkeypatch):
    monkeypatch.setattr("xlii.active_session.active_session", lambda: None)
    prov = ArtifactsProvider()
    assert prov.list(Address(scheme="artifacts")) == []
    assert prov.resolve(Address(scheme="artifacts")).ok is True   # empty container


def test_pane_rows_actions_and_selection(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("new.png", "old.pdf"))
    pane = ArtifactsPane("artifacts://")
    r = pane.render()
    assert not r.empty and len(r.rows) == 2
    assert r.rows[0].accent is True                 # newest leads
    assert "new.png" in r.rows[0].text
    assert ".xlii/artifacts/new.png" in r.rows[0].text

    from xlii.panes import ATTACH

    acts = {a.name: a for a in pane.actions()}
    assert acts["open"].label == "On canvas"
    assert acts["open"].outcome.kind == RETARGET_SLOT
    assert acts["open"].outcome.address == "canvas://new.png"
    assert "view" not in acts
    assert acts["focus"].outcome.kind == ATTACH
    assert acts["focus"].outcome.address == "artifacts://new.png"
    assert "describe" not in acts
    assert acts["edit"].outcome.kind == PREFILL
    assert acts["edit"].outcome.text == '/image edit "…" --ref new.png'
    assert acts["save"].outcome.kind == PREFILL
    assert "--from new.png" in acts["save"].outcome.text
    assert "assets/new.png" in acts["save"].outcome.text

    # The PDF row: no edit (image-only); open puts it on the canvas.
    assert pane.handle("down") is True
    acts2 = {a.name: a for a in pane.actions()}
    assert "edit" not in acts2 and "open" in acts2
    assert acts2["open"].outcome.kind == RETARGET_SLOT
    assert acts2["open"].outcome.address == "canvas://old.pdf"


def test_video_is_not_canvas_worthy(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("clip.mp4",))
    acts = {a.name: a for a in ArtifactsPane("artifacts://").actions()}
    assert acts["view"].outcome.kind == SHOW_MEDIA
    assert "open" not in acts


def test_artifacts_pane_restores_selection_from_full_address(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("new.png", "old.pdf"))
    pane = ArtifactsPane("artifacts://")
    assert pane.select_index(1)
    assert pane.selection().node.name == "old.pdf"
    pane.mount("artifacts://", select="artifacts://old.pdf")
    assert pane.selection().node.name == "old.pdf"


def test_dock_routes_artifacts_root_to_the_pane(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch, files=("a.png",))
    from xlii.panes.dock import Dock

    dock = Dock(slots=("A",))                 # defaults register in __init__
    pane = dock.open_address("artifacts://", slot="A", focus=True)
    assert isinstance(pane, ArtifactsPane)


def test_tui_chrome_has_the_door_but_no_hotkey_letter(tmp_path):
    from xlii.tui.app import _DOORWAY_LETTERS, XliiApp

    assert XliiApp._DOORWAY_SCHEMES.get("a") == "artifacts"
    # 'a' stays OUT of the hotkey letters — Artifacts is a pane, not a menu accel.
    assert "a" not in _DOORWAY_LETTERS


def test_home_catalog_attachments_not_engine_piles():
    from xlii.home_catalog import HOME_CATALOG

    labels = [e.label for e in HOME_CATALOG]
    assert "Attachments" in labels and "Canvas" in labels
    assert "Artifacts" in labels  # the made-things gallery (.xlii/artifacts/)
    assert "Locker" not in labels
