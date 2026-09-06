"""Voice-in: the xAI /v1/stt client (`xlii/stt.py`) + the ask-side resolver.

All offline — the HTTP transport is injected, so the multipart contract
(Bearer auth, `file` field, transcript in `"text"`) and the resolver semantics
(a voice note IS the message) are pinned with no network and no audio model.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from xlii import stt
from xlii.cmds.sessions.ask import _resolve_voice
from xlii.media_in import DEFAULT_MEDIA_PROMPT


# --------------------------------------------------------------------------- #
#  is_audio
# --------------------------------------------------------------------------- #

def test_is_audio_covers_phone_voice_note_formats():
    for name in ("note.m4a", "note.oga", "note.OGG", "note.opus", "clip.mp3", "a.wav"):
        assert stt.is_audio(f"/tmp/{name}") is True
    for name in ("pic.jpg", "doc.pdf", "page.txt", "vid.webm"):
        assert stt.is_audio(f"/tmp/{name}") is False


# --------------------------------------------------------------------------- #
#  transcribe — the wire contract, via an injected transport
# --------------------------------------------------------------------------- #

class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _note(tmp_path, data=b"OggS fake opus"):
    p = tmp_path / "note.oga"
    p.write_bytes(data)
    return p


def test_transcribe_posts_multipart_and_returns_text(tmp_path):
    seen = {}

    def post(url, *, headers, data, files, timeout):
        seen.update(url=url, headers=headers, data=data, files=files)
        return _Resp(payload={"text": "  hello from the shop  ", "duration": 2.1})

    out = stt.transcribe(_note(tmp_path), api_key="K", base_url="https://api.x.ai/v1", post=post)
    assert out == "hello from the shop"                      # stripped transcript
    assert seen["url"] == "https://api.x.ai/v1/stt"
    assert seen["headers"]["Authorization"] == "Bearer K"
    name, payload, mime = seen["files"]["file"]
    assert name == "note.oga" and payload == b"OggS fake opus"
    assert seen["data"] == {}                                # no language → no fields


def test_transcribe_language_opts_into_formatting(tmp_path):
    seen = {}

    def post(url, *, headers, data, files, timeout):
        seen.update(data=data)
        return _Resp(payload={"text": "ok"})

    stt.transcribe(_note(tmp_path), api_key="K", language="en", post=post)
    assert seen["data"] == {"language": "en", "format": "true"}


def test_transcribe_raises_on_http_error_and_bad_payloads(tmp_path):
    p = _note(tmp_path)
    with pytest.raises(stt.SttError, match="HTTP 500"):
        stt.transcribe(p, api_key="K", post=lambda *a, **k: _Resp(status=500, text="boom"))
    with pytest.raises(stt.SttError, match="no transcript"):
        stt.transcribe(p, api_key="K", post=lambda *a, **k: _Resp(payload={"text": "   "}))
    with pytest.raises(stt.SttError, match="not JSON"):
        stt.transcribe(p, api_key="K", post=lambda *a, **k: _Resp(payload=ValueError("nope")))
    with pytest.raises(stt.SttError, match="request failed"):
        stt.transcribe(p, api_key="K", post=lambda *a, **k: (_ for _ in ()).throw(OSError("net")))
    with pytest.raises(stt.SttError, match="cannot read"):
        stt.transcribe(tmp_path / "missing.oga", api_key="K", post=lambda *a, **k: _Resp())


# --------------------------------------------------------------------------- #
#  _resolve_voice — a voice note IS the message
# --------------------------------------------------------------------------- #

def _wired(monkeypatch, transcript="turn the lights off"):
    monkeypatch.setattr(stt, "transcribe",
                        lambda path, **kw: transcript)
    cfg = SimpleNamespace(api_base_url=lambda: "https://api.x.ai/v1",
                          key_pairs=lambda: [])
    pool = SimpleNamespace(primary=lambda: SimpleNamespace(
        chat=SimpleNamespace(api_key="K")))
    ui = SimpleNamespace(print=lambda *a, **k: None)
    return cfg, pool, ui


def test_lone_voice_note_becomes_the_prompt(tmp_path, monkeypatch):
    cfg, pool, ui = _wired(monkeypatch)
    prompt, attach = _resolve_voice(DEFAULT_MEDIA_PROMPT, ["/tmp/note.m4a"],
                                    cfg=cfg, pool=pool, ui=ui)
    # Transcript replaces the placeholder, MARKED so the persona knows it heard
    # speech (unmarked, the model may claim it "can't receive voice" — the live
    # failure of the first phone test).
    assert prompt == "[voice note] turn the lights off"
    assert attach is None                        # audio consumed, nothing left to attach


def test_caption_plus_voice_note_appends_labeled_transcript(tmp_path, monkeypatch):
    cfg, pool, ui = _wired(monkeypatch)
    prompt, _ = _resolve_voice("about the spindle", ["/tmp/note.oga"],
                               cfg=cfg, pool=pool, ui=ui)
    assert prompt.startswith("about the spindle")
    assert "[voice note]" in prompt and "turn the lights off" in prompt


def test_voice_plus_photo_keeps_the_photo_attached(tmp_path, monkeypatch):
    cfg, pool, ui = _wired(monkeypatch)
    prompt, attach = _resolve_voice("", ["/tmp/note.m4a", "/tmp/pic.jpg"],
                                    cfg=cfg, pool=pool, ui=ui)
    assert prompt == "[voice note] turn the lights off"
    assert attach == ["/tmp/pic.jpg"]            # non-audio rides on to vision


def test_failed_transcription_keeps_audio_attached_and_prompt_unchanged(monkeypatch):
    def boom(path, **kw):
        raise stt.SttError("stt HTTP 500")
    monkeypatch.setattr(stt, "transcribe", boom)
    cfg = SimpleNamespace(api_base_url=lambda: "x", key_pairs=lambda: [])
    pool = SimpleNamespace(primary=lambda: SimpleNamespace(chat=SimpleNamespace(api_key="K")))
    ui = SimpleNamespace(print=lambda *a, **k: None)

    prompt, attach = _resolve_voice("hello", ["/tmp/note.m4a"], cfg=cfg, pool=pool, ui=ui)
    assert prompt == "hello"                     # never sink the turn over STT
    assert attach == ["/tmp/note.m4a"]           # left attached (an [attachment] note)


def test_plain_text_and_non_audio_pass_through_byte_identical():
    cfg = pool = None                             # must not be touched
    ui = SimpleNamespace(print=lambda *a, **k: None)
    assert _resolve_voice("hi", None, cfg=cfg, pool=pool, ui=ui) == ("hi", None)
    assert _resolve_voice("hi", ["/tmp/pic.jpg"], cfg=cfg, pool=pool, ui=ui) == ("hi", ["/tmp/pic.jpg"])
