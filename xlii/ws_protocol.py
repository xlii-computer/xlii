"""JSON serialization for the W2 WebSocket event protocol.

Maps the typed events in ``xlii.turn_events`` to JSON objects — the durable seam
for browser workbench (W3), Tauri (W4), and mobile clients. Never invent a
parallel event model; new presentation kinds start as dataclasses in
turn_events.py.

Kernel home since godzilla-mothra B5 (it was the last kernel-shaped module
homed in the face package, ``xlii/tui/ws_protocol.py`` — a shim remains there).
Stdlib only; no rich/textual.

Spec: docs/ws-event-protocol.md
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from xlii import __version__
from xlii.turn_events import (
    AssistantAnswer,
    ChromeState,
    CommandCatalog,
    ConfirmRequest,
    FileOut,
    MetaMessage,
    ModeState,
    PaneDeck,
    PluginCatalog,
    Prefill,
    ShellRan,
    ToolFinished,
    ToolStarted,
    UserTurn,
)

PROTOCOL_VERSION = "2"


def _jsonify(value: Any) -> Any:
    """Recursively normalize event payload values into JSON-serializable forms.

    Supported recursive value kinds are:
    - JSON primitives: ``str``, ``int``, ``float``, ``bool``, ``None``
    - ``pathlib.Path`` (converted to ``str``)
    - ``dict`` (values recursively normalized)
    - ``list``/``tuple`` (elements recursively normalized)

    Any other value type is rejected to keep the event protocol payload
    explicitly JSON-safe at serialization time.
    """
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonify(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(
        f"event payload contains non-JSON-serializable value of type "
        f"{type(value).__name__}"
    )


# NOTE: This maps only top-level wire event dataclasses.
# Nested payload dataclasses in turn_events.py (for example pane/command/plugin
# rows) are intentionally not standalone event types; they serialize via their
# parent event payloads (PaneDeck/CommandCatalog/PluginCatalog).
_TYPE_NAMES: dict[type, str] = {
    ShellRan: "shell_ran",
    ToolStarted: "tool_started",
    ToolFinished: "tool_finished",
    MetaMessage: "meta_message",
    UserTurn: "user_turn",
    AssistantAnswer: "assistant_answer",
    ConfirmRequest: "confirm_request",
    ModeState: "mode_state",
    FileOut: "file_out",
    Prefill: "prefill",
    PaneDeck: "pane_deck",
    ChromeState: "chrome_state",
    CommandCatalog: "command_catalog",
    PluginCatalog: "plugin_catalog",
}


def event_type_name(event: object) -> str:
    """Snake-case wire name for a typed event instance."""
    try:
        return _TYPE_NAMES[type(event)]
    except KeyError as exc:
        raise TypeError(f"not a known event type: {type(event).__name__}") from exc


def serialize_event(event: object) -> dict[str, Any]:
    """Turn a typed event dataclass into a flat JSON-ready dict with ``type``."""
    if not is_dataclass(event):
        raise TypeError(f"not an event dataclass: {type(event).__name__}")
    payload = _jsonify(asdict(event))
    return {"type": event_type_name(event), **payload}


def hello_message(
    *,
    session_id: str,
    view_posture: str = "desk",
    tailnet_glass: bool = False,
    resumed: bool = False,
) -> dict[str, Any]:
    """First server message after a successful WebSocket upgrade."""
    vp = (view_posture or "desk").strip().lower()
    if vp not in ("desk", "phone"):
        vp = "desk"
    return {
        "type": "hello",
        "protocol": PROTOCOL_VERSION,
        "version": __version__,
        "session": session_id,
        "view_posture": vp,
        "tailnet_glass": bool(tailnet_glass),
        "resumed": bool(resumed),
    }


def handshake_payload(port: int, token: str) -> dict[str, Any]:
    """Stdout handshake line for ``xlii serve --ws/--face --handshake`` (Tauri).

    ``protocol`` is additive (v2): older hosts ignore unknown keys."""
    return {
        "port": port,
        "token": token,
        "version": __version__,
        "protocol": PROTOCOL_VERSION,
    }


def handshake_error(message: str) -> dict[str, Any]:
    """Stdout line when --handshake boot fails — Tauri surfaces this, not a hang."""
    return {
        "error": message,
        "version": __version__,
        "protocol": PROTOCOL_VERSION,
    }
