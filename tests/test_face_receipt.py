"""Face crash/idle receipt — daemon reads a file, not a live session."""

from __future__ import annotations

from xlii.face_receipt import (
    format_face_receipt,
    read_face_receipt,
    write_face_receipt,
)


def test_write_read_round_trip(tmp_path):
    write_face_receipt(
        reason="idle",
        project="calc",
        grant="abc123",
        state_dir=tmp_path,
        now=1000.0,
    )
    row = read_face_receipt(tmp_path)
    assert row["reason"] == "idle"
    assert row["project"] == "calc"
    assert row["grant"] == "abc123"
    assert row["at"] == 1000.0


def test_format_mentions_desk_and_age(tmp_path):
    write_face_receipt(
        reason="crash", project="lab", grant="g1",
        extra="TimeoutError", state_dir=tmp_path, now=10.0,
    )
    text = format_face_receipt(read_face_receipt(tmp_path), now=40.0)
    assert "reason=crash" in text
    assert "desk=lab" in text
    assert "grant=g1" in text
    assert "TimeoutError" in text
    assert "30s ago" in text


def test_missing_receipt_is_honest(tmp_path):
    assert read_face_receipt(tmp_path) == {}
    assert "no face receipt" in format_face_receipt({}, now=1.0)
