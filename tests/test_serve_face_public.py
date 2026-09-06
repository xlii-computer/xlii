"""The public face route (tauri-face V4) — grant wall + proxy + flip pins.

The SAME face_assets build the desktop app renders, served by PublicServer
behind the shipped pairing gate, each grant proxied to one shared face backend
(injectable spawner; the real one runs `xlii serve --face --handshake --view phone`).
Skips without the [web] extra, same doctrine as
test_serve_public_gate.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.test_serve_face import (  # noqa: F401 — helpers + autouse cleanup
    _cleanup_servers,
    _fake_state,
    _start_server,
)


def _need_web() -> None:
    pytest.importorskip("textual_serve")
    pytest.importorskip("aiohttp")


async def _ws_recv_until(ws, type_: str, *, limit: int = 20) -> dict:
    """Drain face-wire frames until *type_* (catalogs may land mid-handshake)."""
    import json

    for _ in range(limit):
        msg = json.loads(await ws.receive_str())
        if msg.get("type") == type_:
            return msg
    raise AssertionError(f"no {type_!r} within {limit} frames")


@pytest.fixture
def gate():
    from xlii.serve_gate import GateStore

    return GateStore(lockout_threshold=5, lockout_window_s=300,
                     lockout_duration_s=300)


def _make_server(tmp_path, gate, **overrides):
    _need_web()
    from xlii.serve_public import make_public_server

    kw = dict(
        gate=gate,
        base_url="https://example.test",
        preview_command="true --preview",
        state_dir=tmp_path,
        audit_log=tmp_path / "audit.log",
        sweep_interval_s=3600,
        host="127.0.0.1",
        port=8042,
    )
    kw.update(overrides)
    return make_public_server("true", **kw)


def _run_with_client(tmp_path, gate, coro_fn, **server_overrides):
    _need_web()
    from aiohttp.test_utils import TestClient, TestServer

    server = _make_server(tmp_path, gate, **server_overrides)

    async def _main():
        async def _quiet_startup(_app):
            pass

        server.on_startup = _quiet_startup  # type: ignore[method-assign]
        app = await server._make_app()
        tserver = TestServer(app)
        client = TestClient(tserver)
        await client.start_server()
        try:
            return await coro_fn(client, server)
        finally:
            async with server._face_lock:
                backend = server._face_backend
                server._face_backend = None
            if backend is not None:
                await server._reap_face_backend(backend, reason="shutdown", sid="")
            await client.close()

    return asyncio.run(_main())


async def _pair(
    client, gate, server, *, now=1_000.0, mode: str = "full",
    clock: dict | None = None,
) -> dict[str, str]:
    """Pair and return the grant as an explicit Cookie header: the grant
    cookie is Secure-flagged, and aiohttp's jar (correctly) refuses to send
    Secure cookies over the test client's plain http."""
    from xlii.serve_gate import mint_code
    from xlii.serve_public import GRANT_COOKIE

    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode=mode, now=now)
    if clock is not None:
        server._now_fn = lambda: clock["now"]
    else:
        server._now_fn = lambda: now
    r = await client.post("/pair", data={"code": code}, allow_redirects=False)
    assert r.status in {302, 303}
    set_cookie = "; ".join(r.headers.getall("Set-Cookie", []))
    token = set_cookie.split(f"{GRANT_COOKIE}=", 1)[1].split(";", 1)[0]
    return {"Cookie": f"{GRANT_COOKIE}={token}"}


class _FakeProc:
    """The spawner-contract shape: .stdin(.close), .wait(), .kill()."""

    def __init__(self):
        self.stdin_closed = False
        self.killed = False
        self.returncode = None
        # Lazily bind the Event to the running loop (construction often
        # happens outside asyncio.run / the test client loop).
        self._done = None
        outer = self

        class _Stdin:
            def close(self):
                outer.stdin_closed = True
                outer._event().set()

        self.stdin = _Stdin()

    def _event(self) -> asyncio.Event:
        if self._done is None:
            self._done = asyncio.Event()
        return self._done

    async def wait(self):
        await self._event().wait()
        return self.returncode if self.returncode is not None else 0

    def kill(self):
        self.killed = True
        self.returncode = -9
        self._event().set()


# ------------------------------------------------------------------- gates


def test_public_face_spawn_uses_phone_view():
    from pathlib import Path

    src = Path("xlii/serve_public.py").read_text()
    assert '"--view", "phone"' in src


def test_face_routes_401_without_grant(tmp_path, gate):
    async def body(client, _server):
        for path in ("/face", "/face/"):
            r = await client.get(path, allow_redirects=False)
            assert r.status == 303, f"{path} should redirect to / without a grant"
            assert r.headers.get("Location") == "/"
        for path in ("/face/ws", "/face/css/face.css"):
            r = await client.get(path, allow_redirects=False)
            assert r.status == 401, f"{path} leaked without a grant"

    _run_with_client(tmp_path, gate, body)


def test_granted_face_serves_real_index(tmp_path, gate):
    async def body(client, server):
        grant = await _pair(client, gate, server)
        r = await client.get("/face", headers=grant, allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/face/"
        r = await client.get("/face/", headers=grant)
        assert r.status == 200
        text = await r.text()
        assert "<title>xlii</title>" in text
        assert 'data-view="phone"' in text
        css = await client.get("/face/css/face.css", headers=grant)
        assert css.status == 200

    _run_with_client(tmp_path, gate, body)


def test_granted_landing_is_face(tmp_path, gate):
    async def body(client, server):
        grant = await _pair(client, gate, server)
        r = await client.get("/", headers=grant, allow_redirects=False)
        assert r.status == 302
        assert r.headers["Location"] == "/face/"

    _run_with_client(tmp_path, gate, body)


def test_pair_redirects_to_face(tmp_path, gate):
    async def body(client, server):
        from xlii.serve_gate import mint_code

        code = mint_code()
        gate.add_pending(code, ttl_s=300, mode="full", now=1_000.0)
        server._now_fn = lambda: 1_000.0
        r = await client.post("/pair", data={"code": code}, allow_redirects=False)
        assert r.status in {302, 303}
        assert r.headers["Location"] == "/face/"

    _run_with_client(tmp_path, gate, body)


def test_preview_grant_cannot_enter_face(tmp_path, gate):
    async def body(client, server):
        async def spawner():
            raise AssertionError("preview grants must not spawn a face backend")

        server._face_spawner = spawner
        grant = await _pair(client, gate, server, mode="preview")
        for path in ("/face", "/face/", "/face/ws", "/face/css/face.css"):
            r = await client.get(path, headers=grant, allow_redirects=False)
            assert r.status == 403, f"{path} accepted a preview grant"

    _run_with_client(tmp_path, gate, body)


def test_face_default_does_not_redirect_preview_grants(tmp_path, gate):
    async def body(client, server):
        grant = await _pair(client, gate, server, mode="preview")
        r = await client.get("/", headers=grant, allow_redirects=False)
        assert not (r.status == 302 and r.headers.get("Location") == "/face/")

    _run_with_client(tmp_path, gate, body)


def test_face_default_false_keeps_tui_landing(tmp_path, gate):
    async def body(client, server):
        grant = await _pair(client, gate, server)
        r = await client.get("/", headers=grant, allow_redirects=False)
        # Opt out: the granted landing stays whatever handle_index does
        # (textual-serve's page) — pin only that it is NOT the face redirect.
        assert not (r.status == 302 and r.headers.get("Location") == "/face/")

    _run_with_client(tmp_path, gate, body, face_default=False)


# -------------------------------------------------------------------- proxy


def test_face_ws_proxies_to_backend(tmp_path, gate):
    """Full pump: a granted browser socket reaches a REAL in-thread face
    server through the proxy — hello + mode_state arrive, ping → pong."""
    import json

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc = _FakeProc()

    async def body(client, server):
        async def spawner():
            return proc, port, "tok"

        server._face_spawner = spawner
        grant = await _pair(client, gate, server)
        ws = await client.ws_connect("/face/ws", headers=grant)
        hello = await _ws_recv_until(ws, "hello")
        assert hello["type"] == "hello"
        ms = await _ws_recv_until(ws, "mode_state")
        assert ms["type"] == "mode_state" and ms["posture"] == "chat"
        await _ws_recv_until(ws, "chrome_state")  # F1
        await ws.send_str(json.dumps({"type": "ping"}))
        pong = await _ws_recv_until(ws, "pong")
        assert pong["type"] == "pong"
        await ws.close()
        # The handler reaps the child once the browser leg closes.
        await asyncio.wait_for(proc._event().wait(), timeout=5)
        assert proc.stdin_closed

    _run_with_client(tmp_path, gate, body, face_linger_s=0)


def test_face_ws_reuses_one_backend_per_grant(tmp_path, gate):
    """Two browser tabs under one grant must be views over one face session.

    Spawning one backend per tab creates sibling full REPL sessions for the
    same project, bypassing the nested-session guard and risking lost writes.
    """
    import json

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc = _FakeProc()
    spawns = 0

    async def body(client, server):
        nonlocal spawns

        async def spawner():
            nonlocal spawns
            spawns += 1
            return proc, port, "tok"

        server._face_spawner = spawner
        grant = await _pair(client, gate, server)
        ws1 = await client.ws_connect("/face/ws", headers=grant)
        await _ws_recv_until(ws1, "hello")
        await _ws_recv_until(ws1, "mode_state")
        await _ws_recv_until(ws1, "chrome_state")  # F1

        ws2 = await client.ws_connect("/face/ws", headers=grant)
        await _ws_recv_until(ws2, "hello")
        await _ws_recv_until(ws2, "mode_state")
        await _ws_recv_until(ws2, "chrome_state")  # F1
        assert spawns == 1

        # Closing the old tab must not reap the shared child while ws2 is active.
        await ws1.close()
        for _ in range(20):
            backend = server._face_backend
            if backend is not None and len(backend.sockets) == 1 and backend.pending == 0:
                break
            await asyncio.sleep(0.05)
        backend = server._face_backend
        assert backend is not None
        assert len(backend.sockets) == 1 and backend.pending == 0
        assert not proc.stdin_closed

        await ws2.send_str(json.dumps({"type": "ping"}))
        pong = await _ws_recv_until(ws2, "pong")
        assert pong["type"] == "pong"
        await ws2.close()
        await asyncio.wait_for(proc._event().wait(), timeout=5)
        assert proc.stdin_closed

    _run_with_client(tmp_path, gate, body, face_linger_s=0)


def test_face_ws_input_refreshes_idle_activity_not_ping(tmp_path, gate):
    """A borrowed open tab should still idle out on pings, but not mid-use."""
    import json

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc = _FakeProc()
    clock = {"now": 1_000.0}

    async def body(client, server):
        async def spawner():
            return proc, port, "tok"

        def last_activity(sid: str) -> float:
            return next(s.last_activity for s in server.gate.sessions() if s.id == sid)

        server._face_spawner = spawner
        grant = await _pair(client, gate, server, now=clock["now"], clock=clock)
        server._now_fn = lambda: clock["now"]
        ws = await client.ws_connect("/face/ws", headers=grant)
        sid = server.gate.sessions()[0].id
        assert last_activity(sid) == 1_000.0
        await _ws_recv_until(ws, "hello")
        await _ws_recv_until(ws, "mode_state")
        await _ws_recv_until(ws, "chrome_state")  # F1

        # Switch to code posture before advancing the clock so the later /exit
        # input stays local and never asks the persona engine.
        await ws.send_str(json.dumps({"type": "set_posture", "posture": "code"}))
        await _ws_recv_until(ws, "mode_state")
        await _ws_recv_until(ws, "chrome_state")  # F1

        clock["now"] = 1_200.0
        await ws.send_str(json.dumps({"type": "ping"}))
        assert json.loads((await ws.receive_str()))["type"] == "pong"
        assert last_activity(sid) == 1_000.0

        await ws.send_str(json.dumps({"type": "input", "text": "/exit"}))

        async def touched():
            while last_activity(sid) != 1_200.0:
                await asyncio.sleep(0.01)

        await asyncio.wait_for(touched(), timeout=5)
        await ws.close()
        await asyncio.wait_for(proc._event().wait(), timeout=5)

    _run_with_client(tmp_path, gate, body, face_linger_s=0)


def test_revoke_closes_browser_socket_and_child(tmp_path, gate):
    import json

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc = _FakeProc()

    async def body(client, server):
        async def spawner():
            return proc, port, "tok"

        server._face_spawner = spawner
        grant = await _pair(client, gate, server)
        ws = await client.ws_connect("/face/ws", headers=grant)
        hello = json.loads((await ws.receive_str()))
        assert hello["type"] == "hello"
        mode = json.loads((await ws.receive_str()))
        assert mode["type"] == "mode_state"
        sid = server.gate.sessions()[0].id
        assert await server._revoke_now(sid) is True
        # The browser leg is closed by revoke; the child's stdin follows.
        for _ in range(5):
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=2)
            except asyncio.TimeoutError:
                pytest.fail("timed out waiting for revoke close frame on /face/ws")
            if msg.type.name in {"CLOSE", "CLOSING", "CLOSED"}:
                break
        else:
            pytest.fail("revoke did not close the browser socket")
        await asyncio.wait_for(proc._event().wait(), timeout=5)
        assert proc.stdin_closed

    _run_with_client(tmp_path, gate, body, face_linger_s=0)


def test_face_ws_cap_refuses(tmp_path, gate):
    async def body(client, server):
        grant = await _pair(client, gate, server)
        backend = server._face_backend
        if backend is None:
            from xlii.serve_public import _FaceBackend
            backend = _FaceBackend(proc=object(), port=1, token="t")
            server._face_backend = backend
        backend.sockets.update({object() for _ in range(server._face_view_cap())})
        r = await client.get("/face/ws", headers=grant)
        assert r.status == 503

    _run_with_client(tmp_path, gate, body)


def test_face_ws_view_cap_refuses(tmp_path, gate):
    """A single grant cannot open unbounded face views over one backend."""
    from xlii.serve_public import DEFAULT_MAX_FACE_VIEWS_PER_BACKEND, _FaceBackend

    async def body(client, server):
        grant = await _pair(client, gate, server)
        backend = _FaceBackend(proc=object(), port=1, token="t")
        backend.sockets.update({object() for _ in range(DEFAULT_MAX_FACE_VIEWS_PER_BACKEND)})
        server._face_backend = backend
        r = await client.get("/face/ws", headers=grant)
        assert r.status == 503

    _run_with_client(tmp_path, gate, body)


def test_spawn_failure_is_502(tmp_path, gate):
    async def body(client, server):
        async def spawner():
            raise RuntimeError("no backend for you")

        server._face_spawner = spawner
        grant = await _pair(client, gate, server)
        r = await client.get("/face/ws", headers=grant)
        assert r.status == 502

    _run_with_client(tmp_path, gate, body)


def test_face_ws_prepare_failure_reaps_spawned_child(tmp_path, gate, monkeypatch):
    _need_web()
    from aiohttp import web
    from xlii.serve_public import make_public_server

    server = make_public_server(
        "true",
        gate=gate,
        base_url="https://example.test",
        preview_command="true --preview",
        state_dir=tmp_path,
        audit_log=tmp_path / "audit.log",
        sweep_interval_s=3600,
        host="127.0.0.1",
        port=8042,
    )
    proc = _FakeProc()

    async def spawner():
        return proc, 12345, "tok"

    class BoomWS:
        closed = False

        def __init__(self, *args, **kwargs):
            pass

        async def prepare(self, request):
            raise ConnectionResetError("client left during websocket prepare")

        async def close(self):
            self.closed = True

    async def main():
        server._face_spawner = spawner
        server.face_linger_s = 0
        monkeypatch.setattr(web, "WebSocketResponse", BoomWS)
        with pytest.raises(ConnectionResetError):
            await server.handle_face_ws({"serve_session_id": "sid"})
        await asyncio.wait_for(proc._event().wait(), timeout=5)
        assert proc.stdin_closed
        assert server._session_ws == {}
        assert server._face_backend is None

    asyncio.run(main())


# ------------------------------------------------------------------ settings


def test_settings_default_face_on(monkeypatch):
    from xlii.config import GlobalConfig
    from xlii.serve_public import resolve_public_settings

    monkeypatch.setattr(GlobalConfig, "load", classmethod(lambda cls: GlobalConfig()))
    s = resolve_public_settings(SimpleNamespace(base_url="https://x.test"))
    assert s["face_default"] is True


def test_config_face_default_strict_bool(tmp_path):
    from xlii.config import GlobalConfig

    cfg = GlobalConfig()
    assert cfg.serve_public_face_default is True
    cfg.serve = {"public": {"face_default": True}}
    assert cfg.serve_public_face_default is True
    cfg.serve = {"public": {"face_default": False}}
    assert cfg.serve_public_face_default is False
    cfg.serve = {"public": {"face_default": "yes"}}
    assert cfg.serve_public_face_default is False
    cfg.serve = {}
    assert cfg.serve_public_face_default is True


# -------------------------------------------------- session continuity (slices 0–2)


def test_face_spawn_and_reap_audit_lines(tmp_path, gate):
    """Slice 0: audit contains face_spawned / face_reaped around one session."""
    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc = _FakeProc()

    async def body(client, server):
        async def spawner():
            return proc, port, "tok"

        server._face_spawner = spawner
        grant = await _pair(client, gate, server)
        ws = await client.ws_connect("/face/ws", headers=grant)
        await _ws_recv_until(ws, "hello")
        await ws.close()
        await asyncio.sleep(0.2)
        audit = (tmp_path / "audit.log").read_text()
        assert "face_spawned sid=" in audit
        assert "face_view_closed sid=" in audit
        assert "face_linger_armed sid=" in audit

    _run_with_client(tmp_path, gate, body, face_linger_s=60)


def test_face_back_refresh_reuses_backend(tmp_path, gate):
    """Slice 1 primary acceptance: Back/refresh same grant, same child."""
    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc = _FakeProc()
    spawns = 0
    clock = {"now": 1_000.0}

    async def body(client, server):
        nonlocal spawns

        async def spawner():
            nonlocal spawns
            spawns += 1
            return proc, port, "tok"

        server._face_spawner = spawner
        grant = await _pair(client, gate, server, now=clock["now"], clock=clock)
        ws = await client.ws_connect("/face/ws", headers=grant)
        await _ws_recv_until(ws, "hello")
        await ws.close()
        assert not proc.stdin_closed
        r = await client.get("/", headers=grant, allow_redirects=False)
        assert r.status == 302 and r.headers["Location"] == "/face/"
        ws2 = await client.ws_connect("/face/ws", headers=grant)
        await _ws_recv_until(ws2, "hello")
        assert spawns == 1
        assert not proc.stdin_closed
        await ws2.close()

    _run_with_client(tmp_path, gate, body, face_linger_s=1800)


def test_face_linger_zero_reaps_immediately(tmp_path, gate):
    """face_linger_s=0 restores immediate reap-at-zero-views."""
    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc = _FakeProc()

    async def body(client, server):
        async def spawner():
            return proc, port, "tok"

        server._face_spawner = spawner
        grant = await _pair(client, gate, server)
        ws = await client.ws_connect("/face/ws", headers=grant)
        await _ws_recv_until(ws, "hello")
        await ws.close()
        await asyncio.wait_for(proc._event().wait(), timeout=5)
        assert proc.stdin_closed
        audit = (tmp_path / "audit.log").read_text()
        assert "face_reaped" in audit and "reason=views0" in audit

    _run_with_client(tmp_path, gate, body, face_linger_s=0)


def test_face_linger_expiry_reaps(tmp_path, gate):
    """Linger timer reaps with no socket present."""
    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc = _FakeProc()
    clock = {"now": 1_000.0}

    async def body(client, server):
        async def spawner():
            return proc, port, "tok"

        server._face_spawner = spawner
        server.sweep_interval_s = 0.05
        grant = await _pair(client, gate, server, now=clock["now"], clock=clock)
        ws = await client.ws_connect("/face/ws", headers=grant)
        await _ws_recv_until(ws, "hello")
        await ws.close()
        for _ in range(50):
            if server._face_backend is not None and server._face_backend.idle_since is not None:
                break
            await asyncio.sleep(0.05)
        assert server._face_backend is not None
        assert server._face_backend.idle_since is not None
        assert not proc.stdin_closed
        clock["now"] = 1_061.0
        await server._check_face_linger_expiry()
        await asyncio.wait_for(proc._event().wait(), timeout=5)
        audit = (tmp_path / "audit.log").read_text()
        assert "face_reaped" in audit and "reason=linger" in audit

    _run_with_client(tmp_path, gate, body, face_linger_s=60)


def test_backend_activity_touch_advances_idle(tmp_path, gate):
    """assistant_chunk from backend advances grant idle clock; pong does not."""
    import json

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc = _FakeProc()
    clock = {"now": 1_000.0}

    async def body(client, server):
        async def spawner():
            return proc, port, "tok"

        def last_activity(sid: str) -> float:
            return next(s.last_activity for s in server.gate.sessions() if s.id == sid)

        server._face_spawner = spawner
        grant = await _pair(client, gate, server, now=clock["now"], clock=clock)
        ws = await client.ws_connect("/face/ws", headers=grant)
        sid = server.gate.sessions()[0].id
        await _ws_recv_until(ws, "hello")
        clock["now"] = 1_200.0
        backend = server._face_backend
        assert backend is not None
        server._touch_grants_from_backend_frame(
            backend, json.dumps({"type": "assistant_chunk", "text": "hi"})
        )
        assert last_activity(sid) == 1_200.0
        server._touch_grants_from_backend_frame(
            backend, json.dumps({"type": "pong"})
        )
        assert last_activity(sid) == 1_200.0
        await ws.close()

    _run_with_client(tmp_path, gate, body, face_linger_s=0)


def test_p2_attach_new_grant_to_lingering_backend(tmp_path, gate):
    """Slice 2: grant B attaches to live backend after grant A expires."""
    import json

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc = _FakeProc()
    spawns = 0
    clock = {"now": 1_000.0}

    async def body(client, server):
        nonlocal spawns

        async def spawner():
            nonlocal spawns
            spawns += 1
            return proc, port, "tok"

        server._face_spawner = spawner
        grant_a = await _pair(client, gate, server, now=clock["now"], clock=clock)
        ws = await client.ws_connect("/face/ws", headers=grant_a)
        hello_a = await _ws_recv_until(ws, "hello")
        session_id = hello_a["session"]
        await ws.send_str(json.dumps({"type": "input", "text": "first grant"}))
        await ws.close()
        sid_a = server.gate.sessions()[0].id
        assert await server._revoke_now(sid_a) is True
        assert sid_a not in [s.id for s in server.gate.sessions()]
        assert not proc.stdin_closed
        grant_b = await _pair(client, gate, server, now=clock["now"], clock=clock)
        ws2 = await client.ws_connect("/face/ws", headers=grant_b)
        hello_b = await _ws_recv_until(ws2, "hello")
        assert spawns == 1
        assert hello_b.get("resumed") is True
        assert hello_b["session"] == session_id
        await ws2.close()

    _run_with_client(tmp_path, gate, body, face_linger_s=1800)


def test_failed_pump_then_linger_expiry_reaps_child(tmp_path, gate):
    """B1: a dead upstream pump must not block linger reap (stdin + audit)."""
    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc = _FakeProc()
    clock = {"now": 1_000.0}

    async def body(client, server):
        async def spawner():
            return proc, port, "tok"

        server._face_spawner = spawner
        grant = await _pair(client, gate, server, now=clock["now"], clock=clock)
        ws = await client.ws_connect("/face/ws", headers=grant)
        await _ws_recv_until(ws, "hello")
        backend = server._face_backend
        assert backend is not None

        async def _boom():
            raise ConnectionError("upstream pump died")

        if backend.upstream_task is not None:
            backend.upstream_task.cancel()
            try:
                await backend.upstream_task
            except (asyncio.CancelledError, Exception):
                pass
        failed = asyncio.create_task(_boom())
        await asyncio.sleep(0)
        assert failed.done() and failed.exception() is not None
        backend.upstream_task = failed

        await ws.close()
        for _ in range(50):
            if backend.idle_since is not None:
                break
            await asyncio.sleep(0.05)
        assert not proc.stdin_closed
        clock["now"] = 1_061.0
        await server._check_face_linger_expiry()
        await asyncio.wait_for(proc._event().wait(), timeout=5)
        assert proc.stdin_closed
        audit = (tmp_path / "audit.log").read_text()
        assert "face_reaped" in audit and "reason=linger" in audit

    _run_with_client(tmp_path, gate, body, face_linger_s=60)


def test_dead_proc_during_linger_respawns_on_reconnect(tmp_path, gate):
    """B2: a dead child during linger must respawn on reconnect, not re-arm linger."""
    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    proc1 = _FakeProc()
    proc2 = _FakeProc()
    spawns = 0
    clock = {"now": 1_000.0}

    async def body(client, server):
        nonlocal spawns

        async def spawner():
            nonlocal spawns
            spawns += 1
            return (proc1 if spawns == 1 else proc2), port, "tok"

        server._face_spawner = spawner
        grant = await _pair(client, gate, server, now=clock["now"], clock=clock)
        ws = await client.ws_connect("/face/ws", headers=grant)
        await _ws_recv_until(ws, "hello")
        await ws.close()
        for _ in range(50):
            backend = server._face_backend
            if backend is not None and backend.idle_since is not None:
                break
            await asyncio.sleep(0.05)
        assert server._face_backend is not None
        proc1.returncode = 1
        ws2 = await client.ws_connect("/face/ws", headers=grant)
        await _ws_recv_until(ws2, "hello")
        assert spawns == 2
        assert proc1.stdin_closed
        assert not proc2.stdin_closed
        await ws2.close()

    _run_with_client(tmp_path, gate, body, face_linger_s=1800)


def test_public_banner_mentions_grants_mortal():
    from xlii.serve_public import public_banner_lines

    lines = public_banner_lines(
        base_url="https://x.test",
        host="127.0.0.1",
        port=8042,
        audit_log="/tmp/audit.log",
    )
    assert any("grants are mortal" in ln for ln in lines)
    assert any("face lingers" in ln for ln in lines)
