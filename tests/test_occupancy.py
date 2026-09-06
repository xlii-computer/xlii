"""Vector O — occupancy kernel (mouth, Face lock, remote-lab sitting)."""

import inspect
import textwrap

from xlii.occupancy import (
    LOCK_VERBS,
    Occupancy,
    default_occupancy,
    load_glass_config,
)


def test_desk_default_accepts_all_input():
    occ = Occupancy()
    assert occ.mouth == "desk"
    assert occ.face_accepts_input("hello")
    assert occ.face_accepts_input("/status")
    assert occ.allowed_face_verbs() == frozenset()
    assert not occ.remote_lab_allows_dollar(now=100.0)


def test_phone_takes_mouth_only_lock_unlock():
    occ = Occupancy()
    occ.take_mouth("me")
    assert occ.mouth == "me"
    assert not occ.face_accepts_input("hello")
    assert not occ.face_accepts_input("/status")
    assert occ.face_accepts_input("/lock")
    assert occ.face_accepts_input("/unlock")
    assert occ.face_accepts_input("unlock")
    assert occ.allowed_face_verbs() == LOCK_VERBS


def test_desk_take_mouth_restores_without_phone_secret():
    occ = Occupancy()
    occ.take_mouth("me")
    assert not occ.face_accepts_input("hello")
    occ.take_mouth("desk")
    assert occ.face_accepts_input("hello")
    assert occ.face_accepts_input("/status")


def test_face_locked_only_lock_unlock():
    occ = Occupancy()
    occ.lock_face()
    assert occ.face.locked
    assert not occ.face_accepts_input("/status")
    assert occ.face_accepts_input("/unlock")
    assert occ.allowed_face_verbs() == LOCK_VERBS


def test_unlock_face_clears_lock():
    occ = Occupancy()
    occ.lock_face()
    occ.unlock_face()
    assert not occ.face.locked
    assert occ.face_accepts_input("/status")


def test_face_fail_count_independent():
    occ = Occupancy()
    assert occ.face.fail_count == 0
    occ.note_face_unlock_fail()
    occ.note_face_unlock_fail()
    assert occ.face.fail_count == 2
    occ.unlock_face()
    assert occ.face.fail_count == 2


def test_reboot_fresh_occupancy():
    occ = Occupancy()
    occ.take_mouth("me")
    occ.lock_face()
    occ.open_remote_lab(now=1.0)
    fresh = Occupancy()
    assert fresh.mouth == "desk"
    assert not fresh.face.locked
    assert not fresh.remote_lab.open


def test_remote_lab_requires_open_for_dollar():
    occ = Occupancy()
    assert not occ.remote_lab_allows_dollar(now=0.0)
    occ.open_remote_lab(now=100.0)
    assert occ.remote_lab_allows_dollar(now=100.0)
    assert occ.remote_lab_allows_dollar(now=200.0)


def test_remote_lab_lock_refuses_dollar():
    occ = Occupancy()
    occ.open_remote_lab(now=100.0)
    occ.lock_remote_lab()
    assert occ.remote_lab.open
    assert occ.remote_lab.locked
    assert not occ.remote_lab_allows_dollar(now=100.0)


def test_drop_remote_lab_closes_sitting():
    occ = Occupancy()
    occ.open_remote_lab(now=100.0)
    occ.drop_remote_lab()
    assert not occ.remote_lab.open
    assert not occ.remote_lab.locked
    assert not occ.remote_lab_allows_dollar(now=100.0)


def test_remote_lab_device_bind():
    occ = Occupancy()
    occ.open_remote_lab(now=100.0, device="phone")
    assert occ.remote_lab.device == "phone"
    occ.drop_remote_lab()
    assert occ.remote_lab.device == ""


def test_remote_lab_glass_tier_and_id():
    occ = Occupancy()
    occ.open_remote_lab(now=100.0, device="phone", tier="glass")
    assert occ.remote_lab.tier == "glass"
    assert len(occ.remote_lab.id) == 16
    first = occ.remote_lab.id
    occ.drop_remote_lab()
    assert occ.remote_lab.tier == "door"
    assert occ.remote_lab.id == ""
    occ.open_remote_lab(now=101.0, tier="glass")
    assert occ.remote_lab.id != first


def test_remote_lab_idle_autolock_via_tick():
    occ = Occupancy()
    occ.remote_lab.idle_s = 1800.0
    occ.open_remote_lab(now=0.0)
    assert occ.remote_lab_allows_dollar(now=0.0)
    occ.tick(now=1800.0)
    assert occ.remote_lab.open
    assert occ.remote_lab.locked
    assert not occ.remote_lab_allows_dollar(now=1800.0)


def test_remote_lab_agent_activity_resets_idle():
    occ = Occupancy()
    occ.remote_lab.idle_s = 1800.0
    occ.open_remote_lab(now=0.0)
    occ.record_remote_lab_agent(now=1700.0)
    occ.tick(now=1800.0)
    assert not occ.remote_lab.locked
    assert occ.remote_lab_allows_dollar(now=1800.0)
    occ.tick(now=3500.0)
    assert occ.remote_lab.locked


def test_open_remote_lab_has_no_who_parameter():
    sig = inspect.signature(Occupancy.open_remote_lab)
    assert "who" not in sig.parameters
    assert "now" in sig.parameters


def test_remote_lab_default_idle_is_five_minutes():
    from xlii.occupancy import REMOTE_LAB_IDLE_S

    occ = Occupancy()
    assert REMOTE_LAB_IDLE_S == 300.0
    assert occ.remote_lab.idle_s == 300.0
    occ.open_remote_lab(now=0.0, source="phone")
    assert occ.remote_lab.source == "phone"
    occ.tick(now=299.0)
    assert not occ.remote_lab.locked
    occ.tick(now=300.0)
    assert occ.remote_lab.locked


def test_load_glass_config_missing_file(tmp_path, monkeypatch):
    missing = tmp_path / "missing.toml"
    monkeypatch.setenv("XLII_FACE_TOML", str(missing))
    lock = load_glass_config()
    assert lock.level == "home"
    assert not lock.black
    assert not lock.locked


def test_load_glass_config_round_trip(tmp_path, monkeypatch):
    cfg = tmp_path / "face.toml"
    cfg.write_text(
        textwrap.dedent(
            """
            [glass]
            level = "cafe"
            black = true
            idle_s = 120
            remote_lab_idle_s = 900
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("XLII_FACE_TOML", str(cfg))
    lock = load_glass_config()
    assert lock.level == "cafe"
    assert lock.black is True
    assert not lock.locked
    assert lock.fail_count == 0

    occ = default_occupancy()
    assert occ.face.level == "cafe"
    assert occ.face.black is True
    assert not occ.face.locked
    assert occ.remote_lab.idle_s == 900.0


def test_load_glass_config_corrupt_toml(tmp_path, monkeypatch):
    bad = tmp_path / "face.toml"
    bad.write_text("not valid [[[", encoding="utf-8")
    monkeypatch.setenv("XLII_FACE_TOML", str(bad))
    lock = load_glass_config()
    assert lock.level == "home"
    assert not lock.black


def test_no_daemon_or_serve_face_imports():
    import ast

    from xlii import occupancy as mod

    tree = ast.parse(inspect.getsource(mod))
    banned = {"xlii.daemon", "xlii.serve_face", "daemon_gate"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in banned
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in banned
