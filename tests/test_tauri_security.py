"""Security pins for the desktop face Tauri capability boundary."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TAURI = ROOT / "desktop" / "src-tauri"


def _capability(name: str) -> dict:
    return json.loads((TAURI / "capabilities" / name).read_text())


def test_custom_commands_are_capability_gated():
    build_rs = (TAURI / "build.rs").read_text()
    assert "AppManifest::new().commands" in build_rs
    assert '"handshake"' in build_rs
    assert '"spawn_error"' in build_rs
    assert '"prepare_close"' in build_rs


def test_bundled_asset_shell_owns_sidecar_token_ipc_only_locally():
    cap = _capability("default.json")
    permissions = set(cap["permissions"])

    assert "remote" not in cap
    assert {"allow-handshake", "allow-spawn-error"} <= permissions
    assert not {p for p in permissions if p.startswith("shell:")}


def test_remote_live_face_cannot_read_sidecar_token_or_spawn_shells():
    cap = _capability("live-face-remote.json")
    permissions = set(cap["permissions"])

    assert cap["remote"]["urls"] == ["http://127.0.0.1:*", "http://localhost:*"]
    assert "allow-handshake" not in permissions
    assert "allow-spawn-error" not in permissions
    assert "allow-prepare-close" in permissions
    assert not {p for p in permissions if p.startswith("shell:")}


def test_native_close_requests_graceful_face_exit_before_killing_sidecar():
    main_rs = (TAURI / "src" / "main.rs").read_text()

    assert "CloseRequested" in main_rs
    assert "api.prevent_close()" in main_rs
    assert "__xliiRequestExit" in main_rs
    assert "fn prepare_close" in main_rs
    assert "RunEvent::Exit" in main_rs
    assert "child.kill()" in main_rs


def test_prepare_close_permission_is_declared():
    app_toml = (TAURI / "permissions" / "app.toml").read_text()
    assert 'identifier = "allow-prepare-close"' in app_toml
    assert '"prepare_close"' in app_toml


def test_desktop_window_is_undecorated():
    """OS title bar is off; the face menubar is the only chrome."""
    conf = json.loads((TAURI / "tauri.conf.json").read_text())
    windows = conf["app"]["windows"]
    assert windows, "no windows configured"
    assert windows[0].get("decorations") is False
    menubar = (ROOT / "xlii" / "face_assets" / "js" / "menubar.js").read_text()
    assert "startDragging" in menubar
    assert "startResizeDragging" in menubar
    assert "win-controls" in menubar
    assert "toggleMaximize" in menubar
    assert "tauri-host" in menubar
    for name in ("default.json", "live-face-remote.json"):
        perms = set(_capability(name)["permissions"])
        assert "core:window:allow-start-dragging" in perms
        assert "core:window:allow-start-resize-dragging" in perms
