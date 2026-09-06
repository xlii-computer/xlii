"""Face Focus button — pin a viewed leaf as next-turn context (not a rewind)."""

from __future__ import annotations

from pathlib import Path

from tests.test_serve_face import (  # noqa: F401 — _cleanup_servers is autouse
    _cleanup_servers,
    _connect,
    _fake_state,
    _recv_until,
    _send,
    _start_server,
)
from xlii.addressing.builtins.register import register_builtins


def _locker_state(tmp_path):
    state = _fake_state(tmp_path)
    files: list[dict] = []
    state.attached_files = files
    xli = tmp_path / ".xlii"
    xli.mkdir(exist_ok=True)
    state.project.project_root = tmp_path
    state.project.xli_dir = xli

    def attach_file(path, *, once=False):
        entry = {
            "name": Path(path).name,
            "path": str(Path(path).resolve()),
            "enabled": True,
            "once": once,
            "kind": "file",
        }
        files.append(entry)
        return entry

    def set_file_enabled(name_or_path, enabled):
        hit = False
        for e in files:
            if e["name"] == name_or_path or e["path"] == name_or_path:
                e["enabled"] = bool(enabled)
                hit = True
        return hit

    def live_attachment_paths():
        return [e["path"] for e in files if e.get("enabled")]

    def consume_once_attachments():
        for e in files:
            if e.get("once") and e.get("enabled"):
                e["enabled"] = False

    def remove_file(name_or_path):
        before = len(files)
        files[:] = [
            e for e in files
            if e["name"] != name_or_path and e["path"] != name_or_path
        ]
        return len(files) < before

    state.attach_file = attach_file
    state.set_file_enabled = set_file_enabled
    state.live_attachment_paths = live_attachment_paths
    state.consume_once_attachments = consume_once_attachments
    state.remove_file = remove_file
    return state


def test_focus_wire_pins_file(tmp_path):
    register_builtins()
    f = tmp_path / "hello.txt"
    f.write_text("line\n", encoding="utf-8")
    state = _locker_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    _recv_until(conn, "hello")
    _send(conn, {"type": "focus", "address": f"file://{f}"})
    ev, _ = _recv_until(conn, "focus_state")
    assert ev["items"]
    assert ev["items"][0]["title"] == "hello.txt"
    assert ev["items"][0]["once"] is True
    assert state.attached_files[0]["once"] is True
    conn.close()


def test_focus_clear_drops_pin(tmp_path):
    register_builtins()
    f = tmp_path / "hello.txt"
    f.write_text("line\n", encoding="utf-8")
    state = _locker_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    _recv_until(conn, "hello")
    _send(conn, {"type": "focus", "address": f"file://{f}"})
    _recv_until(conn, "focus_state")
    _send(conn, {"type": "focus", "clear": True})
    ev, _ = _recv_until(conn, "focus_state")
    assert ev["items"] == []
    assert state.attached_files[0]["enabled"] is False
    conn.close()


def test_pin_canvas_work_holds_until_replaced(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from xlii.artifacts import write_artifact
    from xlii.serve_face import FaceServer

    register_builtins()
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
    )
    state = _locker_state(tmp_path)
    monkeypatch.setattr("xlii.active_session.active_session", lambda: state)
    older = write_artifact(tmp_path, png, ext="png")
    newer = write_artifact(tmp_path, png, ext="png")
    older_name = older.rsplit("/", 1)[-1]
    newer_name = newer.rsplit("/", 1)[-1]
    srv = SimpleNamespace(state=state, _focus=[], sent=[])
    srv.send = lambda obj: srv.sent.append(obj)
    srv._emit_focus_state = lambda **k: FaceServer._emit_focus_state(srv, **k)
    FaceServer.pin_canvas_work(srv, f"canvas://{older_name}")
    assert srv._focus[0]["title"] == older_name
    assert srv._focus[0]["once"] is False
    assert state.attached_files[0]["role"] == "canvas"
    assert state.attached_files[0]["once"] is False
    FaceServer.pin_canvas_work(srv, f"canvas://{newer_name}")
    live = [e for e in state.attached_files if e.get("enabled")]
    assert [e["name"] for e in live] == [newer_name]
    assert srv._focus[0]["title"] == newer_name


def test_face_assets_name_focus_and_fkeys():
    from xlii.serve_face import default_assets_dir

    assets = default_assets_dir()
    html = (assets / "index.html").read_text(encoding="utf-8")
    js = (assets / "js/transcript.js").read_text(encoding="utf-8")
    app = (assets / "js/app.js").read_text(encoding="utf-8")
    assert 'id="fkeybar"' in html
    assert 'id="focuschip"' in html
    assert "Focus" in js
    assert "handleFkey" in app
    assert "focus" in app
    assert 'addr.startsWith("canvas://")' in app
