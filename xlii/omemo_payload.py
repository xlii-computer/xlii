"""Small helpers for validating slixmpp-omemo encrypted stanzas.

The optional XMPP modules import this file from code paths that already depend
on slixmpp, but the helper itself stays dependency-free so the fail-closed
payload contract can be unit-tested without a live XMPP stack.
"""

from __future__ import annotations

from typing import Any

OMEMO_KEY_PATH = ".//{eu.siacs.conversations.axolotl}key"


def sealed_recipient_keys(encrypted: Any) -> list[tuple[str | None, str]]:
    """Return recipient key ids sealed into an encrypted OMEMO stanza.

    Raises ValueError when the stanza cannot be inspected. Callers use that as a
    hard failure because a keyless OMEMO fallback body is not deliverable.
    """
    xml = getattr(encrypted, "xml", None)
    if xml is None:
        raise ValueError("encrypted stanza has no XML payload")
    try:
        keys = xml.findall(OMEMO_KEY_PATH)
    except AttributeError as exc:
        raise ValueError("encrypted stanza XML cannot be searched") from exc
    return [
        (
            key.get("rid"),
            "prekey" if key.get("prekey") in ("1", "true") else "msg",
        )
        for key in keys
    ]
