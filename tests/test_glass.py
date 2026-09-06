"""Face glass proofs and rider $ gate."""

from xlii.glass import face_may_accept, lock_face, rider_agent_allowed, unlock_face
from xlii.occupancy_store import load_live, mutate


def test_lock_unlock_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setenv("XLII_FACE_TOML", str(tmp_path / "face.toml"))
    (tmp_path / "face.toml").write_text("[glass]\nlevel = \"home\"\n")
    monkeypatch.delenv("XLII_DAEMON_TOTP_SECRET", raising=False)
    lock_face()
    assert load_live().face.locked
    ok, reason = unlock_face(code="")
    assert ok and reason == "ok"
    assert not load_live().face.locked
    assert load_live().mouth == "desk"


def test_cafe_totp_fail_does_not_touch_daemon_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setenv("XLII_FACE_TOML", str(tmp_path / "face.toml"))
    (tmp_path / "face.toml").write_text("[glass]\nlevel = \"cafe\"\n")
    from xlii import totp

    secret = totp.generate_secret()
    monkeypatch.setenv("XLII_DAEMON_TOTP_SECRET", secret)
    mutate(lambda o: o.take_mouth("me"))
    lock_face()
    ok, reason = unlock_face(code="000000", now=1_700_000_000)
    assert not ok
    assert reason == "bad totp"
    occ = load_live()
    assert occ.face.fail_count == 1
    assert occ.face.locked
    assert occ.mouth == "me"
    from xlii.daemon_gate import ElevationGate

    gate = ElevationGate(secret)
    assert gate.is_locked() is False


def test_unlock_face_reclaims_mouth_from_phone(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    monkeypatch.setenv("XLII_FACE_TOML", str(tmp_path / "face.toml"))
    (tmp_path / "face.toml").write_text("[glass]\nlevel = \"home\"\n")
    monkeypatch.delenv("XLII_DAEMON_TOTP_SECRET", raising=False)
    mutate(lambda o: o.take_mouth("me"))
    lock_face()
    ok, reason = unlock_face(code="")
    assert ok and reason == "ok"
    assert load_live().mouth == "desk"
    assert not load_live().face.locked


def test_face_may_accept_when_phone_has_mouth(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    mutate(lambda o: o.take_mouth("me"))
    ok, reason = face_may_accept("hello")
    assert not ok
    assert "mouth" in reason
    ok, _ = face_may_accept("/unlock")
    assert ok


def test_rider_without_sitting_mojo_ok_lab_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    allowed, kind = rider_agent_allowed(rider=True, persona="mojo", now=10)
    assert allowed and kind == "mojo"
    assert load_live().mouth == "me"
    mutate(lambda o: o.take_mouth("desk"))
    allowed, kind = rider_agent_allowed(rider=True, persona="", now=10)
    assert not allowed
    assert "sitting" in kind
    assert load_live().mouth == "desk"


def test_rejected_rider_dollar_does_not_steal_mouth(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    assert load_live().mouth == "desk"
    allowed, kind = rider_agent_allowed(rider=True, persona="", now=10)
    assert not allowed and "sitting" in kind
    assert load_live().mouth == "desk"
    ok, reason = face_may_accept("hello")
    assert ok and not reason


def test_rider_dollar_when_sitting_open(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    mutate(lambda o: o.open_remote_lab(now=100.0), now=100.0)
    allowed, kind = rider_agent_allowed(rider=True, persona="mojo", now=100.0)
    assert allowed and kind == "dollar"
