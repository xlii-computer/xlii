"""Face window grab — pick the xlii window, never a terminal in this repo."""

from __future__ import annotations

from xlii.window_shot import PNG_MAGIC, is_png, pick_face_id
import xlii.window_shot as ws


def test_pick_face_id_prefers_tauri_class():
    rows = [
        ("0x1", "kitty.kitty", "xlii — iXaac-lab"),
        ("0x2", "xlii-desktop.Xlii-desktop", "xlii"),
        ("0x3", "firefox.Firefox", "xlii — Mozilla Firefox"),
    ]
    assert pick_face_id(rows) == "0x2"


def test_pick_face_id_skips_terminals_even_when_titled_xlii():
    rows = [
        ("0x1", "kitty.kitty", "xlii"),
        ("0x2", "gnome-terminal-server.Gnome-terminal", "xlii"),
    ]
    assert pick_face_id(rows) is None


def test_pick_face_id_title_exact_when_class_unknown():
    rows = [
        ("0x9", "unknown.Unknown", "xlii"),
        ("0x8", "unknown.Unknown", "something else"),
    ]
    assert pick_face_id(rows) == "0x9"


def test_is_png_rejects_empty_and_svg(tmp_path):
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    assert is_png(empty) is False
    svg = tmp_path / "blank.svg"
    svg.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    assert is_png(svg) is False
    png = tmp_path / "ok.png"
    png.write_bytes(PNG_MAGIC + b"\x00" * 40)
    assert is_png(png) is True


def test_grab_face_window_import(tmp_path, monkeypatch):
    dest = tmp_path / "shot.png"
    payload = PNG_MAGIC + b"\x00" * 40

    monkeypatch.setattr(ws.shutil, "which", lambda name: name in {"wmctrl", "import", "xdotool"})
    monkeypatch.setattr(ws, "_wmctrl_rows", lambda: [("0xabc", "xlii-desktop.Xlii-desktop", "xlii")])
    monkeypatch.setattr(ws, "_xdotool_ids", lambda kind, value: [])
    monkeypatch.setattr(ws, "_active_id", lambda: "")

    def fake_run(cmd, timeout=6):
        from types import SimpleNamespace
        if cmd[:2] == ["import", "-silent"]:
            dest.write_bytes(payload)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(ws, "_run", fake_run)
    assert ws.grab_face_window(dest) is True
    assert dest.read_bytes()[:8] == PNG_MAGIC


def test_grab_face_window_false_when_no_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(ws.shutil, "which", lambda name: None)
    monkeypatch.setattr(ws, "_wmctrl_rows", lambda: [])
    monkeypatch.setattr(ws, "_xdotool_ids", lambda kind, value: [])
    monkeypatch.setattr(ws, "_active_id", lambda: "")
    assert ws.grab_face_window(tmp_path / "nope.png") is False
