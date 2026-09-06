"""xAI Imagine image generation — thin client over the OpenAI-compatible API.

Never returns base64 to callers outside this module; artifacts.py decodes and
writes bytes locally.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import httpx
from openai import OpenAI

DEFAULT_IMAGE_MODEL = "grok-imagine-image"
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_RESOLUTION = "1k"

# Image generation legitimately takes tens of seconds, but the OpenAI SDK's
# default 600s timeout + 2 retries turns a slow/stalled endpoint into a ~30min
# silent hang in the REPL. Bound it so a stuck request surfaces as an error the
# handler can report instead of looking dead. Override via XLII_IMAGE_TIMEOUT.
_DEFAULT_IMAGE_TIMEOUT_S = 120.0
_DEFAULT_IMAGE_RETRIES = 1


def _image_timeout() -> float:
    raw = os.environ.get("XLII_IMAGE_TIMEOUT", "").strip()
    try:
        val = float(raw)
        return val if val > 0 else _DEFAULT_IMAGE_TIMEOUT_S
    except ValueError:
        return _DEFAULT_IMAGE_TIMEOUT_S


def _api_base() -> str:
    """Region-aware base URL (cfg.region / XAI_REGION). Callers pass api_key but
    not cfg, so resolve here — media calls are seconds-long, a config read is
    noise. Falls back to the global edge if config is unreadable."""
    try:
        from xlii.config import GlobalConfig

        return GlobalConfig.load().api_base_url()
    except Exception:
        return "https://api.x.ai/v1"

# Flat per-image ballparks for operator guidance — always label "(approx)".
_MEDIA_PRICING_USD: dict[str, float] = {
    "grok-imagine-image": 0.05,
    "grok-imagine-image-quality": 0.10,
    "grok-2-image": 0.05,
}

# Flat per-video ballparks (M4 kickoff line) — always label "(approx)".
# Live rates unverified until the operator wire pass; keep the figure honest.
_VIDEO_PRICING_USD: dict[str, float] = {
    "grok-imagine-video": 0.50,
}


@dataclass
class GeneratedImage:
    data: bytes
    mime_type: str
    model: str
    revised_prompt: str = ""


def _ext_for_mime(mime: str) -> str:
    m = (mime or "").lower().split(";")[0].strip()
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
    }.get(m, "png")


def estimate_image_cost(model: str, n: int = 1) -> str:
    """Human-readable approximate cost line for one or more images."""
    unit = _MEDIA_PRICING_USD.get(model, 0.05)
    total = unit * max(1, n)
    return f"~${total:.2f} (approx)"


def estimate_video_cost(model: str = "grok-imagine-video") -> str:
    """Human-readable approximate cost line for one video kickoff (M4)."""
    unit = _VIDEO_PRICING_USD.get(model, 0.50)
    return f"~${unit:.2f} (approx)"


def generate_image(
    prompt: str,
    *,
    api_key: str,
    model: str = DEFAULT_IMAGE_MODEL,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    resolution: str = DEFAULT_RESOLUTION,
    n: int = 1,
) -> list[GeneratedImage]:
    """Call POST /v1/images/generations with b64_json; return decoded bytes."""
    if not prompt.strip():
        raise ValueError("prompt is required")
    client = OpenAI(
        api_key=api_key,
        base_url=_api_base(),
        timeout=_image_timeout(),
        max_retries=_DEFAULT_IMAGE_RETRIES,
    )
    # aspect_ratio + resolution are xAI Imagine extensions, not OpenAI SDK
    # kwargs — passing them directly raises "unexpected keyword argument". The
    # SDK forwards extra_body into the request JSON, which is how xAI receives
    # them. model/prompt/n/response_format are real SDK params.
    resp = client.images.generate(
        model=model,
        prompt=prompt.strip(),
        n=max(1, min(10, n)),
        response_format="b64_json",
        extra_body={"aspect_ratio": aspect_ratio, "resolution": resolution},
    )
    return _decode_image_data(resp.data, model=model)


def _fetch_bytes(url: str) -> bytes:
    with httpx.Client(timeout=_image_timeout()) as http:
        r = http.get(url)
    if r.status_code >= 400 or not r.content:
        raise RuntimeError(f"image URL fetch failed ({r.status_code})")
    return r.content


def _get(item: Any, key: str) -> Any:
    """Read *key* off an SDK model OR a plain dict (faked responses)."""
    return getattr(item, key, None) or (item.get(key) if isinstance(item, dict) else None)


def _decode_image_data(data: Any, *, model: str) -> list[GeneratedImage]:
    """Decode an images API ``data`` array (SDK objects or dicts) to bytes.
    Shared by generate + edit so both handle the same b64/mime/revised shape."""
    out: list[GeneratedImage] = []
    for item in data or []:
        b64 = _get(item, "b64_json")
        raw = b""
        mime = str(_get(item, "mime_type") or "")
        if b64:
            raw = base64.b64decode(b64)
        else:
            url = str(_get(item, "url") or "")
            if url.startswith("http://") or url.startswith("https://"):
                raw = _fetch_bytes(url)
        if not raw:
            raise RuntimeError("image API returned no b64_json payload")
        out.append(
            GeneratedImage(
                data=raw,
                mime_type=mime or "image/png",
                model=model,
                revised_prompt=str(_get(item, "revised_prompt") or ""),
            )
        )
    if not out:
        raise RuntimeError("image API returned empty data array")
    return out


# Reference-image edits (media-artifacts M5). xAI's /v1/images/edits accepts up
# to a few reference images; the SDK's images.edit takes `image=` as one file or
# a list. Kept SEPARATE from generate_image so the paid-tool schema and the
# no-references common case stay simple.
MAX_REFERENCE_IMAGES = 3


def _mime_for_path(path: Any) -> str:
    ext = str(getattr(path, "suffix", "") or "").lower()
    return {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "image/png")


def _image_url_part(path: Any) -> dict[str, str]:
    """xAI edits want a JSON ``{url, type}`` — data URI, not multipart."""
    from pathlib import Path as _Path

    p = _Path(path)
    raw = p.read_bytes()
    uri = f"data:{_mime_for_path(p)};base64,{base64.b64encode(raw).decode('ascii')}"
    return {"url": uri, "type": "image_url"}


def _err_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    err = payload.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err.get("type") or "")
    if isinstance(err, str):
        return err
    return str(payload.get("message") or "")


def _post_json(path: str, body: dict[str, Any], *, api_key: str) -> dict[str, Any]:
    """JSON POST against the Imagine API. Avoid the OpenAI SDK here:

    * ``images.edit()`` sends multipart (xAI rejects it)
    * ``client.post(..., cast_to=dict)`` unpacks ``get_args(dict)`` and
      raises ``not enough values to unpack (expected 2, got 0)``
    """
    url = _api_base().rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with httpx.Client(timeout=_image_timeout()) as http:
        r = http.post(url, headers=headers, json=body)
    payload: Any = None
    try:
        payload = r.json()
    except Exception:
        payload = None
    if r.status_code >= 400:
        msg = _err_from_payload(payload) or (r.text or "")[:240] or f"HTTP {r.status_code}"
        raise RuntimeError(msg)
    if not isinstance(payload, dict):
        raise RuntimeError("image API returned non-JSON")
    return payload


def edit_image(
    prompt: str,
    reference_paths: "Sequence[Any]",
    *,
    api_key: str,
    model: str = DEFAULT_IMAGE_MODEL,
    n: int = 1,
) -> list[GeneratedImage]:
    """Call POST /v1/images/edits with up to MAX_REFERENCE_IMAGES references.

    xAI requires ``application/json`` (data-URI ``image``). The OpenAI SDK
    ``images.edit()`` sends multipart/form-data and the API rejects it with
    ``Expected request with Content-Type: application/json``.
    """
    from pathlib import Path as _Path
    if not prompt.strip():
        raise ValueError("prompt is required")
    refs = [_Path(p) for p in (reference_paths or [])]
    if not refs:
        raise ValueError("edit_image needs at least one reference image")
    if len(refs) > MAX_REFERENCE_IMAGES:
        raise ValueError(
            f"too many reference images ({len(refs)}); max {MAX_REFERENCE_IMAGES}")
    for r in refs:
        if not r.exists():
            raise FileNotFoundError(f"reference image not found: {r}")

    parts = [_image_url_part(r) for r in refs]
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt.strip(),
        "n": max(1, min(4, n)),
        "response_format": "b64_json",
        "image": parts[0] if len(parts) == 1 else parts,
    }
    payload = _post_json("/images/edits", body, api_key=api_key)
    return _decode_image_data(payload.get("data"), model=model)


# --- Video (media-artifacts M4) — async, command-first ------------------------
#
# Contract per the proposal: POST /v1/videos/generations returns a request_id;
# GET /v1/videos/{request_id} reports status and, when done, the video payload.
# The exact live wire shape is UNVERIFIED until the operator's live pass —
# parsing below is tolerant (b64 first, url noted as unsupported explicitly)
# and every caller treats errors as data, not crashes.

DEFAULT_VIDEO_MODEL = "grok-imagine-video"

VIDEO_PENDING = "pending"
VIDEO_DONE = "done"
VIDEO_FAILED = "failed"


def _video_base(api_key: str):
    from openai import OpenAI
    return OpenAI(
        api_key=api_key,
        base_url=_api_base(),
        timeout=_image_timeout(),
        max_retries=_DEFAULT_IMAGE_RETRIES,
    )


def generate_video_start(prompt: str, *, api_key: str,
                         model: str = DEFAULT_VIDEO_MODEL) -> str:
    """Kick off an async video generation; return the resumable request_id."""
    if not prompt.strip():
        raise ValueError("prompt is required")
    client = _video_base(api_key)
    resp = client.post(
        "/videos/generations",
        cast_to=dict,
        body={"model": model, "prompt": prompt},
    )
    request_id = str((resp or {}).get("request_id") or (resp or {}).get("id") or "")
    if not request_id:
        raise RuntimeError(f"video start returned no request id: {resp!r}")
    return request_id


def generate_video_status(request_id: str, *, api_key: str) -> "tuple[str, Optional[bytes]]":
    """Poll one async video job. Returns (status, video_bytes_when_done)."""
    client = _video_base(api_key)
    resp = client.get(f"/videos/{request_id}", cast_to=dict) or {}
    return parse_video_status_json(resp)


def parse_video_status_json(payload: dict[str, Any]) -> "tuple[str, Optional[bytes]]":
    """Test seam — normalize a status payload to (pending|done|failed, bytes)."""
    raw_status = str(payload.get("status") or "").lower()
    if raw_status in ("failed", "error", "cancelled"):
        return (VIDEO_FAILED, None)
    b64 = None
    data = payload.get("data")
    if isinstance(data, list) and data:
        b64 = data[0].get("b64_json")
    b64 = b64 or payload.get("b64_json")
    if b64:
        return (VIDEO_DONE, base64.b64decode(b64))
    if raw_status in ("done", "succeeded", "completed"):
        # Completed but no inline payload we understand (e.g. url-only) —
        # surface honestly rather than writing an empty file.
        raise RuntimeError(
            "video reported done but carried no b64 payload "
            "(url-only responses are not supported yet — file an issue with the shape)"
        )
    return (VIDEO_PENDING, None)


def generate_image_from_response_json(payload: dict[str, Any], *, model: str) -> list[GeneratedImage]:
    """Test seam — build GeneratedImage list from a faked HTTP JSON body."""
    out: list[GeneratedImage] = []
    for item in payload.get("data") or []:
        b64 = item.get("b64_json")
        if not b64:
            continue
        out.append(
            GeneratedImage(
                data=base64.b64decode(b64),
                mime_type=str(item.get("mime_type") or "image/png"),
                model=model,
                revised_prompt=str(item.get("revised_prompt") or ""),
            )
        )
    if not out:
        raise RuntimeError("fake payload had no decodable images")
    return out
