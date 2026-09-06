"""`xlii serve --ws` (browser-ui W2) — offline protocol + server pins."""

from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from xlii.cmds.serve_ws import (
    _accept_key,
    _parse_client_message,
    _read_frame,
    _send_text,
    run_ws_turn,
    serve_ws,
)
from xlii.tui.events import (
    AssistantAnswer,
    MetaMessage,
    ShellRan,
    ToolFinished,
    ToolStarted,
    UserTurn,
)
from xlii.tui.ws_protocol import (
    PROTOCOL_VERSION,
    hello_message,
    handshake_payload,
    serialize_event,
)


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "xlii", *args],
        capture_output=True,
        text=True,
    )


def _start_ws_server(project, token: str) -> int:
    """Start serve_ws on an ephemeral port; return the REAL bound port.

    Readiness comes from serve_ws's ``on_bound`` callback, which fires after
    ``listen()`` — so a returned port is always connectable. This replaces the
    old bind-probe-close-rebind dance, whose close→rebind gap (widened by
    coverage tracing) intermittently produced ConnectionRefusedError or a
    connect swallowed by the dying probe listener → a timeout-less recv hang
    (the CI stall at ~70%)."""
    ready = threading.Event()
    holder: list[int] = []

    def _on_bound(p: int) -> None:
        holder.append(p)
        ready.set()

    threading.Thread(
        target=lambda: serve_ws(
            project, host="127.0.0.1", port=0, token=token, on_bound=_on_bound,
        ),
        daemon=True,
    ).start()
    assert ready.wait(timeout=10), "serve_ws did not come up within 10s"
    return holder[0]


def _ws_connect(host: str, port: int, token: str) -> socket.socket:
    # timeout=5 sticks as the socket timeout, so every recv below (and the
    # frame reads in the tests) fails fast instead of hanging the suite.
    conn = socket.create_connection((host, port), timeout=5)
    key = base64.b64encode(b"test-client-key!!").decode()
    req = (
        f"GET /?token={token} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n\r\n"
    )
    conn.sendall(req.encode())
    # Read the 101 response ONE byte at a time, stopping exactly at the header
    # terminator. A bulk recv(4096) can pull the coalesced hello FRAME along
    # with the 101 and silently discard it below — then _read_frame waits for a
    # frame the client already consumed while the server waits for the client's
    # next frame: the deadlock behind the historic ~70% CI hang.
    resp = b""
    while not resp.endswith(b"\r\n\r\n"):
        chunk = conn.recv(1)
        if not chunk:  # EOF: without this, b"" loops forever (100% CPU)
            raise AssertionError(f"server closed during handshake; got {resp!r}")
        resp += chunk
    head = resp.split(b"\r\n\r\n", 1)[0]
    assert b"101" in head.split(b"\r\n", 1)[0]
    assert _accept_key(key).encode() in head
    return conn


@pytest.mark.parametrize(
    "event,expected_type",
    [
        (UserTurn("hi"), "user_turn"),
        (AssistantAnswer("ok", streamed=True), "assistant_answer"),
        (ToolStarted("grep", "/pat/"), "tool_started"),
        (ToolFinished("read_file", "x", "body"), "tool_finished"),
        (MetaMessage("note", "warn"), "meta_message"),
        (
            ShellRan("ls", __import__("pathlib").Path("/tmp"), "out", "", 0),
            "shell_ran",
        ),
    ],
)
def test_serialize_event_maps_dataclasses(event, expected_type):
    d = serialize_event(event)
    assert d["type"] == expected_type
    assert "type" not in {k for k in d if k == "type"} or d["type"] == expected_type


def test_hello_and_handshake_shapes():
    hello = hello_message(session_id="ws-abc")
    assert hello["type"] == "hello"
    assert hello["protocol"] == PROTOCOL_VERSION
    assert "version" in hello
    hs = handshake_payload(4321, "tok")
    assert hs == {"port": 4321, "token": "tok", "version": hs["version"],
                  "protocol": PROTOCOL_VERSION}


def test_serve_ws_flags_registered():
    r = _run("serve", "--help")
    assert r.returncode == 0
    assert "--ws" in r.stdout
    assert "--handshake" in r.stdout
    assert "--token" in r.stdout


def test_parse_client_message_validation():
    with pytest.raises(ValueError, match="invalid JSON"):
        _parse_client_message("not json")
    with pytest.raises(ValueError, match="type field"):
        _parse_client_message("{}")


def test_bad_token_gets_403(tmp_path):
    from tests.helpers import make_project

    project = make_project(tmp_path)
    token = "good-token"
    port = _start_ws_server(project, token)

    conn = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(b"bad-key!!!!!!!!!!!").decode()
    conn.sendall(
        (
            "GET /?token=wrong HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n\r\n"
        ).encode()
    )
    resp = conn.recv(256)
    conn.close()
    assert b"403" in resp


def test_ws_turn_stream_with_faked_agent(tmp_path, monkeypatch):
    from tests.helpers import make_project

    project = make_project(tmp_path)
    events: list[dict] = []

    class _FakeAgent:
        def __init__(self, **kw):
            self.history = [{"role": "system", "content": "SYS"}]
            self.console = kw["console"]
            self._renderer_cache = None

        def _renderer(self):
            from xlii.tui.renderer import Renderer

            if self._renderer_cache is None:
                self._renderer_cache = Renderer(self.console, plain=True)
            return self._renderer_cache

        def run_turn(self, prompt):
            cb = getattr(self.console, "on_content_chunk", None)
            if cb:
                cb("hel")
                cb("lo")
            self._renderer().emit(AssistantAnswer("hello", streamed=True))
            return ("", set(), None)

    monkeypatch.setattr("xlii.config.GlobalConfig", SimpleNamespace(load=SimpleNamespace))
    monkeypatch.setattr("xlii.pool.ClientPool", SimpleNamespace(from_config=lambda cfg: object()))
    monkeypatch.setattr("xlii.agent.Agent", _FakeAgent)
    monkeypatch.setattr("xlii.agent.SessionState", SimpleNamespace(from_flat=lambda **kw: object()))

    rc = run_ws_turn(
        project=project,
        session_id="test-session",
        prompt="say hi",
        sink=events.append,
    )
    assert rc == 0
    types = [e["type"] for e in events]
    assert types[0] == "user_turn"
    assert "assistant_chunk" in types
    assert "assistant_answer" in types
    assert events[-1] == {"type": "turn_done", "ok": True, "exit_code": 0}


def test_live_ws_ping_and_hello(tmp_path):
    from tests.helpers import make_project

    project = make_project(tmp_path)
    token = "test-token-abc"
    port = _start_ws_server(project, token)

    conn = _ws_connect("127.0.0.1", port, token)
    frame = _read_frame(conn)
    assert frame is not None
    _, payload = frame
    hello = json.loads(payload.decode())
    assert hello["type"] == "hello"
    assert hello["protocol"] == PROTOCOL_VERSION
    assert hello["session"].startswith("ws-")

    _send_text(conn, json.dumps({"type": "ping"}))
    frame = _read_frame(conn)
    assert frame is not None
    pong = json.loads(frame[1].decode())
    assert pong == {"type": "pong"}
    conn.close()


def test_handshake_payload_is_one_line_json():
    token = "handshake-tok"
    line = json.dumps(handshake_payload(12345, token))
    parsed = json.loads(line)
    assert parsed["port"] == 12345
    assert parsed["token"] == token
    assert "version" in parsed


def test_large_event_frame_round_trips_through_send_and_read():
    """A >64KiB payload (e.g. tool_finished carrying a big file read) must survive
    _send_text -> _read_frame. Regression for the missing 64-bit (127) length
    branch on the send path, which used to overflow struct.pack('>H', ...)."""
    a, b = socket.socketpair()
    b.settimeout(10)  # safety net: never hang if the send branch is broken
    payload = json.dumps({
        "type": "tool_finished",
        "tool": "read_file",
        "receipt": "read big.txt",
        "body": "x" * (100 * 1024),
    })
    assert len(payload.encode()) > 0xFFFF  # forces the 127 / >Q length branch

    send_error: list[BaseException] = []

    def _send():
        try:
            _send_text(a, payload)
        except BaseException as exc:  # pragma: no cover - only trips before the fix
            send_error.append(exc)

    t = threading.Thread(target=_send, daemon=True)
    t.start()
    try:
        frame = _read_frame(b)
    finally:
        t.join(timeout=5)
        a.close()
        b.close()

    assert not send_error, f"_send_text raised on a >64KiB payload: {send_error!r}"
    assert frame is not None
    opcode, data = frame
    assert opcode == 0x1
    assert data.decode() == payload


def test_oversized_frame_rejected_before_allocation():
    """An inbound frame whose declared length exceeds MAX_FRAME_BYTES is
    rejected (None) without _recv_exact ever allocating for it — the header
    alone must not let a client drive the server to OOM."""
    import struct as _struct

    from xlii.ws_server import MAX_FRAME_BYTES

    a, b = socket.socketpair()
    b.settimeout(5)
    # A 127/>Q header declaring a payload past the cap, with NO payload behind
    # it. If the cap were missing, _read_frame would block trying to read
    # MAX_FRAME_BYTES+1 bytes into a bytearray.
    a.sendall(bytes([0x81, 127]) + _struct.pack(">Q", MAX_FRAME_BYTES + 1))
    a.close()
    try:
        assert _read_frame(b) is None
    finally:
        b.close()


def test_ws_turn_non_yolo_installs_auto_deny_confirm(tmp_path, monkeypatch):
    """A non-yolo WS turn must swap in a silent auto-denying confirm so a gated
    intent never falls through to input() on the server's stdin/stdout, and must
    restore the original confirm afterward."""
    import xlii.tools as tools_mod
    from tests.helpers import make_project

    project = make_project(tmp_path)
    before = tools_mod._confirm
    captured: dict = {}

    class _FakeAgent:
        def __init__(self, **kw):
            self.history = [{"role": "system", "content": "SYS"}]
            self.console = kw["console"]
            self._renderer_cache = None

        def _renderer(self):
            from xlii.tui.renderer import Renderer

            if self._renderer_cache is None:
                self._renderer_cache = Renderer(self.console, plain=True)
            return self._renderer_cache

        def run_turn(self, prompt):
            captured["during"] = tools_mod._confirm
            self._renderer().emit(AssistantAnswer("ok", streamed=True))
            return ("ok", set(), None)

    monkeypatch.setattr("xlii.config.GlobalConfig", SimpleNamespace(load=SimpleNamespace))
    monkeypatch.setattr("xlii.pool.ClientPool", SimpleNamespace(from_config=lambda cfg: object()))
    monkeypatch.setattr("xlii.agent.Agent", _FakeAgent)
    monkeypatch.setattr("xlii.agent.SessionState", SimpleNamespace(from_flat=lambda **kw: object()))

    events: list[dict] = []
    rc = run_ws_turn(
        project=project,
        session_id="deny-session",
        prompt="do something risky",
        sink=events.append,
        yolo=False,
    )
    assert rc == 0
    # During the turn the injectable confirm is NOT the default (input) ...
    assert captured["during"] is not before
    # ... and it declines cleanly, printing nothing / never touching stdin.
    assert captured["during"]("approve modifies_system command?\n[y/N] ") == ""
    # ... and the original confirm is restored once the turn ends.
    assert tools_mod._confirm is before


def test_handshake_subprocess_emits_one_json_line_and_exits_on_stdin_close(tmp_path):
    """End-to-end: `serve --ws --handshake --port 0` prints EXACTLY one parseable
    JSON handshake line on stdout (nothing more), rejects a tokenless connection
    with 403, and exits when stdin closes."""
    root = tmp_path / "proj"
    (root / ".xlii").mkdir(parents=True)
    (root / ".xlii" / "project.json").write_text(json.dumps({
        "name": "wsproj",
        "collection_id": "",
        "created_at": "2026-01-01T00:00:00Z",
        "local_only": True,
    }))

    env = dict(os.environ)
    env.pop("XAI_MANAGEMENT_API_KEY", None)

    proc = subprocess.Popen(
        [sys.executable, "-m", "xlii", "serve", "--ws", "--handshake", "--port", "0"],
        cwd=str(root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        # Exactly one handshake line, flushed before the accept loop starts.
        line = _readline_with_timeout(proc.stdout, timeout=20)
        assert line, "no handshake line on stdout"
        parsed = json.loads(line)
        assert set(parsed) == {"port", "token", "version", "protocol"}
        assert isinstance(parsed["port"], int) and parsed["port"] > 0
        assert parsed["token"]

        # Tokenless connection is refused with 403 (token mandatory on loopback).
        c = socket.create_connection(("127.0.0.1", parsed["port"]), timeout=5)
        c.sendall(
            b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Upgrade: websocket\r\nSec-WebSocket-Key: abc\r\n\r\n"
        )
        resp = c.recv(256)
        c.close()
        assert b"403" in resp

        # Closing stdin drains _watch_stdin -> os._exit(0).
        proc.stdin.close()
        rc = proc.wait(timeout=15)
        assert rc == 0

        # No second stdout line after the handshake.
        rest = proc.stdout.read()
        assert rest.strip() == "", f"unexpected extra stdout: {rest!r}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def _readline_with_timeout(stream, *, timeout: float) -> str:
    """Read one line from a text pipe without hanging the suite forever."""
    result: list[str] = []

    def _read():
        result.append(stream.readline())

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    return result[0] if result else ""
