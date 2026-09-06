"""The W2 WebSocket server — RFC6455 codec, upgrade handshake, accept loop.

Pure transport over stdlib sockets: no agent/tui coupling (the one dispatch
point — a client ``turn`` message — calls ``xlii.agent_dispatch``'s
``run_headless_turn`` lazily). Stdlib WebSocket only — no extra beyond base
xlii.

Kernel home of what lived in ``cmds/serve_ws.py`` (godzilla-mothra B5); the
cmd file keeps the argparse façade. Spec: docs/ws-event-protocol.md
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import socket
import struct
import sys
import threading
from typing import Any, Callable, Optional

from xlii.ws_protocol import handshake_payload, hello_message

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Cap the declared payload of an inbound frame. This is an inbound
# remote-execution channel; without a bound, a client's 64-bit length field
# (frame header, no data yet) makes _recv_exact grow an unbounded bytearray →
# OOM. The control protocol is tiny JSON (a turn prompt); 8 MiB is generous.
MAX_FRAME_BYTES = 8 * 1024 * 1024


def _accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + WS_MAGIC).encode()).digest()
    return base64.b64encode(digest).decode()


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes, coalescing short reads. Returns ``b""`` on a
    clean EOF or a truncated read — ``recv`` can return fewer bytes than asked
    even for the tiny 2/4/8-byte frame headers, so every fixed-width read below
    goes through here rather than trusting a single ``recv``."""
    buf = bytearray()
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            return b""
        buf += chunk
    return bytes(buf)


def _read_frame(conn: socket.socket) -> tuple[int, bytes] | None:
    head = _recv_exact(conn, 2)
    if len(head) < 2:
        return None
    opcode = head[0] & 0x0F
    masked = head[1] & 0x80
    length = head[1] & 0x7F
    if length == 126:
        ext = _recv_exact(conn, 2)
        if len(ext) < 2:
            return None
        length = struct.unpack(">H", ext)[0]
    elif length == 127:
        ext = _recv_exact(conn, 8)
        if len(ext) < 8:
            return None
        length = struct.unpack(">Q", ext)[0]
    if length > MAX_FRAME_BYTES:
        # Reject an oversized frame rather than allocate for it — treat as a
        # dead connection so _handle_connection closes it.
        return None
    if masked:
        mask = _recv_exact(conn, 4)
        if len(mask) < 4:
            return None
    else:
        mask = b""
    payload = _recv_exact(conn, length)
    if length and len(payload) < length:
        return None
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload


def _send_text(conn: socket.socket, text: str) -> None:
    """Send ``text`` as a single unmasked WebSocket text frame. The length field
    is symmetric with ``_read_frame``: 7-bit for < 126 bytes, 16-bit (126) up to
    65535, and 64-bit (127) beyond — a tool_finished event carrying a large file
    read easily exceeds 65535 bytes and MUST take the >Q branch, not overflow the
    >H one."""
    data = text.encode()
    n = len(data)
    if n < 126:
        header = bytes([0x81, n])
    elif n <= 0xFFFF:
        header = bytes([0x81, 126]) + struct.pack(">H", n)
    else:
        header = bytes([0x81, 127]) + struct.pack(">Q", n)
    conn.sendall(header + data)


def _parse_client_message(raw: str) -> dict[str, Any]:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"invalid JSON: {e}") from e
    if not isinstance(msg, dict) or "type" not in msg:
        raise ValueError("message must be a JSON object with a type field")
    return msg


def _handle_connection(
    conn: socket.socket,
    *,
    token: str,
    project,
    yolo: bool,
) -> None:
    from xlii.agent_dispatch import run_headless_turn

    session_id = f"ws-{secrets.token_hex(8)}"
    # One pool for the whole connection (reused across turns) — building it per
    # turn leaks an httpx client set each time on a long-lived connection.
    pool = None
    cfg = None
    with conn:
        # Read the upgrade request ONE byte at a time, stopping exactly at the
        # header terminator. A bulk recv can pull a pipelined first FRAME along
        # with the headers and silently discard it (nothing below re-reads the
        # remainder) — the client then waits forever for a reply to a frame the
        # server never saw. Same deadlock class as the historic test hang; a
        # few hundred recv(1) calls per handshake is nothing for this server.
        request = b""
        while not request.endswith(b"\r\n\r\n"):
            chunk = conn.recv(1)
            if not chunk:
                return
            request += chunk
        head = request.decode(errors="replace")
        request_line = head.split("\r\n", 1)[0]
        headers = {
            line.split(":", 1)[0].strip().lower(): line.split(":", 1)[1].strip()
            for line in head.split("\r\n")[1:]
            if ":" in line
        }
        path = request_line.split(" ")[1] if len(request_line.split(" ")) > 1 else "/"
        query = path.split("?", 1)[1] if "?" in path else ""
        params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
        if not secrets.compare_digest(params.get("token", ""), token):
            conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\nbad token\r\n")
            return
        client_key = headers.get("sec-websocket-key", "")
        if not client_key:
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\nwebsocket only\r\n")
            return
        conn.sendall(
            (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {_accept_key(client_key)}\r\n\r\n"
            ).encode()
        )

        def send(obj: dict[str, Any]) -> None:
            _send_text(conn, json.dumps(obj, separators=(",", ":")))

        send(hello_message(session_id=session_id))

        while True:
            frame = _read_frame(conn)
            if frame is None:
                return
            opcode, payload = frame
            if opcode == 0x8:
                return
            if opcode != 0x1:
                continue
            try:
                msg = _parse_client_message(payload.decode(errors="replace"))
            except ValueError as e:
                send({"type": "error", "message": str(e)})
                continue
            kind = msg.get("type")
            if kind == "ping":
                send({"type": "pong"})
            elif kind == "turn":
                prompt = (msg.get("prompt") or "").strip()
                if not prompt:
                    send({"type": "error", "message": "turn requires prompt"})
                    continue
                if pool is None:
                    from xlii.client import MissingCredentials
                    from xlii.config import GlobalConfig
                    from xlii.pool import ClientPool
                    cfg = GlobalConfig.load()
                    try:
                        pool = ClientPool.from_config(cfg)
                    except MissingCredentials as e:
                        send({"type": "error", "message": str(e)})
                        send({"type": "turn_done", "ok": False, "exit_code": 1})
                        continue
                run_headless_turn(
                    project=project,
                    session_id=session_id,
                    prompt=prompt,
                    sink=send,
                    yolo=yolo,
                    pool=pool,
                    cfg=cfg,
                )
            else:
                send({"type": "error", "message": f"unknown client message type: {kind}"})


def _watch_stdin(on_close: "Callable[[], None] | None" = None) -> None:
    """Exit when stdin closes. Optional ``on_close`` runs graceful teardown first."""
    sys.stdin.buffer.read()
    if on_close is not None:
        try:
            on_close()
        except Exception:
            pass
    os._exit(0)


def serve_ws(
    project,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token: Optional[str] = None,
    handshake: bool = False,
    yolo: bool = False,
    expose: bool = False,
    on_bound: Optional[Callable[[int], None]] = None,
) -> int:
    """Run the W2 WebSocket server until interrupted or stdin closes (handshake mode).

    ``on_bound`` (when given) is called with the real bound port after
    ``listen()`` — i.e. only once a connect can actually succeed. In-process
    callers (tests, embedders) pass ``port=0`` + ``on_bound`` instead of
    probing for a free port themselves, which is inherently racy
    (bind-close-rebind: the probed port can be lost, and a connect can land in
    the probe listener's backlog and die with it)."""
    from xlii.bind_posture import prepare_bind

    host, refused = prepare_bind(host, expose=expose, surface="serve-ws")
    if refused:
        print(f"serve-ws: {refused}", file=sys.stderr)
        return 1

    if host not in _LOOPBACK:
        print(
            f"serve-ws: WARNING binding {host} (non-localhost) — token auth is mandatory "
            "but the port is still a remote agent shell. Use tailnet/private interfaces only.",
            file=sys.stderr,
        )

    auth_token = token or secrets.token_hex(16)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, port))
    server.listen(4)
    bound_port = server.getsockname()[1]
    if on_bound is not None:
        on_bound(bound_port)

    if handshake:
        line = json.dumps(handshake_payload(bound_port, auth_token), separators=(",", ":"))
        print(line, flush=True)
        threading.Thread(target=_watch_stdin, daemon=True).start()
    else:
        print(
            f"serve-ws: ws://{host}:{bound_port}/?token={auth_token}",
            file=sys.stderr,
        )
        print(
            "serve-ws: token required on every connection (even loopback). "
            "One session per WebSocket connection.",
            file=sys.stderr,
        )

    try:
        while True:
            conn, _ = server.accept()
            threading.Thread(
                target=_handle_connection,
                args=(conn,),
                kwargs={
                    "token": auth_token,
                    "project": project,
                    "yolo": yolo,
                },
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        print("\nserve-ws: stopped", file=sys.stderr)
    finally:
        server.close()
    return 0
