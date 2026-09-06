"""Shared bind-address policy for networked surfaces.

Wildcard binds (0.0.0.0 / ::) are always refused. Non-loopback binds require
an explicit ``--expose`` so a tailnet/LAN listen is a conscious choice, not
the default. ``--host tailnet`` resolves via the local tailscaled (never a
CGNAT guess) and still requires ``--expose``.
"""

from __future__ import annotations

LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
WILDCARD = frozenset({"0.0.0.0", "::", ""})


def resolve_bind_host(host: str) -> tuple[str, str | None]:
    """Resolve ``tailnet`` to this machine's Tailscale IPv4.

    Other hosts pass through. Refusal is a string; never fall back to a guess.
    """
    h = (host or "").strip()
    if h.lower() != "tailnet":
        return h, None
    from xlii.tailnet import self_status

    st = self_status()
    if st is None or not st.up:
        return h, "tailscale is down or logged out — cannot --host tailnet"
    if not st.ipv4:
        return h, "tailscaled has no tailnet IPv4"
    return st.ipv4, None


def bind_error(host: str, *, expose: bool, surface: str) -> str | None:
    """Return a refusal message when *host* is not an allowed bind, else None."""
    h = (host or "").strip()
    if h.lower() == "tailnet":
        h, err = resolve_bind_host(h)
        if err:
            return f"{surface}: {err}"
    if h in WILDCARD:
        return (
            f"{surface}: refusing wildcard bind {host!r} — bind 127.0.0.1 "
            "or a specific tailnet/LAN address with --expose"
        )
    if h not in LOOPBACK and not expose:
        return (
            f"{surface}: refusing non-loopback bind {host!r} — "
            "pass --expose if this is a private/tailnet address"
        )
    return None


def bind_advisory(host: str) -> str | None:
    """Warn when a non-loopback bind is not actually ours on the tailnet.

    Silent when tailscaled is absent (bring-your-own; no guess).
    """
    h = (host or "").strip()
    if not h or h in LOOPBACK or h in WILDCARD or h.lower() == "tailnet":
        return None
    from xlii.tailnet import is_tailnet_ip, self_status

    if self_status() is None:
        return None
    if is_tailnet_ip(h):
        return None
    return (
        f"bind {h!r} is not a tailnet address for this machine "
        "(hotel-wifi/LAN?) — prefer --host tailnet"
    )


def prepare_bind(host: str, *, expose: bool, surface: str) -> tuple[str, str | None]:
    """Resolve then refuse. Returns ``(resolved_host, error_or_none)``."""
    resolved, err = resolve_bind_host(host)
    if err:
        return host, f"{surface}: {err}"
    refused = bind_error(resolved, expose=expose, surface=surface)
    if refused:
        return resolved, refused
    return resolved, None

