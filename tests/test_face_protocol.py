"""Protocol v2 pins — the face-wire event shapes (tauri-face V1a).

Offline: serialization names/fields, the version bump, and the handshake line.
The face SERVER consuming these lands separately (serve_face); these pins keep
the wire contract stable underneath it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from xlii.turn_events import (
    CommandCatalog,
    CommandEntry,
    ConfirmRequest,
    FileOut,
    ModeState,
    PluginActionEntry,
    PluginCatalog,
    PluginEntry,
    PluginParamEntry,
    Prefill,
)
from xlii.ws_protocol import (
    PROTOCOL_VERSION,
    event_type_name,
    handshake_error,
    handshake_payload,
    hello_message,
    serialize_event,
)


def test_protocol_version_is_2():
    assert PROTOCOL_VERSION == "2"
    assert hello_message(session_id="s")["protocol"] == "2"


def test_handshake_payload_carries_protocol():
    p = handshake_payload(1234, "tok")
    assert p["port"] == 1234
    assert p["token"] == "tok"
    assert p["protocol"] == "2"
    assert "version" in p


def test_handshake_error_is_json_without_port():
    e = handshake_error("vault sealed")
    assert e["error"] == "vault sealed"
    assert "port" not in e
    assert e["protocol"] == "2"


def test_confirm_request_serialization():
    ev = ConfirmRequest(id="c1", prompt="run: rm -rf build?")
    d = serialize_event(ev)
    assert d == {
        "type": "confirm_request",
        "id": "c1",
        "prompt": "run: rm -rf build?",
        "danger": "",
    }


def test_mode_state_serialization():
    ev = ModeState(
        mode="code",
        color="green",
        exit_hint="/off to exit",
        placeholder="code",
        hint="bare input runs in the shell",
        ask_primary=False,
        posture="code",
    )
    d = serialize_event(ev)
    assert d["type"] == "mode_state"
    assert d["ask_primary"] is False
    assert d["posture"] == "code"
    assert d["exit_hint"] == "/off to exit"
    assert d["overlay"] == ""


def test_file_out_serialization_b64_and_path_forms():
    img = serialize_event(FileOut(name="a.png", kind="image", b64="AAAA"))
    assert img == {"type": "file_out", "name": "a.png", "kind": "image",
                   "b64": "AAAA", "path": "", "address": ""}
    doc = serialize_event(FileOut(name="r.pdf", kind="pdf", path="/tmp/r.pdf"))
    assert doc["b64"] == ""
    assert doc["path"] == "/tmp/r.pdf"


def test_prefill_serialization():
    assert serialize_event(Prefill(text="/tasks run build ")) == {
        "type": "prefill",
        "text": "/tasks run build ",
    }


def test_command_catalog_serialization():
    d = serialize_event(CommandCatalog(commands=[
        CommandEntry(name="plan", description="Plan mode"),
        CommandEntry(name="jobs", description=""),
    ]))
    assert d["type"] == "command_catalog"
    assert d["commands"] == [
        {"name": "plan", "description": "Plan mode"},
        {"name": "jobs", "description": ""},
    ]


def test_plugin_catalog_serialization():
    d = serialize_event(PluginCatalog(plugins=[
        PluginEntry(
            id="coingecko", name="CoinGecko", description="prices",
            effect="read-only", trust="subscription", subscribed=True, ready=True,
            actions=[PluginActionEntry(
                id="price", description="lookup",
                params=[PluginParamEntry(name="ids", required=True, description="slugs")],
            )],
        ),
    ]))
    assert d["type"] == "plugin_catalog"
    assert d["plugins"][0]["id"] == "coingecko"
    assert d["plugins"][0]["actions"][0]["id"] == "price"
    assert d["plugins"][0]["actions"][0]["params"][0]["name"] == "ids"


def test_event_type_names_registered():
    assert event_type_name(ConfirmRequest(id="x", prompt="p")) == "confirm_request"
    assert event_type_name(
        ModeState(mode="", color="", exit_hint="", placeholder="", hint="",
                  ask_primary=True, posture="chat")
    ) == "mode_state"
    assert event_type_name(FileOut(name="n", kind="image")) == "file_out"
    assert event_type_name(Prefill(text="t")) == "prefill"
    assert event_type_name(CommandCatalog()) == "command_catalog"
    assert event_type_name(PluginCatalog()) == "plugin_catalog"


def test_serialize_event_rejects_non_json_payload_value():
    @dataclass
    class _BadEvent:
        payload: object

    with pytest.raises(TypeError, match="non-JSON-serializable value"):
        serialize_event(_BadEvent(payload=object()))
