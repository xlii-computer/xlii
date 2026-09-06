"""Skin packs — discovery, lint, HTTP /skins/, y2k showcase, CLI."""
from __future__ import annotations

import json
import socket
import time

from xlii import skin_packs as sp
from xlii.serve_face import default_assets_dir

from tests.test_serve_face import (  # noqa: F401 — _cleanup_servers is autouse
    _cleanup_servers,
    _fake_state,
    _start_server,
)

Y2K = default_assets_dir() / "skins" / "y2k"
CLASSIC = default_assets_dir() / "skins" / "classic"


def _http_get(port: int, path: str) -> bytes:
    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    c.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    time.sleep(0.15)
    chunks = []
    c.settimeout(2)
    try:
        while True:
            chunk = c.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    except TimeoutError:
        # The socket timeout is how this reader knows the response is complete.
        pass
    c.close()
    return b"".join(chunks)


def test_y2k_showcase_passes_check():
    assert Y2K.is_dir()
    errors = sp.check_pack(Y2K)
    assert errors == [], errors


def test_classic_showcase_passes_check():
    assert CLASSIC.is_dir()
    errors = sp.check_pack(CLASSIC)
    assert errors == [], errors


def test_y2k_fills_chrome_bitmaps():
    css = (Y2K / "skin.css").read_text()
    for slot in (
        "--chrome-menubar-img: url(",
        "--chrome-pane-img: url(",
        "--chrome-btn-img: url(",
        "--chrome-btn-hover-img: url(",
        "--chrome-btn-active-img: url(",
        "--chrome-input-img: url(",
        "--chrome-inputbar-img: url(",
        "--chrome-status-img: url(",
        "--chrome-slot-img: url(",
        "--chrome-win-img: url(",
        "--chrome-win-close-img: url(",
        "--texture-bg: url(",
        "--texture-transcript: url(",
    ):
        assert slot in css, slot
    assert "--slice-btn: none;" in css


def test_discover_includes_y2k():
    packs = {p.name: p for p in sp.discover_packs()}
    assert "y2k" in packs
    assert "classic" in packs
    assert packs["y2k"].skin_id == "pack:y2k"
    assert packs["classic"].skin_id == "pack:classic"
    assert packs["y2k"].origin == "builtin"
    assert sp.is_known_skin("pack:y2k")
    assert sp.is_known_skin("pack:classic")
    assert sp.is_known_skin("mojo")
    assert not sp.is_known_skin("banana")
    assert not sp.is_known_skin("pack:dark")


def test_catalog_builtins_then_y2k():
    ids = [row["id"] for row in sp.catalog_entries()]
    assert ids[:4] == ["dark", "light", "slate", "mojo"]
    assert "pack:y2k" in ids
    assert "pack:classic" in ids


def test_missing_toml_is_not_a_pack(tmp_path):
    d = tmp_path / "ghost"
    d.mkdir()
    (d / "skin.css").write_text('html[data-skin="pack:ghost"] { --bg: #000; }\n')
    assert sp.check_pack(d)
    assert sp._load_pack(d, origin="user") is None


def test_corrupt_toml_is_not_a_pack(tmp_path):
    d = tmp_path / "bork"
    d.mkdir()
    (d / "skin.toml").write_text("label = [\n")
    (d / "skin.css").write_text('html[data-skin="pack:bork"] { --bg: #000; }\n')
    errs = sp.check_pack(d)
    assert any("skin.toml" in e for e in errs)


def test_selector_outside_scope_fails(tmp_path):
    d = tmp_path / "leak"
    d.mkdir()
    (d / "skin.toml").write_text('label = "Leak"\n')
    (d / "skin.css").write_text("body { background: red; }\n")
    errs = sp.check_pack(d)
    assert any("selector must live under" in e for e in errs)


def test_https_url_refused(tmp_path):
    d = tmp_path / "net"
    d.mkdir()
    (d / "skin.toml").write_text('label = "Net"\n')
    (d / "skin.css").write_text(
        'html[data-skin="pack:net"] { --texture-bg: url("https://evil.example/x.png"); }\n'
    )
    errs = sp.check_pack(d)
    assert any("url()" in e for e in errs)


def test_reserved_name_refused(tmp_path):
    d = tmp_path / "dark"
    d.mkdir()
    (d / "skin.toml").write_text('label = "Nope"\n')
    (d / "skin.css").write_text('html[data-skin="pack:dark"] { --bg: #000; }\n')
    errs = sp.check_pack(d)
    assert any("compiled skin" in e for e in errs)


def test_oversize_file_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "MAX_FILE_BYTES", 64)
    d = tmp_path / "huge"
    d.mkdir()
    (d / "skin.toml").write_text('label = "Huge"\n')
    (d / "skin.css").write_text(
        'html[data-skin="pack:huge"] { --bg: #000; }\n'
    )
    (d / "blob.png").write_bytes(b"x" * 200)
    errs = sp.check_pack(d)
    assert any("blob.png" in e and "cap" in e for e in errs)


def test_install_copies_local_dir_only(tmp_path):
    src = tmp_path / "mint"
    src.mkdir()
    (src / "skin.toml").write_text('label = "Mint"\nblurb = "test pack"\n')
    (src / "skin.css").write_text(
        'html[data-skin="pack:mint"] { --bg: #0f0; --fg: #000; }\n'
    )
    dest = tmp_path / "skins"
    ok, msg = sp.install_pack(src, dest_parent=dest)
    assert ok, msg
    assert (dest / "mint" / "skin.css").is_file()
    ok2, msg2 = sp.install_pack(src, dest_parent=dest)
    assert not ok2
    assert "already installed" in msg2


def test_user_pack_discovered(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_DIR", tmp_path)
    d = tmp_path / "skins" / "userpack"
    d.mkdir(parents=True)
    (d / "skin.toml").write_text('label = "User"\nblurb = "from config dir"\n')
    (d / "skin.css").write_text(
        'html[data-skin="pack:userpack"] { --bg: #111; }\n'
    )
    assert sp.find_pack("userpack") is not None
    assert sp.is_known_skin("pack:userpack")


def test_skins_http_catalog_and_y2k(tmp_path):
    port, _t = _start_server(_fake_state(tmp_path), assets_dir=default_assets_dir())
    raw = _http_get(port, "/skins/catalog")
    assert b"200" in raw.split(b"\r\n", 1)[0]
    body = raw.split(b"\r\n\r\n", 1)[1]
    data = json.loads(body)
    ids = [s["id"] for s in data["skins"]]
    assert "dark" in ids and "pack:y2k" in ids
    css = _http_get(port, "/skins/y2k/skin.css")
    assert b"200" in css.split(b"\r\n", 1)[0]
    assert b'html[data-skin="pack:y2k"]' in css
    svg = _http_get(port, "/skins/y2k/btn.svg")
    assert b"200" in svg.split(b"\r\n", 1)[0]
    assert b"<svg" in svg
    classic = _http_get(port, "/skins/classic/btn.svg")
    assert b"200" in classic.split(b"\r\n", 1)[0]
    assert b"<svg" in classic


def test_skins_http_traversal_404(tmp_path):
    port, _t = _start_server(_fake_state(tmp_path), assets_dir=default_assets_dir())
    for path in (
        "/skins/../css/face.css",
        "/skins/y2k/../../css/face.css",
        "/skins/y2k/../../../pyproject.toml",
        "/skins/y2k/nope.exe",
    ):
        raw = _http_get(port, path)
        status = raw.split(b"\r\n", 1)[0]
        assert b"404" in status, path


def test_skins_http_oversize_404(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "MAX_FILE_BYTES", 10)
    port, _t = _start_server(_fake_state(tmp_path), assets_dir=default_assets_dir())
    raw = _http_get(port, "/skins/y2k/skin.css")
    assert b"404" in raw.split(b"\r\n", 1)[0]
    catalog_raw = _http_get(port, "/skins/catalog")
    data = json.loads(catalog_raw.split(b"\r\n\r\n", 1)[1])
    assert "pack:y2k" not in [s["id"] for s in data["skins"]]


def test_set_face_skin_accepts_y2k_pack(tmp_path):
    from types import SimpleNamespace

    from xlii.serve_face import FaceServer

    saves = []
    state = _fake_state(tmp_path)
    state.cfg = SimpleNamespace(face_skin="", save=lambda: saves.append(1))
    sent: list[dict] = []
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = sent.append  # type: ignore[method-assign]
    assert server.set_face_skin("pack:y2k") is True
    assert state.cfg.face_skin == "pack:y2k"
    chrome = [e for e in sent if e.get("type") == "chrome_state"]
    assert chrome and chrome[-1].get("face_skin") == "pack:y2k"
    assert server.set_face_skin("pack:does-not-exist") is False
    assert state.cfg.face_skin == "pack:y2k"


def test_cli_skin_check_y2k():
    from xlii.cli import build_parser

    p = build_parser()
    ns = p.parse_args(["skin", "check", str(Y2K)])
    assert ns.func(ns) == 0


def test_cli_skin_list_mentions_y2k(capsys):
    from xlii.cli import build_parser

    p = build_parser()
    ns = p.parse_args(["skin", "list"])
    assert ns.func(ns) == 0
    out = capsys.readouterr().out
    assert "pack:y2k" in out
    assert "pack:classic" in out
    assert "dark" in out
