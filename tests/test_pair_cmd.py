"""``xlii pair`` CLI — mint/show without the [daemon] extra."""

from __future__ import annotations

import json
from argparse import Namespace

from xlii.cli import build_parser
from xlii.cmds.pair import cmd_pair
from xlii.pairing_gate import PairingStore


def _cfg(tmp_path):
    p = tmp_path / "daemon.toml"
    p.write_text(
        '[daemon]\n'
        'jid = "throne@desk.tailnet"\n'
        '[whitelist]\n'
        'allowed_jids = ["me@phone.tailnet"]\n'
    )
    return p


def test_parser_has_pair_and_daemon_pair():
    p = build_parser()
    args = p.parse_args(["pair", "--no-wait", "--rail", "daemon"])
    assert args.command == "pair" and args.no_wait
    args2 = p.parse_args(["daemon", "pair", "--no-wait"])
    assert args2.daemon_action == "pair"


def test_cmd_pair_mints_window_without_plaintext_in_store(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XLII_PAIRING_PATH", str(tmp_path / "xlii-pairing.json"))
    fp = "ab" * 32
    rc = cmd_pair(Namespace(
        rail="daemon", invite="", ttl=600, no_wait=True,
        config=str(_cfg(tmp_path)),
        jid="throne@desk.tailnet", sid=812345, fingerprint=fp,
    ))
    assert rc == 0
    out = capsys.readouterr().out
    assert "pair code:" in out
    assert "xmpp:throne@desk.tailnet?omemo-sid-812345=" in out
    assert fp in out
    store = PairingStore(tmp_path / "xlii-pairing.json")
    window = store.get("daemon")
    assert window is not None and not window.consumed_by
    blob = (tmp_path / "xlii-pairing.json").read_text()
    # The grouped code appears on stdout; the store keeps only the hash.
    code_line = [ln for ln in out.splitlines() if ln.startswith("pair code:")][0]
    grouped = code_line.split()[2]
    assert grouped not in blob
    assert json.loads(blob)["windows"]["daemon"]["code_hash"] == window.code_hash


def test_cmd_pair_invite_refuses_bad_jid(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XLII_PAIRING_PATH", str(tmp_path / "p.json"))
    rc = cmd_pair(Namespace(
        rail="daemon", invite="not-a-jid", ttl=600, no_wait=True,
        config=str(_cfg(tmp_path)),
        jid="a@b", sid=1, fingerprint="ab" * 32,
    ))
    assert rc == 2
    assert "bare JID" in capsys.readouterr().err


def test_cmd_pair_refuses_unwired_rails(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XLII_PAIRING_PATH", str(tmp_path / "p.json"))
    rc = cmd_pair(Namespace(
        rail="notify", invite="", ttl=600, no_wait=True,
        config=str(_cfg(tmp_path)),
        jid="a@b", sid=1, fingerprint="ab" * 32,
    ))
    assert rc == 2
    assert "not wired yet" in capsys.readouterr().err


def test_cmd_pair_refuses_missing_state_without_overrides(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XLII_PAIRING_PATH", str(tmp_path / "p.json"))
    rc = cmd_pair(Namespace(
        rail="daemon", invite="", ttl=600, no_wait=True,
        config=str(_cfg(tmp_path)),
        jid="", sid=0, fingerprint="",
    ))
    assert rc == 3
    assert "run the rail once first" in capsys.readouterr().err
