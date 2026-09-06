"""`$EDITOR` handoff: the suspend-aware launcher seam + no-editor grace.

The reported bug: under the TUI an external editor launched into the same tty as
Textual (no suspend), so the two fought and the editor's keystrokes garbled the
input box. The fix routes the launch through an installable launcher (the TUI
suspends around it) and returns a sentinel — rather than half-launching a broken
editor — when none is configured.
"""

from __future__ import annotations

from types import SimpleNamespace

import xlii.editor as ed


def test_needs_tty_and_wait_flags():
    assert ed.needs_tty("nano")
    assert ed.needs_tty("vim")
    assert not ed.needs_tty("pluma")
    assert not ed.needs_tty("code")
    assert ed.waits_for_exit("nano")
    assert ed.waits_for_exit("code -w")
    assert not ed.waits_for_exit("pluma")
    assert not ed.waits_for_exit("code")


def test_resolve_editor_prefers_cfg_over_env(monkeypatch):
    monkeypatch.setattr(ed.shutil, "which", lambda c: f"/usr/bin/{c}")
    monkeypatch.setenv("EDITOR", "myed")
    cfg = SimpleNamespace(editor="code -w")
    assert ed.resolve_editor(cfg) == "code -w"
    assert ed.editor_source(cfg) == "config"


def test_resolve_editor_prefers_EDITOR_then_VISUAL_then_vi(monkeypatch):
    monkeypatch.setattr(ed.shutil, "which", lambda c: f"/usr/bin/{c}")  # all resolve
    monkeypatch.setenv("EDITOR", "myed")
    monkeypatch.setenv("VISUAL", "myvis")
    assert ed.resolve_editor() == "myed"
    monkeypatch.delenv("EDITOR")
    assert ed.resolve_editor() == "myvis"
    monkeypatch.delenv("VISUAL")
    assert ed.resolve_editor() == "vi"


def test_resolve_editor_none_when_nothing_on_path(monkeypatch):
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setattr(ed.shutil, "which", lambda c: None)  # nothing installed
    assert ed.resolve_editor() is None


def test_open_for_edit_returns_sentinel_when_no_editor(monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "resolve_editor", lambda cfg=None: None)
    assert ed.open_for_edit(tmp_path / "f.txt") == ed.EDITOR_UNAVAILABLE


def test_open_for_edit_routes_through_installed_launcher(monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "resolve_editor", lambda cfg=None: "nano")
    captured: dict = {}
    prev = ed.set_editor_launcher(lambda cmd: captured.setdefault("cmd", cmd) is None or 0)
    try:
        rc = ed.open_for_edit(tmp_path / "f.txt")
    finally:
        ed.set_editor_launcher(prev)
    assert rc == 0
    assert captured["cmd"] == ["nano", str(tmp_path / "f.txt")]


def test_open_for_edit_editor_with_args(monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "resolve_editor", lambda cfg=None: "code -w")  # editor + flag
    captured: dict = {}
    prev = ed.set_editor_launcher(lambda cmd: captured.setdefault("cmd", cmd) is None or 0)
    try:
        ed.open_for_edit(tmp_path / "f.txt")
    finally:
        ed.set_editor_launcher(prev)
    assert captured["cmd"] == ["code", "-w", str(tmp_path / "f.txt")]


def test_open_for_edit_filenotfound_maps_to_sentinel(monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "resolve_editor", lambda cfg=None: "ghost")

    def _boom(cmd):
        raise FileNotFoundError()

    prev = ed.set_editor_launcher(_boom)
    try:
        assert ed.open_for_edit(tmp_path / "f.txt") == ed.EDITOR_UNAVAILABLE
    finally:
        ed.set_editor_launcher(prev)


def test_no_launcher_uses_plain_subprocess(monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "resolve_editor", lambda cfg=None: "nano")
    monkeypatch.setattr(ed, "_have_tty", lambda: True)
    calls: dict = {}
    monkeypatch.setattr(ed.subprocess, "call", lambda cmd: calls.setdefault("cmd", cmd) is None or 0)
    prev = ed.set_editor_launcher(None)  # inline REPL: no host launcher
    try:
        ed.open_for_edit(tmp_path / "f.txt")
    finally:
        ed.set_editor_launcher(prev)
    assert calls["cmd"][0] == "nano"


def test_tty_editor_refused_without_a_terminal(monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "resolve_editor", lambda cfg=None: "nano")
    monkeypatch.setattr(ed, "_have_tty", lambda: False)
    called = []
    monkeypatch.setattr(ed.subprocess, "call", lambda cmd: called.append(cmd) or 0)
    prev = ed.set_editor_launcher(None)
    try:
        assert ed.open_for_edit(tmp_path / "f.txt") == ed.EDITOR_NEEDS_TTY
    finally:
        ed.set_editor_launcher(prev)
    assert called == []


def test_gui_editor_detaches(monkeypatch, tmp_path):
    monkeypatch.setattr(ed, "resolve_editor", lambda cfg=None: "pluma")
    monkeypatch.setattr(ed, "_have_tty", lambda: False)

    class _Proc:
        def wait(self, timeout=None):
            raise ed.subprocess.TimeoutExpired(cmd=["pluma"], timeout=timeout)

    popped = []
    monkeypatch.setattr(
        ed.subprocess, "Popen",
        lambda *a, **k: popped.append((a, k)) or _Proc(),
    )
    prev = ed.set_editor_launcher(None)
    try:
        assert ed.open_for_edit(tmp_path / "f.txt") == ed.EDITOR_DETACHED
    finally:
        ed.set_editor_launcher(prev)
    assert popped and popped[0][0][0][0] == "pluma"
    assert popped[0][1].get("start_new_session") is True


def test_resolve_editor_reads_ambient_cfg(monkeypatch):
    from xlii import active_session

    monkeypatch.setattr(ed.shutil, "which", lambda c: f"/usr/bin/{c}")
    monkeypatch.setenv("EDITOR", "nano")
    prev = active_session.set_active_session(
        SimpleNamespace(cfg=SimpleNamespace(editor="pluma"))
    )
    try:
        assert ed.resolve_editor() == "pluma"
        assert ed.editor_source() == "config"
    finally:
        active_session.set_active_session(prev)


def test_editor_launched_helper_hint_on_sentinel():
    from xlii.editor import EDITOR_DETACHED, EDITOR_NEEDS_TTY, EDITOR_UNAVAILABLE
    from xlii.repl_cmds.chat import _editor_launched

    lines: list[str] = []
    console = SimpleNamespace(print=lambda *a, **k: lines.append(" ".join(str(x) for x in a)))

    assert _editor_launched(EDITOR_UNAVAILABLE, console) is False
    assert any("no editor configured" in ln for ln in lines)

    lines.clear()
    assert _editor_launched(EDITOR_NEEDS_TTY, console) is False
    assert any("needs a terminal" in ln for ln in lines)

    lines.clear()
    assert _editor_launched(1, console) is False
    assert any("exited 1" in ln for ln in lines)

    lines.clear()
    assert _editor_launched(EDITOR_DETACHED, console) is True
    assert lines == []

    lines.clear()
    assert _editor_launched(0, console) is True   # launched → no hint, caller reports success
    assert lines == []
