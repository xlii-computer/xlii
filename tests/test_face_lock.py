"""Face glass assets + chrome fields."""

from pathlib import Path

from tests.test_serve_face import (  # noqa: F401 — _cleanup_servers is autouse
    _cleanup_servers,
    _connect,
    _fake_state,
    _recv_event,
    _recv_until,
    _send,
    _start_server,
)


def test_overlay_in_index():
    html = Path("xlii/face_assets/index.html").read_text()
    assert 'id="glass"' in html
    app = Path("xlii/face_assets/js/app.js").read_text()
    assert "./lock.js" in app
    css = Path("xlii/face_assets/css/face.css").read_text()
    assert "#glass" in css


def test_lock_unlock_wire(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    port, _t = _start_server(_fake_state(tmp_path))
    conn = _connect(port)
    _recv_until(conn, "chrome_state")
    _send(conn, {"type": "lock"})
    chrome = None
    for _ in range(16):
        ev = _recv_event(conn)
        if ev.get("type") == "chrome_state" and ev.get("glass") in ("locked", "black"):
            chrome = ev
            break
    assert chrome is not None
    _send(conn, {"type": "unlock", "code": ""})
    unlocked = None
    for _ in range(16):
        ev = _recv_event(conn)
        if ev.get("type") == "chrome_state" and ev.get("glass") in ("", None):
            unlocked = ev
            break
    assert unlocked is not None
    conn.close()


def test_chrome_state_includes_glass(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from xlii.glass import live_chrome, lock_face

    g, m, v = live_chrome()
    assert g == ""
    assert m == "desk"
    assert v == ""
    lock_face()
    g, m, v = live_chrome()
    assert g == "locked"
