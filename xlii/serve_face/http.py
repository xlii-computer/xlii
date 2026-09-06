"""``serve_face`` entry + static HTTP / WebSocket accept loop.

Extracted from the former ``xlii/serve_face.py`` god-file.
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from xlii.serve_face.server import FaceServer
from xlii.serve_face.wire import (
    WireRenderer,
    _LOOPBACK,
    _WireFile,
    _is_scratch_desk_root,
    default_assets_dir,
)
from xlii.ws_protocol import handshake_error, handshake_payload, hello_message
from xlii.ws_server import _accept_key, _send_text, _watch_stdin

# ---------------------------------------------------------------- HTTP static


_CONTENT_TYPES = {".html": "text/html; charset=utf-8",
                  ".css": "text/css; charset=utf-8",
                  ".js": "text/javascript; charset=utf-8",
                  ".webm": "video/webm",
                  ".png": "image/png",
                  ".svg": "image/svg+xml",
                  ".jpg": "image/jpeg",
                  ".jpeg": "image/jpeg",
                  ".webp": "image/webp",
                  ".gif": "image/gif",
                  ".woff2": "font/woff2",
                  ".webmanifest": "application/manifest+json",
                  ".toml": "application/toml; charset=utf-8"}


def _http_bytes(
    conn: socket.socket,
    status: str,
    body: bytes,
    ctype: str,
    *,
    extra: str = "",
) -> None:
    conn.sendall(
        (f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\n"
         f"Content-Length: {len(body)}\r\n{extra}"
         "Connection: close\r\n\r\n").encode()
        + body
    )


def _serve_static(
    conn: socket.socket,
    path: str,
    assets_dir: Optional[Path],
    *,
    extra: str = "",
    view_phone: bool = False,
) -> None:
    """Plain-HTTP side of the socket: the face page itself. Ungated on
    loopback — the assets are the same public bytes the wheel ships; the WS
    is what the token protects. The tailnet glass listener WhoIs-gates
    before calling this."""
    clean = path.split("?", 1)[0]
    if assets_dir is None or not assets_dir.is_dir():
        body = b"face assets not installed\n"
        _http_bytes(conn, "404 Not Found", body, "text/plain; charset=utf-8", extra=extra)
        return
    if clean in ("/", "/index.html"):
        target = assets_dir / "index.html"
    else:
        # Any other path resolves inside the assets dir — the SAME layout the
        # Tauri webview serves at its root, so index.html's relative links
        # (css/…, js/…, vendor/…) work identically on both surfaces.
        target = (assets_dir / clean.lstrip("/")).resolve()
        try:
            target.relative_to(assets_dir.resolve())
        except ValueError:
            _http_bytes(conn, "403 Forbidden", b"", "text/plain", extra=extra)
            return
    if not target.is_file():
        _http_bytes(conn, "404 Not Found", b"", "text/plain", extra=extra)
        return
    body = target.read_bytes()
    if view_phone and target.name == "index.html":
        body = body.replace(
            b'<html lang="en"',
            b'<html lang="en" data-view="phone"',
            1,
        )
    ctype = _CONTENT_TYPES.get(target.suffix.lower()) or (
        mimetypes.guess_type(target.name)[0] or "application/octet-stream")
    _http_bytes(conn, "200 OK", body, ctype, extra=extra)


def _serve_skins(conn: socket.socket, path: str, *, extra: str = "") -> bool:
    """Read-only pack route. True when the request was for ``/skins/``.

    Door-tier TailnetDoor never calls this (404s first). Glass tier WhoIs-
    gates, then serves. Traversal, bad extensions, and oversize files 404.
    """
    clean = path.split("?", 1)[0]
    if clean != "/skins/catalog" and not clean.startswith("/skins/"):
        return False
    from xlii.skin_packs import catalog_entries, resolve_pack_file

    if clean in ("/skins/catalog", "/skins/catalog.json"):
        body = json.dumps({"skins": catalog_entries()}, separators=(",", ":")).encode()
        _http_bytes(conn, "200 OK", body, "application/json", extra=extra)
        return True
    rest = clean[len("/skins/"):]
    if "/" not in rest:
        _http_bytes(conn, "404 Not Found", b"", "text/plain", extra=extra)
        return True
    name, rel = rest.split("/", 1)
    target = resolve_pack_file(name, rel)
    if target is None:
        _http_bytes(conn, "404 Not Found", b"", "text/plain", extra=extra)
        return True
    try:
        body = target.read_bytes()
    except OSError:
        _http_bytes(conn, "404 Not Found", b"", "text/plain", extra=extra)
        return True
    ctype = _CONTENT_TYPES.get(target.suffix.lower()) or (
        mimetypes.guess_type(target.name)[0] or "application/octet-stream")
    _http_bytes(conn, "200 OK", body, ctype, extra=extra)
    return True


def _read_http_body(request: bytes, conn: socket.socket, headers: dict[str, str]) -> bytes:
    try:
        clen = int(headers.get("content-length") or 0)
    except ValueError:
        clen = 0
    leftover = (
        request.split(b"\r\n\r\n", 1)[1]
        if b"\r\n\r\n" in request else b""
    )
    body = leftover
    while len(body) < clen:
        chunk = conn.recv(min(65536, clen - len(body)))
        if not chunk:
            break
        body += chunk
    return body[:clen]


# ------------------------------------------------------------------ the server


def _handle_connection(
    conn: socket.socket,
    *,
    token: str,
    server: FaceServer,
    on_tailnet_bind: bool = False,
    remote_only: bool = False,
    grants: Any = None,
) -> None:
    with conn:
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
        parts = request_line.split(" ")
        method = parts[0] if parts else "GET"
        path = parts[1] if len(parts) > 1 else "/"
        try:
            peer = conn.getpeername()
        except OSError:
            peer = None

        from xlii.serve_face.glass import SECURITY_HEADER_BLOCK, sitting_tier

        glass = bool(on_tailnet_bind) and (not remote_only) and sitting_tier() == "glass"
        # Phone UI from CONNECTION provenance — never leftover view_posture of
        # another client (that caused desk loopback to re-enter attach_glass).
        boot_phone = bool(getattr(server, "_boot_view_phone", False))
        phone = glass or boot_phone

        if "websocket" not in headers.get("upgrade", "").lower():
            route = path.split("?", 1)[0]
            if route in ("/remote-turn", "/remote"):
                body = _read_http_body(request, conn, headers)
                from xlii.face_remote import handle_http_remote_turn

                status, payload, ctype = handle_http_remote_turn(
                    method=method, path=path, headers=headers, body=body,
                    token=token, server=server,
                    peer=peer, on_tailnet_bind=on_tailnet_bind,
                )
                extra = SECURITY_HEADER_BLOCK if glass else ""
                conn.sendall((
                    f"HTTP/1.1 {status} {'OK' if status == 200 else 'ERR'}\r\n"
                    f"Content-Type: {ctype}\r\n"
                    f"Content-Length: {len(payload)}\r\n"
                    f"{extra}"
                    "Connection: close\r\n\r\n"
                ).encode() + payload)
                return
            if on_tailnet_bind and not glass:
                conn.sendall(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
                return
            if glass:
                _handle_glass_http(
                    conn, method=method, path=path, headers=headers,
                    request=request, peer=peer, server=server, grants=grants,
                )
                return
            if _serve_skins(conn, path):
                return
            _serve_static(
                conn, path, server.assets_dir, view_phone=phone,
            )
            return

        if on_tailnet_bind and not glass:
            conn.sendall(b"HTTP/1.1 404 Not Found\r\nConnection: close\r\n\r\n")
            return

        glass_grant = None
        if glass:
            glass_grant = _glass_grant(headers, peer, grants)
            if glass_grant is None:
                _http_bytes(
                    conn, "403 Forbidden", b"need grant\n",
                    "text/plain; charset=utf-8", extra=SECURITY_HEADER_BLOCK,
                )
                return
        else:
            query = path.split("?", 1)[1] if "?" in path else ""
            params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
            if not secrets.compare_digest(params.get("token", ""), token):
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\n\r\nbad token\r\n")
                return
        client_key = headers.get("sec-websocket-key", "")
        if not client_key:
            conn.sendall(b"HTTP/1.1 400 Bad Request\r\n\r\nwebsocket only\r\n")
            return
        conn.sendall((
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {_accept_key(client_key)}\r\n\r\n"
        ).encode())

        # Real tailnet glass (grant present) → attach_glass_client.
        # Boot ``--view phone`` / desk / Tauri → attach_client only (phone UI
        # via view_posture / _boot_view_phone, without _client_glass sitting gate).
        attached = (
            server.attach_glass_client(conn, grant=glass_grant) if glass
            else server.attach_client(conn)
        )
        if not attached:
            with server._send_lock:
                _send_text(conn, json.dumps(
                    {"type": "error",
                     "message": "another client is mid-turn on this session"},
                    separators=(",", ":")))
            try:
                conn.close()
            except OSError:
                pass
            return

        server.send(hello_message(
            session_id=server._wire_session_id,
            view_posture="phone" if phone else "desk",
            tailnet_glass=glass,
            resumed=server._had_client,
        ))
        server._had_client = True
        if phone:
            try:
                from xlii.serve_face.glass import phone_open_dual

                phone_open_dual(server)
            except Exception:
                pass
        server._apply_workbench_chrome()  # quick-launch pack chrome (not posture)
        server.send(server.mode_state())
        server.send(server.chrome_state())  # F1: the HUD rail + surface door
        try:
            server._emit_focus_state(even_empty=False)
        except Exception:  # noqa: BLE001
            pass
        try:
            server.send(server.command_catalog())  # slash popup — chat-safe in [M]
        except Exception:  # noqa: BLE001 — catalog is a nicety; never block connect
            pass
        try:
            server.send(server.plugin_catalog())  # M2.1: Plugins menu
        except Exception:  # noqa: BLE001
            pass
        try:
            server.send(server.workbench_catalog())  # Workbench menu packs
        except Exception:  # noqa: BLE001
            pass
        try:
            server.send(server.console_catalog())  # Commands → System/Network/…
        except Exception:  # noqa: BLE001
            pass
        try:
            server.send(server.pane_catalog())
        except Exception:  # noqa: BLE001
            pass
        server.deck.send_snapshot()  # B1: the workbench's pane strip, when any
        try:
            server.note_open_stream(getattr(server.state, "project", None))
            server._stream_live = server.live_stream_id()
        except Exception:  # noqa: BLE001 — catalog can start empty
            pass
        server.sync_stream()  # Face tape = this project's parked history
        try:
            server._emit_resume_meta_from_disk()
        except Exception:  # noqa: BLE001
            pass
        # three-faces Q5: one-line home door welcome on scratch desks
        try:
            from xlii.status import surface_axis

            if surface_axis(server.state) == "scratch":
                from xlii.project_paths import user_home

                home = str(user_home())
                server.send({
                    "type": "meta_message",
                    "text": f"Home — shell at {home} · config "
                            f"~/.xlii/scratch/home · never-sync · chat [M] · "
                            "switch into a project when ready",
                    "level": "info",
                })
        except Exception:  # noqa: BLE001 — welcome line is optional chrome
            pass
        server.reader(
            conn,
            conn_glass=glass,
            grant_mode=str(getattr(glass_grant, "mode", "") or "full"),
        )  # runs until disconnect/shutdown


def _handle_glass_http(
    conn: socket.socket,
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    request: bytes,
    peer: Any,
    server: FaceServer,
    grants: Any,
) -> None:
    """WhoIs every request; pairing page until a sitting-bound grant exists."""
    from xlii.face_remote import auth_tailnet_peer
    from xlii.serve_face.glass import (
        SECURITY_HEADER_BLOCK,
        cookie_value,
        form_code,
        pairing_html,
        set_cookie_header,
        sitting_id,
        spend_webcode,
    )

    if grants is None:
        _http_bytes(
            conn, "403 Forbidden", b"forbidden\n",
            "text/plain; charset=utf-8", extra=SECURITY_HEADER_BLOCK,
        )
        return
    device, denied = auth_tailnet_peer(peer, rate_limit=False)
    if denied is not None:
        _http_bytes(
            conn, "403 Forbidden", b"forbidden\n",
            "text/plain; charset=utf-8", extra=SECURITY_HEADER_BLOCK,
        )
        return

    route = path.split("?", 1)[0]
    query = path.split("?", 1)[1] if "?" in path else ""
    params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
    now = time.time()

    if method == "POST" and route == "/pair":
        body = _read_http_body(request, conn, headers)
        code = form_code(body)
        grant, err = spend_webcode(
            grants, code=code, device=device, remote=device, now=now,
        )
        if grant is None:
            _http_bytes(
                conn, "403 Forbidden",
                pairing_html(error=err or "bad code"),
                "text/html; charset=utf-8", extra=SECURITY_HEADER_BLOCK,
            )
            return
        extra = set_cookie_header(grant.token) + SECURITY_HEADER_BLOCK
        conn.sendall(
            b"HTTP/1.1 302 Found\r\nLocation: /\r\n"
            + extra.encode()
            + b"Content-Length: 0\r\nConnection: close\r\n\r\n"
        )
        return

    sid = sitting_id()
    grant = grants.valid(cookie_value(headers), device=device, sitting_id=sid) if grants else None
    if grant is None:
        prefills = params.get("code") or ""
        _http_bytes(
            conn, "200 OK", pairing_html(code=prefills),
            "text/html; charset=utf-8", extra=SECURITY_HEADER_BLOCK,
        ) if route in ("/", "/index.html", "/pair") else _http_bytes(
            conn, "403 Forbidden", b"need grant\n",
            "text/plain; charset=utf-8", extra=SECURITY_HEADER_BLOCK,
        )
        return
    if _serve_skins(conn, path, extra=SECURITY_HEADER_BLOCK):
        return
    _serve_static(
        conn, path, server.assets_dir,
        extra=SECURITY_HEADER_BLOCK, view_phone=True,
    )


def _glass_grant(headers: dict[str, str], peer: Any, grants: Any) -> Any:
    from xlii.face_remote import auth_tailnet_peer
    from xlii.serve_face.glass import cookie_value, sitting_id

    device, denied = auth_tailnet_peer(peer, rate_limit=False)
    if denied is not None or grants is None:
        return None
    return grants.valid(
        cookie_value(headers), device=device, sitting_id=sitting_id(),
    )


class TailnetDoor:
    """Second listener on the tailnet IP. Lives only while a sitting is open.

    Loopback Face serves the UI. Sitting tier ``door`` (default) answers
    only ``/remote-turn``; ``glass`` (``/remote-control open --glass``)
    serves assets, skins, and a grant-gated WebSocket.
    """

    def __init__(self, *, token: str, server: FaceServer, port: int) -> None:
        from xlii.serve_face.glass import GlassGrantStore

        self.token = token
        self.server = server
        self.port = port
        self.grants = GlassGrantStore()
        self._lock = threading.Lock()
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.bound: Optional[tuple[str, int]] = None
        # Live accepted sockets (HTTP + WS). Listener stop alone leaves
        # per-connection reader threads streaming — track and kill on drop.
        self._live_lock = threading.Lock()
        self._live_conns: set[socket.socket] = set()
        self._active_sitting_id = ""
        self._active_sitting_tier = "door"

    def sync(self) -> None:
        from xlii.face_remote import sitting_allows_remote
        from xlii.serve_face.glass import sitting_id, sitting_tier

        want = sitting_allows_remote()
        sid = sitting_id() if want else ""
        tier = sitting_tier() if want else "door"
        with self._lock:
            if want and self._sock is None:
                self._start_locked()
                if self._sock is not None:
                    self._active_sitting_id = sid
                    self._active_sitting_tier = tier
            elif want and self._sock is not None:
                if (sid != self._active_sitting_id
                        or tier != self._active_sitting_tier):
                    self.grants.drop_all()
                    self._kill_live_conns()
                    self._active_sitting_id = sid
                    self._active_sitting_tier = tier
            elif not want and self._sock is not None:
                self.grants.drop_all()
                self._kill_live_conns()
                self._stop_locked()
                self._active_sitting_id = ""
                self._active_sitting_tier = "door"

    def close(self) -> None:
        with self._lock:
            self._kill_live_conns()
            self._stop_locked()

    def _track_conn(self, conn: socket.socket) -> None:
        with self._live_lock:
            self._live_conns.add(conn)

    def _untrack_conn(self, conn: socket.socket) -> None:
        with self._live_lock:
            self._live_conns.discard(conn)

    def _kill_live_conns(self) -> None:
        """Close every accepted connection — mid-WS drop must die, not linger."""
        with self._live_lock:
            conns = list(self._live_conns)
            self._live_conns.clear()
        for c in conns:
            try:
                c.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                c.close()
            except OSError:
                pass

    def _start_locked(self) -> None:
        from xlii.tailnet import self_status

        st = self_status()
        if st is None or not st.up or not st.ipv4:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((st.ipv4, self.port))
        except OSError as e:
            print(
                f"serve-face: tailnet door bind {st.ipv4}:{self.port} failed "
                f"({e.strerror or e})",
                file=sys.stderr,
            )
            sock.close()
            return
        sock.listen(4)
        sock.settimeout(0.5)
        self._sock = sock
        self._stop.clear()
        self.bound = sock.getsockname()[:2]
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()
        name = st.dns_name or st.node_name or st.ipv4
        from xlii.serve_face.glass import sitting_tier

        if sitting_tier() == "glass":
            print(
                f"serve-face: tailnet glass http://{name}:{self.bound[1]}/",
                file=sys.stderr,
            )
        else:
            print(
                f"serve-face: tailnet door http://{name}:{self.bound[1]}/remote-turn",
                file=sys.stderr,
            )

    def _stop_locked(self) -> None:
        self._stop.set()
        sock = self._sock
        thread = self._thread
        self._sock = None
        self._thread = None
        self.bound = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                # Best-effort teardown: socket may already be closed or not connected.
                pass
            try:
                sock.close()
            except OSError:
                # Best-effort teardown: close errors during shutdown are non-fatal.
                pass
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def _accept(self) -> None:
        while not self._stop.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                conn, _addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self._track_conn(conn)
            threading.Thread(
                target=self._serve_one,
                args=(conn,),
                daemon=True,
            ).start()

    def _serve_one(self, conn: socket.socket) -> None:
        try:
            _handle_connection(
                conn,
                token=self.token,
                server=self.server,
                on_tailnet_bind=True,
                grants=self.grants,
            )
        finally:
            self._untrack_conn(conn)


def _emit_handshake_failure(msg: str, *, handshake: bool) -> None:
    """Stderr always; stdout JSON only in --handshake so Tauri can stop spinning."""
    print(f"serve-face: {msg}", file=sys.stderr)
    if handshake:
        print(json.dumps(handshake_error(msg), separators=(",", ":")), flush=True)


def serve_face(
    root: Path,
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    token: Optional[str] = None,
    handshake: bool = False,
    yolo: bool = False,
    force: bool = False,
    replace_instance: bool = False,
    expose: bool = False,
    assets_dir: Optional[Path] = None,
    on_bound: Optional[Callable[[int], None]] = None,
    on_server: Optional[Callable[["FaceServer"], None]] = None,
    boot: Any = None,
    view: str = "desk",
) -> int:
    """Run the face server over one live session until stdin closes (handshake
    mode), the client sends ``/exit``, or interrupt.

    ``boot`` (tests/embedders) injects a pre-built CodeSession-shaped object
    and skips ``build_code_session``. ``on_bound`` fires with the real port
    after ``listen()`` — the race-free readiness signal (same contract as
    ``serve_ws``). ``on_server`` hands the embedder the live FaceServer so it
    can trigger a shutdown (tests MUST — an abandoned server keeps the confirm
    hook + active_session swapped for the whole process)."""
    import xlii.tools as _tools
    from xlii import active_session as _active_session
    from xlii.outbox import grant_local_outbox, release_local_outbox

    assets = assets_dir if assets_dir is not None else default_assets_dir()

    if boot is None:
        try:
            from xlii.panic_mail import check_on_wake

            r = check_on_wake()
            if r.status == "kill":
                _emit_handshake_failure("panic kill — not starting", handshake=handshake)
                return 1
            if r.status == "destroy_run":
                _emit_handshake_failure("panic destroy — not starting", handshake=handshake)
                return 1
        except Exception:
            # panic-mail is an optional guard: if the check itself fails, boot
            # proceeds rather than bricking the surface.
            pass

    from xlii.bind_posture import bind_advisory, prepare_bind

    host, refused = prepare_bind(host, expose=expose, surface="serve-face")
    if refused:
        _emit_handshake_failure(refused, handshake=handshake)
        return 1

    if host not in _LOOPBACK:
        print(
            f"serve-face: WARNING binding {host} (non-localhost) — token auth is "
            "mandatory but the port is still a remote shell. Use tailnet/private "
            "interfaces only.",
            file=sys.stderr,
        )
        note = bind_advisory(host)
        if note:
            print(f"serve-face: WARNING {note}", file=sys.stderr)

    # The wire console exists before any client: sends just drop until one
    # attaches (mode_state on connect covers the catch-up).
    server_box: list[FaceServer] = []

    def _send(obj: dict[str, Any]) -> None:
        if server_box:
            server_box[0].send(obj)

    from rich.console import Console
    wire_console = Console(file=_WireFile(_send), force_terminal=False,
                           no_color=True, width=100)
    # No Live preview on a wire console — the render callback is the ONE
    # assistant_answer emitter (same shape as the Textual transcript console).
    wire_console.supports_live = False

    # One face per environment: refuse (or replace) before expensive boot.
    from xlii.face_instance import claim, prepare_serve, release

    ok_start, inst_msg = prepare_serve(replace=replace_instance)
    if not ok_start:
        _emit_handshake_failure(inst_msg, handshake=handshake)
        return 1
    if inst_msg:
        print(f"serve-face: {inst_msg}", file=sys.stderr)

    # Bind BEFORE the (expensive) session boot: a taken pinned port fails in
    # milliseconds with a dry message instead of after a full resume+sync.
    # Early connections sit in the listen backlog until the accept loop runs.
    auth_token = token or secrets.token_hex(16)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as e:
        _emit_handshake_failure(
            f"cannot bind {host}:{port} ({e.strerror or e}) — "
            "another server is on that port; pass --port 0 for an ephemeral "
            "one, or a different --port",
            handshake=handshake,
        )
        sock.close()
        return 1
    sock.listen(4)
    bound_port = sock.getsockname()[1]
    claim(port=bound_port, token=auth_token, host=host)

    if boot is None:
        from xlii.repl_cmds import register_all
        from xlii.session_boot import build_code_session
        register_all()
        # Boot on a REAL stderr console: the refusal reasons (nested-session
        # panel, launch gate, missing credentials) must reach the operator's
        # terminal — the wire console drops everything until a client
        # attaches, which reduced every refusal to a bare "(refused)".
        boot_console = Console(stderr=True)
        # three-faces Q5: projects under ~/.xlii/scratch/ are home/scratch desks
        # — never-sync + scratch surface (7am story for Tauri/face).
        scratch_desk = _is_scratch_desk_root(root)
        outcome = build_code_session(
            root, yolo=yolo, interactive=False, force=force,
            console=boot_console, scratch=scratch_desk, no_sync=scratch_desk,
            launch=True,
        )
        if outcome.status != "ok":
            detail = (outcome.reason or outcome.status).strip()
            msg = f"could not open a session at {root}: {detail}"
            # Tauri only reads stdout for the handshake line. An error
            # object here is how the window learns the vault/boot refusal
            # instead of spinning on "sidecar starting".
            _emit_handshake_failure(msg, handshake=handshake)
            try:
                release()
            except Exception:  # noqa: BLE001
                pass
            sock.close()
            return 1 if outcome.status == "refused" else 0
        boot = outcome.session
        # The session is the wire's from here: repoint both consoles (the
        # same swap run_tui_over_session does) so turn output streams to the
        # client. Injected boots (tests/embedders) keep their own console.
        boot.state.console = wire_console
        boot.state.agent.console = wire_console
    server = FaceServer(boot=boot, yolo=yolo, assets_dir=assets)
    if (view or "desk").strip().lower() == "phone":
        server._boot_view_phone = True
        server.view_posture = "phone"
    server_box.append(server)
    if on_server is not None:
        on_server(server)
    state = server.state

    # Streaming: content deltas → assistant_chunk frames.
    state.console.on_content_chunk = (
        lambda t: server.send({"type": "assistant_chunk", "text": t}))
    # The face renderer: typed events straight to the wire (replace, not wrap).
    state.agent._renderer_cache = WireRenderer(state.agent, server.send)

    # Panel host ASAP (before accept / on_bound) so /panel never races a client.
    prev_panel_host_early = None
    try:
        from xlii.face_panes import FacePanelHost
        from xlii.ui import set_panel_host

        prev_panel_host_early = set_panel_host(FacePanelHost(server))
    except Exception as e:  # noqa: BLE001
        print(f"serve-face: panel host unavailable ({type(e).__name__}: {e}) "
              "— /panel still works via face intercept", file=sys.stderr)
        prev_panel_host_early = None

    if on_bound is not None:
        on_bound(bound_port)

    if handshake:
        line = json.dumps(handshake_payload(bound_port, auth_token),
                          separators=(",", ":"))
        print(line, flush=True)

        def _stdin_on_close() -> None:
            done = threading.Event()

            def _graceful() -> None:
                try:
                    from xlii.face_receipt import note_project
                    from xlii.face_instance import release as face_release

                    note_project(
                        getattr(server.state, "project", None), reason="stdin",
                    )
                    face_release()
                    server._request_session_end(reason="stdin")
                except Exception:
                    pass
                finally:
                    done.set()

            threading.Thread(
                target=_graceful, name="face-stdin-exit", daemon=True,
            ).start()
            done.wait(timeout=5.0)

        threading.Thread(
            target=lambda: _watch_stdin(_stdin_on_close), daemon=True,
        ).start()
    else:
        print(f"serve-face: http://{host}:{bound_port}/?token={auth_token}",
              file=sys.stderr)
        print("serve-face: ONE live session; the token gates the WebSocket.",
              file=sys.stderr)

    # The face owns this process's confirm hook for its lifetime: gated tool
    # intents round-trip to the client instead of blanket auto-deny. Restored
    # on the way out so an embedding test process is left clean.
    with _tools._CONFIRM_SWAP_LOCK:
        prev_confirm = _tools._confirm
        _tools._confirm = server.confirm.ask
    prev_session = _active_session.set_active_session(state)
    prev_panel_host = prev_panel_host_early
    granted = None
    try:
        granted = grant_local_outbox(state.agent.session)
    except Exception:
        # granted stays None: the surface still serves, and the release in the
        # teardown below is skipped accordingly.
        pass

    worker = threading.Thread(target=server.worker, daemon=True)
    worker.start()
    sock.settimeout(0.5)
    host_loopback = host in _LOOPBACK
    door = TailnetDoor(token=auth_token, server=server, port=bound_port) if host_loopback else None
    try:
        while not server._shutdown.is_set():
            try:
                conn, _ = sock.accept()
            except socket.timeout:
                if door is not None:
                    door.sync()
                continue
            threading.Thread(
                target=_handle_connection, args=(conn,),
                kwargs={
                    "token": auth_token,
                    "server": server,
                    "on_tailnet_bind": not host_loopback,
                },
                daemon=True,
            ).start()
    except KeyboardInterrupt:
        print("\nserve-face: stopped", file=sys.stderr)
    finally:
        if door is not None:
            door.close()
        server._shutdown.set()
        server.confirm.deny_all()
        with server._client_lock:
            live, server._client = server._client, None
        if live is not None:
            try:
                live.close()
            except OSError:
                # Already closed.
                pass
        try:
            from xlii.ui import set_panel_host

            set_panel_host(prev_panel_host)
        except Exception:  # noqa: BLE001
            pass
        with _tools._CONFIRM_SWAP_LOCK:
            _tools._confirm = prev_confirm
        _active_session.set_active_session(prev_session)
        if granted is not None:
            try:
                release_local_outbox(state.agent.session)
            except Exception:
                # Teardown path: a failed release must not mask why we are
                # exiting.
                pass
        try:
            release()
        except Exception:  # noqa: BLE001
            pass
        sock.close()
    return 0
