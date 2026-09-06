"""Multimodal turn content — fold local files into a chat-completions user turn.

U0 of `proposals/upload-locker.md`: the seam that lets an image reach Grok. Today
every user turn is a plain text string; when image attachments are live, the
message ``content`` becomes the OpenAI/xAI parts array (a text part followed by
one ``image_url`` part per image). Text files fold into the text instead, so a
turn with no images stays a plain string — byte-identical to the pre-multimodal
path. Unsupported, missing, or oversized files degrade to a short note rather than
failing the turn. No locker, no GUI here — just ``paths -> content``.

xAI image-understanding limits (docs.x.ai, verified 2026-06-20): ``jpg``/``jpeg``
or ``png`` only, 20 MiB per image, base64 data URLs accepted, vision on grok-4.x.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence, Union

# Vision input: format -> mime, the per-image cap, and the detail level. Only
# these image types reach the model as an `image_url` part; anything else folds
# into text (or is described, if binary).
IMAGE_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MiB — xAI per-image cap
IMAGE_DETAIL = "high"

# Suffixes we can safely inline as text. Everything else is named, not embedded:
# base64-ing a binary the model can't read only burns context.
_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".rst", ".text", ".log",
    ".csv", ".tsv", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".bash", ".zsh", ".fish",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".rs", ".go", ".rb", ".java", ".kt",
    ".html", ".css", ".xml", ".sql", ".lua", ".pl", ".r", ".jl", ".scala", ".env",
}
_MAX_TEXT_BYTES = 256 * 1024  # 256 KiB — inline cap for a text/extracted file
_MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MiB — refuse to read a PDF bigger than this
_PDF_MAX_PAGES = 50  # cap pages we extract so a huge PDF can't blow up the turn

PartList = list[dict[str, Any]]


@dataclass
class PreparedTurn:
    """The result of folding attachments into a user turn."""

    outgoing: Union[str, PartList]   # what the API receives THIS turn
    compact: str                     # text-only form to keep in history afterwards
    has_images: bool                 # True => `outgoing` is a parts array
    summary: list[str] = field(default_factory=list)  # human notes (for the REPL)


def _human(n: float) -> str:
    """Bytes as a short human size (e.g. `18.4 MiB`)."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if n < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GiB"  # unreachable; satisfies the type checker


def model_supports_vision(model: Optional[str]) -> bool:
    """Heuristic: does this Grok model accept image input? The grok-4 family
    (grok-4, grok-4-1, grok-4.1, grok-4.3, grok-4-fast, …) and any '…vision…'
    model do; build/code/older models (grok-build, grok-code, grok-3, grok-2) do
    not. Name-based, mirroring how the TUI classifies context windows. A wrong
    guess here only changes a warning, never correctness: the caller omits images
    for a non-vision model so the request can't stall on input the model can't read.
    """
    if not model:
        return False
    m = model.lower().replace("_", "-")
    if "vision" in m:
        return True
    return m.startswith("grok-4") or m.startswith("grok4")


def classify_kind(path: Any) -> str:
    """A coarse kind label for the locker UI: image / text / pdf / other."""
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_MIME:
        return "image"
    if suffix in _TEXT_SUFFIXES:
        return "text"
    if suffix == ".pdf":
        return "pdf"
    return "other"


def human_bytes(n: float) -> str:
    """Public wrapper for the short human size used by the locker command."""
    return _human(n)


def estimate_live_cost(paths: Sequence[Any]) -> str:
    """A rough, explicitly-approximate size/token line for the live locker set.

    Token figures are a ballpark for guidance only (text ≈ chars/4; an image at
    `detail: high` is a few hundred to ~1k+ tokens depending on resolution) — the
    line is labelled `(approx)` so it's never mistaken for a billed total.
    """
    total = 0
    tokens = 0
    n = 0
    for raw in paths:
        p = Path(raw)
        try:
            if not p.is_file():
                continue
            size = p.stat().st_size
        except OSError:
            continue
        n += 1
        total += size
        suffix = p.suffix.lower()
        if suffix in IMAGE_MIME:
            tokens += 1100  # ballpark per image at detail=high
        elif suffix in _TEXT_SUFFIXES:
            tokens += min(size, _MAX_TEXT_BYTES) // 4
    if not n:
        return "nothing enabled"
    plural = "s" if n != 1 else ""
    return f"{n} file{plural} · {_human(float(total))} · ~{tokens:,} tokens (approx)"


def _extract_pdf_text(path: Path, *, max_pages: int = _PDF_MAX_PAGES) -> Optional[str]:
    """Best-effort PDF text extraction (U3). See :func:`xlii.pdf_text.extract_pdf_text`."""
    from xlii.pdf_text import extract_pdf_text

    return extract_pdf_text(path, max_pages=max_pages)


def _image_part(path: Path) -> dict[str, Any]:
    """A chat-completions `image_url` content-part with an inline base64 data URL."""
    mime = IMAGE_MIME[path.suffix.lower()]
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{mime};base64,{b64}", "detail": IMAGE_DETAIL},
    }


def prepare_user_turn(
    text: str, attachments: Optional[Sequence[Any]] = None
) -> PreparedTurn:
    """Fold file `attachments` (path-likes) into the user turn `text`.

    With no attachments — or only text/unsupported files — `outgoing` stays a
    plain string (byte-identical to the non-multimodal path). As soon as one image
    is live, `outgoing` becomes a parts array: a text part (the typed text plus any
    inlined text files / notes) followed by one `image_url` part per image.
    `compact` is the text-only form to keep in history *after* the turn, so a
    20 MiB base64 blob never rides along in subsequent turns — the locker re-injects
    live files next turn if still enabled.
    """
    if not attachments:
        return PreparedTurn(outgoing=text, compact=text, has_images=False)

    text_chunks: list[str] = [text] if text else []
    image_parts: PartList = []
    image_notes: list[str] = []   # placeholders kept in history after the turn
    summary: list[str] = []

    for raw in attachments:
        p = Path(raw)
        name = p.name or str(raw)
        suffix = p.suffix.lower()

        try:
            is_file = p.is_file()
        except OSError:
            is_file = False
        if not is_file:
            note = f"[attachment not found: {name}]"
            text_chunks.append(note)
            summary.append(f"⚠ {name} — not found")
            continue

        try:
            size = p.stat().st_size
        except OSError:
            size = 0

        if suffix in IMAGE_MIME:
            if size > MAX_IMAGE_BYTES:
                text_chunks.append(f"[image too large to send ({_human(size)} > 20 MiB): {name}]")
                summary.append(f"⚠ {name} — {_human(size)} > 20 MiB, skipped")
                continue
            try:
                image_parts.append(_image_part(p))
            except OSError:
                text_chunks.append(f"[image could not be read: {name}]")
                summary.append(f"⚠ {name} — unreadable")
                continue
            image_notes.append(f"[image: {name}]")
            summary.append(f"🖼 {name} ({_human(size)})")
            continue

        if suffix in _TEXT_SUFFIXES:
            if size > _MAX_TEXT_BYTES:
                text_chunks.append(f"[text file too large to inline ({_human(size)}): {name}]")
                summary.append(f"⚠ {name} — {_human(size)}, not inlined")
                continue
            try:
                body = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text_chunks.append(f"[file could not be read: {name}]")
                summary.append(f"⚠ {name} — unreadable")
                continue
            text_chunks.append(f"Attached file `{name}`:\n```\n{body.rstrip()}\n```")
            summary.append(f"📄 {name} (inlined, {_human(size)})")
            continue

        if suffix == ".pdf":
            # Grok has no native PDF input, so we extract text and inline it
            # (U3, default-to-text). No extractor installed → a note pointing at
            # the [files] extra; scanned/empty PDF → a note, not a crash.
            if size > _MAX_PDF_BYTES:
                text_chunks.append(f"[PDF too large to read ({_human(size)}): {name}]")
                summary.append(f"⚠ {name} — {_human(size)}, not read")
                continue
            text = _extract_pdf_text(p)
            if text is None:
                text_chunks.append(f"[PDF not sent ({name}) — install the files extra for PDF "
                                   "text: pip install 'xlii[files]']")
                summary.append(f"⚠ {name} — needs the [files] extra (pypdf)")
                continue
            if not text:
                text_chunks.append(f"[PDF has no extractable text ({name}) — likely scanned images]")
                summary.append(f"⚠ {name} — no extractable text (scanned?)")
                continue
            clipped = text[:_MAX_TEXT_BYTES]
            trunc = " …(truncated)" if len(text) > _MAX_TEXT_BYTES else ""
            text_chunks.append(f"Attached PDF `{name}` (extracted text){trunc}:\n```\n{clipped.rstrip()}\n```")
            summary.append(f"📄 {name} (PDF text, {_human(size)})")
            continue

        # Unknown / binary: name it, don't embed garbage.
        text_chunks.append(f"[attachment (unsupported type, not sent): {name}]")
        summary.append(f"⚠ {name} — unsupported type, not sent")

    text_blob = "\n\n".join(c for c in text_chunks if c)

    if not image_parts:
        # No image ⇒ stay on the plain-string path (text files already folded in).
        return PreparedTurn(outgoing=text_blob, compact=text_blob, has_images=False, summary=summary)

    parts: PartList = []
    if text_blob:
        parts.append({"type": "text", "text": text_blob})
    parts.extend(image_parts)
    compact = "\n".join(x for x in ([text_blob] + image_notes) if x)
    return PreparedTurn(outgoing=parts, compact=compact, has_images=True, summary=summary)
