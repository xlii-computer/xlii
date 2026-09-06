"""`xlii code --tauri` (tauri-face V3) — launcher pins.

The desktop app itself is Rust (built outside Python CI — desktop/README.md);
these pin only the Python launcher: flag parses, missing binary is a dry rc-1
message, found binary is exec'd from the project root.
"""

from __future__ import annotations

from types import SimpleNamespace

import xlii.cmds.sessions.code as code_mod
from xlii.cmds.sessions.code import _launch_tauri, cmd_code


def test_flag_registered():
    import argparse

    from xlii.cmds.sessions.register import register as register_sessions

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    register_sessions(sub)
    args = parser.parse_args(["code", "--tauri"])
    assert args.tauri is True


def test_missing_binary_falls_back_to_live_browser_face(tmp_path, monkeypatch, capsys):
    # _resolve_desktop_exe also checks repo release build + ~/.local/bin — mock
    # that, not only shutil.which, or a local cargo build will exec for real.
    monkeypatch.setattr(code_mod, "_resolve_desktop_exe", lambda: None)
    calls = []
    monkeypatch.setattr(code_mod, "_launch_live_face_browser",
                        lambda root: calls.append(root) or 0)
    rc = _launch_tauri(tmp_path)
    assert rc == 0
    assert calls == [tmp_path.resolve()]
    out = capsys.readouterr().out
    assert "not on PATH" in out
    assert "live browser face" in out.lower() or "live face" in out.lower()


def test_found_binary_execs_from_project_root(tmp_path, monkeypatch):
    import os

    calls = {}
    monkeypatch.setattr(code_mod, "_resolve_desktop_exe",
                        lambda: "/usr/bin/xlii-desktop")
    monkeypatch.setattr(os, "chdir", lambda p: calls.setdefault("cwd", p))
    monkeypatch.setattr(os, "execvp",
                        lambda exe, argv: calls.setdefault("exec", (exe, argv)))
    _launch_tauri(tmp_path)
    assert calls["cwd"] == tmp_path
    assert calls["exec"] == ("/usr/bin/xlii-desktop", ["/usr/bin/xlii-desktop"])


def test_cmd_code_routes_tauri_before_boot(tmp_path, monkeypatch):
    """--tauri never boots a session in this process — the sidecar does."""
    monkeypatch.setattr(code_mod, "_resolve_project_target", lambda t: tmp_path)
    monkeypatch.setattr(code_mod, "_launch_tauri", lambda target, **k: 42)
    booted = []
    monkeypatch.setattr(code_mod, "build_code_session",
                        lambda *a, **k: booted.append(1))
    args = SimpleNamespace(target=None, tauri=True, face_replace=False)
    assert cmd_code(args) == 42
    assert not booted
