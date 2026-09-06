"""Desk /remote-control sitting — occupancy store, not Face JS."""

from xlii.occupancy_store import load_live
from xlii.repl_cmds import remote_lab as rl


class _Con:
    def __init__(self):
        self.lines = []

    def print(self, *a, **k):
        self.lines.append(" ".join(str(x) for x in a))


def test_sitting_open_notice_is_whoami_plus_dollar():
    from xlii.repl_cmds.remote_lab import sitting_open_notice

    text = sitting_open_notice(
        node="node1", persona="mojo", jid="node1@home.xlii-remote.com",
    )
    assert "node1" in text and "mojo" in text
    assert "node1@home.xlii-remote.com" in text
    assert "$" in text
    assert "desk" not in text.lower() or "this glass" in text


def test_open_for_device(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setattr("xlii.tailnet.peer_online", lambda name: True)
    con = _Con()
    rl.h_remote_control("/remote-control open --for phone", {"console": con})
    occ = load_live()
    assert occ.remote_lab.open
    assert occ.remote_lab.device == "phone"
    blob = " ".join(con.lines).lower()
    assert "sitting open for phone" in blob
    assert "online" in blob


def test_open_glass_tier(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    con = _Con()
    rl.h_remote_control(
        "/remote-control open --glass --for phone", {"console": con},
    )
    occ = load_live()
    assert occ.remote_lab.open
    assert occ.remote_lab.tier == "glass"
    assert occ.remote_lab.device == "phone"
    blob = " ".join(con.lines).lower()
    assert "tailnet glass" in blob


def test_open_lock_drop(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    con = _Con()
    ctx = {"console": con}
    rl.h_remote_control("/remote-control open", ctx)
    occ = load_live()
    assert occ.remote_lab.open and not occ.remote_lab.locked
    blob = " ".join(con.lines).lower()
    assert "sitting open" in blob
    assert "mojo" in blob
    assert "what is this project" in blob
    assert "?what is this project" not in blob
    assert "5 min" in blob
    assert "/xsu" in blob
    rl.h_remote_control("/remote-control lock", ctx)
    assert load_live().remote_lab.locked
    rl.h_remote_control("/remote-control drop", ctx)
    occ = load_live()
    assert not occ.remote_lab.open
    assert "dropped" in " ".join(con.lines).lower() or "gone" in " ".join(con.lines).lower()


def test_mute_me_rewrites_allowlist(tmp_path, monkeypatch):
    daemon_toml = tmp_path / "daemon.toml"
    daemon_toml.write_text(
        '[account]\njid = "throne@example.test"\n\n'
        '[whitelist]\nallowed_jids = ["throne@example.test", "me@example.test"]\n'
    )
    monkeypatch.setattr(rl, "DEFAULT_CONFIG_PATH", daemon_toml)
    con = _Con()
    rl.h_mute("/mute me", {"console": con})
    text = daemon_toml.read_text()
    assert "me@example.test" not in text
    assert "throne@example.test" in text
