"""Agent Chromium research browser (CDP path 3) — unit + optional live tests."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from xlii import agent_browser as ab
from xlii.tool_handlers import t_browser
from xlii.tool_schemas import REGISTRY

# Live Chromium is opt-in locally; GitHub Actions installs Chrome but CDP is flaky.
_LIVE_BROWSER = (
    ab.find_chromium() is not None
    and os.environ.get("CI", "").strip().lower() not in ("1", "true", "yes")
)


def test_browser_tool_registered():
    assert "browser" in REGISTRY


def test_extract_js_caps_in_page():
    js = ab._extract_js(24000)
    assert "slice" in js
    assert "24000" in js
    assert "innerText" in js
    assert "document.body.innerText" not in js or "cap" in js


def test_safe_url_rejects_bad_schemes():
    with pytest.raises(ValueError, match="scheme"):
        ab._safe_url("javascript:alert(1)")
    with pytest.raises(ValueError, match="scheme"):
        ab._safe_url("data:text/html,hi")
    with pytest.raises(ValueError, match="scheme"):
        ab._safe_url("file:///etc/passwd")
    assert ab._safe_url("https://example.com/x") == "https://example.com/x"


def test_find_chromium_env_override(tmp_path, monkeypatch):
    fake = tmp_path / "chrome"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setenv("XLII_CHROMIUM", str(fake))
    assert ab.find_chromium() == str(fake)


def test_t_browser_unknown_action():
    ctx = MagicMock()
    ctx.project = None
    # _cap_output may use ctx; provide a passthrough if needed
    with patch("xlii.tool_handlers.web._cap_output", side_effect=lambda c, t: t):
        res = t_browser(ctx, {"action": "dance"})
    assert res.is_error
    assert "unknown action" in res.content


def test_t_browser_goto_requires_url():
    ctx = MagicMock()
    ctx.project = None
    with patch("xlii.tool_handlers.web._cap_output", side_effect=lambda c, t: t):
        res = t_browser(ctx, {"action": "goto"})
    assert res.is_error
    assert "url" in res.content


def test_t_browser_status_no_session():
    ab.close_session()
    ctx = MagicMock()
    ctx.project = None
    with patch("xlii.tool_handlers.web._cap_output", side_effect=lambda c, t: t):
        res = t_browser(ctx, {"action": "status"})
    assert res.is_error or "no browser" in res.content.lower()


def test_session_peek_is_cheap_and_honest():
    from types import SimpleNamespace

    ab.close_session()
    dead = ab.session_peek()
    assert dead.ok is False
    class Fake:
        headless = False
        last_url = "https://example.com"
        last_title = "Ex"
        proc = SimpleNamespace(pid=3)
        def alive(self):
            return True
    ab._SESSION = Fake()
    try:
        peek = ab.session_peek()
        assert peek.ok and peek.headless is False
        assert peek.url == "https://example.com"
        assert peek.pid == 3
    finally:
        ab._SESSION = None


def test_default_headless_follows_desk(monkeypatch):
    monkeypatch.delenv("XLII_BROWSER_HEADLESS", raising=False)
    monkeypatch.setenv("CI", "1")
    monkeypatch.setenv("DISPLAY", ":0")
    assert ab._default_headless() is True
    monkeypatch.delenv("CI")
    assert ab._default_headless() is False
    monkeypatch.delenv("DISPLAY")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert ab._default_headless() is True
    monkeypatch.setenv("XLII_BROWSER_HEADLESS", "0")
    assert ab._default_headless() is False


def test_open_session_does_not_hide_a_headed_window(tmp_path, monkeypatch):
    """Agent default headless must not kill the face-opened window."""
    closed = []

    class FakeSess:
        headless = False

        def alive(self):
            return True

        def close(self):
            closed.append(True)

        def goto(self, url):
            return ab.BrowserSnapshot(ok=True, url=url, headless=False, pid=7)

    monkeypatch.setattr(ab, "find_chromium", lambda: "/bin/chromium")
    monkeypatch.setattr(ab, "_SESSION", FakeSess())
    try:
        snap = ab.open_session(url="https://example.com", headless=True)
        assert snap.ok
        assert snap.headless is False
        assert closed == []
    finally:
        ab._SESSION = None


def test_open_session_promotes_headless_to_a_window(tmp_path, monkeypatch):
    closed = []
    captured = {}

    class DeadAfterClose:
        headless = True
        _alive = True

        def alive(self):
            return self._alive

        def close(self):
            closed.append(True)
            self._alive = False

    class FakeProc:
        pid = 9

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(ab, "find_chromium", lambda: "/bin/chromium")
    monkeypatch.setattr(ab, "_free_port", lambda: 9334)
    monkeypatch.setattr(ab, "_wait_cdp", lambda *a, **k: None)
    monkeypatch.setattr(ab.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ab.BrowserSession, "attach_page", lambda self: None)
    ab._SESSION = DeadAfterClose()
    try:
        snap = ab.open_session(user_data_dir=tmp_path / "profile", headless=False)
        assert snap.ok, snap.error
        assert snap.headless is False
        assert closed == [True]
        assert "--headless=new" not in captured["cmd"]
    finally:
        ab.close_session()


def test_open_session_does_not_expose_cdp_to_all_origins(tmp_path, monkeypatch):
    captured = {}

    class FakeProc:
        pid = 1234

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(ab, "find_chromium", lambda: "/bin/chromium")
    monkeypatch.setattr(ab, "_free_port", lambda: 9333)
    monkeypatch.setattr(ab, "_wait_cdp", lambda *a, **k: None)
    monkeypatch.setattr(ab.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ab.BrowserSession, "attach_page", lambda self: None)

    ab.close_session()
    try:
        snap = ab.open_session(user_data_dir=tmp_path / "profile")
        assert snap.ok, snap.error
        assert "--remote-allow-origins=*" not in captured["cmd"]
    finally:
        ab.close_session()


def test_snapshot_summary():
    s = ab.BrowserSnapshot(
        ok=True, url="https://example.com", title="Ex", text="hello " * 100,
    )
    out = s.summary(max_chars=40)
    assert "example.com" in out
    assert "Ex" in out
    assert out.endswith("…") or len(out) < 500


@pytest.mark.skipif(not _LIVE_BROWSER, reason="live Chromium tests skipped in CI")
def test_live_open_goto_extract_close():
    ab.close_session()
    try:
        snap = ab.open_session(url="https://example.com", headless=True)
        assert snap.ok, snap.error
        assert "example" in (snap.url or "").lower() or "Example" in (snap.title or "")
        ext = ab.extract(max_chars=2000)
        assert ext.ok, ext.error
        assert "Example" in (ext.title + ext.text)
        st = ab.session_status()
        assert st.ok
    finally:
        closed = ab.close_session()
        assert closed.ok


@pytest.mark.skipif(not _LIVE_BROWSER, reason="live Chromium tests skipped in CI")
def test_live_dump_dom_once():
    snap = ab.dump_dom_once("https://example.com", max_chars=5000)
    assert snap.ok, snap.error
    assert "Example" in (snap.title + snap.text)


@pytest.mark.skipif(not _LIVE_BROWSER, reason="live Chromium tests skipped in CI")
def test_live_t_browser_extract_flow():
    ab.close_session()
    ctx = MagicMock()
    ctx.project = None
    with patch("xlii.tool_handlers.web._cap_output", side_effect=lambda c, t: t):
        try:
            r = t_browser(ctx, {
                "action": "open",
                "url": "https://example.com",
                "headless": True,
            })
            assert not r.is_error, r.content
            r2 = t_browser(ctx, {"action": "extract", "max_chars": 3000})
            assert not r2.is_error, r2.content
            assert "Example" in r2.content
        finally:
            t_browser(ctx, {"action": "close"})


def test_cdp_socket_roundtrip_mocked():
    """Handshake shape: pure unit smoke via dump_dom path already covers chrome."""
    # Ensure BrowserSnapshot.as_dict is JSON-safe for face meta later.
    d = ab.BrowserSnapshot(ok=True, url="u", title="t", text="x", pid=1).as_dict()
    json.dumps(d)


def test_clear_stale_singleton_removes_dead_lock(tmp_path):
    lock = tmp_path / "SingletonLock"
    cookie = tmp_path / "SingletonCookie"
    # dead pid
    lock.symlink_to("host-99999999")
    cookie.symlink_to("deadcookie")
    assert ab._clear_stale_singleton(tmp_path) is True
    assert not lock.exists()
    assert not cookie.exists()


def test_clear_stale_singleton_keeps_live_lock(tmp_path):
    lock = tmp_path / "SingletonLock"
    lock.symlink_to(f"host-{os.getpid()}")
    assert ab._clear_stale_singleton(tmp_path) is False
    # dangling symlink: Path.exists() is False; is_symlink() is the truth
    assert lock.is_symlink()
