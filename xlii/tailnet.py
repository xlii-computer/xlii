"""Talk to the local tailscaled. Never guess a CGNAT prefix.

Identity comes from LocalAPI WhoIs on the unix socket only root/operator
can reach. Every failure (socket absent, daemon down, malformed reply)
returns None / False — callers treat that as deny/absent, never as an
error the user must handle.

Bring-your-own tailscaled. No SDK.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

DEFAULT_SOCKET = "/var/run/tailscale/tailscaled.sock"
_TIMEOUT_S = 1.5
_HOST = "local-tailscaled.sock"


@dataclass(frozen=True)
class TailnetPeer:
    node_name: str
    dns_name: str
    stable_id: str
    addrs: tuple[str, ...]
    user: str
    tags: tuple[str, ...]
    online: bool = True

    def identities(self) -> tuple[str, ...]:
        """Matchable names: hostname, MagicDNS, first label, stable id."""
        out: list[str] = []
        seen: set[str] = set()
        for raw in (self.node_name, self.dns_name, self.stable_id):
            s = (raw or "").strip().rstrip(".").lower()
            if not s or s in seen:
                continue
            seen.add(s)
            out.append(s)
            if "." in s:
                short = s.split(".", 1)[0]
                if short and short not in seen:
                    seen.add(short)
                    out.append(short)
        return tuple(out)


@dataclass(frozen=True)
class TailnetSelf:
    up: bool
    ipv4: str
    addrs: tuple[str, ...]
    dns_name: str
    node_name: str
    peers: tuple[TailnetPeer, ...] = ()


def socket_path() -> Path:
    override = (
        os.environ.get("XLII_TAILSCALE_SOCKET")
        or os.environ.get("TAILSCALE_SOCKET")
        or ""
    ).strip()
    if override:
        return Path(override)
    return Path(DEFAULT_SOCKET)


def self_status() -> Optional[TailnetSelf]:
    raw = _get("/localapi/v0/status")
    if not isinstance(raw, dict):
        return None
    return _parse_status(raw)


def whois(ip: str, port: int) -> Optional[TailnetPeer]:
    addr = _whois_addr(ip, port)
    raw = _get(f"/localapi/v0/whois?addr={quote(addr)}")
    if not isinstance(raw, dict):
        return None
    return _parse_whois(raw)


def is_tailnet_ip(ip: str) -> bool:
    """True only if tailscaled lists this address. Never a 100.64/10 prefix check."""
    want = _bare_ip(ip)
    if not want:
        return False
    st = self_status()
    if st is None:
        return False
    if want in st.addrs:
        return True
    return any(want in p.addrs for p in st.peers)


def peer_online(name: str) -> Optional[bool]:
    """True/False if a named peer is in status; None if tailscaled is absent."""
    st = self_status()
    if st is None:
        return None
    want = (name or "").strip().rstrip(".").lower()
    if not want:
        return False
    for p in st.peers:
        if want in p.identities():
            return bool(p.online)
    return False


def names_match(wanted: str, peer: TailnetPeer) -> bool:
    w = (wanted or "").strip().rstrip(".").lower()
    if not w:
        return False
    return w in peer.identities()


# ------------------------------------------------------------------ internals


def _whois_addr(ip: str, port: int) -> str:
    host = (ip or "").strip()
    if host.startswith("[") and "]" in host:
        return f"{host}:{int(port)}"
    if ":" in host:
        return f"[{host}]:{int(port)}"
    return f"{host}:{int(port)}"


def _bare_ip(raw: str) -> str:
    s = (raw or "").strip()
    if "/" in s:
        s = s.split("/", 1)[0]
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    if "%" in s:
        s = s.split("%", 1)[0]
    return s


def _get(path: str) -> Optional[dict[str, Any]]:
    sock = socket_path()
    try:
        if not sock.exists():
            return None
    except OSError:
        return None
    conn: Optional[_UnixHTTP] = None
    try:
        conn = _UnixHTTP(str(sock), timeout=_TIMEOUT_S)
        conn.request("GET", path, headers={"Host": _HOST})
        resp = conn.getresponse()
        body = resp.read()
        if resp.status != 200:
            return None
        data = json.loads(body.decode("utf-8") or "null")
    except (OSError, ValueError, http.client.HTTPException, json.JSONDecodeError):
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except OSError:
                # Best-effort cleanup: ignore close errors to preserve deny/absent semantics.
                pass
    return data if isinstance(data, dict) else None


def _parse_status(raw: dict[str, Any]) -> TailnetSelf:
    backend = str(raw.get("BackendState") or "")
    up = backend == "Running"
    self_raw = raw.get("Self") if isinstance(raw.get("Self"), dict) else {}
    addrs = _ip_list(self_raw.get("TailscaleIPs"))
    ipv4 = next((a for a in addrs if ":" not in a), "")
    dns = str(self_raw.get("DNSName") or self_raw.get("Name") or "").rstrip(".")
    node = str(self_raw.get("HostName") or "").strip() or (
        dns.split(".", 1)[0] if dns else ""
    )
    peers: list[TailnetPeer] = []
    peer_map = raw.get("Peer") if isinstance(raw.get("Peer"), dict) else {}
    for item in peer_map.values():
        if not isinstance(item, dict):
            continue
        p = _peer_from_status(item)
        if p is not None:
            peers.append(p)
    return TailnetSelf(
        up=up,
        ipv4=ipv4,
        addrs=tuple(addrs),
        dns_name=dns,
        node_name=node,
        peers=tuple(peers),
    )


def _peer_from_status(raw: dict[str, Any]) -> Optional[TailnetPeer]:
    addrs = _ip_list(raw.get("TailscaleIPs") or raw.get("Addresses"))
    dns = str(raw.get("DNSName") or raw.get("Name") or "").rstrip(".")
    host = str(raw.get("HostName") or "").strip()
    if not host and dns:
        host = dns.split(".", 1)[0]
    if not host and not dns and not addrs:
        return None
    tags = raw.get("Tags") if isinstance(raw.get("Tags"), list) else []
    return TailnetPeer(
        node_name=host or (dns.split(".", 1)[0] if dns else ""),
        dns_name=dns,
        stable_id=str(raw.get("StableID") or raw.get("ID") or ""),
        addrs=tuple(addrs),
        user=str(raw.get("User") or ""),
        tags=tuple(str(t) for t in tags),
        online=bool(raw.get("Online", True)),
    )


def _parse_whois(raw: dict[str, Any]) -> Optional[TailnetPeer]:
    node = raw.get("Node") if isinstance(raw.get("Node"), dict) else {}
    if not node:
        return None
    info = node.get("Hostinfo") if isinstance(node.get("Hostinfo"), dict) else {}
    host = str(info.get("Hostname") or node.get("HostName") or "").strip()
    dns = str(node.get("Name") or node.get("DNSName") or "").rstrip(".")
    if not host and dns:
        host = dns.split(".", 1)[0]
    addrs = _ip_list(node.get("Addresses") or node.get("TailscaleIPs"))
    user_raw = raw.get("UserProfile") if isinstance(raw.get("UserProfile"), dict) else {}
    user = str(user_raw.get("LoginName") or user_raw.get("DisplayName") or "")
    tags = node.get("Tags") if isinstance(node.get("Tags"), list) else []
    return TailnetPeer(
        node_name=host,
        dns_name=dns,
        stable_id=str(node.get("StableID") or node.get("ID") or ""),
        addrs=tuple(addrs),
        user=user,
        tags=tuple(str(t) for t in tags),
        online=True,
    )


def _ip_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        ip = _bare_ip(str(item or ""))
        if ip:
            out.append(ip)
    return out


class _UnixHTTP(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float = _TIMEOUT_S) -> None:
        super().__init__(_HOST, timeout=timeout)
        self._unix = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._unix)
        self.sock = sock


def main() -> int:
    """Live smoke: print self + first peer. Offline → 'tailscale: unavailable'."""
    st = self_status()
    if st is None:
        print("tailscale: unavailable")
        return 1
    state = "up" if st.up else "down"
    print(f"self {st.node_name or '-'} {st.dns_name or '-'} {st.ipv4 or '-'} ({state})")
    for p in st.peers:
        flag = "online" if p.online else "offline"
        print(f"peer {p.node_name} {p.dns_name} {' '.join(p.addrs)} ({flag})")
    return 0 if st.up else 2


if __name__ == "__main__":
    raise SystemExit(main())
