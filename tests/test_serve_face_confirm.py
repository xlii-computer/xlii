"""The face confirm bridge (tauri-face V1b) — approve/deny/timeout/cancel pins.

The wire approve/deny channel is what separates the face from the headless
`--ws` body (which blanket auto-denies): a gated tool intent round-trips to
the client as a ``confirm_request`` and the answer reaches the blocked gate.
Distinct from tests/test_serve_ws_confirm.py, which pins the OLD auto-deny
swap lock.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import xlii.serve_face as serve_face_mod
from xlii.serve_face import _ConfirmBridge

from tests.test_serve_face import (  # noqa: F401 — shared offline helpers
    _cleanup_servers,
    _connect,
    _fake_state,
    _recv_event,
    _recv_until,
    _send,
    _start_server,
)


# ------------------------------------------------------------- bridge (unit)


def test_bridge_approve():
    sent: list[dict] = []
    bridge = _ConfirmBridge(sent.append)
    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(bridge.ask("run it?")))
    t.start()
    for _ in range(100):
        if sent:
            break
        time.sleep(0.01)
    req = sent[0]
    assert req["type"] == "confirm_request" and req["prompt"] == "run it?"
    bridge.resolve(req["id"], True)
    t.join(timeout=5)
    assert out == ["y"]


def test_bridge_deny():
    sent: list[dict] = []
    bridge = _ConfirmBridge(sent.append)
    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(bridge.ask("rm -rf?")))
    t.start()
    for _ in range(100):
        if sent:
            break
        time.sleep(0.01)
    bridge.resolve(sent[0]["id"], False)
    t.join(timeout=5)
    assert out == [""]


def test_bridge_timeout_denies(monkeypatch):
    monkeypatch.setattr(serve_face_mod, "CONFIRM_TIMEOUT_S", 0.05)
    bridge = _ConfirmBridge(lambda obj: None)
    assert bridge.ask("anyone there?") == ""


def test_bridge_unknown_id_ignored():
    bridge = _ConfirmBridge(lambda obj: None)
    bridge.resolve("nope", True)  # no pending entry — must not raise


def test_bridge_deny_all_unblocks():
    bridge = _ConfirmBridge(lambda obj: None)
    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(bridge.ask("still there?")))
    t.start()
    time.sleep(0.05)
    bridge.deny_all()
    t.join(timeout=5)
    assert out == [""]


# ------------------------------------------------------------ wire (E2E-ish)


def test_confirm_round_trips_mid_turn(tmp_path, monkeypatch):
    """A gated intent inside a [?] turn reaches the client and the approval
    reaches the gate — the reader answers while the worker is blocked."""
    import xlii.cmds.sessions.ask as ask_mod
    import xlii.cmds.sessions.resolve as resolve_mod
    import xlii.persona as persona_mod
    import xlii.repl_cmds.mojo as mojo_mod
    import xlii.tools as tools_mod

    answers: list[str] = []

    def fake_oneshot(persona, prompt, **kw):
        # Simulate a gated tool intent mid-turn: the engine consults the
        # process confirm hook, which the face routed to its bridge.
        answers.append(tools_mod._confirm("send the file?"))
        return "done"

    monkeypatch.setattr(ask_mod, "run_persona_oneshot", fake_oneshot)
    monkeypatch.setattr(persona_mod, "resolve_default_persona", lambda **kw: "ixaac")
    monkeypatch.setattr(resolve_mod, "_lookup_persona",
                        lambda pid: SimpleNamespace(name="iXaac", id=pid))
    monkeypatch.setattr(mojo_mod, "build_mojo_ambient", lambda state, q, **k: "")

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    _recv_event(conn)  # hello
    _recv_event(conn)  # mode_state
    _send(conn, {"type": "input", "text": "ship it"})
    req, _ = _recv_until(conn, "confirm_request")
    assert req["prompt"] == "send the file?"
    _send(conn, {"type": "confirm", "id": req["id"], "approve": True})
    done, _ = _recv_until(conn, "turn_done")
    assert done["ok"] is True
    assert answers == ["y"]
    conn.close()


def test_confirm_deny_over_wire(tmp_path, monkeypatch):
    import xlii.cmds.sessions.ask as ask_mod
    import xlii.cmds.sessions.resolve as resolve_mod
    import xlii.persona as persona_mod
    import xlii.repl_cmds.mojo as mojo_mod
    import xlii.tools as tools_mod

    answers: list[str] = []

    def fake_oneshot(persona, prompt, **kw):
        answers.append(tools_mod._confirm("dangerous?"))
        return "refused then"

    monkeypatch.setattr(ask_mod, "run_persona_oneshot", fake_oneshot)
    monkeypatch.setattr(persona_mod, "resolve_default_persona", lambda **kw: "ixaac")
    monkeypatch.setattr(resolve_mod, "_lookup_persona",
                        lambda pid: SimpleNamespace(name="iXaac", id=pid))
    monkeypatch.setattr(mojo_mod, "build_mojo_ambient", lambda state, q, **k: "")

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    _recv_event(conn)
    _recv_event(conn)
    _send(conn, {"type": "input", "text": "try it"})
    req, _ = _recv_until(conn, "confirm_request")
    _send(conn, {"type": "confirm", "id": req["id"], "approve": False})
    done, _ = _recv_until(conn, "turn_done")
    assert done["ok"] is True
    assert answers == [""]
    conn.close()


def test_disconnect_denies_pending(tmp_path, monkeypatch):
    """Client vanishes while a confirm is pending → the gate reads a denial,
    the turn completes instead of hanging until timeout."""
    import xlii.cmds.sessions.ask as ask_mod
    import xlii.cmds.sessions.resolve as resolve_mod
    import xlii.persona as persona_mod
    import xlii.repl_cmds.mojo as mojo_mod
    import xlii.tools as tools_mod

    finished = threading.Event()
    answers: list[str] = []

    def fake_oneshot(persona, prompt, **kw):
        answers.append(tools_mod._confirm("still there?"))
        finished.set()
        return "carried on"

    monkeypatch.setattr(ask_mod, "run_persona_oneshot", fake_oneshot)
    monkeypatch.setattr(persona_mod, "resolve_default_persona", lambda **kw: "ixaac")
    monkeypatch.setattr(resolve_mod, "_lookup_persona",
                        lambda pid: SimpleNamespace(name="iXaac", id=pid))
    monkeypatch.setattr(mojo_mod, "build_mojo_ambient", lambda state, q, **k: "")

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    _recv_event(conn)
    _recv_event(conn)
    _send(conn, {"type": "input", "text": "go"})
    _recv_until(conn, "confirm_request")
    conn.close()  # walk away mid-question
    assert finished.wait(timeout=10), "turn stayed blocked after disconnect"
    assert answers == [""]


def test_cancel_reaches_both_agents(tmp_path):
    from xlii.serve_face import FaceServer

    cancels: list[str] = []
    state = _fake_state(tmp_path)
    state.agent.request_cancel = lambda: cancels.append("live")
    server = FaceServer(boot=SimpleNamespace(state=state))
    server._oneshot_agent = SimpleNamespace(
        request_cancel=lambda: cancels.append("oneshot"))
    server.cancel()
    assert cancels == ["live", "oneshot"]


def test_cancel_denies_pending_confirm(tmp_path):
    """Face heartbeat says stop=deny — cancel must unblock a mid-turn gate."""
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.agent.request_cancel = lambda: None
    server = FaceServer(boot=SimpleNamespace(state=state))
    out: list[str] = []
    t = threading.Thread(target=lambda: out.append(server.confirm.ask("run it?")))
    t.start()
    for _ in range(100):
        with server.confirm._lock:
            if server.confirm._pending:
                break
        time.sleep(0.01)
    with server.confirm._lock:
        assert server.confirm._pending, "confirm never registered"
    server.cancel()
    t.join(timeout=5)
    assert out == [""]


def test_chat_cancel_reaches_persona_setup_window(tmp_path, monkeypatch):
    """Stop during [?] one-shot setup must reach the turn even before the
    transient persona agent exists for request_cancel fan-out."""
    import xlii.cmds.sessions.ask as ask_mod
    import xlii.cmds.sessions.resolve as resolve_mod
    import xlii.persona as persona_mod
    import xlii.repl_cmds.mojo as mojo_mod

    started = threading.Event()
    cancel_seen = threading.Event()

    def fake_oneshot(persona, prompt, **kw):
        cancelled = kw["cancelled"]
        started.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if cancelled():
                cancel_seen.set()
                return "stopped"
            time.sleep(0.01)
        return "missed cancel"

    monkeypatch.setattr(ask_mod, "run_persona_oneshot", fake_oneshot)
    monkeypatch.setattr(persona_mod, "resolve_default_persona", lambda **kw: "ixaac")
    monkeypatch.setattr(resolve_mod, "_lookup_persona",
                        lambda pid: SimpleNamespace(name="iXaac", id=pid))
    monkeypatch.setattr(mojo_mod, "build_mojo_ambient", lambda state, q, **k: "")

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    _recv_event(conn)
    _recv_event(conn)
    _send(conn, {"type": "input", "text": "go"})
    assert started.wait(timeout=5), "persona setup did not start"
    _send(conn, {"type": "cancel"})
    done, seen = _recv_until(conn, "turn_done")
    answer = next(e for e in seen if e.get("type") == "assistant_answer")
    assert done["ok"] is True
    assert answer["markdown"] == "stopped"
    assert cancel_seen.is_set()
    conn.close()
