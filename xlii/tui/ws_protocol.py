"""Compatibility shim — the W2 event-protocol serialization moved kernel-side
to :mod:`xlii.ws_protocol` (godzilla-mothra B5). The names below are the same
objects; new code imports from ``xlii.ws_protocol``.
"""

from __future__ import annotations

from xlii.ws_protocol import (  # noqa: F401
    PROTOCOL_VERSION,
    event_type_name,
    handshake_payload,
    hello_message,
    serialize_event,
)
