"""`xlii artifact video` — async kickoff → status → file (media M4, faked wire)."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

import xlii.cmds.artifact as art
from xlii.media_client import (
    VIDEO_DONE,
    VIDEO_FAILED,
    VIDEO_PENDING,
    parse_video_status_json,
)


@pytest.fixture(autouse=True)
def _no_real_key(monkeypatch):
    monkeypatch.setattr(art, "_api_key_from_config", lambda: "k-test")


def _args(tmp_path, prompt=None, request_id=None, wait=0, model=None):
    return SimpleNamespace(prompt=prompt, request_id=request_id,
                           path=str(tmp_path), wait=wait, model=model)


def _record(tmp_path, rid):
    p = tmp_path / ".xlii" / "artifacts" / f"video-{rid}.json"
    return json.loads(p.read_text()) if p.exists() else None


# --- payload normalization (the test seam) --------------------------------------


def test_parse_status_pending_done_failed():
    assert parse_video_status_json({"status": "queued"}) == (VIDEO_PENDING, None)
    assert parse_video_status_json({"status": "failed"}) == (VIDEO_FAILED, None)
    status, data = parse_video_status_json(
        {"status": "done", "data": [{"b64_json": base64.b64encode(b"MP4!").decode()}]})
    assert status == VIDEO_DONE and data == b"MP4!"


def test_parse_status_done_without_payload_is_loud():
    with pytest.raises(RuntimeError, match="url-only"):
        parse_video_status_json({"status": "done"})


# --- kickoff ---------------------------------------------------------------------


def test_start_persists_resumable_record(tmp_path, monkeypatch):
    from tests.helpers import FakeConsole
    from xlii.media_client import estimate_video_cost

    fake = FakeConsole()
    monkeypatch.setattr(art, "console", fake)
    monkeypatch.setattr("xlii.media_client.generate_video_start",
                        lambda prompt, **kw: "req-123")
    rc = art.cmd_artifact_video(_args(tmp_path, prompt="a fox running"))
    assert rc == 0
    rec = _record(tmp_path, "req-123")
    assert rec["prompt"] == "a fox running"
    assert rec["status"] == "pending"
    # M4 cost line — kickoff prints an "(approx)" estimate (principle 1).
    assert "(approx)" in estimate_video_cost()
    assert "(approx)" in fake.text



def test_start_failure_is_clean(tmp_path, monkeypatch):
    def _boom(prompt, **kw):
        raise RuntimeError("api down")

    monkeypatch.setattr("xlii.media_client.generate_video_start", _boom)
    assert art.cmd_artifact_video(_args(tmp_path, prompt="x")) == 1


# --- status / polling ---------------------------------------------------------------


def test_status_ready_writes_mp4_and_marks_done(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.media_client.generate_video_start",
                        lambda prompt, **kw: "req-9")
    art.cmd_artifact_video(_args(tmp_path, prompt="x"))

    monkeypatch.setattr("xlii.media_client.generate_video_status",
                        lambda rid, **kw: (VIDEO_DONE, b"MP4-BYTES"))
    rc = art.cmd_artifact_video(_args(tmp_path, prompt="status", request_id="req-9"))
    assert rc == 0
    rec = _record(tmp_path, "req-9")
    assert rec["status"] == "done"
    assert (tmp_path / rec["artifact"]).read_bytes() == b"MP4-BYTES"


def test_status_pending_returns_zero_and_stays_resumable(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.media_client.generate_video_status",
                        lambda rid, **kw: (VIDEO_PENDING, None))
    rc = art.cmd_artifact_video(_args(tmp_path, prompt="status", request_id="req-w"))
    assert rc == 0                             # pending async ≠ failure
    assert _record(tmp_path, "req-w")["status"] == "pending"


def test_status_failed_reports(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.media_client.generate_video_status",
                        lambda rid, **kw: (VIDEO_FAILED, None))
    assert art.cmd_artifact_video(
        _args(tmp_path, prompt="status", request_id="req-f")) == 1


def test_status_rejects_request_id_path_segments(tmp_path, monkeypatch):
    def _status(*a, **kw):
        raise AssertionError("unsafe request ids must not reach the API")

    monkeypatch.setattr("xlii.media_client.generate_video_status", _status)

    rc = art.cmd_artifact_video(
        _args(tmp_path, prompt="status", request_id="../../escape")
    )

    assert rc == 1
    assert not (tmp_path / ".xlii").exists()


def test_start_rejects_unsafe_request_id_before_writing(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.media_client.generate_video_start",
                        lambda prompt, **kw: "../escape")

    rc = art.cmd_artifact_video(_args(tmp_path, prompt="x"))

    assert rc == 1
    assert not (tmp_path / ".xlii").exists()


def test_wait_timeout_is_clean_and_resumable(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.media_client.generate_video_start",
                        lambda prompt, **kw: "req-slow")
    monkeypatch.setattr("xlii.media_client.generate_video_status",
                        lambda rid, **kw: (VIDEO_PENDING, None))
    monkeypatch.setattr("time.sleep", lambda s: None)
    rc = art.cmd_artifact_video(_args(tmp_path, prompt="x", wait=1))
    assert rc == 0
    assert _record(tmp_path, "req-slow")["status"] == "pending"


def test_cli_parser_accepts_video_forms():
    from xlii.cli import build_parser

    parser = build_parser()
    args = parser.parse_args(["artifact", "video", "a fox", "--wait", "30"])
    assert args.prompt == "a fox" and args.wait == 30
    args = parser.parse_args(["artifact", "video", "status", "req-1"])
    assert args.prompt == "status" and args.request_id == "req-1"
