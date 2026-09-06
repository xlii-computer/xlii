"""Outbound media for the XMPP mouth: encrypt a file the way clients expect.

The inverse of ``xlii/media_in.py``: a reply file is AES-256-GCM encrypted with
a fresh key+IV (XEP-0454, OMEMO Media Sharing), uploaded to the server's file
share (XEP-0363 — the daemon does that part), and the message body carries
``aesgcm://host/path#<hex(iv) || hex(key)>`` — IV first, 12-byte GCM nonce, the
16-byte tag appended to the ciphertext. Conversations renders such a body as an
inline image/audio/file. This module is the pure crypto+URL half; no network.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def encrypt_for_share(data: bytes) -> tuple[bytes, bytes, bytes]:
    """Encrypt ``data`` for OMEMO media sharing.

    Returns ``(ciphertext, iv, key)`` — ciphertext has the 16-byte GCM tag
    appended (what AESGCM.encrypt produces and what receivers expect), the IV is
    the standard 12-byte GCM nonce, the key is a fresh 32-byte AES-256 key."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = os.urandom(32)
    iv = os.urandom(12)
    return AESGCM(key).encrypt(iv, data, None), iv, key


def aesgcm_url(https_url: str, iv: bytes, key: bytes) -> str:
    """The ``aesgcm://`` link for an uploaded encrypted blob: https scheme
    swapped, fragment = ``hex(iv) || hex(key)`` (IV FIRST — the exact layout
    ``media_in._parse_url`` and every OMEMO client parse)."""
    if not https_url.lower().startswith("https://"):
        raise ValueError(f"share URL must be https: {https_url!r}")
    return "aesgcm://" + https_url[len("https://"):].split("#", 1)[0] + f"#{iv.hex()}{key.hex()}"


# Mime by suffix for the upload's Content-Type; a receiver mostly keys off the
# filename, but a correct type helps clients pick the right renderer.
_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
    ".pdf": "application/pdf", ".txt": "text/plain", ".md": "text/plain",
    ".m4a": "audio/mp4", ".mp3": "audio/mpeg", ".oga": "audio/ogg",
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav",
    ".mp4": "video/mp4", ".webm": "video/webm",
}


def mime_for(path: Any) -> str:
    return _MIME.get(Path(str(path)).suffix.lower(), "application/octet-stream")


def prepare_upload(path: Any) -> tuple[str, str, bytes, bytes, bytes]:
    """Everything the daemon needs to ship one outbox file:
    ``(filename, mime, ciphertext, iv, key)``. Pure read+encrypt — the caller
    does the XEP-0363 upload and builds the link with :func:`aesgcm_url`."""
    p = Path(str(path))
    ciphertext, iv, key = encrypt_for_share(p.read_bytes())
    return p.name, mime_for(p), ciphertext, iv, key
