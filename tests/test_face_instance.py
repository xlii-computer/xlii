"""One face per environment — lock, liveness, resume/replace."""

from __future__ import annotations

import os

import pytest

from xlii import face_instance as fi


@pytest.fixture()
def lock_dir(tmp_path, monkeypatch):
    d = tmp_path / "locks"
    d.mkdir()
    monkeypatch.setenv("XLII_FACE_LOCK_DIR", str(d))
    yield d


def test_claim_and_read(lock_dir):
    rec = fi.claim(port=9876, token="abc", host="127.0.0.1")
    assert rec.pid == os.getpid()
    assert rec.port == 9876
    got = fi.read_record()
    assert got is not None
    assert got.token == "abc"
    assert got.url.endswith("token=abc")
    fi.release()
    assert fi.read_record() is None


def test_stale_record_cleared_when_pid_dead(lock_dir):
    dead = fi.FaceRecord(
        pid=99999999, port=1, token="x", host="127.0.0.1", started_at=0.0,
    )
    fi.write_record(dead)
    assert fi.discover_live() is None
    assert not (lock_dir / "face.json").exists() or fi.read_record() is None


def test_prepare_launch_ok_when_empty(lock_dir):
    action, live = fi.prepare_launch(prefer=None, interactive=False)
    assert action == "ok"
    assert live is None


def test_prepare_launch_resume_noninteractive(lock_dir, monkeypatch):
    rec = fi.FaceRecord(pid=4242, port=12345, token="tok", host="127.0.0.1")
    monkeypatch.setattr(fi, "discover_live", lambda: rec)
    action, live = fi.prepare_launch(prefer=None, interactive=False)
    assert action == "resume"
    assert live is not None and live.port == 12345

    stopped = []
    monkeypatch.setattr(fi, "stop_face", lambda *a, **k: stopped.append(True))
    action2, live2 = fi.prepare_launch(prefer="replace", interactive=False)
    assert action2 == "ok" and live2 is None
    assert stopped


def test_prepare_serve_refuses_other_live(lock_dir, monkeypatch):
    other = fi.FaceRecord(pid=1, port=9, token="t", host="127.0.0.1")
    monkeypatch.setattr(fi, "discover_live", lambda: other)
    ok, msg = fi.prepare_serve(replace=False)
    assert ok is False
    assert "already running" in msg
    monkeypatch.setattr(fi, "stop_face", lambda *a, **k: None)
    ok2, msg2 = fi.prepare_serve(replace=True)
    assert ok2 is True


def test_resolve_policy_env(lock_dir, monkeypatch):
    rec = fi.FaceRecord(pid=1, port=9, token="t")
    monkeypatch.setattr(fi, "discover_live", lambda: rec)
    monkeypatch.setenv("XLII_FACE_INSTANCE", "replace")
    assert fi.resolve_policy(interactive=False) == "replace"
    monkeypatch.setenv("XLII_FACE_INSTANCE", "resume")
    assert fi.resolve_policy(interactive=False) == "resume"
    # legacy synonym
    monkeypatch.setenv("XLII_FACE_INSTANCE", "attach")
    assert fi.resolve_policy(interactive=False) == "resume"
