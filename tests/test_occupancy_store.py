"""Live occupancy file — runtime dir, reboot-hostile, Face↔daemon."""

import fcntl
import os

from xlii.glass import face_may_accept, rider_agent_allowed
from xlii.occupancy import Occupancy
from xlii.occupancy_store import (
    apply_face_idle,
    glass_wire,
    load_live,
    mutate,
    occupancy_path,
    save_live,
)


def test_path_override(tmp_path, monkeypatch):
    p = tmp_path / "occ.json"
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(p))
    assert occupancy_path() == p


def test_round_trip_remote_lab_device(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    occ = Occupancy()
    occ.open_remote_lab(now=50.0, source="face", device="phone", tier="glass")
    save_live(occ)
    got = load_live(now=50.0)
    assert got.remote_lab.open is True
    assert got.remote_lab.device == "phone"
    assert got.remote_lab.source == "face"
    assert got.remote_lab.tier == "glass"
    assert got.remote_lab.id == occ.remote_lab.id


def test_round_trip_mouth(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    occ = Occupancy()
    occ.take_mouth("me")
    occ.lock_face()
    save_live(occ, face_last_input_at=10.0)
    got = load_live(now=10.0)
    assert got.mouth == "me"
    assert got.face.locked is True


def test_corrupt_file_defaults(tmp_path, monkeypatch):
    p = tmp_path / "occ.json"
    p.write_text("not json")
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(p))
    occ = load_live()
    assert occ.mouth == "desk"
    assert not occ.face.locked


def test_corrupt_field_types_defaults(tmp_path, monkeypatch):
    p = tmp_path / "occ.json"
    p.write_text('{"mouth":"desk","face":{"fail_count":"abc"},"remote_lab":{}}')
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(p))
    occ = load_live()
    assert occ.mouth == "desk"
    assert occ.face.fail_count == 0

    p.write_text('{"mouth":"desk","face":{},"remote_lab":{"opened_at":"bad"}}')
    occ = load_live()
    assert occ.remote_lab.opened_at == 0.0


def test_mutate_and_glass_wire(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    mutate(lambda o: o.lock_face())
    occ = load_live()
    assert glass_wire(occ) == "locked"
    occ.face.black = True
    assert glass_wire(occ) == "black"


def test_face_idle_locks(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setenv("XLII_FACE_TOML", str(tmp_path / "face.toml"))
    (tmp_path / "face.toml").write_text("[glass]\nidle_s = 30\n")
    occ = Occupancy()
    assert apply_face_idle(occ, last_input_at=0, now=100) is False
    assert apply_face_idle(occ, last_input_at=1, now=40) is True
    assert occ.face.locked


def test_rmw_cycles_hold_an_exclusive_flock(tmp_path, monkeypatch):
    """mutate serializes read-modify-write via flock — Face and daemon share this file."""
    occ_path = tmp_path / "occ.json"
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(occ_path))
    seen = {}
    from xlii import occupancy_store

    orig_load = occupancy_store._load_bundle_unlocked

    def load_probe(*, now=None):
        lock = occ_path.with_name(occ_path.name + ".lock")
        fd = os.open(lock, os.O_RDWR)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                seen["held"] = True
            else:  # pragma: no cover
                fcntl.flock(fd, fcntl.LOCK_UN)
                seen["held"] = False
        finally:
            os.close(fd)
        return orig_load(now=now)

    monkeypatch.setattr(occupancy_store, "_load_bundle_unlocked", load_probe)
    mutate(lambda o: o.take_mouth("me"))
    held = seen.pop("held")
    assert held is True


def test_daemon_mouth_not_clobbered_after_rider_turn(tmp_path, monkeypatch):
    """Once daemon holds mouth=me, Face cannot accept arbitrary input or revert it."""
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    allowed, _ = rider_agent_allowed(rider=True, persona="mojo", now=10.0)
    assert allowed
    assert load_live(now=10.0).mouth == "me"

    ok, reason = face_may_accept("hello", now=10.0)
    assert not ok
    assert "mouth" in reason
    assert load_live(now=10.0).mouth == "me"
