"""`xlii serve --face` (tauri-face V1b) — offline server pins.

An in-thread server over an injected fake session (no network to xAI, no real
project boot): token gate, static assets, posture routing, uploads, and the
teardown restores. The persona/one-shot engine and the code-turn spine are
monkeypatched at their import homes — these tests pin the SERVER's contract,
not the engines'.
"""

from __future__ import annotations

import base64
import json
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import xlii.serve_face as serve_face_mod
from xlii.serve_face import FaceServer, serve_face
from xlii.ws_server import _read_frame, _send_text


@pytest.fixture(autouse=True)
def _isolate_face_receipt(monkeypatch, tmp_path):
    """Land/exit must not overwrite the node's real last_face.json."""
    monkeypatch.setattr(
        "xlii.serve_spool.default_state_dir", lambda: tmp_path / "face-state")


# --------------------------------------------------------------------- fakes


def _fake_agent():
    from xlii.agent import SessionState
    return SimpleNamespace(
        console=None, rail=None, debug=None, plan_mode=False, active_mode=None,
        howto_mode=False, history=[], model_override=None,
        session=SessionState(),
        request_cancel=lambda: None,
        run_turn=lambda *a, **k: ("", set(), None),
    )


def _fake_state(tmp_path):
    st = SimpleNamespace(
        shell_cwd=tmp_path,
        project=SimpleNamespace(project_root=tmp_path, name="proj",
                                local_only=True, xli_dir=tmp_path),
        agent=_fake_agent(),
        cfg=None, pool=None, persona=None, yolo=False, no_sync=False,
        console=SimpleNamespace(print=lambda *a, **k: None),
        journal=None,
        attached_files=[],
        pending_persona_switch=None,
    )
    st.agent.console = st.console
    st.attach_file = lambda p, **k: st.attached_files.append(str(p))
    st.as_context_dict = lambda: {
        "console": st.console, "agent": st.agent, "project": st.project,
        "cfg": st.cfg, "pool": st.pool, "state": st,
        "persona": st.persona, "yolo": st.yolo,
    }
    st.save = lambda: None
    return st


# Servers a test started; the autouse fixture below tears every one down —
# an abandoned face server keeps tools._confirm + active_session swapped for
# the whole process and poisons unrelated tests (it holds them until exit).
_LIVE_SERVERS: list[tuple[object, threading.Thread]] = []


@pytest.fixture(autouse=True)
def _cleanup_servers():
    yield
    while _LIVE_SERVERS:
        srv, thread = _LIVE_SERVERS.pop()
        srv._shutdown.set()
        thread.join(timeout=10)
        assert not thread.is_alive(), "face server did not shut down"


def _start_server(state, *, assets_dir=None, token="tok", view="desk"
                   ) -> tuple[int, threading.Thread]:
    ready = threading.Event()
    holder: list[int] = []
    srv_holder: list[object] = []

    def _on_bound(p: int) -> None:
        holder.append(p)
        ready.set()

    boot = SimpleNamespace(state=state)
    t = threading.Thread(
        target=lambda: serve_face(
            Path("."), port=0, token=token, assets_dir=assets_dir,
            on_bound=_on_bound, on_server=srv_holder.append, boot=boot,
            view=view,
        ),
        daemon=True,
    )
    t.start()
    assert ready.wait(timeout=10), "serve_face did not come up"
    _LIVE_SERVERS.append((srv_holder[0], t))
    return holder[0], t


def _connect(port: int, token="tok") -> socket.socket:
    conn = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(b"test-client-key!!").decode()
    conn.sendall(
        (f"GET /?token={token} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
         "Upgrade: websocket\r\nConnection: Upgrade\r\n"
         f"Sec-WebSocket-Key: {key}\r\n\r\n").encode())
    resp = b""
    while not resp.endswith(b"\r\n\r\n"):
        chunk = conn.recv(1)
        if not chunk:
            break
        resp += chunk
    assert b"101" in resp.split(b"\r\n", 1)[0], resp
    return conn


def _recv_event(conn) -> dict:
    frame = _read_frame(conn)
    assert frame is not None, "connection closed while awaiting an event"
    _opcode, payload = frame
    return json.loads(payload.decode())


def _recv_until(conn, type_, *, limit=30) -> tuple[dict, list[dict]]:
    """Read events until one of ``type_`` arrives; returns (it, everything)."""
    seen: list[dict] = []
    for _ in range(limit):
        ev = _recv_event(conn)
        seen.append(ev)
        if ev.get("type") == type_:
            return ev, seen
    raise AssertionError(f"no {type_} in {seen}")


def _send(conn, obj: dict) -> None:
    _send_text(conn, json.dumps(obj))


# --------------------------------------------------------------------- tests


def test_bad_token_403(tmp_path):
    port, _t = _start_server(_fake_state(tmp_path))
    conn = socket.create_connection(("127.0.0.1", port), timeout=5)
    conn.sendall(b"GET /?token=wrong HTTP/1.1\r\nHost: x\r\n"
                 b"Upgrade: websocket\r\nSec-WebSocket-Key: abc\r\n\r\n")
    resp = conn.recv(1024)
    assert b"403" in resp
    conn.close()


def test_hello_then_mode_state_chat_default(tmp_path):
    port, _t = _start_server(_fake_state(tmp_path))
    conn = _connect(port)
    hello = _recv_event(conn)
    assert hello["type"] == "hello"
    assert hello["protocol"] == "2"
    from xlii import __version__
    assert hello["version"] == __version__
    ms = _recv_event(conn)
    assert ms["type"] == "mode_state"
    assert ms["posture"] == "chat"           # iXaac-first
    assert ms["mode"] == "chat"
    assert ms.get("overlay", "") == ""
    conn.close()


def test_open_terminal_launches_mc(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    launched = {}

    def fake_launch(cwd, *, run="", preferred=""):
        launched["run"] = run
        launched["cwd"] = str(cwd)
        return True, "opened mc in a new terminal"

    monkeypatch.setattr("xlii.interactive.launch_in_external_terminal", fake_launch)
    state = _fake_state(tmp_path)
    state.shell_cwd = tmp_path / "desk"
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.open_terminal("mc") is True
    assert launched["run"] == "mc"
    assert launched["cwd"].endswith("desk")
    assert sent[-1]["type"] == "meta_message"
    assert sent[-1]["level"] == "success"


def test_open_terminal_honors_home_dest(tmp_path, monkeypatch):
    from xlii.project_paths import user_home
    from xlii.serve_face import FaceServer

    launched = {}

    def fake_launch(cwd, *, run="", preferred=""):
        launched["cwd"] = str(cwd)
        return True, "opened"

    monkeypatch.setattr("xlii.interactive.launch_in_external_terminal", fake_launch)
    state = _fake_state(tmp_path)
    state.shell_cwd = tmp_path / "desk"
    state.cfg = SimpleNamespace(tui_terminal="", tui_terminal_cwd="home", tui_terminal_cwd_path="")
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = lambda ev: None  # type: ignore[method-assign]
    assert server.open_terminal() is True
    assert launched["cwd"] == str(user_home())


def test_set_terminal_cwd_path_persists_custom(tmp_path):
    from xlii.serve_face import FaceServer

    saves = []
    state = _fake_state(tmp_path)
    state.cfg = SimpleNamespace(
        tui_terminal_cwd="project", tui_terminal_cwd_path="",
        save=lambda: saves.append(1),
    )
    sent: list[dict] = []
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = sent.append  # type: ignore[method-assign]
    assert server.set_terminal_cwd_path(str(tmp_path)) is True
    assert state.cfg.tui_terminal_cwd == "custom"
    assert state.cfg.tui_terminal_cwd_path == str(tmp_path)
    assert saves
    assert any(e.get("type") == "chrome_state" for e in sent)
    assert any(e.get("type") == "meta_message" and "new terminal" in e.get("text", "") for e in sent)


def test_set_face_skin_persists(tmp_path):
    from xlii.serve_face import FaceServer

    saves = []
    state = _fake_state(tmp_path)
    state.cfg = SimpleNamespace(face_skin="", save=lambda: saves.append(1))
    sent: list[dict] = []
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = sent.append  # type: ignore[method-assign]
    assert server.set_face_skin("mojo") is True
    assert state.cfg.face_skin == "mojo"
    assert saves
    chrome = [e for e in sent if e.get("type") == "chrome_state"]
    assert chrome and chrome[-1].get("face_skin") == "mojo"
    assert server.set_face_skin("banana") is False
    assert state.cfg.face_skin == "mojo"


def test_set_face_fkeys_and_bold_persist(tmp_path):
    from xlii.serve_face import FaceServer

    saves = []
    state = _fake_state(tmp_path)
    state.cfg = SimpleNamespace(
        face_skin="", face_fkeys=True, face_bold=False,
        save=lambda: saves.append(1),
    )
    sent: list[dict] = []
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = sent.append  # type: ignore[method-assign]
    assert server.set_face_fkeys(False) is True
    assert state.cfg.face_fkeys is False
    assert server.set_face_bold(True) is True
    assert state.cfg.face_bold is True
    assert saves
    chrome = [e for e in sent if e.get("type") == "chrome_state"]
    assert chrome and chrome[-1].get("face_fkeys") is False
    assert chrome[-1].get("face_bold") is True


def test_face_clear_screen_emits_clear_transcript(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    sent: list[dict] = []
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = sent.append  # type: ignore[method-assign]
    assert server._try_face_clear_screen("/cls") is True
    assert any(e.get("type") == "clear_transcript" for e in sent)
    sent.clear()
    assert server._try_face_clear_screen("/clear-screen") is True
    assert server._try_face_clear_screen("hello") is False
    sent.clear()
    assert server._run_chat_input("/cls") is True
    assert any(e.get("type") == "clear_transcript" for e in sent)
    assert not any(e.get("type") == "user_turn" for e in sent)


def test_plan_gateway_from_chat_flips_to_lab(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    monkeypatch.setattr("xlii.glass.face_may_accept", lambda *_a, **_k: (True, ""))
    seen: list[tuple] = []

    def fake_process(state, user_input):
        seen.append((getattr(state, "command_scope", None), user_input))
        return None, True

    monkeypatch.setattr("xlii.repl.process_repl_input", fake_process)
    state = _fake_state(tmp_path)
    sent: list[dict] = []
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = sent.append  # type: ignore[method-assign]
    assert server.posture == "chat"
    assert server._run_chat_input("/plan") is True
    assert server.posture == "code"
    assert state.command_scope == "code"
    assert seen and str(seen[-1][1]).strip().startswith("/plan")
    assert any(
        e.get("type") == "meta_message" and "cold" in (e.get("text") or "")
        and "--from-mojo" in (e.get("text") or "")
        for e in sent
    )
    sent.clear()
    seen.clear()
    server.posture = "chat"
    server._sync_command_scope()
    assert server._run_chat_input("/plan --from-mojo") is True
    assert "--from-mojo" in str(seen[-1][1])
    assert any(
        e.get("type") == "meta_message" and "carrying recent talk" in (e.get("text") or "")
        for e in sent
    )
    # Talk never self-starts — PlanController is not armed from [M].
    assert not getattr(state.agent, "plan_mode", False)


def test_command_catalog_follows_posture(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    chat_names = {c["name"] for c in server.command_catalog()["commands"]}
    assert "help" in chat_names and "cls" in chat_names
    assert "plan" in chat_names  # gateway into [$] — talk never self-starts plan
    assert "execute" not in chat_names
    server._set_posture("code")
    code_names = {c["name"] for c in server.command_catalog()["commands"]}
    assert "plan" in code_names and "execute" in code_names
    assert "clear" in code_names


def test_set_posture_resends_command_catalog(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    sent: list[dict] = []
    server = FaceServer(boot=SimpleNamespace(state=state))
    server._client = object()
    server.send = sent.append  # type: ignore[method-assign]
    server._set_posture("code")
    cats = [e for e in sent if e.get("type") == "command_catalog"]
    assert cats
    names = {c["name"] for c in cats[-1]["commands"]}
    assert "plan" in names and "execute" in names
    sent.clear()
    server._set_posture("chat")
    cats = [e for e in sent if e.get("type") == "command_catalog"]
    assert cats
    names = {c["name"] for c in cats[-1]["commands"]}
    assert "help" in names and "cls" in names
    assert "plan" in names
    assert "execute" not in names


def test_write_task_saves_toml(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    sent: list[dict] = []
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = sent.append  # type: ignore[method-assign]
    ok = server.write_task({
        "name": "yo",
        "description": "test pipe",
        "steps": [
            {"kind": "shell", "id": "", "body": "echo hi"},
            {"kind": "slash", "id": "", "body": "/status"},
        ],
    })
    assert ok is True
    from xlii.tasks import load_pipeline, pipeline_path

    assert pipeline_path(tmp_path, "yo").is_file()
    pipe = load_pipeline(tmp_path, "yo")
    assert len(pipe.steps) == 2
    assert any("wrote" in (e.get("text") or "") for e in sent if e.get("type") == "meta_message")


def _plugin_write_spec(pid: str, *, effect: str = "read-only",
                       trust: str = "subscription") -> dict:
    return {
        "id": pid,
        "name": pid,
        "effect": effect,
        "trust": trust,
        "auth": "none",
        "actions": [{"id": "run", "command": "echo hi", "params": []}],
        "subscribe": True,
    }


def test_write_plugin_high_risk_subscribe_gated(tmp_path, monkeypatch):
    """plugin_write with subscribe=true must not attach a high-risk plugin
    from an unelevated session — the same /admin unlock gate plugin_subscribe
    enforces (previously bypassed via the maker's save)."""
    import xlii.plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", tmp_path / "plugins")
    state = _fake_state(tmp_path)  # no `elevated` attr → unelevated
    sent: list[dict] = []
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = sent.append  # type: ignore[method-assign]

    spec = _plugin_write_spec("danger", effect="local-system",
                              trust="always-confirm")
    assert server.write_plugin(spec) is True  # the save itself succeeded
    assert (tmp_path / "plugins" / "danger.md").is_file()
    # …but the subscribe was refused by the gate.
    assert "danger" not in plugin_mod.load_subscriptions(tmp_path)
    warns = [e for e in sent if e.get("type") == "meta_message"
             and e.get("level") == "warn"]
    assert warns and "admin unlock" in (warns[-1].get("text") or "")

    # Elevated session (/admin unlock) → the same save subscribes.
    state.elevated = True
    assert server.write_plugin(spec) is True
    assert "danger" in plugin_mod.load_subscriptions(tmp_path)


def test_write_plugin_low_risk_subscribes(tmp_path, monkeypatch):
    import xlii.plugin as plugin_mod

    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", tmp_path / "plugins")
    state = _fake_state(tmp_path)
    sent: list[dict] = []
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = sent.append  # type: ignore[method-assign]

    assert server.write_plugin(_plugin_write_spec("wx")) is True
    assert "wx" in plugin_mod.load_subscriptions(tmp_path)
    ok_msgs = [e for e in sent if e.get("type") == "meta_message"
               and e.get("level") == "success"]
    assert ok_msgs and "subscribed" in (ok_msgs[-1].get("text") or "")


def test_talk_tape_reconnect_repaints(tmp_path, monkeypatch):
    """Slice 3a: same-backend reload repaints talk turns."""
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.view_posture = "phone"
    server._begin_talk_turn("hello tape")
    server._finish_talk_turn("world reply")
    turns = server.stream_turns()
    assert turns == [
        {"role": "user", "text": "hello tape"},
        {"role": "assistant", "text": "world reply"},
    ]
    server._begin_talk_turn("mid turn")
    inflight = server.stream_turns()
    assert any(t.get("in_flight") for t in inflight)
    server._finish_talk_turn("done")
    assert server._talk_in_flight is None


def test_emit_resume_meta_only_after_disk_seed(tmp_path, monkeypatch):
    """W6: resume meta only when this connect seeded an empty tape from disk."""
    import time

    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.view_posture = "phone"
    server._begin_talk_turn("live turn")
    server._finish_talk_turn("live reply")
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]
    monkeypatch.setattr(
        "xlii.face_receipt.read_face_receipt",
        lambda state_dir=None: {"reason": "linger", "at": time.time()},
    )

    server._emit_resume_meta_from_disk()
    assert not any("resumed from disk" in (m.get("text") or "") for m in sent)

    fresh = FaceServer(boot=SimpleNamespace(state=state))
    fresh.view_posture = "phone"
    sent2: list[dict] = []
    fresh.send = sent2.append  # type: ignore[method-assign]
    disk_turns = [
        {"role": "user", "text": "old"},
        {"role": "assistant", "text": "hi"},
    ]
    fresh._seed_talk_from_disk = lambda: disk_turns  # type: ignore[method-assign]

    fresh._emit_resume_meta_from_disk()
    meta = [m for m in sent2 if "resumed from disk" in (m.get("text") or "")]
    assert len(meta) == 1

    fresh._emit_resume_meta_from_disk()
    meta = [m for m in sent2 if "resumed from disk" in (m.get("text") or "")]
    assert len(meta) == 1

    fresh._append_talk_turn("user", "new live")
    turns = fresh.stream_turns()
    assert any(t["text"] == "old" for t in turns)
    assert any(t["text"] == "new live" for t in turns)


def test_stream_turns_skips_system_and_tools(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.agent.history = [
        {"role": "system", "content": "you are xlii"},
        {"role": "user", "content": "hi from A"},
        {"role": "assistant", "content": "hello A"},
        {"role": "tool", "content": "secret tool dump"},
        {"role": "user", "content": "again"},
        {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
    ]
    server = FaceServer(boot=SimpleNamespace(state=state))
    turns = server.stream_turns()
    assert turns == [
        {"role": "user", "text": "hi from A"},
        {"role": "assistant", "text": "hello A"},
        {"role": "user", "text": "again"},
        {"role": "assistant", "text": "ok"},
    ]


def test_note_open_stream_skips_home(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.project.name = "scratch/home"
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.note_open_stream(state.project)
    assert server.open_streams() == []


def test_note_open_stream_lists_project(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.note_open_stream(state.project)
    rows = server.open_streams()
    assert len(rows) == 1
    assert rows[0]["name"] == "proj"
    assert rows[0]["id"]


def test_stream_turns_for_uses_stash(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.agent.history = [{"role": "user", "content": "live room"}]
    other = tmp_path / "other"
    other.mkdir()
    server = FaceServer(boot=SimpleNamespace(state=state))
    server._open_streams = [{
        "id": "other", "name": "other", "path": str(other),
        "label": "other", "kind": "lab",
    }]
    server.state._history_stash = {
        "code:other": [
            {"role": "user", "content": "from other"},
            {"role": "assistant", "content": "yes"},
            {"role": "tool", "content": "nope"},
        ],
    }
    turns = server.stream_turns_for("other")
    assert turns == [
        {"role": "user", "text": "from other"},
        {"role": "assistant", "text": "yes"},
    ]


def test_emit_stream_peek_sends_parked_tape(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]
    server._open_streams = [{
        "id": "other", "name": "other", "path": str(tmp_path / "missing"),
        "label": "other", "kind": "lab",
    }]
    server.state._history_stash = {
        "code:other": [{"role": "user", "content": "peek me"}],
    }
    server.emit_stream_peek("b", "other")
    evs = [e for e in sent if e.get("type") == "stream_peek"]
    assert evs and evs[-1]["slot"] == "b" and evs[-1]["id"] == "other"
    assert evs[-1]["turns"][0]["text"] == "peek me"


def test_bind_open_stream_remaps_peek(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    (other / ".xlii").mkdir()
    server = FaceServer(boot=SimpleNamespace(state=state))
    here = str(tmp_path.resolve())
    server._open_streams = [
        {"id": "proj", "name": "proj", "path": here, "label": "proj", "kind": "lab"},
        {"id": "other", "name": "other", "path": str(other.resolve()),
         "label": "other", "kind": "lab"},
    ]
    server._stream_live = "proj"
    server.deck._slot_a = "stream"
    server.deck._slot_b = "stream:other"
    state.project = SimpleNamespace(
        project_root=other, name="other", local_only=True, xli_dir=other / ".xlii",
    )
    server._bind_open_stream(state.project)
    assert server.deck.slot_tuple() == ("stream:proj", "stream", "b")
    assert server._stream_live == "other"


def test_sync_stream_sends_project_tape(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.agent.history = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "in this folder"},
        {"role": "assistant", "content": "noted"},
    ]
    sent: list[dict] = []
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = sent.append  # type: ignore[method-assign]
    server.sync_stream()
    evs = [e for e in sent if e.get("type") == "stream_sync"]
    assert evs
    assert evs[-1]["project"] == "proj"
    assert evs[-1]["turns"][0]["text"] == "in this folder"


def test_classify_user_kind_and_infer_meta_level():
    from xlii.serve_face import classify_user_kind, infer_meta_level

    assert classify_user_kind("/ops", posture="code") == "slash"
    assert classify_user_kind("ip addr", posture="code") == "shell"
    assert classify_user_kind("hello", posture="chat") == "talk"
    assert classify_user_kind("cd Downloads", posture="chat") == "shell"
    assert classify_user_kind("ls", posture="chat") == "shell"
    assert classify_user_kind("what is ops", posture="code", overlay="howto") == "talk"
    assert infer_meta_level("ops mode ON — OS diagnostics") == "mode"
    assert infer_meta_level("✓ left ops — back to the base surface") == "success"
    assert infer_meta_level("[green]✓[/green] left howto") == "success"
    assert infer_meta_level("just a note") == "info"


def test_send_feed_view_reveals_stream_before_emit():
    calls: list[str] = []
    sent: list[dict] = []
    server = SimpleNamespace(
        deck=SimpleNamespace(_reveal_stream=lambda: calls.append("reveal")),
        send=lambda obj: sent.append(obj),
    )

    FaceServer._send_feed_view(server, {"type": "feed_view", "kind": "plugin"})

    assert calls == ["reveal"]
    assert sent == [{"type": "feed_view", "kind": "plugin"}]


def test_overlay_word_howto_ops_and_disc():
    from xlii.status import overlay_word

    assert overlay_word(SimpleNamespace(howto_mode=True, agent=None)) == "howto"
    assert overlay_word(SimpleNamespace(howto_mode=False, agent=None)) == ""

    class _Ops:
        def status_tag(self):
            return ("OPS", "OPS")

    class _Disc:
        def status_tag(self):
            return ("DISC", "DISC")

    assert overlay_word(SimpleNamespace(
        howto_mode=False, agent=SimpleNamespace(active_mode=_Ops()))) == "ops"
    assert overlay_word(SimpleNamespace(
        howto_mode=False, agent=SimpleNamespace(active_mode=_Disc()))) == "disc"


def test_howto_owns_flip_then_exit_overlay_clears(tmp_path):
    """Faux-mode (howto) is named on mode_state.overlay; exit_overlay is /off."""
    state = _fake_state(tmp_path)
    state.howto_mode = True
    state.attached_docs = []
    port, _t = _start_server(state)
    conn = _connect(port)
    ms, _ = _recv_until(conn, "mode_state")
    assert ms["overlay"] == "howto"
    _send(conn, {"type": "exit_overlay"})
    ms2, _ = _recv_until(conn, "mode_state")
    assert ms2.get("overlay", "") == ""
    assert state.howto_mode is False
    conn.close()


def test_pane_deck_on_connect_and_pane_action(tmp_path):
    """B1: a workbench with panes sends a deck after hello; pane_action ops
    round-trip over the live socket."""
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = _fake_state(tmp_path)
    state.workbench = BUILTIN_WORKBENCHES["code"]
    (tmp_path / "marker.txt").write_text("hi")
    port, _t = _start_server(state)
    conn = _connect(port)
    deck_ev, _ = _recv_until(conn, "pane_deck")
    assert deck_ev["workbench"] == "code"
    explorer = next(p for p in deck_ev["panes"] if p["id"] == "explorer")
    assert any("marker.txt" in r["text"] for r in explorer["rows"])
    _send(conn, {"type": "pane_action", "pane": "explorer", "op": "select", "index": 0})
    deck_ev2, _ = _recv_until(conn, "pane_deck")
    explorer2 = next(p for p in deck_ev2["panes"] if p["id"] == "explorer")
    assert sum(1 for r in explorer2["rows"] if r["selected"]) == 1
    conn.close()


def test_no_pane_deck_without_workbench(tmp_path):
    """B1: no workbench row (the chat default) = no deck, today's face."""
    state = _fake_state(tmp_path)
    state.workbench = None
    port, _t = _start_server(state)
    conn = _connect(port)
    tape, seen = _recv_until(conn, "stream_sync")
    kinds = [e.get("type") for e in seen]
    assert kinds[0] == "hello"
    assert "mode_state" in kinds
    assert "chrome_state" in kinds
    cat = next(e for e in seen if e.get("type") == "command_catalog")
    names = {c["name"] for c in cat["commands"]}
    # Connect is [M] — chat-safe slash, not the lab catalog.
    assert "help" in names and "status" in names
    assert "clear" in names and "cls" in names
    assert "plan" in names  # [M] catalog offers the lab gateway
    assert "tui" not in names and "terminal" not in names
    plugs = next(e for e in seen if e.get("type") == "plugin_catalog")
    assert isinstance(plugs["plugins"], list)
    assert "workbench_catalog" in kinds
    assert "console_catalog" in kinds
    assert "pane_catalog" in kinds
    assert tape["type"] == "stream_sync"
    conn.settimeout(0.5)
    while True:
        try:
            kinds.append(_recv_event(conn)["type"])
        except TimeoutError:
            break
    assert "pane_deck" not in kinds
    conn.close()


def test_chrome_state_on_connect_and_posture_flip(tmp_path, monkeypatch):
    """F1: the HUD rail ships on connect and refreshes on posture flips."""
    from xlii.workbench import BUILTIN_WORKBENCHES

    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    state = _fake_state(tmp_path)
    state.workbench = BUILTIN_WORKBENCHES["code"]
    port, _t = _start_server(state)
    conn = _connect(port)
    chrome, _ = _recv_until(conn, "chrome_state")
    assert chrome["project"] == "proj"
    assert chrome["workbench"] == "code"
    # three-faces: workbench no longer flips posture — face stays chat default
    # until the user flips; quick_launch carries the code pack doors.
    assert chrome["posture"] == "chat"
    assert chrome.get("surface") in ("code", "chat", "scratch")
    assert chrome["persona"]            # the resolved default persona
    assert chrome["providers_total"] > 0
    ql = chrome.get("quick_launch") or []
    assert any(b.get("id") == "plan" for b in ql)
    assert any(b.get("id") == "git" for b in ql)
    # Drain connect burst (hello · mode_state · chrome · catalogs) before flip.
    for _ in range(4):
        _recv_event(conn)
    _send(conn, {"type": "set_posture", "posture": "code"})
    ms, _ = _recv_until(conn, "mode_state")
    assert ms["posture"] == "code"
    chrome2, _ = _recv_until(conn, "chrome_state")
    assert chrome2["posture"] == "code"
    conn.close()


def test_static_index_and_traversal(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "index.html").write_text("<title>xlii</title>")
    (assets / "app.js").write_text("export {}")
    (tmp_path / "secret.txt").write_text("nope")
    port, _t = _start_server(_fake_state(tmp_path), assets_dir=assets)

    def _get(path):
        c = socket.create_connection(("127.0.0.1", port), timeout=5)
        c.sendall(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        time.sleep(0.1)
        data = c.recv(65536)
        c.close()
        return data

    ok = _get("/")
    assert b"200 OK" in ok and b"text/html" in ok and b"<title>xlii</title>" in ok
    js = _get("/app.js")
    assert b"200 OK" in js and b"text/javascript" in js
    assert b"403" in _get("/../secret.txt")
    assert b"404" in _get("/missing.js")
    assert b"404" in _get("/whatever")


def test_static_no_assets_dir_404(tmp_path):
    port, _t = _start_server(_fake_state(tmp_path),
                             assets_dir=tmp_path / "nope")
    c = socket.create_connection(("127.0.0.1", port), timeout=5)
    c.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    time.sleep(0.1)
    assert b"404" in c.recv(4096)
    c.close()


def test_chat_input_routes_to_persona_oneshot(tmp_path, monkeypatch):
    calls = {}

    def fake_oneshot(persona, prompt, **kw):
        calls["persona"] = persona
        calls["prompt"] = prompt
        calls["kw"] = kw
        return "the answer"

    import xlii.cmds.sessions.ask as ask_mod
    import xlii.cmds.sessions.resolve as resolve_mod
    import xlii.persona as persona_mod
    import xlii.repl_cmds.mojo as mojo_mod
    monkeypatch.setattr(ask_mod, "run_persona_oneshot", fake_oneshot)
    monkeypatch.setattr(persona_mod, "resolve_default_persona",
                        lambda **kw: "ixaac")
    monkeypatch.setattr(resolve_mod, "_lookup_persona",
                        lambda pid: SimpleNamespace(name="iXaac", id=pid))
    monkeypatch.setattr(mojo_mod, "build_mojo_ambient",
                        lambda state, q, **k: "AMBIENT")

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    _recv_event(conn)  # hello
    _recv_event(conn)  # mode_state
    _send(conn, {"type": "input", "text": "what did I do today?"})
    done, seen = _recv_until(conn, "turn_done")
    types = [e["type"] for e in seen]
    assert "user_turn" in types and "assistant_answer" in types
    answer = next(e for e in seen if e["type"] == "assistant_answer")
    assert answer["markdown"] == "the answer"
    assert answer["mode"] == "chat"
    assert done["ok"] is True
    # The engine got the RAW question + the fused ambient, phone-model flags.
    assert calls["prompt"] == "what did I do today?"
    assert calls["kw"]["ambient_context"] == "AMBIENT"
    assert calls["kw"]["persist"] is True
    assert calls["kw"]["drain"] is False
    conn.close()


def test_code_slash_handled_no_turn(tmp_path, monkeypatch):
    import xlii.repl as repl_mod
    seen_inputs: list[str] = []

    def fake_process(state, text):
        seen_inputs.append(text)
        return None, True  # handled — no agent turn

    monkeypatch.setattr(repl_mod, "process_repl_input", fake_process)
    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    # hello · mode_state · chrome_state · command_catalog · plugin_catalog
    for _ in range(5):
        _recv_event(conn)
    _send(conn, {"type": "set_posture", "posture": "code"})
    ms, _ = _recv_until(conn, "mode_state")
    assert ms["posture"] == "code"
    _send(conn, {"type": "input", "text": "/status"})
    done, seen = _recv_until(conn, "turn_done")
    assert done["ok"] is True
    ut = [e for e in seen if e.get("type") == "user_turn"]
    assert len(ut) == 1 and ut[0]["text"] == "/status"
    assert ut[0].get("kind") == "slash"
    assert seen_inputs == ["/status"]
    conn.close()


def test_code_agent_turn_through_drive_turn(tmp_path, monkeypatch):
    import xlii.conversation as conv_mod
    import xlii.repl as repl_mod

    monkeypatch.setattr(repl_mod, "process_repl_input",
                        lambda state, text: (text, False))

    def fake_drive_turn(state, prompt, run_turn, *, render, on_error, **kw):
        result = SimpleNamespace(reply=f"did: {prompt}", dirty=set(), stats=None)
        render(result, prompt)
        return result

    monkeypatch.setattr(conv_mod, "drive_turn", fake_drive_turn)
    import xlii.status as status_mod
    monkeypatch.setattr(status_mod, "turn_record",
                        lambda state: ("code", "green", ""))

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    for _ in range(5):  # hello · mode_state · chrome · command_catalog · plugin_catalog
        _recv_event(conn)
    _send(conn, {"type": "set_posture", "posture": "code"})
    _recv_until(conn, "mode_state")
    _send(conn, {"type": "input", "text": "fix the bug"})
    # M2.2: agent turn is backgrounded — busy_state frees the prompt first.
    bs, seen_bs = _recv_until(conn, "busy_state")
    assert bs.get("agent") is True and bs.get("hard") is False
    done, seen = _recv_until(conn, "turn_done")
    seen = seen_bs + seen
    types = [e["type"] for e in seen]
    assert types.index("user_turn") < types.index("assistant_answer")
    answer = next(e for e in seen if e["type"] == "assistant_answer")
    assert answer["markdown"] == "did: fix the bug"
    assert answer["mode"] == "code"
    assert done["ok"] is True
    conn.close()


def test_bg_default_allows_btw_while_agent_runs(tmp_path, monkeypatch):
    """M2.2: while a bg agent turn runs, /btw is accepted; a second ask is not."""
    import threading
    import xlii.conversation as conv_mod
    import xlii.repl as repl_mod

    hold = threading.Event()
    released = threading.Event()

    def fake_drive_turn(state, prompt, run_turn, *, render, on_error, **kw):
        hold.wait(timeout=5)
        result = SimpleNamespace(reply="done", dirty=set(), stats=None)
        render(result, prompt)
        released.set()
        return result

    monkeypatch.setattr(conv_mod, "drive_turn", fake_drive_turn)
    monkeypatch.setattr(repl_mod, "process_repl_input",
                        lambda state, text: (None, True) if text.strip().startswith("/btw")
                        else (text, False))
    import xlii.status as status_mod
    monkeypatch.setattr(status_mod, "turn_record",
                        lambda state: ("code", "green", ""))

    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    for _ in range(5):
        _recv_event(conn)
    _send(conn, {"type": "set_posture", "posture": "code"})
    _recv_until(conn, "mode_state")
    _send(conn, {"type": "input", "text": "long research"})
    bs, _ = _recv_until(conn, "busy_state")
    assert bs["agent"] is True

    # Disallowed second ask while agent runs — refused at submit (error),
    # worker allow-list meta, or "already running" if bare looked shell-primary.
    _send(conn, {"type": "input", "text": "another ask"})
    refused = False
    for _ in range(20):
        ev = _recv_event(conn)
        blob = (ev.get("message") or ev.get("text") or "").lower()
        if ev.get("type") in ("error", "meta_message") and (
                "agent" in blob and ("btw" in blob or "running" in blob or "steer" in blob)):
            refused = True
            if ev.get("type") == "meta_message":
                try:
                    _recv_until(conn, "turn_done")
                except Exception:
                    # Draining to turn_done is opportunistic here -- refused was already recorded above.
                    pass
            break
        if ev.get("type") == "turn_done":
            continue
    assert refused, "expected refuse of second agent ask while bg turn runs"

    # Allowed steer slash.
    _send(conn, {"type": "input", "text": "/btw focus on the tests"})
    done_btw, _ = _recv_until(conn, "turn_done")
    assert done_btw["ok"] is True

    hold.set()
    done, _ = _recv_until(conn, "turn_done")
    assert done["ok"] is True
    assert released.is_set()
    conn.close()


@pytest.mark.parametrize("text", ["!git status", "?> summarize that"])
def test_chat_bg_agent_routes_allowed_dispatcher_inputs_without_dropping_uploads(
    tmp_path, monkeypatch, text
):
    import xlii.repl as repl_mod

    seen_inputs: list[str] = []

    def fake_process(state, user_input):
        seen_inputs.append(user_input)
        return None, True

    monkeypatch.setattr(repl_mod, "process_repl_input", fake_process)
    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]
    server._agent_running.set()
    server.pending_uploads = [str(tmp_path / "staged.png")]

    assert server._run_chat_input(text) is True
    assert seen_inputs == [text]
    assert server.pending_uploads == [str(tmp_path / "staged.png")]
    ut = [ev for ev in sent if ev.get("type") == "user_turn"]
    assert len(ut) == 1 and ut[0]["text"] == text
    assert ut[0].get("kind") == "shell"


def test_chat_bg_agent_refusal_preserves_staged_uploads(tmp_path):
    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]
    server._agent_running.set()
    server.pending_uploads = [str(tmp_path / "staged.png")]

    assert server._run_chat_input("second persona ask") is True
    assert server.pending_uploads == [str(tmp_path / "staged.png")]
    assert not any(ev.get("type") == "user_turn" for ev in sent)
    assert any("already running" in ev.get("text", "") for ev in sent)


def test_code_error_turn_drains_outbox_to_wire(tmp_path, monkeypatch):
    import xlii.conversation as conv_mod
    import xlii.outbox as outbox_mod
    import xlii.repl as repl_mod

    state = _fake_state(tmp_path)
    outbox = outbox_mod.grant_local_outbox(state.agent.session)
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.posture = "code"
    sent: list[dict] = []

    def capture(obj):
        sent.append(obj)
        if obj.get("type") == "turn_done":
            server._shutdown.set()

    def fake_drive_turn(state, prompt, run_turn, *, render, on_error, **kw):
        (outbox / "failure.png").write_bytes(b"PNG")
        on_error(RuntimeError("boom"))

    monkeypatch.setattr(repl_mod, "process_repl_input",
                        lambda state, text: (text, False))
    monkeypatch.setattr(conv_mod, "drive_turn", fake_drive_turn)
    server.send = capture  # type: ignore[method-assign]

    assert server.submit("make an image") is True
    t = threading.Thread(target=server.worker)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "face worker did not finish the injected turn"

    file_out = next(e for e in sent if e.get("type") == "file_out")
    assert file_out["name"] == "failure.png"
    assert base64.b64decode(file_out["b64"]) == b"PNG"
    assert file_out.get("path")
    assert (outbox / "delivered" / "failure.png").exists()
    assert [e.get("type") for e in sent].index("file_out") < [
        e.get("type") for e in sent
    ].index("turn_done")
    done = next(e for e in sent if e.get("type") == "turn_done")
    assert done["ok"] is False


def test_upload_code_posture_attaches(tmp_path, monkeypatch):
    import xlii.repl as repl_mod
    monkeypatch.setattr(repl_mod, "process_repl_input",
                        lambda state, text: (None, True))
    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    _recv_event(conn)
    _recv_event(conn)
    _send(conn, {"type": "set_posture", "posture": "code"})
    _recv_event(conn)
    payload = base64.b64encode(b"PNGDATA").decode()
    _send(conn, {"type": "upload", "name": "shot.png", "b64": payload})
    meta, _ = _recv_until(conn, "meta_message")
    assert "attached" in meta["text"]
    assert len(state.attached_files) == 1
    saved = Path(state.attached_files[0])
    assert saved.read_bytes() == b"PNGDATA"
    assert saved.parent == tmp_path / "uploads"
    assert saved.name.endswith("-shot.png")
    conn.close()


def test_upload_chat_posture_stages_for_oneshot(tmp_path, monkeypatch):
    calls = {}

    import xlii.cmds.sessions.ask as ask_mod
    import xlii.cmds.sessions.resolve as resolve_mod
    import xlii.persona as persona_mod
    import xlii.repl_cmds.mojo as mojo_mod

    def fake_oneshot(persona, prompt, **kw):
        calls["kw"] = kw
        return "ok"

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
    payload = base64.b64encode(b"IMG").decode()
    _send(conn, {"type": "upload", "name": "arrows.png", "b64": payload})
    _recv_until(conn, "meta_message")
    _send(conn, {"type": "input", "text": "look at the arrows"})
    _recv_until(conn, "turn_done")
    atts = calls["kw"]["attachments"]
    assert atts and atts[0].endswith("-arrows.png")
    conn.close()


def test_upload_oversize_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(serve_face_mod, "MAX_UPLOAD_BYTES", 4)
    state = _fake_state(tmp_path)
    port, _t = _start_server(state)
    conn = _connect(port)
    _recv_event(conn)
    _recv_event(conn)
    _send(conn, {"type": "upload", "name": "big.bin",
                 "b64": base64.b64encode(b"12345").decode()})
    err, _ = _recv_until(conn, "error")
    assert "over" in err["message"] or "empty" in err["message"]
    assert not (tmp_path / "uploads").exists()
    conn.close()


def test_attach_client_refused_mid_turn(tmp_path):
    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    a, b = socket.socketpair()
    assert server.attach_client(a) is True
    server._busy.set()
    c, d = socket.socketpair()
    assert server.attach_client(c) is False
    server._busy.clear()
    assert server.attach_client(c) is True  # idle → newcomer wins
    for s in (a, b, c, d):
        s.close()


def test_plugin_catalog_and_call_over_wire(tmp_path, monkeypatch):
    """M2.1: plugin_catalog on connect; plugin_call runs invoke_action (no slash)."""
    from xlii import plugin as plugin_mod
    from xlii.plugin_call import ActionResult

    # Point the global plugins dir at a temp stock of one HTTP-style plugin.
    plug_dir = tmp_path / "plugins"
    plug_dir.mkdir()
    (plug_dir / "demo.md").write_text(
        "---\n"
        "id: demo\n"
        "name: Demo\n"
        "description: test plugin\n"
        "risk: low\n"
        "effect: read-only\n"
        "trust: subscription\n"
        "auth_type: none\n"
        "actions:\n"
        "  - id: ping\n"
        "    description: no-arg ping\n"
        "    method: GET\n"
        "    url: https://example.test/ping\n"
        "    output: raw\n"
        "---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug_dir)

    state = _fake_state(tmp_path)
    # Subscribe the project to demo.
    (tmp_path / "plugins.txt").write_text("demo\n", encoding="utf-8")
    monkeypatch.setattr(plugin_mod, "load_subscriptions",
                        lambda xli: ["demo"])

    def fake_invoke(plugin_id, raw, action_id, params, **kw):
        assert plugin_id == "demo" and action_id == "ping"
        return ActionResult(
            plugin_id="demo", action_id="ping", mode="raw",
            code=200, body="pong", user_text="pong", model_text="receipt",
        )

    monkeypatch.setattr("xlii.plugin_call.invoke_action", fake_invoke)

    port, _t = _start_server(state)
    conn = _connect(port)
    cat, _ = _recv_until(conn, "plugin_catalog")
    demo = next(p for p in cat["plugins"] if p["id"] == "demo")
    assert demo["subscribed"] is True
    assert any(a["id"] == "ping" for a in demo["actions"])

    _send(conn, {"type": "plugin_call", "plugin": "demo", "action": "ping",
                 "params": {}})
    done, seen = _recv_until(conn, "turn_done")
    assert done["ok"] is True
    texts = [e.get("text", "") for e in seen if e.get("type") == "meta_message"]
    assert any("demo.ping" in t for t in texts)
    views = [e for e in seen if e.get("type") == "feed_view"]
    assert views and "pong" in views[0].get("text", "")
    assert views[0].get("kind") == "plugin"
    conn.close()


def test_note_plugin_form_keeps_secrets_off_wire(tmp_path, monkeypatch):
    """plugin_form_status must not echo credential-shaped bodies (login JSON)."""
    from xlii import plugin as plugin_mod
    from xlii.plugin_call import ActionResult

    plug_dir = tmp_path / "plugins"
    plug_dir.mkdir()
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug_dir)

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]

    login_body = json.dumps({
        "handle": "nick.bsky.social",
        "accessJwt": "eyJhbGc." + "x" * 80,
        "refreshJwt": "eyJhbGc." + "y" * 80,
    })

    def status_for(result: ActionResult) -> dict:
        sent.clear()
        server._note_plugin_form("demo", "login", {}, result)
        return next(e for e in sent if e["type"] == "plugin_form_status")

    # Schema fallback: user_text is the raw HTTP body carrying session tokens.
    status = status_for(ActionResult(
        plugin_id="demo", action_id="login", mode="schema",
        code=200, body=login_body, user_text=login_body, model_text="receipt",
    ))
    assert status["ok"] is True
    assert status["text"] == "done"
    assert "eyJ" not in json.dumps(status)
    assert "accessJwt" not in json.dumps(status)

    # Same on the failure path (HTTP error body is user_text too).
    status = status_for(ActionResult(
        plugin_id="demo", action_id="login", mode="raw",
        code=401, body=login_body, user_text=login_body, model_text="receipt",
        error="HTTP 401",
    ))
    assert status["ok"] is False
    assert status["text"] == "failed"
    assert "eyJ" not in json.dumps(status)

    # Non-JSON-shaped secrets still get redacted before the wire.
    token_body = json.dumps({"token": "abc123", "note": "ok"})
    status = status_for(ActionResult(
        plugin_id="demo", action_id="login", mode="raw",
        code=200, body=token_body, user_text=token_body, model_text="receipt",
    ))
    assert "abc123" not in status["text"]
    assert "ok" in status["text"]

    # Plain results pass through untouched.
    status = status_for(ActionResult(
        plugin_id="demo", action_id="login", mode="raw",
        code=200, body="pong", user_text="pong", model_text="receipt",
    ))
    assert status["text"] == "pong"


def test_exec_plugin_call_redacts_before_truncating(tmp_path, monkeypatch):
    """A >12k JSON body must be redacted first — a cut breaks JSON parsing,
    so truncate-then-redact would stream live JWTs into the transcript."""
    from xlii import plugin as plugin_mod
    from xlii.plugin_call import ActionResult

    plug_dir = tmp_path / "plugins"
    plug_dir.mkdir()
    (plug_dir / "demo.md").write_text(
        "---\n"
        "id: demo\n"
        "name: Demo\n"
        "description: test plugin\n"
        "risk: low\n"
        "effect: read-only\n"
        "trust: subscription\n"
        "auth_type: none\n"
        "actions:\n"
        "  - id: login\n"
        "    description: fake login\n"
        "    method: POST\n"
        "    url: https://example.test/login\n"
        "    output: raw\n"
        "---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug_dir)
    monkeypatch.setattr(plugin_mod, "load_subscriptions",
                        lambda xli: ["demo"])

    jwt = "eyJhbGc." + "x" * 80
    big_body = json.dumps({"accessJwt": jwt, "data": "y" * 13000})

    def fake_invoke(plugin_id, raw, action_id, params, **kw):
        return ActionResult(
            plugin_id="demo", action_id="login", mode="raw",
            code=200, body=big_body, user_text=big_body, model_text="receipt",
        )

    monkeypatch.setattr("xlii.plugin_call.invoke_action", fake_invoke)

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]

    assert server._exec_plugin_call("demo", "login", {}) is True
    metas = [e for e in sent if e.get("type") == "meta_message"]
    wire = json.dumps(metas)
    assert jwt not in wire
    assert "eyJhbGc" not in wire
    assert any("truncated" in (m.get("text") or "") for m in metas)


def test_exec_plugin_call_redacts_body_with_stored_suffix(tmp_path, monkeypatch):
    """invoke_action appends 'stored plugin.X' after a vault write; the suffix
    breaks whole-text JSON parsing and must not defeat redaction of the login
    body it rides on."""
    from xlii import plugin as plugin_mod
    from xlii.plugin_call import ActionResult

    plug_dir = tmp_path / "plugins"
    plug_dir.mkdir()
    (plug_dir / "demo.md").write_text(
        "---\n"
        "id: demo\n"
        "name: Demo\n"
        "description: test plugin\n"
        "risk: low\n"
        "effect: read-only\n"
        "trust: subscription\n"
        "auth_type: none\n"
        "actions:\n"
        "  - id: login\n"
        "    description: fake login\n"
        "    method: POST\n"
        "    url: https://example.test/login\n"
        "    output: raw\n"
        "---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug_dir)
    monkeypatch.setattr(plugin_mod, "load_subscriptions",
                        lambda xli: ["demo"])

    jwt = "eyJhbGc." + "x" * 80
    login = json.dumps({"accessJwt": jwt, "handle": "nick.bsky.social"})
    user_text = f"{login}\n\nstored plugin.DEMO_TOKEN"

    def fake_invoke(plugin_id, raw, action_id, params, **kw):
        return ActionResult(
            plugin_id="demo", action_id="login", mode="raw",
            code=200, body=login, user_text=user_text, model_text="receipt",
            stored=["plugin.DEMO_TOKEN"],
        )

    monkeypatch.setattr("xlii.plugin_call.invoke_action", fake_invoke)

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]

    assert server._exec_plugin_call("demo", "login", {}) is True
    metas = [e for e in sent if e.get("type") == "meta_message"]
    wire = json.dumps(metas)
    assert jwt not in wire
    assert "eyJhbGc" not in wire
    # The vault receipt itself (key names, not values) still reaches the user.
    assert "stored plugin.DEMO_TOKEN" in wire


def test_exec_plugin_call_withholds_unparseable_secret_body(tmp_path, monkeypatch):
    """A non-JSON body carrying a live token can't be structurally redacted;
    a secret-flagged payload must fail closed — receipt only, body withheld."""
    from xlii import plugin as plugin_mod
    from xlii.plugin_call import ActionResult

    plug_dir = tmp_path / "plugins"
    plug_dir.mkdir()
    (plug_dir / "demo.md").write_text(
        "---\n"
        "id: demo\n"
        "name: Demo\n"
        "description: test plugin\n"
        "risk: low\n"
        "effect: read-only\n"
        "trust: subscription\n"
        "auth_type: none\n"
        "actions:\n"
        "  - id: login\n"
        "    description: fake login\n"
        "    method: POST\n"
        "    url: https://example.test/login\n"
        "    output: raw\n"
        "---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug_dir)
    monkeypatch.setattr(plugin_mod, "load_subscriptions",
                        lambda xli: ["demo"])

    jwt = "eyJhbGciOiJIUzI1NiJ9." + "x" * 40 + "." + "y" * 40
    raw_body = f"<html>error — echoed session {jwt}</html>"
    user_text = f"{raw_body}\n\nstored plugin.DEMO_TOKEN"

    def fake_invoke(plugin_id, raw, action_id, params, **kw):
        return ActionResult(
            plugin_id="demo", action_id="login", mode="raw",
            code=200, body=raw_body, user_text=user_text, model_text="receipt",
            stored=["plugin.DEMO_TOKEN"],
        )

    monkeypatch.setattr("xlii.plugin_call.invoke_action", fake_invoke)

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]

    assert server._exec_plugin_call("demo", "login", {}) is True
    metas = [e for e in sent if e.get("type") == "meta_message"]
    wire = json.dumps(metas)
    assert jwt not in wire
    assert "eyJhbGc" not in wire
    assert "<html>" not in wire
    # The vault receipt still reaches the user.
    assert "stored plugin.DEMO_TOKEN" in wire


def test_exec_plugin_call_withholds_non_jwt_stored_echo(tmp_path, monkeypatch):
    """A non-JWT API key echoed in HTML must not ride the wire after a vault write."""
    from xlii import plugin as plugin_mod
    from xlii.plugin_call import ActionResult

    plug_dir = tmp_path / "plugins"
    plug_dir.mkdir()
    (plug_dir / "demo.md").write_text(
        "---\n"
        "id: demo\n"
        "name: Demo\n"
        "description: test plugin\n"
        "risk: low\n"
        "effect: read-only\n"
        "trust: subscription\n"
        "auth_type: none\n"
        "actions:\n"
        "  - id: login\n"
        "    description: fake login\n"
        "    method: POST\n"
        "    url: https://example.test/login\n"
        "    output: raw\n"
        "---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug_dir)
    monkeypatch.setattr(plugin_mod, "load_subscriptions",
                        lambda xli: ["demo"])

    secret = "sk-live-not-a-jwt-secret"
    raw_body = f"<html>invalid key {secret}</html>"
    user_text = f"{raw_body}\n\nstored plugin.API_KEY"

    def fake_invoke(plugin_id, raw, action_id, params, **kw):
        return ActionResult(
            plugin_id="demo", action_id="login", mode="raw",
            code=401, body=raw_body, user_text=user_text, model_text="receipt",
            stored=["plugin.API_KEY"],
        )

    monkeypatch.setattr("xlii.plugin_call.invoke_action", fake_invoke)

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]

    assert server._exec_plugin_call("demo", "login", {}) is True
    metas = [e for e in sent if e.get("type") == "meta_message"]
    wire = json.dumps(metas)
    assert secret not in wire
    assert "<html>" not in wire
    assert "stored plugin.API_KEY" in wire


def test_note_plugin_form_withholds_non_jwt_stored_echo(tmp_path):
    from xlii.plugin_call import ActionResult

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]

    secret = "sk-live-not-a-jwt-secret"
    user_text = f"<html>invalid key {secret}</html>\n\nstored plugin.API_KEY"
    server._note_plugin_form("demo", "login", {}, ActionResult(
        plugin_id="demo", action_id="login", mode="raw",
        code=401, body=user_text, user_text=user_text, model_text="receipt",
        stored=["plugin.API_KEY"],
    ))
    status = next(e for e in sent if e["type"] == "plugin_form_status")
    assert secret not in json.dumps(status)
    assert status["text"] == "stored plugin.API_KEY"
    """Duplicate plugin menu clicks must not launch duplicate HTTP actions."""
    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    started = threading.Event()
    release = threading.Event()
    calls: list[tuple[str, str, dict]] = []

    def fake_run(plugin_id, action_id, params):
        calls.append((plugin_id, action_id, params))
        started.set()
        release.wait(timeout=5)
        server._busy.clear()

    server._run_plugin_call = fake_run  # type: ignore[method-assign]

    assert server._start_plugin_call("demo", "ping", {"x": "1"}) is True
    assert server._busy.is_set()
    assert server._start_plugin_call("demo", "ping", {"x": "2"}) is False

    assert started.wait(timeout=5), "plugin worker did not start"
    release.set()
    for _ in range(100):
        if not server._busy.is_set():
            break
        time.sleep(0.01)
    assert not server._busy.is_set()
    assert calls == [("demo", "ping", {"x": "1"})]


def test_plugin_call_allowed_while_background_agent_runs(tmp_path):
    """Plugin invoke is a fetch — agent-running must not block it."""
    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    calls: list[tuple[str, str, dict]] = []

    def fake_run(plugin_id, action_id, params):
        calls.append((plugin_id, action_id, params))
        server._busy.clear()

    server._run_plugin_call = fake_run  # type: ignore[method-assign]
    server._agent_running.set()

    assert server._start_plugin_call("demo", "ping", {}) is True
    assert calls == [("demo", "ping", {})]


def test_exit_shuts_down_and_restores_seams(tmp_path, monkeypatch):
    import xlii.tools as tools_mod
    from xlii import active_session

    sentinel = lambda p: "sentinel"  # noqa: E731
    monkeypatch.setattr(tools_mod, "_confirm", sentinel)
    state = _fake_state(tmp_path)
    port, t = _start_server(state)
    conn = _connect(port)
    _recv_event(conn)
    _recv_event(conn)
    # While the server runs, its confirm bridge owns the hook…
    assert tools_mod._confirm is not sentinel
    assert active_session.active_session() is state
    _send(conn, {"type": "set_posture", "posture": "code"})
    _recv_event(conn)
    _send(conn, {"type": "input", "text": "/exit"})
    end, seen = _recv_until(conn, "session_end")
    assert end.get("reason") == "exit"
    assert "bye" in (end.get("message") or "").lower()
    # Graceful exit may emit meta progress before session_end.
    assert any(e.get("type") == "meta_message" for e in seen) or end.get("message")
    t.join(timeout=10)
    assert not t.is_alive(), "server did not shut down on /exit"
    # …and gives it back on the way out.
    assert tools_mod._confirm is sentinel
    assert active_session.active_session() is not state
    conn.close()


def test_quit_also_emits_session_end(tmp_path):
    state = _fake_state(tmp_path)
    port, t = _start_server(state)
    conn = _connect(port)
    _recv_event(conn)
    _recv_event(conn)
    _send(conn, {"type": "set_posture", "posture": "code"})
    _recv_event(conn)
    _send(conn, {"type": "input", "text": "/quit"})
    end, _ = _recv_until(conn, "session_end")
    assert end.get("reason") == "quit"
    t.join(timeout=10)
    assert not t.is_alive()
    conn.close()


def test_chrome_scratch_injects_switch_door(tmp_path):
    """Home surface always offers switch even on a chat-only pack."""
    from xlii.serve_face import FaceServer
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = _fake_state(tmp_path)
    state.scratch = True
    state.project = SimpleNamespace(
        project_root=tmp_path, name="scratch/home",
        local_only=True, xli_dir=tmp_path,
    )
    state.workbench = BUILTIN_WORKBENCHES["chat"]
    server = FaceServer(boot=SimpleNamespace(state=state))
    chrome = server.chrome_state()
    assert chrome["type"] == "chrome_state"
    assert chrome.get("surface") == "scratch" or chrome.get("project") == ""
    ids = [b.get("id") for b in chrome.get("quick_launch") or []]
    assert "switch" in ids
    assert ids[0] == "switch"


def test_join_project_live_switches(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.scratch = True
    state.project = SimpleNamespace(
        project_root=tmp_path / "home", name="scratch/home",
        local_only=True, xli_dir=tmp_path / "home",
    )
    (tmp_path / "home").mkdir()
    target = SimpleNamespace(
        project_root=tmp_path / "lab", name="lab",
        local_only=False, xli_dir=tmp_path / "lab",
    )
    (tmp_path / "lab").mkdir()
    seen = []

    def fake_switch(ctx, project, **kw):
        seen.append(project.name)
        ctx["state"].project = project
        ctx["state"].scratch = False
        return True

    monkeypatch.setattr(
        "xlii.repl_cmds.switch.switch_to_code_project", fake_switch)
    monkeypatch.setattr(
        "xlii.registry.Registry.load",
        lambda: SimpleNamespace(entries=[]))
    monkeypatch.setattr(
        "xlii.project_resolver.resolve_registered_project",
        lambda name, registry=None: SimpleNamespace(project=target, entry=None),
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.join_project("lab") is True
    assert seen == ["lab"]
    assert state.project.name == "lab"
    assert server.posture == "code"
    types = [e.get("type") for e in sent]
    assert "meta_message" in types
    assert "chrome_state" in types or "mode_state" in types
    texts = [e.get("text") for e in sent if e.get("type") == "meta_message"]
    assert any(t and "lab" in t for t in texts)


def test_join_collection_lands_talk(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.scratch = True
    state.project = SimpleNamespace(
        project_root=tmp_path / "home", name="scratch/home",
        local_only=True, xli_dir=tmp_path / "home", kind=None,
    )
    pile = SimpleNamespace(
        project_root=tmp_path / "italy", name="italy",
        local_only=True, xli_dir=tmp_path / "italy", kind="collection",
    )
    (tmp_path / "italy").mkdir()

    def fake_switch(ctx, project, **kw):
        ctx["state"].project = project
        ctx["state"].scratch = False
        return True

    monkeypatch.setattr(
        "xlii.repl_cmds.switch.switch_to_code_project", fake_switch)
    monkeypatch.setattr(
        "xlii.registry.Registry.load",
        lambda: SimpleNamespace(entries=[]))
    monkeypatch.setattr(
        "xlii.project_resolver.resolve_registered_project",
        lambda name, registry=None: SimpleNamespace(project=pile, entry=None),
    )
    monkeypatch.setattr(
        "xlii.project_paths.is_home_desk_project", lambda p: False)
    monkeypatch.setattr("xlii.workbench.save_active_type", lambda *a, **k: None)
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.posture = "code"
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.join_project("italy") is True
    assert server.posture == "chat"
    texts = [e.get("text") for e in sent if e.get("type") == "meta_message"]
    assert any(t and "talk" in t and "collection" in t for t in texts)


def test_join_persona_project_lands_talk(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    persona = SimpleNamespace(
        project_root=tmp_path / "ixaac", name="chat/ixaac",
        local_only=True, xli_dir=tmp_path / "ixaac", kind=None,
    )
    (tmp_path / "ixaac").mkdir()

    def fake_switch(ctx, project, **kw):
        ctx["state"].project = project
        return True

    monkeypatch.setattr(
        "xlii.repl_cmds.switch.switch_to_code_project", fake_switch)
    monkeypatch.setattr(
        "xlii.registry.Registry.load",
        lambda: SimpleNamespace(entries=[]))
    monkeypatch.setattr(
        "xlii.project_resolver.resolve_registered_project",
        lambda name, registry=None: SimpleNamespace(project=persona, entry=None),
    )
    monkeypatch.setattr(
        "xlii.project_paths.is_home_desk_project", lambda p: False)
    monkeypatch.setattr("xlii.workbench.save_active_type", lambda *a, **k: None)
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.posture = "code"
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.join_project("chat/ixaac") is True
    assert server.posture == "chat"


def test_go_home_leaves_project_without_flipping_posture(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    desk = tmp_path / "scratch-home"
    desk.mkdir()
    home = SimpleNamespace(
        project_root=desk, name="scratch/home",
        local_only=True, xli_dir=desk / ".xlii",
    )
    (desk / ".xlii").mkdir()
    state = _fake_state(tmp_path)
    state.scratch = False
    state.project = SimpleNamespace(
        project_root=tmp_path / "lab", name="lab",
        local_only=False, xli_dir=tmp_path / "lab",
    )
    (tmp_path / "lab").mkdir()
    seen = []

    def fake_switch(ctx, project, **kw):
        seen.append(project.name)
        ctx["state"].project = project
        return True

    monkeypatch.setattr(
        "xlii.cmds.project.scratch._ensure_home_desk", lambda: desk)
    monkeypatch.setattr("xlii.config.ProjectConfig.load", lambda root: home)
    monkeypatch.setattr(
        "xlii.repl_cmds.switch.switch_to_code_project", fake_switch)
    monkeypatch.setattr(
        "xlii.project_paths.is_home_desk_project",
        lambda p: getattr(p, "name", "") == "scratch/home",
    )
    monkeypatch.setattr(
        "xlii.project_paths.scratch_home_roam_cwd", lambda p: tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.posture = "chat"
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.go_home() is True
    assert seen == ["scratch/home"]
    assert server.posture == "chat"
    assert state.scratch is True
    texts = [e.get("text") for e in sent if e.get("type") == "meta_message"]
    assert any(t and "blank slate" in t for t in texts)


def test_go_home_already_there_is_noop(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.scratch = True
    state.project = SimpleNamespace(
        project_root=tmp_path, name="scratch/home",
        local_only=True, xli_dir=tmp_path,
    )
    monkeypatch.setattr(
        "xlii.cmds.project.scratch._ensure_home_desk", lambda: tmp_path)
    monkeypatch.setattr(
        "xlii.config.ProjectConfig.load",
        lambda root: state.project,
    )
    monkeypatch.setattr(
        "xlii.project_paths.is_home_desk_project", lambda p: True)
    called = []
    monkeypatch.setattr(
        "xlii.repl_cmds.switch.switch_to_code_project",
        lambda *a, **k: called.append(1),
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.posture = "code"
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.go_home() is True
    assert called == []
    assert server.posture == "code"


def test_chrome_state_persona_island_is_solo_not_mojo(tmp_path):
    """HUD on chat/fred shows fred, not ixaac, and hides the chat/fred folder pill."""
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.project = SimpleNamespace(
        project_root=tmp_path, name="chat/fred",
        local_only=True, xli_dir=tmp_path, bound_persona="ixaac",
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    chrome = server.chrome_state()
    assert chrome["persona"] == "fred"
    assert chrome["project"] == ""


def test_chrome_state_includes_meter_and_session(tmp_path):
    from xlii.agent_stats import TurnStats
    from xlii.serve_face import FaceServer

    stats = TurnStats(context_tokens=270_000)
    stats.orch.model = "grok-4.3"
    state = _fake_state(tmp_path)
    state.agent.session.last_turn_stats = stats
    state.agent.session.session_tokens = 84_000
    state.agent.session.session_cost = 0.12
    server = FaceServer(boot=SimpleNamespace(state=state))
    chrome = server.chrome_state()
    assert chrome["meter"] == "270K / 1M"
    assert chrome["session"].startswith("sess 84K")


def test_chrome_state_includes_pane_side(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.cfg = SimpleNamespace(tui_panel_side="left", panel_width_pct=50)
    server = FaceServer(boot=SimpleNamespace(state=state))
    chrome = server.chrome_state()
    assert chrome.get("pane_side") == "left"
    assert chrome.get("pane_width_pct") == 50
    state.cfg.tui_panel_side = "right"
    assert server.chrome_state().get("pane_side") == "right"


def test_chrome_state_includes_howto_and_about(tmp_path):
    from xlii.serve_face import FaceServer

    chrome = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path))).chrome_state()
    ids = [r.get("id") for r in (chrome.get("howto") or [])]
    assert "install" in ids and "index" not in ids
    assert chrome.get("about_line")
    assert chrome.get("about_credit") == "Say hello — hello@xlii.computer"
    assert chrome.get("about_taglines")
    assert chrome.get("about_version")
    facts = chrome.get("about_facts") or []
    assert any("tests green" in str(x) for x in facts)
    assert any("modules" in str(x) for x in facts)
    assert "year of the" in (chrome.get("about_signs") or "")


def test_chrome_state_includes_fabric_nodes(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.cfg = SimpleNamespace(fabric_nodes={"node1": {"remote": "box"}, "pi": {}})
    chrome = FaceServer(boot=SimpleNamespace(state=state)).chrome_state()
    assert chrome.get("fabric_nodes") == ["node1", "pi"]


def test_chrome_state_includes_desk_slots(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    chrome = server.chrome_state()
    assert chrome.get("slot_a") == "stream"
    assert chrome.get("slot_b") == ""
    ids = {r.get("id") for r in (chrome.get("slot_catalog") or [])}
    assert "stream" in ids and "home" in ids


def test_chrome_state_includes_journal_when_recording(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    assert server.chrome_state().get("journal") in ("", None)
    state.journal = SimpleNamespace(is_recording=lambda: True)
    assert server.chrome_state().get("journal") == "on"
    state.journal = SimpleNamespace(is_recording=lambda: False)
    assert server.chrome_state().get("journal") in ("", None)


def test_chrome_state_includes_recent_desks(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    monkeypatch.setattr(
        "xlii.recent_desks.list_recent",
        lambda: [{"name": "iXaac-lab", "path": "/tmp/lab", "kind": "lab",
                  "label": "iXaac-lab"}],
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    recent = server.chrome_state().get("recent") or []
    assert recent and recent[0]["label"] == "iXaac-lab"
    assert recent[0]["kind"] == "lab"


def test_chrome_state_includes_browser_when_session_live(tmp_path, monkeypatch):
    from xlii.agent_browser import BrowserSnapshot
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    assert server.chrome_state().get("browser") in ("", None)
    monkeypatch.setattr(
        "xlii.agent_browser.session_peek",
        lambda: BrowserSnapshot(ok=True, url="https://example.com/x",
                                headless=False, pid=9),
    )
    chrome = server.chrome_state()
    assert chrome.get("browser") == "window"
    assert "example.com" in (chrome.get("browser_url") or "")
    monkeypatch.setattr(
        "xlii.agent_browser.session_peek",
        lambda: BrowserSnapshot(ok=True, url="https://example.com/y",
                                headless=True, pid=9),
    )
    assert server.chrome_state().get("browser") == "hidden"


def test_chrome_state_includes_job_flags(tmp_path):
    from xlii.jobs import JobRegistry
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.job_registry = JobRegistry()
    state.job_registry.adopt("task", "build")
    server = FaceServer(boot=SimpleNamespace(state=state))
    chrome = server.chrome_state()
    assert chrome.get("jobs_active") == 1
    assert chrome.get("jobs_unseen") == 0
    pills = chrome.get("jobs") or []
    assert any(p.get("kind") == "task" and p.get("name") == "build" for p in pills)


def test_chrome_state_includes_cwd(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    chrome = server.chrome_state()
    assert chrome.get("cwd")
    assert "proj" in chrome["cwd"] or str(tmp_path) in chrome["cwd"]


def test_chat_idle_routes_desk_nav_and_bang_to_dispatcher(tmp_path, monkeypatch):
    """[?] used to swallow `cd`/`ls`/`!` as persona talk — the live cwd died."""
    import xlii.repl as repl_mod
    from xlii.serve_face import FaceServer

    seen: list[str] = []

    def fake_process(state, user_input):
        seen.append(user_input)
        return None, True

    monkeypatch.setattr(repl_mod, "process_repl_input", fake_process)
    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server._run_chat_input("cd Downloads") is True
    assert server._run_chat_input("ls") is True
    assert server._run_chat_input("!ls -la") is True
    assert seen == ["cd Downloads", "ls", "!ls -la"]
    kinds = [ev.get("kind") for ev in sent if ev.get("type") == "user_turn"]
    assert kinds == ["shell", "shell", "shell"]


def test_chrome_state_includes_chat_tier(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.agent = SimpleNamespace(
        session=SimpleNamespace(chat_tier="fast"),
        cfg=SimpleNamespace(orchestrator_model="m"),
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    chrome = server.chrome_state()
    assert chrome.get("chat_tier") == "fast"
    state.agent.session.chat_tier = None
    assert server.chrome_state().get("chat_tier") == "off"


def test_chrome_state_includes_face_prefs(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    state.cfg = SimpleNamespace(face_skin="mojo", face_fkeys=False, face_bold=True)
    server = FaceServer(boot=SimpleNamespace(state=state))
    chrome = server.chrome_state()
    assert chrome.get("face_skin") == "mojo"
    assert chrome.get("face_fkeys") is False
    assert chrome.get("face_bold") is True


def test_chrome_state_includes_trust_ladder(tmp_path):
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    assert server.chrome_state().get("trust") == "safe"
    state.yolo = True
    assert server.chrome_state().get("trust") == "yolo"
    state.freeball = True
    assert server.chrome_state().get("trust") == "freeball"


def test_open_browser_opens_url(tmp_path, monkeypatch):
    """Face browser door prefers headed agent Chromium; OS webbrowser is fallback."""
    from xlii.serve_face import FaceServer
    from xlii.agent_browser import BrowserSnapshot

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    opened = []
    sessions = []

    def _fake_open(url="", headless=True):
        sessions.append({"url": url, "headless": headless})
        return BrowserSnapshot(
            ok=True, url=url or "about:blank", title="t",
            text="ok", pid=42, headless=headless,
        )

    monkeypatch.setattr(
        "webbrowser.open",
        lambda url, new=0: opened.append((url, new)) or True,
    )

    # Prefer agent path when Chromium is "found"
    monkeypatch.setattr("xlii.agent_browser.find_chromium", lambda: "/bin/chromium")
    monkeypatch.setattr("xlii.agent_browser.open_session", _fake_open)
    assert server.open_browser("https://example.com/x") is True
    assert opened == []  # did not fall through to OS browser
    assert sessions[-1]["headless"] is False  # face door = visible window
    assert any("browser →" in str(e.get("text", "")).lower() for e in sent)
    assert any("example.com" in str(e.get("text", "")) for e in sent)
    assert any("window" in str(e.get("text", "")).lower() for e in sent)

    # OS fallback when no Chromium — prefer new window
    sent.clear()
    monkeypatch.setattr("xlii.agent_browser.find_chromium", lambda: None)
    assert server.open_browser("https://example.com/y") is True
    assert opened == [("https://example.com/y", 1)]


def test_research_tool_kg_opens_wiki(tmp_path):
    from xlii.serve_face import FaceServer
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = _fake_state(tmp_path)
    # kg/canvas doors live on the chat pack (research is not a peer pack)
    state.workbench = BUILTIN_WORKBENCHES["chat"]
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.open_research_tool("kg") is True
    assert any(e.get("type") == "pane_focus" and e.get("pane") == "wiki" for e in sent)
    assert any("kg door" in str(e.get("text", "")).lower() for e in sent)


def test_save_screenshot_writes_png(tmp_path, monkeypatch):
    import base64

    from xlii.serve_face import FaceServer

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
    )
    desk = tmp_path / "Desktop"
    desk.mkdir()
    monkeypatch.setattr("xlii.project_paths.user_home", lambda: tmp_path)
    sent = []
    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    server.send = sent.append
    assert server.save_screenshot(base64.b64encode(png).decode("ascii"))
    shots = list(desk.glob("xlii-*.png"))
    assert len(shots) == 1 and shots[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert any("screenshot →" in str(e.get("text", "")) for e in sent)


def test_save_screenshot_rejects_garbage(tmp_path):
    from xlii.serve_face import FaceServer

    sent = []
    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    server.send = sent.append
    assert server.save_screenshot("not-a-png") is False
    assert any("not an image" in str(e.get("text", "")) for e in sent)


def test_save_screenshot_rejects_svg(tmp_path, monkeypatch):
    import base64

    from xlii.serve_face import FaceServer

    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"></svg>'
    desk = tmp_path / "Desktop"
    desk.mkdir()
    monkeypatch.setattr("xlii.project_paths.user_home", lambda: tmp_path)
    sent = []
    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    server.send = sent.append
    assert server.save_screenshot(base64.b64encode(svg).decode("ascii"), kind="svg") is False
    assert list(desk.glob("xlii-*.*")) == []
    assert any("not an image" in str(e.get("text", "")) for e in sent)


def test_save_screenshot_grabs_window_when_empty(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f8b0000000049454e44ae426082"
    )
    desk = tmp_path / "Desktop"
    desk.mkdir()
    monkeypatch.setattr("xlii.project_paths.user_home", lambda: tmp_path)

    def fake_grab(dest):
        dest.write_bytes(png)
        return True

    monkeypatch.setattr("xlii.window_shot.grab_face_window", fake_grab)
    sent = []
    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    server.send = sent.append
    assert server.save_screenshot("") is True
    shots = list(desk.glob("xlii-*.png"))
    assert len(shots) == 1 and shots[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert any("screenshot →" in str(e.get("text", "")) for e in sent)


def test_save_screenshot_empty_reports_grab_miss(tmp_path, monkeypatch):
    from xlii.serve_face import FaceServer

    desk = tmp_path / "Desktop"
    desk.mkdir()
    monkeypatch.setattr("xlii.project_paths.user_home", lambda: tmp_path)
    monkeypatch.setattr("xlii.window_shot.grab_face_window", lambda dest: False)
    sent = []
    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    server.send = sent.append
    assert server.save_screenshot("") is False
    assert any("could not grab" in str(e.get("text", "")) for e in sent)


def test_research_tool_canvas_opens_canvas(tmp_path):
    from xlii.serve_face import FaceServer
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = _fake_state(tmp_path)
    state.workbench = BUILTIN_WORKBENCHES["chat"]
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.open_research_tool("canvas") is True
    assert any(
        e.get("type") == "pane_focus" and e.get("pane") == "canvas" for e in sent
    )
    assert not any("spatial" in str(e.get("text", "")).lower() for e in sent)


def test_face_prefill_project_switch_joins(tmp_path, monkeypatch):
    """Projects pane Open must join live — not leave a dead /project slash seed."""
    from xlii.face_panes import _FaceInputSink
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    called = []
    server.join_project = lambda n: called.append(n) or True  # type: ignore
    _FaceInputSink(server).prefill("/project switch my-app")
    assert called == ["my-app"]
    called.clear()
    server.join_project_at = lambda p: called.append(p) or True  # type: ignore
    lab = tmp_path / "lab"
    lab.mkdir()
    _FaceInputSink(server).prefill(f"/project switch @{lab}")
    assert called == [str(lab)]


def test_face_prefill_project_files_binds(tmp_path):
    from xlii.face_panes import _FaceInputSink
    from xlii.serve_face import FaceServer

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    called = []
    server.bind_project_files = lambda a: called.append(a) or True  # type: ignore
    _FaceInputSink(server).prefill("/project files sftp://appbox/srv/apps/foo")
    assert called == ["sftp://appbox/srv/apps/foo"]


def test_plugin_run_opens_form_for_required_params(tmp_path, monkeypatch):
    """Pane Run with holes opens the closed form — never seeds password=."""
    from xlii import plugin as plugin_mod
    from xlii.face_panes import _FaceInputSink
    from xlii.serve_face import FaceServer

    plug_dir = tmp_path / "plugins"
    plug_dir.mkdir()
    (plug_dir / "demo.md").write_text(
        "---\n"
        "id: demo\n"
        "effect: read-only\n"
        "trust: subscription\n"
        "actions:\n"
        "  - id: geocode\n"
        "    method: GET\n"
        "    url: https://example.test/geo\n"
        "    params:\n"
        "      name: {required: true, description: \"city\"}\n"
        "    output: raw\n"
        "---\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(plugin_mod, "PLUGINS_DIR", plug_dir)

    state = _fake_state(tmp_path)
    server = FaceServer(boot=SimpleNamespace(state=state))
    started = []
    server._exec_plugin_call = (  # type: ignore[method-assign]
        lambda pid, aid, params: started.append((pid, aid, params)) or True
    )
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    opened = []
    server.deck.open_plugin_form = (  # type: ignore[method-assign]
        lambda pid, aid, seed=None: opened.append((pid, aid, dict(seed or {}))) or True
    )

    _FaceInputSink(server).prefill("__plugin_call__:demo:geocode")
    assert started == []
    assert opened == [("demo", "geocode", {})]
    assert not [e for e in sent if e.get("type") == "prefill"]
    notes = [e.get("text", "") for e in sent if e.get("type") == "meta_message"]
    assert any("form" in t.lower() for t in notes)

    sent.clear()
    assert server._try_face_plugin_call("/plugin call demo.geocode name=London") is True
    assert started == [("demo", "geocode", {"name": "London"})]


def test_prune_projects_does_not_block_the_reader(tmp_path, monkeypatch):
    """confirm.ask on the websocket reader deadlocks Open/Prune. Must return now."""
    import threading
    import time

    import xlii.registry as R
    from xlii.serve_face import FaceServer

    monkeypatch.setattr(R, "REGISTRY_FILE", tmp_path / "projects.json")
    R.Registry(entries=[
        R.RegistryEntry(
            path=str(tmp_path / "gone"), collection_id="",
            name="ghost", created_at="2026-01-01",
        )
    ]).save()

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    entered = threading.Event()
    release = threading.Event()

    def ask(prompt):
        entered.set()
        release.wait(timeout=2)
        return "y"

    server.confirm.ask = ask  # type: ignore[method-assign]
    t0 = time.monotonic()
    assert server.prune_projects() is True
    assert time.monotonic() - t0 < 0.5
    assert entered.wait(1.0)
    release.set()


def test_home_deck_includes_projects_even_on_chat_pack(tmp_path):
    """join pill is pane:projects — Home must mount it even if pack is chat."""
    from xlii.face_panes import FaceDeck
    from xlii.serve_face import FaceServer
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = _fake_state(tmp_path)
    state.scratch = True
    state.project = SimpleNamespace(
        project_root=tmp_path, name="scratch/home",
        local_only=True, xli_dir=tmp_path,
    )
    state.workbench = BUILTIN_WORKBENCHES["chat"]
    server = FaceServer(boot=SimpleNamespace(state=state))
    deck = FaceDeck(server)
    assert "projects" in deck._workbench_panes()
    snap = deck.snapshot()
    ids = [p.id for p in snap.panes]
    assert "projects" in ids


def test_open_pane_wire_focuses_projects(tmp_path):
    from xlii.serve_face import FaceServer
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = _fake_state(tmp_path)
    state.scratch = True
    state.project = SimpleNamespace(
        project_root=tmp_path, name="scratch/home",
        local_only=True, xli_dir=tmp_path,
    )
    state.workbench = BUILTIN_WORKBENCHES["chat"]
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.deck.open_pane("projects") is True
    types = [e.get("type") for e in sent]
    assert "pane_deck" in types
    assert "pane_focus" in types
    deck_ev = next(e for e in sent if e.get("type") == "pane_deck")
    assert any(p.get("id") == "projects" for p in deck_ev.get("panes") or [])


def test_face_panel_host_opens_home(tmp_path):
    """/panel home uses the panel-host seam on the face (not only Textual)."""
    from xlii.face_panes import FacePanelHost
    from xlii.serve_face import FaceServer
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = _fake_state(tmp_path)
    state.scratch = True
    state.project = SimpleNamespace(
        project_root=tmp_path, name="scratch/home",
        local_only=True, xli_dir=tmp_path,
    )
    state.workbench = BUILTIN_WORKBENCHES["home"]
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    host = FacePanelHost(server)
    assert host.open_doorway("home") is True
    assert any(e.get("type") == "pane_focus" and e.get("pane") == "home" for e in sent)


def test_face_panel_intercept_without_host(tmp_path):
    """Face /panel works even when the TUI panel host is not installed."""
    from xlii.serve_face import FaceServer
    from xlii.tui import panels
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = _fake_state(tmp_path)
    state.scratch = True
    state.project = SimpleNamespace(
        project_root=tmp_path, name="scratch/home",
        local_only=True, xli_dir=tmp_path,
    )
    state.workbench = BUILTIN_WORKBENCHES["home"]
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    panels.set_panel_host(None)
    assert server._try_face_panel_command("/panel home") is True
    assert any(e.get("type") == "pane_focus" and e.get("pane") == "home" for e in sent)
    sent.clear()
    assert server._try_face_panel_command("/home") is True
    assert any(e.get("type") == "pane_focus" and e.get("pane") == "home" for e in sent)
    assert server._try_face_panel_command("/home/user/notes.md") is False
    assert any("panel · home" in str(e.get("text", "")) for e in sent)
    sent.clear()
    assert server._try_face_panel_command("/panel off") is True
    assert any(e.get("type") == "pane_focus" and e.get("pane") == "" for e in sent)


def test_set_workbench_switches_pack(tmp_path):
    from xlii.serve_face import FaceServer
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = _fake_state(tmp_path)
    state.workbench = BUILTIN_WORKBENCHES["chat"]
    (tmp_path / "workbench.json").write_text('{"active":"chat"}\n')
    state.project = SimpleNamespace(
        project_root=tmp_path, name="proj", local_only=True, xli_dir=tmp_path,
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.set_workbench("code") is True
    assert state.workbench.name == "code"
    assert any(e.get("type") == "chrome_state" for e in sent)
    pane_cats = [e for e in sent if e.get("type") == "pane_catalog"]
    assert pane_cats
    ids = {r.get("id") for r in pane_cats[-1].get("panes") or []}
    assert "explorer" in ids and "git" in ids
    assert "wiki" not in ids and "plugins" not in ids


def test_set_workbench_evicts_off_pack_slots(tmp_path):
    from xlii.serve_face import FaceServer
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = _fake_state(tmp_path)
    state.workbench = BUILTIN_WORKBENCHES["code"]
    (tmp_path / "workbench.json").write_text('{"active":"code"}\n')
    state.project = SimpleNamespace(
        project_root=tmp_path, name="proj", local_only=True, xli_dir=tmp_path,
    )
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.deck.set_slot("a", "explorer")
    sent.clear()
    assert server.set_workbench("chat") is True
    a, b, _ = server.deck.slot_tuple()
    assert "explorer" not in (a, b)
    assert "stream" in (a, b)
    cats = [e for e in sent if e.get("type") == "pane_catalog"]
    assert cats
    ids = {r.get("id") for r in cats[-1].get("panes") or []}
    assert "wiki" in ids and "plugins" in ids
    assert "explorer" not in ids


def test_home_pack_prefer_does_not_stomp_workbench_switch(tmp_path):
    """Workbench menu must stick — prefer_home used to reset every chrome_state."""
    from xlii.serve_face import FaceServer
    from xlii.workbench import BUILTIN_WORKBENCHES

    state = _fake_state(tmp_path)
    state.scratch = True
    state.project = SimpleNamespace(
        project_root=tmp_path, name="scratch/home",
        local_only=True, xli_dir=tmp_path,
    )
    state.workbench = BUILTIN_WORKBENCHES["home"]
    (tmp_path / "workbench.json").write_text('{"active":"home"}\n')
    server = FaceServer(boot=SimpleNamespace(state=state))
    sent = []
    server.send = sent.append  # type: ignore[method-assign]
    assert server.set_workbench("code") is True
    assert state.workbench.name == "code"
    # chrome refresh must not force home again
    server.chrome_state()
    assert state.workbench.name == "code"
    server._apply_workbench_chrome()
    assert state.workbench.name == "code"


def test_posture_flip_rejected_mid_turn(tmp_path):
    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    server._busy.set()
    assert server.submit("hello") is False


def test_exit_queues_while_hard_busy_or_agent_running(tmp_path):
    """OS close / title-bar exit must not be refused by the busy gates."""
    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    server._busy.set()
    assert server.submit("/exit") is True
    assert server._inputs.get_nowait() == "/exit"

    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    server._agent_running.set()
    assert server.submit("hello") is False
    assert server.submit("/exit") is True
    assert server._allowed_while_agent("/exit")
    assert server._allowed_while_agent("/quit")


def test_request_session_end_works_while_agent_busy(tmp_path, monkeypatch):
    """Accept-loop /exit calls session end even mid-agent — cancel + bye."""
    monkeypatch.setattr(
        "xlii.exit_sequence.run_graceful_exit",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "xlii.exit_sequence.end_code_session",
        lambda *a, **k: None,
    )
    server = FaceServer(boot=SimpleNamespace(state=_fake_state(tmp_path)))
    sent: list[dict] = []
    server.send = sent.append  # type: ignore[method-assign]
    server._busy.set()
    server._agent_running.set()
    cancelled = {"n": 0}
    server.cancel = lambda: cancelled.__setitem__("n", cancelled["n"] + 1)  # type: ignore

    server._request_session_end("exit")
    assert cancelled["n"] == 1
    assert any(e.get("type") == "session_end" and e.get("reason") == "exit"
               for e in sent)
    assert server._shutdown.is_set()


def test_busy_port_is_a_dry_message_not_a_traceback(tmp_path, capsys):
    """A pinned port that's taken fails fast (BEFORE the session boot) with
    the pass---port-0 hint — the owner hit a raw OSError traceback here."""
    taken = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    taken.bind(("127.0.0.1", 0))
    taken.listen(1)
    port = taken.getsockname()[1]
    try:
        rc = serve_face(tmp_path, port=port,
                        boot=SimpleNamespace(state=_fake_state(tmp_path)))
    finally:
        taken.close()
    assert rc == 1
    err = capsys.readouterr().err
    assert "cannot bind" in err and "--port 0" in err


def test_cmd_serve_face_defaults_to_ephemeral_port(monkeypatch, tmp_path):
    """`xlii serve --face` must not inherit serve's memorable 8042 (it
    collides with the browser-TUI server); an explicit --port still pins."""
    import xlii.cmds.serve_face as cmd_mod
    from xlii.cmds.serve_web import _DEFAULT_PORT

    captured = {}

    def fake_serve_face(root, **kw):
        captured.update(kw)
        return 0

    monkeypatch.setattr(cmd_mod, "serve_face", fake_serve_face)
    monkeypatch.setattr(
        "xlii.cmds.sessions.resolve._resolve_project_target", lambda t: tmp_path)
    import xlii.config as config_mod
    monkeypatch.setattr(config_mod.ProjectConfig, "load",
                        staticmethod(lambda p: SimpleNamespace(name="p")))

    args = SimpleNamespace(host="127.0.0.1", port=_DEFAULT_PORT, token=None,
                           handshake=False, yolo=False, force=False,
                           workspace=None)
    assert cmd_mod.cmd_serve_face(args) == 0
    assert captured["port"] == 0

    args.port = 9001  # explicit non-default pins
    cmd_mod.cmd_serve_face(args)
    assert captured["port"] == 9001


def test_boot_refusal_reason_reaches_stderr(tmp_path, monkeypatch, capsys):
    """The 'it just says refused' bug: boot runs on a REAL stderr console, so
    the refusal explanation (nested-session panel, launch gate, credentials)
    lands in the terminal instead of the not-yet-connected wire console."""
    import xlii.session_boot as boot_mod

    def fake_boot(root, *, console, force=False, **kw):
        console.print("nested session blocked: exit the other session first")
        assert force is True  # --force threads through to the gate
        return boot_mod.BootOutcome("refused")

    monkeypatch.setattr(boot_mod, "build_code_session", fake_boot)
    monkeypatch.setattr(
        "xlii.panic_mail.check_on_wake",
        lambda: SimpleNamespace(status="skipped"),
    )
    rc = serve_face(tmp_path, port=0, force=True)
    assert rc == 1
    err = capsys.readouterr().err
    assert "nested session blocked" in err
    assert "could not open a session" in err
    assert "refused" in err


def test_handshake_boot_refusal_emits_error_json(tmp_path, monkeypatch, capsys):
    """Tauri only reads stdout. A boot refusal must be one JSON error line,
    not a silent exit that leaves the window on 'sidecar starting'."""
    import xlii.session_boot as boot_mod

    def fake_boot(root, *, console, force=False, **kw):
        return boot_mod.BootOutcome("refused", reason="vault sealed")

    monkeypatch.setattr(boot_mod, "build_code_session", fake_boot)
    monkeypatch.setattr(
        "xlii.panic_mail.check_on_wake",
        lambda: SimpleNamespace(status="skipped"),
    )
    rc = serve_face(tmp_path, port=0, handshake=True, force=True)
    assert rc == 1
    captured = capsys.readouterr()
    line = captured.out.strip().splitlines()[-1]
    payload = json.loads(line)
    assert payload["error"]
    assert "vault sealed" in payload["error"]
    assert "port" not in payload
    assert "could not open a session" in captured.err
