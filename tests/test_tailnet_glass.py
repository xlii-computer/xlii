"""G1/G2 — TailnetDoor glass tier: WhoIs on assets, CSP, webcode spend."""
from __future__ import annotations

import base64
import socket
import time
from types import SimpleNamespace

import pytest

from xlii.occupancy_store import load_live, mutate
from xlii.serve_face.http import TailnetDoor
from xlii.serve_gate import mint_code, normalize_code
from xlii.serve_spool import append_pending, default_state_dir
from xlii.tailnet import TailnetPeer, TailnetSelf


@pytest.fixture(autouse=True)
def _clear_agent_browser_on_change():
    """Isolate glass tests from FaceServer on_change listener accumulation.

    ``xlii.agent_browser.on_change`` appends forever; dead FaceServers'
    ``_on_browser_change`` → ``chrome_state`` can still write the *current*
    test's XLII_OCCUPANCY_PATH during a full suite.
    """
    import xlii.agent_browser as ab

    saved = list(ab._ON_CHANGE)
    ab._ON_CHANGE.clear()
    try:
        yield
    finally:
        ab._ON_CHANGE[:] = saved


def _phone_peer():
    return TailnetPeer(
        node_name="phone",
        dns_name="phone.tailnet.ts.net",
        stable_id="nPHONE",
        addrs=("127.0.0.1",),
        user="you@github",
        tags=(),
        online=True,
    )


def _http(host: str, port: int, raw: bytes, timeout: float = 2.0) -> bytes:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.sendall(raw)
    sock.settimeout(timeout)
    chunks = []
    try:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    except TimeoutError:
        pass
    sock.close()
    return b"".join(chunks)


def _open_door(tmp_path, monkeypatch, *, glass: bool = False, device: str = "phone"):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setattr(
        "xlii.face_remote.load_tailnet_allowlist", lambda path=None: ["phone"],
    )
    monkeypatch.setattr("xlii.tailnet.whois", lambda ip, port: _phone_peer())
    st = TailnetSelf(
        up=True, ipv4="127.0.0.1", addrs=("127.0.0.1",),
        dns_name="throne", node_name="throne",
    )
    monkeypatch.setattr("xlii.tailnet.self_status", lambda: st)
    from xlii.serve_face.wire import default_assets_dir

    server = SimpleNamespace(assets_dir=default_assets_dir())
    door = TailnetDoor(token="desk-boot-token", server=server, port=0)
    now = time.time()
    mutate(
        lambda o: o.open_remote_lab(
            now=now, device=device, tier="glass" if glass else "door",
        ),
        now=now,
    )
    door.sync()
    return door


def test_door_tier_still_404s_assets(tmp_path, monkeypatch):
    door = _open_door(tmp_path, monkeypatch, glass=False)
    try:
        assert door.bound is not None
        host, port = door.bound
        got = _http(host, port, b"GET /css/face.css HTTP/1.1\r\nHost: x\r\n\r\n")
        assert got.startswith(b"HTTP/1.1 404")
        assert b"Content-Security-Policy" not in got
    finally:
        door.close()


def test_glass_whois_deny_gets_nothing(tmp_path, monkeypatch):
    door = _open_door(tmp_path, monkeypatch, glass=True)
    try:
        monkeypatch.setattr("xlii.tailnet.whois", lambda ip, port: None)
        host, port = door.bound
        got = _http(host, port, b"GET /css/face.css HTTP/1.1\r\nHost: x\r\n\r\n")
        assert got.startswith(b"HTTP/1.1 403")
        assert b"face.css" not in got
        assert b"Content-Security-Policy" in got
        assert b"{:root" not in got
    finally:
        door.close()


def test_glass_pairing_page_before_grant(tmp_path, monkeypatch):
    door = _open_door(tmp_path, monkeypatch, glass=True)
    try:
        host, port = door.bound
        got = _http(host, port, b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert got.startswith(b"HTTP/1.1 200")
        assert b"webcode" in got.lower() or b"Webcode" in got
        assert b"viewport-fit=cover" in got
        assert b"requestFullscreen" in got
        assert b"createElement(\"iframe\")" in got
        assert b"visualViewport" in got
        assert b"window.location.reload" not in got
        assert b"document.write(doc" in got
        assert b"interactive-widget=resizes-content" in got
        assert b"Content-Security-Policy" in got
        css = _http(host, port, b"GET /css/face.css HTTP/1.1\r\nHost: x\r\n\r\n")
        assert css.startswith(b"HTTP/1.1 403")
        assert b"need grant" in css
    finally:
        door.close()


def test_glass_spend_webcode_then_assets(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    door = _open_door(tmp_path, monkeypatch, glass=True)
    try:
        host, port = door.bound
        code = mint_code()
        append_pending(
            default_state_dir(), code, ttl_s=300, mode="full", now=time.time(),
        )
        raw = (
            b"POST /pair HTTP/1.1\r\nHost: x\r\n"
            b"Content-Type: application/x-www-form-urlencoded\r\n"
            + f"Content-Length: {len('code=' + normalize_code(code))}\r\n\r\n"
            f"code={normalize_code(code)}".encode()
        )
        got = _http(host, port, raw)
        assert b"302" in got.split(b"\r\n", 1)[0]
        assert b"xlii_glass_grant=" in got
        cookie = ""
        for line in got.split(b"\r\n"):
            if line.lower().startswith(b"set-cookie:"):
                cookie = line.split(b":", 1)[1].strip().split(b";")[0].decode()
                break
        assert cookie.startswith("xlii_glass_grant=")
        page = _http(
            host, port,
            f"GET / HTTP/1.1\r\nHost: x\r\nCookie: {cookie}\r\n\r\n".encode(),
        )
        assert page.startswith(b"HTTP/1.1 200")
        assert b"<html" in page.lower() or b"xlii" in page.lower()
        assert b'data-view="phone"' in page
        assert b"enter the webcode" not in page.lower()
        css = _http(
            host, port,
            f"GET /css/face.css HTTP/1.1\r\nHost: x\r\nCookie: {cookie}\r\n\r\n".encode(),
        )
        assert css.startswith(b"HTTP/1.1 200")
        assert b"--bg" in css or b"html" in css
        assert b"Content-Security-Policy" in css
        skins = _http(
            host, port,
            f"GET /skins/y2k/skin.css HTTP/1.1\r\nHost: x\r\nCookie: {cookie}\r\n\r\n".encode(),
        )
        assert skins.startswith(b"HTTP/1.1 200")
        # Boot token never appears on the glass.
        assert b"desk-boot-token" not in page
        assert b"desk-boot-token" not in css
    finally:
        door.close()


def test_glass_webcode_is_single_use(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    door = _open_door(tmp_path, monkeypatch, glass=True)
    try:
        host, port = door.bound
        code = mint_code()
        append_pending(
            default_state_dir(), code, ttl_s=300, mode="full", now=time.time(),
        )
        body = f"code={normalize_code(code)}".encode()
        req = (
            b"POST /pair HTTP/1.1\r\nHost: x\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode() + body
        )
        first = _http(host, port, req)
        assert b"302" in first.split(b"\r\n", 1)[0]
        second = _http(host, port, req)
        assert b"302" not in second.split(b"\r\n", 1)[0]
        assert b"403" in second.split(b"\r\n", 1)[0]
    finally:
        door.close()


def test_glass_drop_refuses_and_kills_grant(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state").mkdir()
    door = _open_door(tmp_path, monkeypatch, glass=True)
    try:
        host, port = door.bound
        code = mint_code()
        append_pending(
            default_state_dir(), code, ttl_s=300, mode="full", now=time.time(),
        )
        body = f"code={normalize_code(code)}".encode()
        got = _http(
            host, port,
            b"POST /pair HTTP/1.1\r\nHost: x\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode() + body,
        )
        cookie = ""
        for line in got.split(b"\r\n"):
            if line.lower().startswith(b"set-cookie:"):
                cookie = line.split(b":", 1)[1].strip().split(b";")[0].decode()
        now = time.time()
        mutate(lambda o: o.drop_remote_lab(), now=now + 1)
        door.sync()
        assert door.bound is None
        refused = False
        for _ in range(20):
            try:
                leftover = socket.create_connection((host, port), timeout=0.2)
                leftover.close()
                time.sleep(0.05)
            except OSError:
                refused = True
                break
        assert refused, "glass still accepting after drop"
        assert cookie
        assert door.grants.get(cookie.split("=", 1)[1]) is None
    finally:
        door.close()


def test_glass_device_mismatch_403(tmp_path, monkeypatch):
    door = _open_door(tmp_path, monkeypatch, glass=True, device="tablet")
    try:
        host, port = door.bound
        got = _http(host, port, b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        assert got.startswith(b"HTTP/1.1 403")
        assert b"forbidden" in got
    finally:
        door.close()


def test_phone_pane_cut_and_git_ro(tmp_path):
    from xlii.face_panes import FaceDeck
    from xlii.turn_events import PaneAction, PaneState
    from xlii.workbench import BUILTIN_WORKBENCHES
    from xlii.ws_protocol import serialize_event
    from tests.test_face_panes import _FakeServer, _state
    from xlii import active_session

    state = _state(tmp_path, BUILTIN_WORKBENCHES["code"])
    prev = active_session.set_active_session(state)
    try:
        server = _FakeServer(state)
        server.view_posture = "phone"
        deck = FaceDeck(server)
        snap = serialize_event(deck.snapshot())
        ids = {p["id"] for p in snap["panes"]}
        assert ids <= {"bookmarks", "git"}
        assert "explorer" not in ids
        assert snap["posture"] == "phone"
        assert deck.open_pane("bookmarks") is True
        assert deck.slot_tuple()[1] == "bookmarks"
        assert deck.open_pane("explorer") is False
        err = [e for e in server.sent if e.get("type") == "error"]
        assert err and "not on the phone glass" in err[-1]["message"]
        server.sent.clear()
        assert deck.set_slot("b", "explorer") is False
        assert any("not on the phone glass" in (e.get("message") or "")
                   for e in server.sent)

        git = PaneState(
            id="git", title="git://",
            actions=[
                PaneAction("view", "View"),
                PaneAction("commit", "Commit"),
                PaneAction("sync", "Sync"),
            ],
        )
        ro = deck._phone_ro(git)
        assert [a.name for a in ro.actions] == ["view"]

        server.sent.clear()
        deck.handle({"pane": "git", "op": "action", "name": "commit"})
        assert any("read-only" in (e.get("message") or "") for e in server.sent)

        server.glass_grant_mode = "preview"
        deck._mounted_for = None
        snap2 = serialize_event(deck.snapshot())
        assert {p["id"] for p in snap2["panes"]} <= {"bookmarks"}
        assert "git" not in {p["id"] for p in snap2["panes"]}
    finally:
        active_session.set_active_session(prev)


def test_glass_mouth_take_and_return(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from xlii.serve_face import FaceServer
    from tests.test_serve_face import _fake_state

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    conn = SimpleNamespace()
    assert server.attach_glass_client(conn, grant=SimpleNamespace(mode="full"))
    occ = load_live()
    assert occ.mouth == "me"
    assert occ.via == "tailnet-glass"
    assert server.view_posture == "phone"
    server.detach_client(conn)
    occ = load_live()
    assert occ.mouth == "desk"
    assert occ.via == ""
    assert server.view_posture == "desk"
    assert server._client_glass is False


def test_glass_pins_chat_posture(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from xlii.serve_face import FaceServer
    from tests.test_serve_face import _fake_state

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    server._client_glass = True
    server.view_posture = "phone"
    sent: list = []
    server.send = sent.append
    server._set_posture("code")
    assert server.posture == "chat"
    assert bool(getattr(server.state, "ask_primary", False)) is True
    mode = server.mode_state()
    assert mode.get("posture") == "chat"
    assert "[$]" not in (mode.get("exit_hint") or "")


def test_glass_input_emits_turn_done(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from xlii.serve_face import FaceServer
    from tests.test_serve_face import _fake_state

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    server._client_glass = True
    server.view_posture = "phone"
    sent: list = []
    server.send = sent.append
    monkeypatch.setattr(
        "xlii.face_remote.ingest_face_turn", lambda *a, **k: "pong",
    )
    server._glass_input("hi")
    deadline = time.time() + 2
    while time.time() < deadline:
        if any(e.get("type") == "turn_done" for e in sent):
            break
        time.sleep(0.02)
    types = [e.get("type") for e in sent]
    assert "busy_state" in types
    assert "turn_done" in types
    assert sent[-1].get("type") == "turn_done"
    assert sent[-1].get("ok") is True


def test_glass_input_refuses_lab_and_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from xlii.serve_face import FaceServer
    from tests.test_serve_face import _fake_state

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    server._client_glass = True
    sent: list = []
    server.send = sent.append
    server._glass_input("[$]")
    assert any("talk-only" in (e.get("message") or "") for e in sent)
    sent.clear()
    server.glass_grant_mode = "preview"
    server._glass_input("what is this project?")
    assert any("read only" in (e.get("message") or "") for e in sent)


def test_glass_ingest_feeds_idle_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    now = 1_700_000_000.0
    mutate(lambda o: o.open_remote_lab(now=now - 10, tier="glass"), now=now - 10)
    from xlii.face_remote import ingest_face_turn

    monkeypatch.setattr("xlii.persona.talk_persona_id", lambda **k: "nobody")
    monkeypatch.setattr(
        "xlii.cmds.sessions.resolve._lookup_persona", lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "xlii.cmds.sessions.resolve.ensure_default_persona", lambda: None,
    )
    monkeypatch.setattr("time.time", lambda: now)
    ingest_face_turn(
        SimpleNamespace(
            state=SimpleNamespace(project=None, cfg=None, pool=None, console=None),
            yolo=False, send=lambda *a, **k: None,
        ),
        "hi",
        claim_mouth=False,
    )
    occ = load_live()
    assert occ.remote_lab.last_agent_at >= now


def test_glass_mark_last(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from xlii.serve_face import FaceServer
    from xlii.transcript import list_marks, write_turn
    from tests.test_serve_face import _fake_state

    state = _fake_state(tmp_path)
    turns = tmp_path / "turns"
    turns.mkdir()
    write_turn(turns, "q", "a")
    state.profile = SimpleNamespace(memory=SimpleNamespace(turns_dir=turns))
    orig = state.as_context_dict
    state.as_context_dict = lambda: {**orig(), "state": state}
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent: list = []
    server.send = sent.append
    server._glass_mark("idea")
    assert [n for n, _ in list_marks(turns)] == ["idea"]
    assert any("idea" in (e.get("text") or "") for e in sent)


def test_glass_mark_last_refuses_window_suffix(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from xlii.serve_face import FaceServer
    from tests.test_serve_face import _fake_state

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    sent: list = []
    server.send = sent.append
    server._glass_mark("bad (window: 2)")
    assert any("(window: N)" in (e.get("message") or "") for e in sent)


def test_glass_mark_last_warns_when_no_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from xlii.serve_face import FaceServer
    from tests.test_serve_face import _fake_state

    state = _fake_state(tmp_path)
    turns = tmp_path / "turns"
    turns.mkdir()
    state.profile = SimpleNamespace(memory=SimpleNamespace(turns_dir=turns))
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent: list = []
    server.send = sent.append
    server._glass_mark("idea")
    assert any("no turns to mark yet" in (e.get("text") or "") for e in sent)


def test_hello_carries_view_posture():
    from xlii.ws_protocol import hello_message

    desk = hello_message(session_id="s")
    assert desk["view_posture"] == "desk"
    assert desk["tailnet_glass"] is False
    phone = hello_message(session_id="s", view_posture="phone")
    assert phone["view_posture"] == "phone"
    assert phone["tailnet_glass"] is False
    glass = hello_message(session_id="s", view_posture="phone", tailnet_glass=True)
    assert glass["tailnet_glass"] is True


def _pair_cookie(host: str, port: int, *, mode: str = "full") -> str:
    code = mint_code()
    append_pending(
        default_state_dir(), code, ttl_s=300, mode=mode, now=time.time(),
    )
    body = f"code={normalize_code(code)}".encode()
    got = _http(
        host, port,
        b"POST /pair HTTP/1.1\r\nHost: x\r\n"
        + f"Content-Length: {len(body)}\r\n\r\n".encode() + body,
    )
    cookie = ""
    for line in got.split(b"\r\n"):
        if line.lower().startswith(b"set-cookie:"):
            cookie = line.split(b":", 1)[1].strip().split(b";")[0].decode()
            break
    assert cookie.startswith("xlii_glass_grant="), got[:200]
    return cookie


def _glass_ws(host: str, port: int, cookie: str) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=2.0)
    key = base64.b64encode(b"glass-mid-ws-key!!").decode()
    sock.sendall(
        (
            "GET / HTTP/1.1\r\nHost: x\r\n"
            "Upgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Cookie: {cookie}\r\n\r\n"
        ).encode()
    )
    resp = b""
    while not resp.endswith(b"\r\n\r\n"):
        chunk = sock.recv(1)
        if not chunk:
            break
        resp += chunk
    assert b"101" in resp.split(b"\r\n", 1)[0], resp[:200]
    return sock


def test_glass_drop_kills_live_ws(tmp_path, monkeypatch):
    """G1 mid-WS gate: drop kills the attached glass socket, not only listen."""
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    (tmp_path / "state").mkdir()
    from xlii.serve_face import FaceServer
    from xlii.serve_face.wire import default_assets_dir
    from tests.test_serve_face import _fake_state

    server = FaceServer(
        boot=SimpleNamespace(state=_fake_state(tmp_path)),
        assets_dir=default_assets_dir(),
    )
    monkeypatch.setattr(
        "xlii.face_remote.load_tailnet_allowlist", lambda path=None: ["phone"],
    )
    monkeypatch.setattr("xlii.tailnet.whois", lambda ip, port: _phone_peer())
    st = TailnetSelf(
        up=True, ipv4="127.0.0.1", addrs=("127.0.0.1",),
        dns_name="throne", node_name="throne",
    )
    monkeypatch.setattr("xlii.tailnet.self_status", lambda: st)
    door = TailnetDoor(token="desk-boot-token", server=server, port=0)
    now = time.time()
    mutate(
        lambda o: o.open_remote_lab(now=now, device="phone", tier="glass"),
        now=now,
    )
    door.sync()
    try:
        assert door.bound is not None
        host, port = door.bound
        cookie = _pair_cookie(host, port)
        ws = _glass_ws(host, port, cookie)
        # Drain a hello (or any first frame) so we know the reader is live.
        ws.settimeout(2.0)
        hello = b""
        try:
            while len(hello) < 2:
                chunk = ws.recv(4096)
                if not chunk:
                    break
                hello += chunk
        except TimeoutError:
            pass
        assert hello, "expected glass hello traffic before drop"
        mutate(lambda o: o.drop_remote_lab(), now=time.time() + 1)
        door.sync()
        assert door.bound is None
        # Attached WS must die — not linger streaming after drop.
        # Timeout alone means still open; only EOF / reset counts as dead.
        dead = False
        ws.settimeout(0.2)
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                chunk = ws.recv(4096)
            except TimeoutError:
                continue
            except (ConnectionResetError, BrokenPipeError, OSError):
                dead = True
                break
            if not chunk:
                dead = True
                break
            # Drain any frames already in flight from the tear-down race.
        try:
            ws.close()
        except OSError:
            pass
        assert dead, "live glass WS still open after sitting drop"
    finally:
        door.close()


def test_glass_reopen_drops_old_grants_and_ws(tmp_path, monkeypatch):
    """Re-opened sitting rotates grant lifetime and tears old phone WS down."""
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    (tmp_path / "state").mkdir()
    from xlii.serve_face import FaceServer
    from xlii.serve_face.wire import default_assets_dir
    from tests.test_serve_face import _fake_state

    server = FaceServer(
        boot=SimpleNamespace(state=_fake_state(tmp_path)),
        assets_dir=default_assets_dir(),
    )
    monkeypatch.setattr(
        "xlii.face_remote.load_tailnet_allowlist", lambda path=None: ["phone"],
    )
    monkeypatch.setattr("xlii.tailnet.whois", lambda ip, port: _phone_peer())
    st = TailnetSelf(
        up=True, ipv4="127.0.0.1", addrs=("127.0.0.1",),
        dns_name="throne", node_name="throne",
    )
    monkeypatch.setattr("xlii.tailnet.self_status", lambda: st)
    door = TailnetDoor(token="desk-boot-token", server=server, port=0)
    now = time.time()
    mutate(
        lambda o: o.open_remote_lab(now=now, device="phone", tier="glass"),
        now=now,
    )
    door.sync()
    try:
        assert door.bound is not None
        host, port = door.bound
        cookie1 = _pair_cookie(host, port)
        ws = _glass_ws(host, port, cookie1)
        mutate(
            lambda o: o.open_remote_lab(now=now + 1, device="phone", tier="glass"),
            now=now + 1,
        )
        door.sync()

        denied = _http(
            host, port,
            f"GET /css/face.css HTTP/1.1\r\nHost: x\r\nCookie: {cookie1}\r\n\r\n".encode(),
        )
        assert denied.startswith(b"HTTP/1.1 403")
        assert b"need grant" in denied

        dead = False
        ws.settimeout(0.2)
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                chunk = ws.recv(4096)
            except TimeoutError:
                continue
            except (ConnectionResetError, BrokenPipeError, OSError):
                dead = True
                break
            if not chunk:
                dead = True
                break
        assert dead, "old WS still alive after sitting re-open"
        ws.close()
    finally:
        door.close()



def _occ_file_snip() -> str:
    """Short occupancy-file dump for assertion messages."""
    import os
    path = (os.environ.get("XLII_OCCUPANCY_PATH") or "").strip()
    if not path:
        return "<no XLII_OCCUPANCY_PATH>"
    try:
        return open(path, encoding="utf-8").read().strip() or "<empty>"
    except OSError as exc:
        return f"<unreadable: {exc}>"


def _wait_mouth_me(*, timeout: float = 1.0):
    """Poll occupancy briefly until glass mouth claim is visible."""
    deadline = time.time() + timeout
    occ = load_live()
    while True:
        if occ.mouth == "me" and (getattr(occ, "via", "") or "") == "tailnet-glass":
            return occ
        if time.time() >= deadline:
            return occ
        time.sleep(0.02)
        occ = load_live()


def _wait_mouth_desk(*, timeout: float = 1.0):
    """Poll occupancy briefly until desk owns the mouth (via cleared)."""
    deadline = time.time() + timeout
    occ = load_live()
    while True:
        if occ.mouth == "desk" and (getattr(occ, "via", "") or "") == "":
            return occ
        if time.time() >= deadline:
            return occ
        time.sleep(0.02)
        occ = load_live()


def test_desk_attach_clears_glass_after_phone(tmp_path, monkeypatch):
    """Desk reconnect after phone must reset glass flags and release mouth."""
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from xlii.serve_face import FaceServer
    from xlii.serve_face.glass import take_glass_mouth
    from tests.test_serve_face import _fake_state

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    phone_a, phone_b = socket.socketpair()
    desk_a, desk_b = socket.socketpair()
    try:
        # Realistic glass attach: sitting open before the phone claims the mouth.
        now = time.time()
        mutate(
            lambda o: o.open_remote_lab(now=now, device="phone", tier="glass"),
            now=now,
        )
        assert server.attach_glass_client(
            phone_a, grant=SimpleNamespace(mode="preview"),
        )
        assert server._client_glass is True
        assert server.view_posture == "phone"
        assert server.glass_grant_mode == "preview"
        # Idempotent re-claim so the release half has a known mouth=me baseline.
        take_glass_mouth()
        occ = load_live()
        assert occ.mouth == "me" and occ.via == "tailnet-glass", (
            f"expected mouth=me/via=tailnet-glass after attach; got "
            f"mouth={occ.mouth!r} via={getattr(occ, 'via', '')!r}; "
            f"occ_file={_occ_file_snip()}"
        )

        assert server.attach_client(desk_a) is True
        # Glass state cleared on desk attach — even before old phone detaches.
        assert server._client_glass is False
        assert server.view_posture == "desk"
        assert server.glass_grant_mode == "full"
        # Poll briefly: mouth release is sync, but occupancy readers can race.
        occ = _wait_mouth_desk()
        assert occ.mouth == "desk", (
            f"expected mouth=desk after desk attach; got mouth={occ.mouth!r} "
            f"via={getattr(occ, 'via', '')!r}; occ_file={_occ_file_snip()}"
        )
        assert occ.via == ""

        # Late detach of the displaced phone must not disturb the desk client.
        server.detach_client(phone_a)
        assert server._client is desk_a
        assert server.view_posture == "desk"
        assert server._client_glass is False
    finally:
        for s in (phone_a, phone_b, desk_a, desk_b):
            try:
                s.close()
            except OSError:
                pass


def test_wire_desk_after_phone_attaches_desk_not_glass(tmp_path, monkeypatch):
    """Loopback desk reconnect must not re-enter attach_glass via leftover posture."""
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from tests.test_serve_face import (
        _LIVE_SERVERS, _connect, _fake_state, _recv_until, _start_server,
    )
    from xlii.occupancy_store import load_live
    from xlii.serve_face.glass import take_glass_mouth

    port, _t = _start_server(_fake_state(tmp_path))
    server = _LIVE_SERVERS[-1][0]
    phone_a, phone_b = socket.socketpair()
    try:
        # Realistic glass attach: sitting open before the phone claims the mouth.
        now = time.time()
        mutate(
            lambda o: o.open_remote_lab(now=now, device="phone", tier="glass"),
            now=now,
        )
        assert server.attach_glass_client(
            phone_a, grant=SimpleNamespace(mode="full"),
        )
        assert server._client_glass is True
        assert server.view_posture == "phone"
        # Idempotent re-claim so the release half has a known mouth=me baseline.
        take_glass_mouth()
        occ = load_live()
        assert occ.mouth == "me" and occ.via == "tailnet-glass", (
            f"expected mouth=me/via=tailnet-glass after attach; got "
            f"mouth={occ.mouth!r} via={getattr(occ, 'via', '')!r}; "
            f"occ_file={_occ_file_snip()}"
        )

        # Desk WS via real _handle_connection — must attach_client, not glass.
        # chrome_state / other preamble can race ahead of hello (attach_glass
        # dual-pane / agent_browser on_change); drain until hello.
        desk = _connect(port)
        hello, _seen = _recv_until(desk, "hello")
        assert hello.get("type") == "hello"
        assert hello.get("view_posture") == "desk"
        deadline = time.time() + 2.0
        while time.time() < deadline and server._client_glass:
            time.sleep(0.02)
        assert server._client_glass is False
        assert server.view_posture == "desk"
        assert server._boot_view_phone is False
        occ = load_live()
        deadline = time.time() + 2.0
        while time.time() < deadline and occ.mouth != "desk":
            time.sleep(0.02)
            occ = load_live()
        assert occ.mouth == "desk"
        assert occ.via == ""
        desk.close()
    finally:
        for s in (phone_a, phone_b):
            try:
                s.close()
            except OSError:
                pass


def test_displaced_glass_frame_never_dispatches(tmp_path, monkeypatch):
    """If a conn is displaced mid-read, its frame must not reach handlers."""
    import xlii.serve_face.server as face_server_mod

    door, server, ws = _open_glass_ws(tmp_path, monkeypatch, mode="full")
    desk_a, desk_b = socket.socketpair()
    calls: list[str] = []
    monkeypatch.setattr(server, "_glass_sitting_lock", lambda: calls.append("lock"))
    orig_parse = face_server_mod._parse_client_message

    def _displace_and_parse(payload: str):
        server.attach_client(desk_a)
        return orig_parse(payload)

    monkeypatch.setattr(face_server_mod, "_parse_client_message", _displace_and_parse)
    try:
        _ws_send_json(ws, {"type": "remote_lock"})
        time.sleep(0.2)
        assert calls == []
    finally:
        try:
            ws.close()
        except OSError:
            pass
        for s in (desk_a, desk_b):
            try:
                s.close()
            except OSError:
                pass
        door.close()


def test_view_phone_loopback_alive_when_sitting_closed(tmp_path, monkeypatch):
    """Local --view phone must keep the reader alive with sitting closed."""
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from tests.test_serve_face import (
        _LIVE_SERVERS, _connect, _fake_state, _recv_event, _send, _start_server,
    )
    from xlii.face_remote import sitting_allows_remote
    from xlii.ws_server import _read_frame

    assert sitting_allows_remote() is False
    port, _t = _start_server(_fake_state(tmp_path), view="phone")
    server = _LIVE_SERVERS[-1][0]
    assert server._boot_view_phone is True
    assert server.view_posture == "phone"

    conn = _connect(port)
    hello = _recv_event(conn)
    assert hello.get("type") == "hello"
    assert hello.get("view_posture") == "phone"
    # Drain chrome/mode frames so ping reply is unambiguous.
    deadline = time.time() + 2.0
    while time.time() < deadline and server._client is None:
        time.sleep(0.02)
    assert server._client is conn or server._client is not None
    assert server._client_glass is False
    assert server.view_posture == "phone"

    # Reader must stay alive — sitting gate only applies to _client_glass.
    time.sleep(0.15)
    _send(conn, {"type": "ping"})
    got_pong = False
    conn.settimeout(2.0)
    for _ in range(20):
        frame = _read_frame(conn)
        assert frame is not None, "reader exited (sitting gate on local phone?)"
        _opcode, payload = frame
        import json
        ev = json.loads(payload.decode())
        if ev.get("type") == "pong":
            got_pong = True
            break
    assert got_pong, "expected pong from live --view phone reader"
    assert server._client_glass is False
    assert sitting_allows_remote() is False
    conn.close()


def _open_glass_ws(tmp_path, monkeypatch, *, mode: str = "full"):
    """Real TailnetDoor glass path: sitting + pair + WS. Returns (door, server, ws)."""
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    (tmp_path / "state").mkdir(exist_ok=True)
    from xlii.serve_face import FaceServer
    from xlii.serve_face.wire import default_assets_dir
    from tests.test_serve_face import _fake_state

    server = FaceServer(
        boot=SimpleNamespace(state=_fake_state(tmp_path)),
        assets_dir=default_assets_dir(),
    )
    monkeypatch.setattr(
        "xlii.face_remote.load_tailnet_allowlist", lambda path=None: ["phone"],
    )
    monkeypatch.setattr("xlii.tailnet.whois", lambda ip, port: _phone_peer())
    st = TailnetSelf(
        up=True, ipv4="127.0.0.1", addrs=("127.0.0.1",),
        dns_name="throne", node_name="throne",
    )
    monkeypatch.setattr("xlii.tailnet.self_status", lambda: st)
    door = TailnetDoor(token="desk-boot-token", server=server, port=0)
    now = time.time()
    mutate(
        lambda o: o.open_remote_lab(now=now, device="phone", tier="glass"),
        now=now,
    )
    door.sync()
    assert door.bound is not None
    host, port = door.bound
    cookie = _pair_cookie(host, port, mode=mode)
    ws = _glass_ws(host, port, cookie)
    ws.settimeout(2.0)
    # Drain hello / chrome so later error frames are unambiguous.
    deadline = time.time() + 2.0
    while time.time() < deadline and not getattr(server, "_client_glass", False):
        time.sleep(0.02)
    assert server._client_glass is True
    assert server.glass_grant_mode == mode
    # Read whatever the server already pushed without blocking forever.
    try:
        while True:
            ws.settimeout(0.15)
            chunk = ws.recv(65536)
            if not chunk:
                break
    except TimeoutError:
        pass
    ws.settimeout(2.0)
    return door, server, ws


def _ws_send_json(ws: socket.socket, obj: dict) -> None:
    import json
    from xlii.ws_server import _send_text

    _send_text(ws, json.dumps(obj))


def _ws_recv_error(ws: socket.socket, *, limit: int = 30) -> dict:
    import json
    from xlii.ws_server import _read_frame

    for _ in range(limit):
        frame = _read_frame(ws)
        assert frame is not None, "WS closed before error"
        _opcode, payload = frame
        ev = json.loads(payload.decode())
        if ev.get("type") == "error":
            return ev
    raise AssertionError("no error frame from glass WS")


def test_preview_glass_ws_refuses_open_terminal(tmp_path, monkeypatch):
    """Preview grant cannot open_terminal/run — refuse before launch."""
    launched: list[str] = []

    def _no_launch(cwd, run="", preferred=""):
        launched.append(run)
        return True, "launched"

    monkeypatch.setattr(
        "xlii.interactive.launch_in_external_terminal", _no_launch,
    )
    door, server, ws = _open_glass_ws(tmp_path, monkeypatch, mode="preview")
    try:
        _ws_send_json(ws, {"type": "open_terminal", "run": "sh -c id"})
        err = _ws_recv_error(ws)
        assert "open_terminal" in (err.get("message") or "")
        assert "preview" in (err.get("message") or "")
        assert launched == [], f"terminal launched despite refuse: {launched}"
        assert server._client_glass is True
    finally:
        try:
            ws.close()
        except OSError:
            pass
        door.close()


def test_full_glass_ws_allows_project_switch_verbs(tmp_path, monkeypatch):
    door, server, ws = _open_glass_ws(tmp_path, monkeypatch, mode="full")
    called: list[tuple[str, str]] = []
    monkeypatch.setattr(
        server, "join_project", lambda name: called.append(("join_project", name)) or True,
    )
    monkeypatch.setattr(
        server, "go_home", lambda: called.append(("go_home", "")) or True,
    )
    try:
        _ws_send_json(ws, {"type": "join_project", "name": "lab"})
        _ws_send_json(ws, {"type": "go_home"})
        deadline = time.time() + 2.0
        while time.time() < deadline and len(called) < 2:
            time.sleep(0.02)
        assert ("join_project", "lab") in called
        assert ("go_home", "") in called
    finally:
        try:
            ws.close()
        except OSError:
            pass
        door.close()


def test_preview_glass_ws_refuses_project_switch_verbs(tmp_path, monkeypatch):
    door, server, ws = _open_glass_ws(tmp_path, monkeypatch, mode="preview")
    called: list[tuple[str, str]] = []
    monkeypatch.setattr(
        server, "join_project", lambda name: called.append(("join_project", name)) or True,
    )
    monkeypatch.setattr(
        server, "go_home", lambda: called.append(("go_home", "")) or True,
    )
    try:
        _ws_send_json(ws, {"type": "join_project", "name": "lab"})
        err = _ws_recv_error(ws)
        assert "join_project" in (err.get("message") or "")
        _ws_send_json(ws, {"type": "go_home"})
        err2 = _ws_recv_error(ws)
        assert "go_home" in (err2.get("message") or "")
        assert called == []
    finally:
        try:
            ws.close()
        except OSError:
            pass
        door.close()


def test_preview_glass_ws_refuses_plugin_and_task_write(tmp_path, monkeypatch):
    door, server, ws = _open_glass_ws(tmp_path, monkeypatch, mode="preview")
    wrote: list[str] = []
    monkeypatch.setattr(
        server, "write_plugin", lambda *a, **k: wrote.append("plugin") or True,
    )
    monkeypatch.setattr(
        server, "write_task", lambda *a, **k: wrote.append("task") or True,
    )
    try:
        _ws_send_json(ws, {"type": "plugin_write", "spec": {"id": "x"}})
        err = _ws_recv_error(ws)
        assert "plugin_write" in (err.get("message") or "")
        _ws_send_json(ws, {"type": "task_write", "spec": {"id": "t"}, "run": True})
        err2 = _ws_recv_error(ws)
        assert "task_write" in (err2.get("message") or "")
        assert wrote == []
    finally:
        try:
            ws.close()
        except OSError:
            pass
        door.close()


def test_full_glass_ws_refuses_open_terminal(tmp_path, monkeypatch):
    """D3: full phone glass is talk-only — no bare-shell open_terminal/run."""
    launched: list[str] = []

    def _no_launch(cwd, run="", preferred=""):
        launched.append(run)
        return True, "launched"

    monkeypatch.setattr(
        "xlii.interactive.launch_in_external_terminal", _no_launch,
    )
    door, server, ws = _open_glass_ws(tmp_path, monkeypatch, mode="full")
    try:
        _ws_send_json(ws, {"type": "open_terminal", "run": "bash"})
        err = _ws_recv_error(ws)
        assert "open_terminal" in (err.get("message") or "")
        assert "full" in (err.get("message") or "")
        assert launched == []
    finally:
        try:
            ws.close()
        except OSError:
            pass
        door.close()


def test_attach_glass_client_rolls_back_on_mouth_failure(tmp_path, monkeypatch):
    """Failed mouth claim must not leave phone-glass posture without a reader."""
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    from xlii.serve_face import FaceServer
    from tests.test_serve_face import _fake_state

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    conn_a, conn_b = socket.socketpair()
    try:
        monkeypatch.setattr(
            "xlii.serve_face.glass.take_glass_mouth",
            lambda: None,
        )
        monkeypatch.setattr(
            "xlii.occupancy_store.load_live",
            lambda: SimpleNamespace(mouth="desk", via=""),
        )
        assert server.attach_glass_client(
            conn_a, grant=SimpleNamespace(mode="full"),
        ) is False
        assert server._client is None
        assert server._client_glass is False
        assert server.view_posture == "desk"
    finally:
        for s in (conn_a, conn_b):
            try:
                s.close()
            except OSError:
                pass
