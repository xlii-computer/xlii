"""Wire helpers, constants, and the confirm/file/renderer types.

Extracted from the former ``xlii/serve_face.py`` god-file. Method and helper
bodies are verbatim; ``default_assets_dir`` walks one extra parent because
this module now lives in ``xlii/serve_face/``.
"""
from __future__ import annotations

import re
import secrets
import sys as _sys
import threading
from pathlib import Path
from typing import Any, Callable

from xlii.turn_events import ConfirmRequest
from xlii.ws_protocol import serialize_event

# Package-level names (re-exported by ``xlii.serve_face``). Tests monkeypatch
# CONFIRM_TIMEOUT_S / MAX_UPLOAD_BYTES on the package; live lookup sees that.
_sf = _sys.modules["xlii.serve_face"]

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# Decoded caps for files riding the wire, both directions — comfortably inside
# the 8 MiB frame ceiling after base64's 4/3 growth.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_INLINE_IMAGE_BYTES = 5 * 1024 * 1024
CONFIRM_TIMEOUT_S = 120.0
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
# Surface-bound commands that would grab a screen/tty this process doesn't
# have. Everything else goes straight through the real dispatcher.
_UNAVAILABLE_SLASH = {"/tui", "/terminal"}


def default_assets_dir() -> Path:
    """The bundled face frontend (V2 ships it as xlii package data)."""
    return Path(__file__).resolve().parent.parent / "face_assets"


def _is_scratch_desk_root(root: Path) -> bool:
    """True when *root* lives under ``~/.xlii/scratch/`` (home/named desks)."""
    try:
        from xlii.project_paths import scratch_store_root

        desk = scratch_store_root()
        return root.expanduser().resolve().is_relative_to(desk)
    except (OSError, ValueError, AttributeError):
        return False


_RICH_TAG = re.compile(r"\[/?[^\]]+\]")


def classify_user_kind(text: str, *, posture: str = "code", overlay: str = "") -> str:
    """``slash`` / ``shell`` / ``talk`` — what the face paints on the you-line."""
    t = (text or "").lstrip()
    if t.startswith("/"):
        return "slash"
    if t.startswith("!") or t.startswith("?>"):
        return "shell"
    try:
        from xlii.desk import is_desk_nav

        if is_desk_nav(t):
            return "shell"
    except Exception:
        # xlii.desk is optional here — fall through to the posture checks below.
        pass
    if posture == "chat" or (overlay or "").strip():
        return "talk"
    return "shell"


def infer_meta_level(text: str) -> str:
    """Pick a meta color from command output (Rich tags already stripped)."""
    plain = _RICH_TAG.sub("", text or "")
    low = plain.strip().lower()
    if not low:
        return "info"
    if low.startswith("✓") or (plain.lstrip().startswith("✓")):
        return "success"
    if any(p in low for p in ("wasn't on", "not in ", "(not in ")):
        return "info"
    if "mode on" in low or low.startswith("howto mode") or low.startswith("ops mode"):
        return "mode"
    if "mode off" in low or low.startswith("left ") or "back to the base" in low:
        return "success"
    return "info"


class _WireFile:
    """File-like sink for the session console: each flushed print becomes one
    ``meta_message`` on the wire. One print = one block (not one gray line
    per newline), so a howto banner or /shum summary stays a unit."""

    def __init__(self, send: Callable[[dict[str, Any]], None]) -> None:
        self._send = send
        self._buf = ""

    def write(self, text: str) -> int:
        self._buf += text
        return len(text)

    def flush(self) -> None:
        if self._buf.strip():
            text = self._buf.rstrip()
            self._send({"type": "meta_message", "text": text,
                        "level": infer_meta_level(text)})
        self._buf = ""


class WireRenderer:
    """The face's renderer: serialize every typed event straight to the wire.

    REPLACES the console renderer (never wraps it — ``RendererTap`` forwards to
    the inner renderer, which would render Rich output into the wire console
    and double every event as meta text). ``console`` mirrors the agent's so
    the ``_renderer()`` freshness guard (agent.py) keeps this installed."""

    def __init__(self, agent: Any, send: Callable[[dict[str, Any]], None]) -> None:
        self._agent = agent
        self._send = send

    @property
    def console(self):
        return self._agent.console

    def emit(self, event: Any) -> None:
        try:
            self._send(serialize_event(event))
        except TypeError:
            # An event kind the wire doesn't know (a future dataclass) — drop
            # rather than kill the turn; the doc rule adds serializers first.
            pass

    def meta(self, text: str, level: str = "info") -> None:
        self._send({"type": "meta_message", "text": text, "level": level})

    def error(self, text: str) -> None:
        self._send({"type": "meta_message", "text": text, "level": "error"})


class _ConfirmBridge:
    """The wire approve/deny channel for gated tool intents.

    A turn-thread caller blocks in ``ask`` (emitting ``confirm_request``);
    the reader thread answers via ``resolve``. Timeout and disconnect both
    deny — a face that went away must never leave a gate hanging open."""

    def __init__(self, send: Callable[[dict[str, Any]], None]) -> None:
        self._send = send
        self._lock = threading.Lock()
        self._pending: dict[str, tuple[threading.Event, list[bool]]] = {}

    def ask(self, prompt: str) -> str:
        cid = secrets.token_hex(8)
        ev = threading.Event()
        box = [False]
        with self._lock:
            self._pending[cid] = (ev, box)
        self._send(serialize_event(ConfirmRequest(id=cid, prompt=prompt)))
        ev.wait(timeout=_sf.CONFIRM_TIMEOUT_S)
        with self._lock:
            self._pending.pop(cid, None)
        return "y" if box[0] else ""

    def resolve(self, cid: str, approve: bool) -> None:
        with self._lock:
            entry = self._pending.get(cid)
        if entry is None:
            return  # unknown/expired id — ignore
        ev, box = entry
        box[0] = bool(approve)
        ev.set()

    def deny_all(self) -> None:
        with self._lock:
            entries = list(self._pending.values())
            self._pending.clear()
        for ev, box in entries:
            box[0] = False
            ev.set()
