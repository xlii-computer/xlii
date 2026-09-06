"""Inbound media for the XMPP mouth (#3 media-in): turn a shared-file URL in a
message body into a local file path the agent turn can SEE.

Chat clients (Conversations, Monal, …) send an encrypted file as an
``aesgcm://host/path#<hex>`` URL in the message body (XEP-0454, OMEMO Media
Sharing): the bytes at the ``https://`` equivalent are AES-256-GCM ciphertext
(the 16-byte GCM tag appended), and the URL fragment is ``hex(iv) || hex(key)``
— IV first per the XEP. Plain ``https://`` media links are fetched as-is.

This module is pure and injectable — the fetcher is a parameter — so the parsing
and the AES-GCM decrypt are unit-tested without a live file-share. The daemon
calls :func:`prepare` to get ``(caption, [paths])`` and passes the paths on as
``xlii ask --attach`` (→ ``multimodal.prepare_user_turn`` → a vision/PDF turn).
``slixmpp_omemo`` does message crypto only, so the file AES-GCM is done here with
the ``cryptography`` dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

# 25 MB — matches the box Prosody's http_file_share cap (fabric.md); a hard
# ceiling so a hostile/huge link can't exhaust the daemon's memory or disk.
MAX_MEDIA_BYTES = 25 * 1024 * 1024
# Per-message caps so one stanza can't fan out into N × 25 MB or N × 30 s fetches.
MAX_MEDIA_ATTACHMENTS = 5

# What the agent turn is asked when a message is *only* an attachment (no caption).
DEFAULT_MEDIA_PROMPT = "The user sent this attachment. Look at it and respond."
DOWNLOAD_FAILED_PROMPT = (
    "The user sent an attachment but it could not be downloaded or decrypted."
)

# URL tokens in a message body: OMEMO media (aesgcm://) or a plain https:// link.
_URL_RE = re.compile(r"(?:aesgcm|https)://[^\s<>]+", re.IGNORECASE)

# Extensions the multimodal layer can actually show or read today (vision: png/jpg;
# documents: pdf + inline text). Audio/video and other image formats are deferred.
_MEDIA_EXT = {
    ".png", ".jpg", ".jpeg",
    ".pdf", ".txt", ".md", ".csv", ".json",
}


@dataclass
class MediaRef:
    """One media URL found in a message body, resolved to something fetchable."""

    url: str                 # the original token (aesgcm:// or https://)
    https_url: str           # the fetchable https:// URL (fragment stripped)
    key: Optional[bytes]     # AES-256 key — aesgcm only
    iv: Optional[bytes]      # GCM nonce — aesgcm only
    filename: str            # basename (keeps the extension → mime detection)

    @property
    def encrypted(self) -> bool:
        return self.key is not None


def _basename(https_url: str) -> str:
    name = Path(urlparse(https_url).path).name or "attachment"
    # Strip anything filesystem-hostile; keep the extension for mime sniffing.
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "attachment"


def _parse_url(token: str) -> Optional[MediaRef]:
    token = token.rstrip(">).,\"'")   # trailing prose punctuation
    p = urlparse(token)
    scheme = p.scheme.lower()
    if scheme == "aesgcm":
        frag = p.fragment.strip().lower()
        # fragment = hex(iv) || hex(key), IV first (XEP-0454). 12-byte IV (GCM
        # standard) → 88 hex; some clients use a 16-byte IV → 96 hex.
        if len(frag) == 88 and _is_hex(frag):
            iv, key = bytes.fromhex(frag[:24]), bytes.fromhex(frag[24:])
        elif len(frag) == 96 and _is_hex(frag):
            iv, key = bytes.fromhex(frag[:32]), bytes.fromhex(frag[32:])
        else:
            return None
        https = "https://" + token.split("://", 1)[1].split("#", 1)[0]
        return MediaRef(url=token, https_url=https, key=key, iv=iv, filename=_basename(https))
    if scheme == "https":
        https = token.split("#", 1)[0]
        name = _basename(https)
        if Path(name).suffix.lower() not in _MEDIA_EXT:
            return None   # a bare link that isn't obviously a file — leave it as text
        return MediaRef(url=token, https_url=https, key=None, iv=None, filename=name)
    return None


def _is_hex(s: str) -> bool:
    try:
        bytes.fromhex(s)
        return True
    except ValueError:
        return False


def extract_media_refs(body: str) -> list[MediaRef]:
    """Every fetchable media URL in a message body (in order, de-duplicated)."""
    seen: set[str] = set()
    refs: list[MediaRef] = []
    for m in _URL_RE.finditer(body or ""):
        ref = _parse_url(m.group(0))
        if ref is not None and ref.url not in seen:
            seen.add(ref.url)
            refs.append(ref)
    return refs


# XEP-0066 out-of-band data: <x xmlns='jabber:x:oob'><url>…</url></x>. Chat
# clients (Conversations) attach an image/file by putting the aesgcm:// link
# HERE — usually with an EMPTY message body — not in the text. Both element tags
# inherit the namespace under ElementTree, so the url tag is namespace-qualified.
_OOB_URL_TAG = "{jabber:x:oob}url"


def oob_urls(stanza: Any) -> list[str]:
    """Media URLs carried in a stanza's OOB ``<url>`` element(s).

    ``stanza`` is a slixmpp stanza (or anything with a ``.xml`` ElementTree root).
    Best-effort + defensive: any parse problem yields no URLs rather than raising
    (a malformed attachment must never sink the message)."""
    xml = getattr(stanza, "xml", None)
    if xml is None:
        return []
    out: list[str] = []
    try:
        for el in xml.iter(_OOB_URL_TAG):
            text = (el.text or "").strip()
            if text:
                out.append(text)
    except Exception:
        return out
    return out


def strip_media_urls(body: str) -> str:
    """The caption left after removing fetchable media URLs — what the user typed."""
    result = body or ""
    for ref in extract_media_refs(body):
        result = result.replace(ref.url, "", 1)
    return result.strip()


def redact_for_audit(body: str) -> str:
    """Strip aesgcm key material from a message body before audit logging."""
    refs = extract_media_refs(body)
    if not refs:
        return body or ""
    result = body or ""
    for ref in refs:
        if ref.encrypted and "#" in ref.url:
            base = ref.url.split("#", 1)[0]
            result = result.replace(ref.url, f"{base}#<redacted>", 1)
    return result


def decrypt_aesgcm(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-256-GCM decrypt an OMEMO-shared file. The 16-byte tag is appended to
    the ciphertext (as XEP-0454 uploads it), which is exactly what AESGCM expects."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key).decrypt(iv, ciphertext, None)


def _http_get(url: str, max_bytes: int) -> bytes:
    """Fetch bytes over https with a hard size cap (the default fetcher)."""
    from urllib.request import Request

    from xlii.url_safe import safe_urlopen

    req = Request(url, headers={"User-Agent": "xlii-daemon"})
    with safe_urlopen(req, timeout=30) as resp:  # noqa: S310 — https only (scheme-gated in _parse_url)
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"media exceeds {max_bytes} bytes")
    return data


def _unique_dest(dest_dir: Path, filename: str) -> Path:
    dest = dest_dir / filename
    if not dest.exists():
        return dest
    stem, suffix = Path(filename).stem, Path(filename).suffix
    n = 2
    while True:
        candidate = dest_dir / f"{stem}-{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def materialize(
    ref: MediaRef,
    dest_dir: Path,
    *,
    fetch: Optional[Callable[[str, int], bytes]] = None,
    max_bytes: int = MAX_MEDIA_BYTES,
) -> Path:
    """Fetch ``ref`` (decrypting an aesgcm:// link) and write it into ``dest_dir``.
    Returns the local path; the extension is preserved so the turn's multimodal
    layer can sniff the mime. ``fetch`` is injectable for tests."""
    get = fetch or _http_get
    data = get(ref.https_url, max_bytes)
    if ref.encrypted:
        data = decrypt_aesgcm(data, ref.key, ref.iv)  # type: ignore[arg-type]
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(dest_dir, ref.filename)
    dest.write_bytes(data)
    return dest


def persist_inbound(
    paths: "list[Path]",
    *,
    caption: str,
    sender: str,
    ts: float,
    source_redacted: str,
    media_dir: Path,
) -> "list[Path]":
    """Move materialized inbound files into a persona's durable media store —
    the media inbox. The old lifecycle unlinked an ear's input after one turn
    (iXaac saw your screenshot once, then it was gone); persisting here is what
    lets the fabric carry it to the throne and a coding session /media-attach
    it later.

    Each file lands as ``<utc-stamp>-<name>`` beside a ``<name>.meta.json``
    sidecar carrying what capture-time alone knows: the caption, the sender,
    the receipt time, and the redacted source URL (never key material — the
    caller passes it through :func:`redact_for_audit`). Returns the persisted
    file paths (sidecars excluded). Best-effort per file: one bad move never
    loses the rest."""
    import json
    import shutil
    import time as _time

    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    stamp = _time.strftime("%Y%m%dT%H%M%SZ", _time.gmtime(ts))
    out: list[Path] = []
    for src in paths:
        src = Path(src)
        try:
            dest = _unique_dest(media_dir, f"{stamp}-{src.name}")
            shutil.move(str(src), str(dest))
        except Exception:  # noqa: BLE001 — per-file best effort
            continue
        out.append(dest)
        meta = {
            "filename": src.name,
            "caption": caption,
            "sender": sender,
            "ts": ts,
            "source_redacted": source_redacted,
        }
        try:
            dest.with_name(dest.name + ".meta.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 — per-file best effort
            continue
    return out


def prepare(
    body: str,
    dest_dir: Path,
    *,
    fetch: Optional[Callable[[str, int], bytes]] = None,
) -> tuple[str, list[Path]]:
    """Resolve a message body with attachments into ``(caption, [paths])`` for an
    agent turn. No media URLs ⇒ ``(body, [])`` (byte-identical to the text path).
    A media URL that fails to fetch is skipped (best-effort); when the body is
    *only* attachments the caption falls back to a default 'look at this' prompt."""
    refs = extract_media_refs(body)
    if not refs:
        return body, []
    refs = refs[:MAX_MEDIA_ATTACHMENTS]
    paths: list[Path] = []
    remaining = MAX_MEDIA_BYTES
    for ref in refs:
        if remaining <= 0:
            break
        try:
            path = materialize(ref, dest_dir, fetch=fetch, max_bytes=remaining)
            size = path.stat().st_size
            remaining -= size
            paths.append(path)
        except Exception:
            continue  # a broken/oversized link must not sink the whole turn
    caption = strip_media_urls(body)
    if paths:
        caption = caption or DEFAULT_MEDIA_PROMPT
    elif refs:
        caption = caption or DOWNLOAD_FAILED_PROMPT
    return caption, paths
