"""T1 LocalAPI client — fake unix socket, no tailscale installed."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from xlii.tailnet import (
    TailnetPeer,
    TailnetSelf,
    is_tailnet_ip,
    names_match,
    peer_online,
    self_status,
    whois,
)

STATUS = {
    "BackendState": "Running",
    "Self": {
        "HostName": "throne",
        "DNSName": "throne.tailnet.ts.net.",
        "StableID": "nSELF",
        "TailscaleIPs": ["100.64.0.1", "fd7a:115c:a1e0::1"],
    },
    "Peer": {
        "nPHONE": {
            "HostName": "phone",
            "DNSName": "phone.tailnet.ts.net.",
            "StableID": "nPHONE",
            "Online": True,
            "TailscaleIPs": ["100.64.0.2"],
        },
        "nOFF": {
            "HostName": "tablet",
            "DNSName": "tablet.tailnet.ts.net.",
            "StableID": "nOFF",
            "Online": False,
            "TailscaleIPs": ["100.64.0.3"],
        },
    },
}

WHOIS_PHONE = {
    "Node": {
        "StableID": "nPHONE",
        "Name": "phone.tailnet.ts.net.",
        "Hostinfo": {"Hostname": "phone"},
        "Addresses": ["100.64.0.2/32"],
    },
    "UserProfile": {"LoginName": "you@github"},
}


class _Fake:
    def __init__(self, sock: socket.socket, stop: threading.Event) -> None:
        self.sock = sock
        self.stop = stop

    def shutdown(self) -> None:
        self.stop.set()
        try:
            self.sock.close()
        except OSError:
            # Best-effort teardown: socket may already be closed during fake-server shutdown.
            pass

    def server_close(self) -> None:
        self.shutdown()


def _http_reply(conn: socket.socket, status: int, body: bytes) -> None:
    reason = "OK" if status == 200 else "ERR"
    conn.sendall(
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n".encode()
        + body
    )


def _serve(sock_path: Path, *, status=STATUS, whois_map=None, raw_body: bytes | None = None):
    whois_map = whois_map if whois_map is not None else {"100.64.0.2:9": WHOIS_PHONE}
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(8)
    srv.settimeout(0.2)
    stop = threading.Event()

    def handle(conn: socket.socket) -> None:
        with conn:
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 65536:
                chunk = conn.recv(4096)
                if not chunk:
                    return
                data += chunk
            line = data.decode("utf-8", "replace").split("\r\n", 1)[0]
            parts = line.split(" ")
            path = parts[1] if len(parts) > 1 else "/"
            route = path.split("?", 1)[0]
            if raw_body is not None:
                _http_reply(conn, 200, raw_body)
                return
            if route == "/localapi/v0/status":
                _http_reply(conn, 200, json.dumps(status).encode())
                return
            if route == "/localapi/v0/whois":
                addr = (parse_qs(urlparse(path).query).get("addr") or [""])[0]
                payload = whois_map.get(addr)
                if payload is None:
                    _http_reply(conn, 404, b"{}")
                    return
                _http_reply(conn, 200, json.dumps(payload).encode())
                return
            _http_reply(conn, 404, b"{}")

    def loop() -> None:
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            threading.Thread(target=handle, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, daemon=True).start()
    return _Fake(srv, stop)


@pytest.fixture
def ts(tmp_path, monkeypatch):
    sock = tmp_path / "tailscaled.sock"
    httpd = _serve(sock)
    monkeypatch.setenv("XLII_TAILSCALE_SOCKET", str(sock))
    # HTTPServer.serve_forever races the first request on a brand-new unix sock.
    deadline = time.time() + 2
    while time.time() < deadline:
        if sock.exists():
            break
        time.sleep(0.01)
    yield sock
    httpd.shutdown()


def test_absent_socket_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_TAILSCALE_SOCKET", str(tmp_path / "nope.sock"))
    assert self_status() is None
    assert whois("100.64.0.2", 9) is None
    assert is_tailnet_ip("100.64.0.1") is False
    assert peer_online("phone") is None


def test_self_status(ts):
    st = self_status()
    assert isinstance(st, TailnetSelf)
    assert st.up is True
    assert st.ipv4 == "100.64.0.1"
    assert "100.64.0.1" in st.addrs
    assert st.node_name == "throne"
    assert st.dns_name == "throne.tailnet.ts.net"
    names = {p.node_name for p in st.peers}
    assert names == {"phone", "tablet"}


def test_whois_and_identities(ts):
    peer = whois("100.64.0.2", 9)
    assert isinstance(peer, TailnetPeer)
    assert peer.node_name == "phone"
    assert "phone" in peer.identities()
    assert "phone.tailnet.ts.net" in peer.identities()
    assert "nphone" in peer.identities()
    assert names_match("phone", peer)
    assert names_match("phone.tailnet.ts.net", peer)
    assert names_match("nPHONE", peer)
    assert not names_match("tablet", peer)
    assert whois("100.64.0.9", 9) is None


def test_is_tailnet_ip_uses_daemon_state_not_prefix(ts):
    assert is_tailnet_ip("100.64.0.1") is True
    assert is_tailnet_ip("100.64.0.2") is True
    assert is_tailnet_ip("100.64.0.9") is False
    assert is_tailnet_ip("100.64.0.1/32") is True


def test_peer_online(ts):
    assert peer_online("phone") is True
    assert peer_online("tablet") is False
    assert peer_online("missing") is False


def test_logged_out_is_down(tmp_path, monkeypatch):
    sock = tmp_path / "tailscaled.sock"
    httpd = _serve(sock, status={"BackendState": "NeedsLogin", "Self": {}})
    monkeypatch.setenv("XLII_TAILSCALE_SOCKET", str(sock))
    time.sleep(0.05)
    st = self_status()
    assert st is not None
    assert st.up is False
    assert st.ipv4 == ""
    httpd.shutdown()


def test_prepare_bind_resolves_tailnet(ts):
    from xlii.bind_posture import bind_advisory, prepare_bind

    host, err = prepare_bind("tailnet", expose=True, surface="serve")
    assert err is None
    assert host == "100.64.0.1"
    host, err = prepare_bind("tailnet", expose=False, surface="serve-face")
    assert err is not None and "--expose" in err
    host, err = prepare_bind("127.0.0.1", expose=False, surface="serve")
    assert err is None and host == "127.0.0.1"
    assert bind_advisory("100.64.0.1") is None
    note = bind_advisory("192.168.1.9")
    assert note is not None and "hotel-wifi" in note


def test_prepare_bind_refuses_when_tailscale_down(tmp_path, monkeypatch):
    from xlii.bind_posture import prepare_bind

    monkeypatch.setenv("XLII_TAILSCALE_SOCKET", str(tmp_path / "nope.sock"))
    host, err = prepare_bind("tailnet", expose=True, surface="serve")
    assert host == "tailnet"
    assert err is not None and "cannot --host tailnet" in err


def test_malformed_json_is_unavailable(tmp_path, monkeypatch):
    sock = tmp_path / "tailscaled.sock"
    httpd = _serve(sock, raw_body=b"not-json")
    monkeypatch.setenv("XLII_TAILSCALE_SOCKET", str(sock))
    time.sleep(0.05)
    assert self_status() is None
    httpd.shutdown()
