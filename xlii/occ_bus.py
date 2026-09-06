"""House occupancy on the wire — one me, every other glass locks.

Farm MUC already joins every daemon/Face. Occupancy is a *different* envelope
(``xlii.occ``) so job ads never see it. The limb that answered publishes a
claim; siblings lock. The spoken-to node does not refuse Mojo.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from xlii.farm import utc_now

OCC_KIND = "xlii.occ"
OP_CLAIM = "claim"
OP_RELEASE = "release"

_sender: Optional[Callable[[str], None]] = None
_listeners: list[Callable[[], None]] = []


def bind_sender(fn: Optional[Callable[[str], None]]) -> None:
    global _sender
    _sender = fn


def on_change(fn: Callable[[], None]) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


def notify() -> None:
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            continue


def encode_claim(node: str, *, who: str = "me") -> str:
    return json.dumps({
        "xlii": OCC_KIND, "op": OP_CLAIM,
        "node": (node or "").strip(), "who": who, "at": utc_now(),
    })


def encode_release(node: str) -> str:
    return json.dumps({
        "xlii": OCC_KIND, "op": OP_RELEASE,
        "node": (node or "").strip(), "at": utc_now(),
    })


def parse_occ(text: str) -> Optional[tuple[str, dict[str, Any]]]:
    raw = (text or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("xlii") != OCC_KIND:
        return None
    op = str(data.get("op") or "").strip()
    if op not in (OP_CLAIM, OP_RELEASE):
        return None
    return op, data


def this_node(fallback: str = "") -> str:
    n = (fallback or "").strip()
    if n:
        return n
    try:
        from xlii.config import GlobalConfig
        from xlii.farm import job_node_name

        return job_node_name(GlobalConfig.load())
    except Exception:
        return ""


def publish_claim(node: str) -> None:
    n = (node or "").strip()
    if not n or _sender is None:
        return
    try:
        _sender(encode_claim(n))
    except Exception:
        # Best-effort notify layer: occupancy is already persisted, so peers converge on their next poll.
        pass


def publish_release(node: str) -> None:
    n = (node or "").strip()
    if not n or _sender is None:
        return
    try:
        _sender(encode_release(n))
    except Exception:
        # As with publish_claim -- the release is already persisted; the bus only speeds up notice.
        pass
