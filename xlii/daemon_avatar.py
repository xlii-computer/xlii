"""XMPP roster avatar — the 42-brain mark as the daemon's photo.

Phone clients (Conversations and friends) show the roster picture from
XEP-0153 (vCard PHOTO). Modern PEP avatars (XEP-0084) are published too
when that plugin is registered.

Prep is slixmpp-free so the contract is testable without the ``[daemon]``
extra. ``xlii.daemon`` publishes on session start.
"""

from __future__ import annotations

import hashlib
import logging
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# Same tree as the Face watermark. Twitter can use the square avatar.
MARK_REL = Path("face_assets/img/mark.png")
AVATAR_REL = Path("face_assets/img/avatar.png")
AVATAR_MIME = "image/png"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def package_root() -> Path:
    return Path(__file__).resolve().parent


def mark_path() -> Path:
    """Original transparent mark (Face watermark / CSS mask)."""
    return package_root() / MARK_REL


def avatar_path() -> Path:
    """Square opaque icon (XMPP vCard + profile picture)."""
    return package_root() / AVATAR_REL


@dataclass(frozen=True)
class Avatar:
    data: bytes
    mime: str
    sha1: str
    path: Path
    width: int
    height: int


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != _PNG_MAGIC:
        raise ValueError("avatar is not a PNG")
    _length, ctype = struct.unpack(">I4s", data[8:16])
    if ctype != b"IHDR":
        raise ValueError("avatar PNG has no IHDR")
    return struct.unpack(">II", data[16:24])


def load_avatar(*, path: Optional[Path] = None) -> Avatar:
    dest = path or avatar_path()
    data = dest.read_bytes()
    width, height = png_size(data)
    return Avatar(
        data=data,
        mime=AVATAR_MIME,
        sha1=hashlib.sha1(data).hexdigest(),
        path=dest,
        width=width,
        height=height,
    )


def _plugin(xmpp: Any, name: str) -> Any:
    try:
        return xmpp[name]
    except Exception:
        return None


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _photo_binval(result: Any) -> Optional[bytes]:
    try:
        data = result["vcard_temp"]["PHOTO"]["BINVAL"]
    except Exception:
        return None
    if not data:
        return None
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        return data.encode("latin-1")
    return None


async def _vcard_photo_sha1(xmpp: Any) -> Optional[str]:
    plug = _plugin(xmpp, "xep_0054")
    if plug is None:
        return None
    try:
        result = await _maybe_await(plug.get_vcard(cached=False))
    except Exception:
        return None
    data = _photo_binval(result)
    if not data:
        return None
    return hashlib.sha1(data).hexdigest()


async def _seed_presence_hash(xmpp: Any, sha1: str) -> None:
    """So the first presence after this can carry the XEP-0153 photo hash."""
    plug = _plugin(xmpp, "xep_0153")
    if plug is None:
        return
    api = getattr(plug, "api", None)
    if api is None:
        return
    jid = getattr(xmpp, "boundjid", None)
    if jid is None:
        return
    try:
        await _maybe_await(api["set_hash"](jid, args=sha1))
    except Exception:
        return


async def publish_daemon_avatar(xmpp: Any) -> str:
    """Publish the packaged avatar if the vCard photo is missing or stale.

    Returns ``set``, ``unchanged``, or ``skipped``. Never raises for a
    missing plugin / missing file / failed IQ — the daemon stays up.
    """
    try:
        avatar = load_avatar()
    except (OSError, ValueError) as exc:
        log.warning("xmpp avatar not loadable: %s", exc)
        return "skipped"

    await _seed_presence_hash(xmpp, avatar.sha1)

    vcard = _plugin(xmpp, "xep_0153")
    pep = _plugin(xmpp, "xep_0084")
    if vcard is None and pep is None:
        return "skipped"

    current = await _vcard_photo_sha1(xmpp)
    if current == avatar.sha1:
        return "unchanged"

    did = False
    if vcard is not None:
        try:
            await _maybe_await(
                vcard.set_avatar(avatar=avatar.data, mtype=avatar.mime)
            )
            did = True
        except Exception as exc:
            log.warning("xmpp vCard avatar failed: %s: %s", type(exc).__name__, exc)

    if pep is not None:
        try:
            await _maybe_await(pep.publish_avatar(avatar.data))
            await _maybe_await(
                pep.publish_avatar_metadata(
                    {
                        "id": avatar.sha1,
                        "type": avatar.mime,
                        # slixmpp writes these as XML text; ints crash tostring()
                        "bytes": str(len(avatar.data)),
                        "height": str(avatar.height),
                        "width": str(avatar.width),
                    }
                )
            )
            did = True
        except Exception as exc:
            log.warning("xmpp PEP avatar failed: %s: %s", type(exc).__name__, exc)

    return "set" if did else "skipped"
