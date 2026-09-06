"""RFC 6238 TOTP — dependency-free (stdlib only).

Used by the daemon's hidden elevation gate (``/xsu``): destructive commands over
the fabric require a fresh time-based one-time code from a *separate*
authenticator, so a compromised chat device alone cannot destroy anything.

Pure and time-injected (``now`` is always passed) so verification is
deterministically unit-testable. SHA-1 / 6 digits / 30 s period — the defaults
every authenticator app (Google Authenticator, Aegis, 1Password, …) speaks.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

DEFAULT_PERIOD = 30
DEFAULT_DIGITS = 6
# ±1 period of clock drift tolerance (the RFC's recommended verification window).
DEFAULT_WINDOW = 1


def generate_secret(length_bytes: int = 20) -> str:
    """A fresh base32 TOTP secret (default 160-bit, the RFC-recommended SHA-1 size).

    Returned unpadded (``=`` stripped) — the form authenticator apps expect in an
    ``otpauth://`` URI.
    """
    return base64.b32encode(secrets.token_bytes(length_bytes)).decode("ascii").rstrip("=")


def _decode_secret(secret_b32: str) -> bytes:
    s = (secret_b32 or "").strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def _hotp(key: bytes, counter: int, digits: int) -> str:
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    truncated = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10 ** digits)).zfill(digits)


def verify(
    secret_b32: str,
    code: str,
    now: float,
    *,
    period: int = DEFAULT_PERIOD,
    digits: int = DEFAULT_DIGITS,
    window: int = DEFAULT_WINDOW,
) -> bool:
    """True iff ``code`` is a valid TOTP for ``secret_b32`` at ``now`` (±``window``).

    Constant-time digit comparison. A malformed secret or a wrong-shaped code
    returns False, never raises — the gate must fail closed on garbage."""
    code = (code or "").strip()
    if len(code) != digits or not code.isdigit():
        return False
    try:
        key = _decode_secret(secret_b32)
    except (ValueError, TypeError, base64.binascii.Error):
        return False
    if not key:
        return False
    counter = int(now // period)
    for drift in range(-window, window + 1):
        c = counter + drift
        if c < 0:                       # before the epoch — no valid code there
            continue
        if hmac.compare_digest(_hotp(key, c, digits), code):
            return True
    return False


def provisioning_uri(secret_b32: str, *, label: str, issuer: str) -> str:
    """The ``otpauth://`` URI an authenticator app scans/imports for this secret."""
    return (
        f"otpauth://totp/{quote(issuer)}:{quote(label)}"
        f"?secret={secret_b32}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={DEFAULT_DIGITS}&period={DEFAULT_PERIOD}"
    )
