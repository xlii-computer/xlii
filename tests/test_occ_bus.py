"""One me: occupancy claims lock sibling glasses, not the spoken-to limb."""

from __future__ import annotations

from xlii.glass import apply_remote_claim, apply_remote_release, rider_agent_allowed
from xlii.occ_bus import encode_claim, parse_occ
from xlii.occupancy_store import load_live


def test_occ_envelope_is_not_a_farm_ad():
    body = encode_claim("acer")
    op, data = parse_occ(body)
    assert op == "claim"
    assert data["node"] == "acer"
    from xlii.farm_muc import parse_farm_body

    assert parse_farm_body(body) is None


def test_remote_claim_locks_sibling(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    apply_remote_claim("acer", self_node="throne")
    occ = load_live()
    assert occ.mouth == "me"
    assert occ.via == "acer"


def test_remote_claim_skips_the_spoken_to_limb(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    apply_remote_claim("acer", self_node="acer")
    occ = load_live()
    assert occ.mouth == "desk"
    assert occ.via == ""


def test_rider_with_mojo_claims_this_limb(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    sent = []
    monkeypatch.setattr("xlii.occ_bus._sender", sent.append)
    allowed, kind = rider_agent_allowed(
        rider=True, persona="mojo", now=10, node="acer",
    )
    assert allowed and kind == "mojo"
    occ = load_live()
    assert occ.mouth == "me"
    assert occ.via == "acer"
    assert sent and "acer" in sent[0]


def test_release_unlocks_mirror(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    apply_remote_claim("acer", self_node="throne")
    apply_remote_release("acer", self_node="throne")
    occ = load_live()
    assert occ.mouth == "desk"
    assert occ.via == ""


def test_occ_stanza_ignores_spoofed_json_node(tmp_path, monkeypatch):
    from xlii.farm_xmpp import ingest_occ_stanza
    from xlii.occ_bus import encode_claim, encode_release

    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    spoof = {
        "from": "jobs@conf.home.xlii-remote.com/node1",
        "body": encode_claim("throne"),
    }
    assert ingest_occ_stanza(spoof, self_node="throne") is True
    occ = load_live()
    assert occ.mouth == "desk"
    assert occ.via == ""

    real = {
        "from": "jobs@conf.home.xlii-remote.com/node1",
        "body": encode_claim("node1"),
    }
    assert ingest_occ_stanza(real, self_node="throne") is True
    occ = load_live()
    assert occ.mouth == "me"
    assert occ.via == "node1"

    release = {
        "from": "jobs@conf.home.xlii-remote.com/node1",
        "body": encode_release("acer"),
    }
    assert ingest_occ_stanza(release, self_node="throne") is True
    occ = load_live()
    assert occ.via == "node1"  # spoofed release did not unlock

    release_ok = {
        "from": "jobs@conf.home.xlii-remote.com/node1",
        "body": encode_release("node1"),
    }
    assert ingest_occ_stanza(release_ok, self_node="throne") is True
    occ = load_live()
    assert occ.mouth == "desk"
    assert occ.via == ""


def test_occ_stanza_non_occ_body_is_not_consumed():
    from xlii.farm_xmpp import ingest_occ_stanza

    stanza = {"from": "jobs@conf/node1", "body": "hello"}
    assert ingest_occ_stanza(stanza, self_node="throne") is False
