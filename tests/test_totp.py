"""RFC 6238 TOTP (stdlib) — the daemon's elevation second factor.

Offline + deterministic (time injected). Pins an RFC test vector so the
implementation is provably interoperable with real authenticator apps.
"""

from __future__ import annotations

from xlii import totp

# RFC 6238 Appendix B, SHA-1 seed "12345678901234567890" → base32:
_RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_rfc6238_vector_sha1():
    # T=59 → counter 1 → 8-digit 94287082; the default 6-digit truncation is its
    # low 6 digits: 287082.
    assert totp.verify(_RFC_SECRET, "287082", now=59)
    # T=1111111109 (RFC table) → 8-digit 07081804 → 6-digit 081804.
    assert totp.verify(_RFC_SECRET, "081804", now=1111111109)


def test_roundtrip_generated_secret():
    secret = totp.generate_secret()
    code = totp._hotp(totp._decode_secret(secret), int(1000 // 30), 6)
    assert totp.verify(secret, code, now=1000)


def test_window_tolerance_and_expiry():
    secret = totp.generate_secret()
    now = 10_000.0
    code = totp._hotp(totp._decode_secret(secret), int(now // 30), 6)
    assert totp.verify(secret, code, now=now)
    # ±1 period drift accepted…
    assert totp.verify(secret, code, now=now + 30)
    assert totp.verify(secret, code, now=now - 30)
    # …but not 2 periods away.
    assert not totp.verify(secret, code, now=now + 90)


def test_bad_and_malformed_fail_closed():
    secret = totp.generate_secret()
    assert not totp.verify(secret, "000000", now=59)      # wrong code
    assert not totp.verify(secret, "12345", now=59)       # wrong length
    assert not totp.verify(secret, "abcdef", now=59)      # non-digit
    assert not totp.verify(secret, "", now=59)            # empty
    assert not totp.verify("not!base32!", "287082", now=59)  # bad secret
    assert not totp.verify("", "287082", now=59)          # no secret


def test_provisioning_uri_shape():
    uri = totp.provisioning_uri("ABC234", label="daemon", issuer="xlii")
    assert uri.startswith("otpauth://totp/xlii:daemon?")
    assert "secret=ABC234" in uri and "issuer=xlii" in uri
    assert "period=30" in uri and "digits=6" in uri and "algorithm=SHA1" in uri
