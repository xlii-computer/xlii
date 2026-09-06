"""Speech-to-text over the xAI ``/v1/stt`` API — voice-in for the mouths.

A Conversations voice note reaches the daemon as an encrypted audio file and is
materialized by media-in exactly like a photo (``xlii/media_in.py``). This
module turns that file into text: ``transcribe()`` does a multipart POST to
``{base}/stt`` (Bearer auth; the ``file`` part last, per the API contract) and
returns the transcript. The caller (``xlii ask``'s voice resolver) then makes
the transcript the TURN'S MESSAGE — a voice note isn't an attachment to look
at, it IS the user talking.

The API accepts OGG/Opus/M4A/AAC/MP3/WAV/FLAC/MP4/MKV containers directly —
exactly what phone clients record — so there is NO transcode step and NO ffmpeg
dependency. Transport is httpx (already present via the openai dependency) and
injectable for tests. Batch STT is priced per audio-hour; a voice note is
seconds, so cost is noise.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any, Callable, Optional

# Containers the xAI STT endpoint documents (docs.x.ai → audio/speech-to-text),
# plus ``.oga`` (the OGG-audio suffix some clients use for opus voice notes).
AUDIO_EXTS = {
    ".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac", ".aac", ".mp4", ".m4a", ".mkv",
}

DEFAULT_TIMEOUT_S = 120.0  # a voice note is seconds of audio; minutes of slack


class SttError(RuntimeError):
    """Transcription failed (transport, HTTP status, or a malformed response)."""


def is_audio(path: Any) -> bool:
    """Whether ``path`` names an audio file the STT endpoint can ingest."""
    return Path(str(path)).suffix.lower() in AUDIO_EXTS


def _default_post(url: str, *, headers: dict, data: dict, files: dict, timeout: float):
    import httpx

    return httpx.post(url, headers=headers, data=data, files=files, timeout=timeout)


def transcribe(
    path: Any,
    *,
    api_key: str,
    base_url: str = "https://api.x.ai/v1",
    language: str = "",
    timeout: float = DEFAULT_TIMEOUT_S,
    post: Optional[Callable[..., Any]] = None,
) -> str:
    """Transcribe one audio file → its text.

    Multipart ``POST {base_url}/stt`` with the audio under the ``file`` field
    (kept last — httpx encodes ``data`` fields before ``files``, which the API
    requires). ``language`` opts into text normalization (``format=true`` needs
    it, per the contract). Raises :class:`SttError` on any failure; never
    returns a fabricated transcript."""
    p = Path(str(path))
    try:
        audio = p.read_bytes()
    except OSError as e:
        raise SttError(f"cannot read audio file: {e}") from e

    data: dict[str, Any] = {}
    if language:
        data["language"] = language
        data["format"] = "true"
    mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"

    do_post = post or _default_post
    try:
        resp = do_post(
            f"{base_url.rstrip('/')}/stt",
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": (p.name, audio, mime)},
            timeout=timeout,
        )
    except Exception as e:  # httpx transport errors and friends
        raise SttError(f"stt request failed: {type(e).__name__}: {e}") from e

    status = getattr(resp, "status_code", None)
    if status != 200:
        detail = ""
        try:
            detail = (resp.text or "")[:200]
        except Exception:
            # detail stays empty; the non-200 status is still reported to the caller.
            pass
        raise SttError(f"stt HTTP {status}: {detail}")
    try:
        text = resp.json().get("text")
    except Exception as e:
        raise SttError(f"stt response was not JSON: {e}") from e
    if not isinstance(text, str) or not text.strip():
        raise SttError("stt response carried no transcript text")
    return text.strip()
