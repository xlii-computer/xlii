"""SSRF-safe URL fetching — shared by the daemon media-in path and TUI @-pinning."""

from __future__ import annotations

from typing import Optional, Union
from urllib.parse import urlparse
from urllib.request import Request

UrlOrRequest = Union[str, Request]


def _blocked_ip(ip) -> bool:
    import ipaddress

    if not isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)):
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
    )


def validate_url_host(url: str) -> Optional[str]:
    """Return an error message when the URL host must not be fetched (SSRF guard)."""
    import ipaddress
    import socket

    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return "invalid url: missing host"
    if host.lower() in ("localhost", "localhost.localdomain"):
        return f"blocked host: {host}"
    try:
        ip = ipaddress.ip_address(host)
        if _blocked_ip(ip):
            return f"blocked host: {host}"
        return None
    except ValueError:
        # host is a DNS name rather than an IP literal, so it is resolved below and every address it returns is
        # checked against _blocked_ip. The guard is not bypassed here.
        pass
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        for res in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
            addr = res[4][0]
            if _blocked_ip(ipaddress.ip_address(addr)):
                return f"blocked host: {host} resolves to a private or loopback address"
    except OSError as e:
        return f"could not resolve host: {e}"
    return None


def _url_string(url_or_req: UrlOrRequest) -> str:
    return url_or_req if isinstance(url_or_req, str) else url_or_req.full_url


def safe_urlopen(url_or_req: UrlOrRequest, timeout: int = 15):
    """Fetch a URL with SSRF checks and redirect re-validation."""
    import urllib.error
    import urllib.request

    err = validate_url_host(_url_string(url_or_req))
    if err:
        raise urllib.error.URLError(err)

    class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            hop_err = validate_url_host(newurl)
            if hop_err:
                raise urllib.error.URLError(hop_err)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    opener = urllib.request.build_opener(_SafeRedirectHandler)
    return opener.open(url_or_req, timeout=timeout)
