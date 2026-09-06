"""Face remote-control ingest — sitting gate, HTTP door, no live XMPP."""

from __future__ import annotations

import json
import socket
import time
from types import SimpleNamespace

from xlii.face_remote import (
    handle_http_remote_turn,
    ingest_face_turn,
    sitting_allows_remote,
)
from xlii.occupancy_store import mutate
from xlii.tool_schemas import WORKER_ROLES, dispatch_subagent_schema, worker_tool_schemas


def test_sitting_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    assert sitting_allows_remote() is False
    mutate(lambda o: o.open_remote_lab(now=1_000.0), now=1_000.0)
    assert sitting_allows_remote(now=1_010.0) is True
    mutate(lambda o: o.drop_remote_lab(), now=1_011.0)
    assert sitting_allows_remote(now=1_012.0) is False


def test_http_refuses_closed_sitting(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    status, body, ctype = handle_http_remote_turn(
        method="POST", path="/remote-turn?token=abc", headers={},
        body=b'{"text":"hi"}', token="abc", server=SimpleNamespace(),
    )
    assert status == 403
    assert ctype == "application/json"
    assert json.loads(body)["ok"] is False


def _phone_peer():
    from xlii.tailnet import TailnetPeer

    return TailnetPeer(
        node_name="phone",
        dns_name="phone.tailnet.ts.net",
        stable_id="nPHONE",
        addrs=("100.64.0.2",),
        user="you@github",
        tags=(),
        online=True,
    )


def test_http_tailnet_allowlisted_no_token(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setattr(
        "xlii.face_remote.load_tailnet_allowlist", lambda path=None: ["phone"],
    )
    monkeypatch.setattr("xlii.tailnet.whois", lambda ip, port: _phone_peer())
    monkeypatch.setattr("xlii.face_remote.ingest_face_turn", lambda s, t: f"echo:{t}")
    now = time.time()
    mutate(lambda o: o.open_remote_lab(now=now), now=now)
    status, body, _ = handle_http_remote_turn(
        method="POST", path="/remote-turn", headers={},
        body=b'{"text":"hi"}', token="secret", server=SimpleNamespace(),
        peer=("100.64.0.2", 9), on_tailnet_bind=True,
    )
    assert status == 200
    assert json.loads(body)["reply"] == "echo:hi"


def test_http_tailnet_unknown_device(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setattr(
        "xlii.face_remote.load_tailnet_allowlist", lambda path=None: ["tablet"],
    )
    monkeypatch.setattr("xlii.tailnet.whois", lambda ip, port: _phone_peer())
    now = time.time()
    mutate(lambda o: o.open_remote_lab(now=now), now=now)
    status, body, _ = handle_http_remote_turn(
        method="POST", path="/remote-turn", headers={},
        body=b'{"text":"hi"}', token="", server=SimpleNamespace(),
        peer=("100.64.0.2", 9), on_tailnet_bind=True,
    )
    assert status == 403
    assert b"allowlisted" in body


def test_http_tailnet_no_whois(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setattr(
        "xlii.face_remote.load_tailnet_allowlist", lambda path=None: ["phone"],
    )
    monkeypatch.setattr("xlii.tailnet.whois", lambda ip, port: None)
    now = time.time()
    mutate(lambda o: o.open_remote_lab(now=now), now=now)
    status, body, _ = handle_http_remote_turn(
        method="POST", path="/remote-turn", headers={},
        body=b'{"text":"hi"}', token="", server=SimpleNamespace(),
        peer=("100.64.0.2", 9), on_tailnet_bind=True,
    )
    assert status == 403
    assert b"not a tailnet peer" in body


def test_http_tailnet_closed_sitting(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setattr(
        "xlii.face_remote.load_tailnet_allowlist", lambda path=None: ["phone"],
    )
    monkeypatch.setattr("xlii.tailnet.whois", lambda ip, port: _phone_peer())
    status, body, _ = handle_http_remote_turn(
        method="POST", path="/remote-turn", headers={},
        body=b'{"text":"hi"}', token="", server=SimpleNamespace(),
        peer=("100.64.0.2", 9), on_tailnet_bind=True,
    )
    assert status == 403
    assert b"sitting closed" in body


def test_http_tailnet_device_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setattr(
        "xlii.face_remote.load_tailnet_allowlist", lambda path=None: ["phone"],
    )
    monkeypatch.setattr("xlii.tailnet.whois", lambda ip, port: _phone_peer())
    now = time.time()
    mutate(lambda o: o.open_remote_lab(now=now, device="tablet"), now=now)
    status, body, _ = handle_http_remote_turn(
        method="POST", path="/remote-turn", headers={},
        body=b'{"text":"hi"}', token="", server=SimpleNamespace(),
        peer=("100.64.0.2", 9), on_tailnet_bind=True,
    )
    assert status == 403
    assert b"bound to tablet" in body


def test_load_tailnet_allowlist(tmp_path, monkeypatch):
    from xlii.face_remote import load_tailnet_allowlist

    toml = tmp_path / "face.toml"
    toml.write_text('[tailnet]\nallowed_devices = ["phone", "nPHONE"]\n')
    monkeypatch.setenv("XLII_FACE_TOML", str(toml))
    assert load_tailnet_allowlist() == ["phone", "nPHONE"]


def test_tailnet_door_follows_sitting(tmp_path, monkeypatch):
    from xlii.serve_face.http import TailnetDoor
    from xlii.tailnet import TailnetSelf

    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    st = TailnetSelf(
        up=True, ipv4="127.0.0.1", addrs=("127.0.0.1",),
        dns_name="throne", node_name="throne",
    )
    monkeypatch.setattr("xlii.tailnet.self_status", lambda: st)
    door = TailnetDoor(token="t", server=SimpleNamespace(), port=0)
    try:
        door.sync()
        assert door.bound is None
        now = time.time()
        mutate(lambda o: o.open_remote_lab(now=now), now=now)
        door.sync()
        assert door.bound is not None
        host, port = door.bound
        sock = socket.create_connection((host, port), timeout=1)
        sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
        got = sock.recv(64)
        sock.close()
        assert got.startswith(b"HTTP/1.1 404")
        mutate(lambda o: o.drop_remote_lab(), now=now + 1)
        door.sync()
        assert door.bound is None
        refused = False
        for _ in range(20):
            try:
                leftover = socket.create_connection((host, port), timeout=0.2)
                leftover.close()
                time.sleep(0.05)
            except OSError:
                refused = True
                break
        assert refused, "door still accepting"
    finally:
        door.close()


def test_http_tailnet_device_match(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setattr(
        "xlii.face_remote.load_tailnet_allowlist", lambda path=None: ["phone"],
    )
    monkeypatch.setattr("xlii.tailnet.whois", lambda ip, port: _phone_peer())
    monkeypatch.setattr("xlii.face_remote.ingest_face_turn", lambda s, t: "ok")
    now = time.time()
    mutate(lambda o: o.open_remote_lab(now=now, device="phone"), now=now)
    status, body, _ = handle_http_remote_turn(
        method="POST", path="/remote-turn", headers={},
        body=b'{"text":"hi"}', token="", server=SimpleNamespace(),
        peer=("100.64.0.2", 9), on_tailnet_bind=True,
    )
    assert status == 200
    assert json.loads(body)["ok"] is True


def test_http_bad_token(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    now = time.time()
    mutate(lambda o: o.open_remote_lab(now=now), now=now)
    status, body, _ = handle_http_remote_turn(
        method="POST", path="/remote-turn?token=nope", headers={},
        body=b'{"text":"hi"}', token="abc", server=SimpleNamespace(),
    )
    assert status == 403
    assert b"bad token" in body


def test_delayed_stanza_is_never_ingested():
    from types import SimpleNamespace
    from xml.etree.ElementTree import fromstring

    from xlii.face_remote import drop_reason_live_only, stanza_delay_stamp

    xml = fromstring(
        "<message><delay xmlns='urn:xmpp:delay' "
        "stamp='2026-08-22T10:00:00Z'/><body>hi</body></message>"
    )
    stamp = stanza_delay_stamp(SimpleNamespace(xml=xml))
    assert stamp is not None
    assert drop_reason_live_only(
        delay_stamp=stamp, live_since=stamp + 3600, now=stamp + 3600,
    ) == "offline-delay"


def test_connect_drain_drops_unstamped_burst():
    from xlii.face_remote import LIVE_GRACE_S, drop_reason_live_only

    live = 1_000.0
    assert drop_reason_live_only(
        delay_stamp=None, live_since=live, now=live + 0.5,
    ) == "connect-drain"
    assert drop_reason_live_only(
        delay_stamp=None, live_since=live, now=live + LIVE_GRACE_S + 0.1,
    ) == ""


def test_bind_face_daemon_is_live_only():
    from xlii.face_remote import bind_face_daemon

    xmpp = SimpleNamespace()
    bind_face_daemon(xmpp, SimpleNamespace())
    assert xmpp.live_only is True
    assert callable(xmpp.turn_handler)


def test_reconcile_does_not_stop_bridge_when_sitting_closes(tmp_path, monkeypatch):
    from xlii import face_remote as fr

    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    stopped = []

    class _Alive:
        def is_alive(self):
            return True

    monkeypatch.setattr(fr, "_stop_bridge_locked", lambda: stopped.append(True))
    monkeypatch.setattr(fr, "_start_bridge_locked", lambda s: None)
    with fr._bridge_lock:
        fr._bridge = _Alive()
    try:
        fr.reconcile_bridge(SimpleNamespace())
    finally:
        with fr._bridge_lock:
            fr._bridge = None
    assert stopped == []


def test_ingest_closed_sitting_message(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    reply = ingest_face_turn(SimpleNamespace(state=None), "hello")
    assert "sitting closed" in reply


def test_bridge_config_refuses_daemon_at(tmp_path, monkeypatch):
    from xlii.face_remote import is_node_daemon_jid, load_bridge_config

    monkeypatch.setattr("xlii.daemon_gate.DEFAULT_CONFIG_PATH", tmp_path / "no-daemon.toml")
    assert is_node_daemon_jid("daemon@home.xlii-remote.com")
    assert not is_node_daemon_jid("face@home.xlii-remote.com")
    toml = tmp_path / "face.toml"
    toml.write_text(
        '[remote]\njid = "daemon@home.xlii-remote.com"\n'
        'allowed_jids = ["me@home.xlii-remote.com"]\n'
    )
    monkeypatch.setenv("XLII_FACE_TOML", str(toml))
    cfg, err = load_bridge_config()
    assert cfg is None
    assert "daemon@" in (err or "")


def test_bridge_skips_when_this_box_has_a_daemon(tmp_path, monkeypatch):
    from xlii.face_remote import load_bridge_config

    daemon = tmp_path / "daemon.toml"
    daemon.write_text('[daemon]\njid = "node1@home.xlii-remote.com"\n')
    monkeypatch.setattr("xlii.daemon_gate.DEFAULT_CONFIG_PATH", daemon)
    toml = tmp_path / "face.toml"
    toml.write_text(
        '[remote]\njid = "node1@home.xlii-remote.com"\n'
        'allowed_jids = ["me@home.xlii-remote.com"]\n'
    )
    monkeypatch.setenv("XLII_FACE_TOML", str(toml))
    cfg, err = load_bridge_config()
    assert cfg is None
    assert "daemon" in (err or "").lower()


def test_bridge_config_reads_face_remote(tmp_path, monkeypatch):
    from xlii.face_remote import load_bridge_config

    monkeypatch.setattr("xlii.daemon_gate.DEFAULT_CONFIG_PATH", tmp_path / "no-daemon.toml")
    toml = tmp_path / "face.toml"
    toml.write_text(
        '[remote]\n'
        'jid = "face@home.xlii-remote.com"\n'
        'password_env = "XMPP_FACE_PASSWORD"\n'
        'allowed_jids = ["me@home.xlii-remote.com"]\n'
    )
    monkeypatch.setenv("XLII_FACE_TOML", str(toml))
    cfg, err = load_bridge_config()
    assert err == ""
    assert cfg is not None
    assert cfg.jid == "face@home.xlii-remote.com"
    assert cfg.password_env == "XMPP_FACE_PASSWORD"
    assert cfg.blind_trust is False


def test_bridge_blind_trust_opt_in(tmp_path, monkeypatch):
    from xlii.face_remote import load_bridge_config

    monkeypatch.setattr("xlii.daemon_gate.DEFAULT_CONFIG_PATH", tmp_path / "no-daemon.toml")
    toml = tmp_path / "face.toml"
    toml.write_text(
        '[remote]\n'
        'jid = "face@home.xlii-remote.com"\n'
        'allowed_jids = ["me@home.xlii-remote.com"]\n'
        'blind_trust = true\n'
    )
    monkeypatch.setenv("XLII_FACE_TOML", str(toml))
    cfg, err = load_bridge_config()
    assert err == ""
    assert cfg is not None
    assert cfg.blind_trust is True


def test_lab_role_is_a_writer_palette():
    assert "lab" in WORKER_ROLES
    names = {s["function"]["name"] for s in worker_tool_schemas(writer=True, role="lab")}
    assert "write_file" in names
    assert "bash" in names
    enum = dispatch_subagent_schema()["function"]["parameters"]["properties"]["role"]["enum"]
    assert "lab" in enum


def test_chat_dispatch_ignores_occupancy_sitting(tmp_path, monkeypatch):
    """K1: hire, not the phone sitting, gates dispatch_subagent on chat."""
    from tests.helpers import make_agent, make_msg

    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    now = time.time()
    mutate(lambda o: o.open_remote_lab(now=now), now=now)
    agent = make_agent(tmp_path)
    agent.session.conversational = True
    agent.session.hire = "none"
    captured = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)
        return (make_msg("ok", None), None, False)

    agent._stream_orchestrator_iteration = fake
    agent.run_turn("hello")
    names = {s["function"]["name"] for s in captured["schemas"]}
    assert "dispatch_subagent" not in names
    assert "write_file" not in names
