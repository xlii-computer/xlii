"""``xlii serve --public`` gate (serve-public V2) — offline + aiohttp pins.

Exit gate from proposals/serve-public-fleet.md:
  GET never consumes · POST consumes exactly once · cookie flags pinned ·
  no-grant WS rejected · wildcard bind refuses · banner text pinned.

Server-integration bits that need textual-serve are gated with skipif on the
``[web]`` extra (same doctrine as daemon tests).
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from types import SimpleNamespace

import pytest

from xlii.cmds.serve_web import cmd_serve
from xlii.serve_public import (
    ENTRY_SPENT_MSG,
    GRANT_COOKIE,
    _TouchingWS,
    client_addr,
    entry_html,
    origin_mismatch,
    public_banner_lines,
    public_bind_error,
    resolve_public_settings,
)

# NOTE: no module-level importorskip — the pure pins below must run without the
# [web] extra (house rule: tests offline & pinned). Server-integration helpers
# skip themselves via _need_web().


def _need_web() -> None:
    pytest.importorskip("textual_serve")
    pytest.importorskip("aiohttp")


# --------------------------------------------------------------------------- #
# Pure / offline pins (no server)
# --------------------------------------------------------------------------- #


def _req(remote: str, **headers: str) -> SimpleNamespace:
    return SimpleNamespace(remote=remote, headers=dict(headers))


def test_client_addr_trusts_xff_only_from_loopback_peer():
    # Behind our own Caddy hop: rightmost XFF entry is what the proxy saw.
    assert client_addr(_req("127.0.0.1", **{"X-Forwarded-For": "203.0.113.9"})) == "203.0.113.9"
    assert (
        client_addr(_req("127.0.0.1", **{"X-Forwarded-For": "6.6.6.6, 203.0.113.9"}))
        == "203.0.113.9"
    )  # client-forged left entries are ignored
    assert client_addr(_req("::1", **{"X-Forwarded-For": "203.0.113.9"})) == "203.0.113.9"
    # Direct (non-loopback) peer: the header is trivially spoofable — ignore it.
    assert client_addr(_req("198.51.100.4", **{"X-Forwarded-For": "1.2.3.4"})) == "198.51.100.4"
    # No header / blank header → the socket peer.
    assert client_addr(_req("127.0.0.1")) == "127.0.0.1"
    assert client_addr(_req("127.0.0.1", **{"X-Forwarded-For": ""})) == "127.0.0.1"


def test_origin_mismatch_rejects_foreign_browser_posts():
    base = "https://xlii-code.com"
    # Matching origin — incl. default-port normalization — passes.
    assert origin_mismatch(_req("127.0.0.1", Origin="https://xlii-code.com"), base) is False
    assert origin_mismatch(_req("127.0.0.1", Origin="https://xlii-code.com:443"), base) is False
    # Foreign origin / scheme flip / garbage → refused.
    assert origin_mismatch(_req("127.0.0.1", Origin="https://evil.example"), base) is True
    assert origin_mismatch(_req("127.0.0.1", Origin="http://xlii-code.com"), base) is True
    # Referer is the fallback signal.
    assert origin_mismatch(_req("127.0.0.1", Referer="https://evil.example/lure"), base) is True
    assert origin_mismatch(_req("127.0.0.1", Referer="https://xlii-code.com/"), base) is False
    # Header-less clients (curl, tests) have no browser to ride — allowed.
    assert origin_mismatch(_req("127.0.0.1"), base) is False


def test_touching_ws_counts_stdin_frames_only():
    """Idle must measure typing, not keepalives — the browser pings on a timer,
    and counting pings would make idle_timeout_s unreachable for an open tab."""
    frames = [
        SimpleNamespace(data='["ping", 1]'),
        SimpleNamespace(data='["stdin", "ls\\n"]'),
        SimpleNamespace(data='["resize", {"width": 80}]'),
        SimpleNamespace(data='["stdin", "x"]'),
        SimpleNamespace(data=b"\x01binary"),
    ]

    class FakeWS:
        marker = "fwd"

        def __aiter__(self):
            async def g():
                for f in frames:
                    yield f
            return g()

    touches = []
    wrapped = _TouchingWS(FakeWS(), lambda: touches.append(1))

    async def drain():
        return [m async for m in wrapped]

    out = asyncio.run(drain())
    assert out == frames                    # frames pass through untouched
    assert len(touches) == 2                # exactly the two stdin envelopes
    assert wrapped.marker == "fwd"          # attribute forwarding intact


def test_public_banner_pins_code_gated_and_audit_path():
    lines = public_banner_lines(
        base_url="https://xlii-code.com",
        host="127.0.0.1",
        port=8042,
        audit_log="/tmp/serve-audit.log",
    )
    joined = "\n".join(lines)
    assert "serve --public:" in joined
    assert "https://xlii-code.com" in joined
    assert "code-gated" in joined
    assert "no terminal stream without a grant" in joined
    assert "loopback" in joined
    assert "Caddy" in joined
    assert "/tmp/serve-audit.log" in joined


def test_wildcard_bind_is_startup_error_not_warning():
    assert public_bind_error("0.0.0.0") is not None
    assert "refusing wildcard" in public_bind_error("0.0.0.0")
    assert public_bind_error("::") is not None
    assert public_bind_error("100.64.0.7") is not None
    assert public_bind_error("127.0.0.1") is None
    assert public_bind_error("localhost") is None


def test_cmd_serve_public_refuses_wildcard_bind(capsys):
    args = argparse.Namespace(
        ws=False,
        public=True,
        host="0.0.0.0",
        port=8042,
        preview=False,
        base_url="https://example.test",
    )
    rc = cmd_serve(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "refusing wildcard" in err


def test_cmd_serve_public_requires_base_url(capsys, monkeypatch, tmp_path):
    # Isolate from the operator's real ~/.config/xlii (test-isolation rule).
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path))
    args = argparse.Namespace(
        ws=False,
        public=True,
        host="127.0.0.1",
        port=8042,
        preview=False,
        base_url=None,
    )
    assert resolve_public_settings(args)["base_url"] is None
    rc = cmd_serve(args)
    assert rc == 1
    err = capsys.readouterr().err
    assert "base URL" in err or "base_url" in err


def test_entry_html_is_a_post_form():
    html = entry_html()
    assert 'method="post"' in html.lower() or "method='post'" in html.lower()
    assert 'action="/pair"' in html
    assert 'name="code"' in html
    assert "X7K2-M9Q4" in html


def test_entry_html_prefills_and_escapes_code():
    # The magic-link prefill: ?code= lands in the input's value.
    assert 'value="X7K2-M9Q4"' in entry_html(code="X7K2-M9Q4")
    # Default renders an empty slot.
    assert 'value=""' in entry_html()
    # Arbitrary input is escaped — the function must stay safe on its own,
    # even though the caller only passes peek-validated codes.
    hostile = entry_html(code='"><script>x</script>')
    # The history helper is a real script; the prefill must still be escaped.
    assert 'value=""><script>' not in hostile
    assert "&lt;script&gt;" in hostile


def test_entry_html_guards_bfcache_and_replaces_pair_landing():
    """Spent-webcode Back must not look like a live pair.

    pageshow/bfcache → try /face/ (live grant stays sitting; dead grant
    303s home). Successful pair uses fetch + location.replace so the
    enter-code GET is not left under /face/ in history.
    """
    html = entry_html()
    assert "pageshow" in html
    assert "location.replace" in html
    assert 'fetch("/pair"' in html or "fetch('/pair'" in html
    assert "back_forward" in html
    assert "persisted" in html
    assert "xlii-face-leave" in html
    assert ENTRY_SPENT_MSG in html
    # Prefill is still a real input — the script clears it on spent restore.
    assert 'id="code"' in html


def test_cmd_serve_public_closed_door_requires_redirect(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("XLII_CONFIG_DIR", str(tmp_path))
    import xlii.serve_public as SP

    monkeypatch.setattr(
        SP,
        "resolve_public_settings",
        lambda a=None: {
            "base_url": "https://example.test",
            "code_ttl_s": 300,
            "session_ttl_s": 28800,
            "idle_timeout_s": 1800,
            "max_sessions": 3,
            "closed_door": True,
            "redirect_off_url": None,
        },
    )
    args = argparse.Namespace(
        ws=False, public=True, host="127.0.0.1", port=8042,
        preview=False, base_url=None,
    )
    rc = cmd_serve(args)
    assert rc == 1
    assert "redirect_off_url" in capsys.readouterr().err


def test_serve_help_lists_public_flag():
    r = subprocess.run(
        [sys.executable, "-m", "xlii", "serve", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "--public" in r.stdout
    assert "--base-url" in r.stdout


# --------------------------------------------------------------------------- #
# aiohttp app: GET / POST / cookie / WS gate (sync wrappers around asyncio)
# --------------------------------------------------------------------------- #


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
    """Spin an aiohttp TestClient, run ``coro_fn(client, server)``, tear down."""
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
            await client.close()

    return asyncio.run(_main())


@pytest.fixture
def gate():
    from xlii.serve_gate import GateStore

    return GateStore(lockout_threshold=5, lockout_window_s=300, lockout_duration_s=300)


def test_get_never_consumes_pending_code(tmp_path, gate):
    from xlii.serve_gate import mint_code

    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode="full", now=1_000.0)

    async def body(client, server):
        server._now_fn = lambda: 1_000.0
        r = await client.get("/")
        assert r.status == 200
        text = await r.text()
        assert "Pairing code" in text
        assert 'name="code"' in text
        # Pending code still there — GET must not consume.
        session = gate.consume(code, now=1_001.0, remote="127.0.0.1")
        assert session is not None

    _run_with_client(tmp_path, gate, body)


def test_post_consumes_exactly_once_and_sets_cookie_flags(tmp_path, gate):
    from xlii.serve_gate import mint_code

    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode="full", now=2_000.0)

    async def body(client, server):
        server._now_fn = lambda: 2_000.0
        r = await client.post("/pair", data={"code": code}, allow_redirects=False)
        assert r.status in {302, 303}
        set_cookie = r.headers.getall("Set-Cookie", [])
        assert set_cookie, "expected Set-Cookie on successful pair"
        joined = "; ".join(set_cookie)
        assert GRANT_COOKIE in joined
        assert "HttpOnly" in joined or "httponly" in joined.lower()
        assert "Secure" in joined or "secure" in joined.lower()
        assert "samesite=strict" in joined.lower()

        r2 = await client.post("/pair", data={"code": code}, allow_redirects=False)
        assert r2.status == 403
        text = await r2.text()
        assert "Invalid or expired" in text or "err" in text

    _run_with_client(tmp_path, gate, body)


def test_no_grant_ws_upgrade_rejected(tmp_path, gate):
    async def body(client, _server):
        r = await client.get("/ws")
        assert r.status == 401
        text = await r.text()
        assert "pairing" in text.lower()

    _run_with_client(tmp_path, gate, body)


def test_ws_with_grant_cookie_passes_middleware(tmp_path, gate):
    from aiohttp.client_exceptions import ServerDisconnectedError
    from xlii.serve_gate import mint_code

    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode="full", now=3_000.0)

    async def body(client, server):
        server._now_fn = lambda: 3_000.0
        r = await client.post("/pair", data={"code": code}, allow_redirects=False)
        assert r.status in {302, 303}
        morsel = r.cookies.get(GRANT_COOKIE)
        assert morsel is not None
        sid = morsel.value

        # Middleware must NOT 401. A plain GET /ws fails the WS handshake
        # (400 / disconnect) — that is past the gate, which is the pin.
        try:
            r2 = await client.get("/ws", cookies={GRANT_COOKIE: sid})
            assert r2.status != 401
        except ServerDisconnectedError:
            pass  # handshake failed after the grant check — gate opened

    _run_with_client(tmp_path, gate, body)


# --------------------------------------------------------------------------- #
# The fix batch: cookie≠sid · CSRF · XFF buckets · revoke drain · cap · audit
# --------------------------------------------------------------------------- #


def test_cookie_is_a_token_not_the_session_id(tmp_path, gate):
    """The sid is a visible admin handle (`serve sessions` / `webcode ls` /
    mirror / audit); the cookie must be a separate secret or every one of
    those surfaces is a bearer-token leak."""
    from xlii.serve_gate import mint_code

    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode="full", now=5_000.0)

    async def body(client, server):
        server._now_fn = lambda: 5_000.0
        r = await client.post("/pair", data={"code": code}, allow_redirects=False)
        assert r.status in {302, 303}
        token = r.cookies[GRANT_COOKIE].value
        sids = [s.id for s in gate.sessions()]
        assert len(sids) == 1
        assert token != sids[0]                      # never the sid itself
        assert server._cookie_tokens[token] == sids[0]
        # Forging the cookie with the (public) sid must NOT open the gate.
        r2 = await client.get("/ws", cookies={GRANT_COOKIE: sids[0]})
        assert r2.status == 401

    _run_with_client(tmp_path, gate, body)


def test_cross_origin_pair_refused_without_consuming(tmp_path, gate):
    """#287 login-CSRF: a foreign page auto-POSTing a code must be refused
    before any gate accounting — no consume, no lockout hit for the victim."""
    from xlii.serve_gate import mint_code

    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode="full", now=6_000.0)

    async def body(client, server):
        server._now_fn = lambda: 6_000.0
        r = await client.post(
            "/pair",
            data={"code": code},
            headers={"Origin": "https://evil.example"},
            allow_redirects=False,
        )
        assert r.status == 403
        assert "Cross-origin" in await r.text()
        assert gate.sessions() == []                     # nothing paired
        # Code survives and lockout was NOT advanced: same-origin retry works.
        r2 = await client.post(
            "/pair",
            data={"code": code},
            headers={"Origin": "https://example.test"},
            allow_redirects=False,
        )
        assert r2.status in {302, 303}

    _run_with_client(tmp_path, gate, body)


def test_xff_gives_per_client_lockout_buckets(tmp_path, gate):
    """#285: behind Caddy every socket peer is loopback — the forwarded client
    address must key the lockout, or one attacker's failures lock out everyone."""
    async def body(client, server):
        server._now_fn = lambda: 7_000.0
        for _ in range(5):
            r = await client.post(
                "/pair",
                data={"code": "WRONGCOD"},
                headers={"X-Forwarded-For": "203.0.113.66"},
                allow_redirects=False,
            )
            assert r.status == 403
        # The hammering client is locked…
        r = await client.post(
            "/pair",
            data={"code": "WRONGCOD"},
            headers={"X-Forwarded-For": "203.0.113.66"},
            allow_redirects=False,
        )
        assert r.status == 429
        # …while a different forwarded client is not.
        r = await client.post(
            "/pair",
            data={"code": "WRONGCOD"},
            headers={"X-Forwarded-For": "198.51.100.7"},
            allow_redirects=False,
        )
        assert r.status == 403

    _run_with_client(tmp_path, gate, body)


def test_sweep_drains_revoke_queue_and_kills_sessions(tmp_path, gate):
    """#284: `xlii serve revoke` / `webcode kill` append to serve-revokes.json;
    the sweep must consume it — gate entry dies, cookie token dies, and the
    mirror is rewritten without the sid (no resurrection on later rewrites)."""
    import json as _json

    from xlii.serve_gate import mint_code
    from xlii.serve_spool import append_revoke

    c1, c2 = mint_code(), mint_code()
    gate.add_pending(c1, ttl_s=300, mode="full", now=8_000.0)
    gate.add_pending(c2, ttl_s=300, mode="full", now=8_000.0)

    async def body(client, server):
        server._now_fn = lambda: 8_000.0
        r1 = await client.post("/pair", data={"code": c1}, allow_redirects=False)
        assert r1.status in {302, 303}
        token1 = r1.cookies[GRANT_COOKIE].value
        sid1 = server._cookie_tokens[token1]

        append_revoke(tmp_path, sid1)
        await server._sweep_tick()

        assert all(s.id != sid1 for s in gate.sessions())
        assert token1 not in server._cookie_tokens
        mirror = _json.loads((tmp_path / "serve-sessions.json").read_text())
        assert all(s["id"] != sid1 for s in mirror["sessions"])
        # Queue is consumed, not re-applied forever.
        assert _json.loads((tmp_path / "serve-revokes.json").read_text())["pending"] == []
        # The dead grant is really dead at the HTTP layer too.
        r = await client.get("/ws", cookies={GRANT_COOKIE: token1})
        assert r.status == 401

        # The 'all' sentinel (webcode kill all / serve revoke all) reaps the rest.
        r2 = await client.post("/pair", data={"code": c2}, allow_redirects=False)
        assert r2.status in {302, 303}
        assert len(gate.sessions()) == 1
        append_revoke(tmp_path, "all")
        await server._sweep_tick()
        assert gate.sessions() == []
        assert server._cookie_tokens == {}

    _run_with_client(tmp_path, gate, body)


def test_concurrent_pairs_cannot_exceed_max_sessions(tmp_path, gate):
    """TOCTOU guard: two valid codes racing the pre-check must still end at
    ≤ max_sessions — the post-consume re-check gives the loser's slot back."""
    from xlii.serve_gate import mint_code

    c1, c2 = mint_code(), mint_code()
    gate.add_pending(c1, ttl_s=300, mode="full", now=9_000.0)
    gate.add_pending(c2, ttl_s=300, mode="full", now=9_000.0)

    async def body(client, server):
        server._now_fn = lambda: 9_000.0
        r1, r2 = await asyncio.gather(
            client.post("/pair", data={"code": c1}, allow_redirects=False),
            client.post("/pair", data={"code": c2}, allow_redirects=False),
        )
        statuses = sorted((r1.status, r2.status))
        assert statuses[0] in {302, 303} and statuses[1] in {303, 503}
        assert len(gate.sessions()) <= 1

    _run_with_client(tmp_path, gate, body, max_sessions=1)


def test_audit_line_survives_path_injection(tmp_path, gate):
    """N2: request.path is percent-decoded attacker input — a %0a must not
    forge a second audit line."""
    async def body(client, server):
        server._now_fn = lambda: 9_500.0
        r = await client.get("/download/%0a999.0%20pair_ok%20sid=forged")
        assert r.status == 401

    _run_with_client(tmp_path, gate, body)
    audit = (tmp_path / "audit.log").read_text()
    lines = [ln for ln in audit.splitlines() if ln.strip()]
    assert len(lines) == 1                       # no forged extra line
    assert "ws_rejected" in lines[0]
    assert "\\n" in lines[0]                     # newline arrived escaped


def test_command_resolves_per_connection_context(tmp_path, gate):
    """#286: preview must come from the request-task's session, never from a
    shared attribute another connection can flip mid-handshake."""
    from xlii.serve_gate import mint_code
    from xlii.serve_public import _WS_SESSION

    server = _make_server(tmp_path, gate)
    assert server.command == "true"                       # no session context

    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode="preview", now=10_000.0)
    session = gate.consume(code, now=10_000.0, remote="203.0.113.1")
    assert session is not None and session.mode == "preview"

    async def in_preview_task():
        tok = _WS_SESSION.set(session)
        try:
            return server.command
        finally:
            _WS_SESSION.reset(tok)

    async def main():
        # The preview context sees the preview command…
        got_preview = await asyncio.create_task(in_preview_task())
        # …while a concurrent bare context still sees the base command.
        got_base = server.command
        return got_preview, got_base

    got_preview, got_base = asyncio.run(main())
    assert got_preview == "true --preview"
    assert got_base == "true"


def test_process_messages_registers_live_socket(tmp_path, gate):
    """The kill switch needs the socket WHILE it is being served — the old
    code registered it only after upstream returned (already closed)."""
    from xlii.serve_gate import mint_code
    from xlii.serve_public import _WS_SESSION

    server = _make_server(tmp_path, gate)
    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode="full", now=11_000.0)
    session = gate.consume(code, now=11_000.0, remote="203.0.113.1")
    assert session is not None
    seen = {}

    class FakeWS:
        def __aiter__(self):
            async def g():
                regs = server._session_ws.get(session.id) or set()
                seen["live_during_serve"] = self in regs
                if False:
                    yield None
            return g()

    async def main():
        tok = _WS_SESSION.set(session)
        try:
            await server._process_messages(FakeWS(), app_service=None)
        finally:
            _WS_SESSION.reset(tok)

    asyncio.run(main())
    assert seen["live_during_serve"] is True
    assert session.id not in server._session_ws       # cleaned up on close


# --------------------------------------------------------------------------- #
# Closed-door (app-serving S1): peek-prefill · 302-off · grant unchanged
# --------------------------------------------------------------------------- #


def test_magic_link_code_prefills_form_without_consuming(tmp_path, gate):
    from xlii.serve_gate import mint_code

    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode="full", now=1_000.0)

    async def body(client, server):
        server._now_fn = lambda: 1_000.0
        # Messy-but-live ?code= → 200 with the normalized grouped prefill.
        r = await client.get("/", params={"code": code.lower()})
        assert r.status == 200
        assert f'value="{code}"' in await r.text()
        # The peek must not consume — the code still pairs.
        assert gate.consume(code, now=1_001.0, remote="127.0.0.1") is not None

    _run_with_client(tmp_path, gate, body)


def test_spent_magic_link_shows_fresh_webcode_not_prefill(tmp_path, gate):
    """Re-GET of a spent ?code= must not look like an open sitting."""
    from xlii.serve_gate import mint_code

    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode="full", now=1_000.0)
    assert gate.consume(code, now=1_000.0, remote="127.0.0.1") is not None

    async def body(client, server):
        server._now_fn = lambda: 1_001.0
        r = await client.get("/", params={"code": code})
        assert r.status == 200
        text = await r.text()
        assert ENTRY_SPENT_MSG in text
        assert f'value="{code}"' not in text
        assert 'value=""' in text
        assert r.headers.get("Cache-Control") == "no-store"

    _run_with_client(tmp_path, gate, body)


def test_live_grant_returns_to_face_even_with_spent_code(tmp_path, gate):
    """Back to /?code=spent with a live cookie must stay on Face, not the form."""
    from xlii.serve_gate import mint_code

    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode="full", now=4_000.0)

    async def body(client, server):
        server._now_fn = lambda: 4_000.0
        r = await client.post("/pair", data={"code": code}, allow_redirects=False)
        assert r.status in {302, 303}
        token = r.cookies[GRANT_COOKIE].value
        r2 = await client.get(
            "/",
            params={"code": code},
            cookies={GRANT_COOKIE: token},
            allow_redirects=False,
        )
        assert r2.status == 302
        assert r2.headers["Location"] == "/face/"

    _run_with_client(tmp_path, gate, body)


def test_closed_door_302s_without_grant_or_live_code(tmp_path, gate):
    from xlii.serve_gate import mint_code

    dead = mint_code()
    gate.add_pending(dead, ttl_s=300, mode="full", now=1_000.0)

    async def body(client, server):
        server._now_fn = lambda: 2_000.0          # `dead` is expired now
        # Bare GET / → off, silently.
        r = await client.get("/", allow_redirects=False)
        assert r.status == 302
        assert r.headers["Location"] == "https://example.org/off"
        # Expired / bogus ?code= → the same silent 302; nothing consumed,
        # no lockout accounting from GET probes.
        for probe in (dead, "ZZZZ-ZZZZ", "garbage"):
            r = await client.get("/", params={"code": probe}, allow_redirects=False)
            assert r.status == 302
        assert gate._fail_windows == {}

        # A live code still opens the prefilled door.
        live = mint_code()
        gate.add_pending(live, ttl_s=300, mode="full", now=2_000.0)
        r = await client.get("/", params={"code": live}, allow_redirects=False)
        assert r.status == 200
        assert f'value="{live}"' in await r.text()

    _run_with_client(
        tmp_path, gate, body,
        closed_door=True, redirect_off_url="https://example.org/off",
    )


def test_closed_door_grant_still_enters(tmp_path, gate):
    """The 302 is surface reduction, not the lock: a paired browser passes."""
    from xlii.serve_gate import mint_code

    code = mint_code()
    gate.add_pending(code, ttl_s=300, mode="full", now=3_000.0)

    async def body(client, server):
        from aiohttp import web

        server._now_fn = lambda: 3_000.0

        async def fake_index(request):
            return web.Response(text="TUI")

        server.handle_index = fake_index

        r = await client.post("/pair", data={"code": code}, allow_redirects=False)
        assert r.status in {302, 303}
        token = r.cookies[GRANT_COOKIE].value
        r2 = await client.get(
            "/", cookies={GRANT_COOKIE: token}, allow_redirects=False
        )
        assert r2.status == 200
        assert await r2.text() == "TUI"

    _run_with_client(
        tmp_path, gate, body,
        closed_door=True, redirect_off_url="https://example.org/off",
        face_default=False,
    )


def test_closed_door_requires_redirect_off_url(tmp_path, gate):
    with pytest.raises(ValueError) as ei:
        _make_server(tmp_path, gate, closed_door=True)
    assert "redirect_off_url" in str(ei.value)
