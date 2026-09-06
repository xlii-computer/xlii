"""Text hygiene — portable text + credibility signals for untrusted ingress.

Product law (operator design 2026-08-12):

- **scan** — always safe; report portability + one **credibility** counter
  (injection-class Unicode only — not CRLF/BOM).
- **strip** — opt-in default pack: LF · drop UTF-8 BOM · strip invisible /
  stego / bidi control junk. Flags only for exceptions.
- Not a lab-watermark eraser. Not a language formatter (compose with ``/xtool``).
- Intended for paste · harness returns · attach · inbox — "take note" / Danger
  Will Robinson, not a full firewall.

Pure functions, no I/O side effects except the path helpers that read/write
bytes explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Codepoint classes
# ---------------------------------------------------------------------------

# Injection-class / stego-ish: feed the credibility counter; stripped by default.
# ZWJ (U+200D) is *not* here — intentional emoji graphemes use it.
_CREDIBILITY_CODEPOINTS: dict[int, str] = {
    0x200B: "ZWSP",           # ZERO WIDTH SPACE
    0x200C: "ZWNJ",           # ZERO WIDTH NON-JOINER
    0xFEFF: "BOM",            # ZWNBSP / BOM mid-stream
    0x2060: "WJ",             # WORD JOINER
    0x00AD: "SOFT_HYPHEN",
    0x2000: "EN_QUAD",
    0x2001: "EM_QUAD",
    0x2002: "EN_SPACE",
    0x2003: "EM_SPACE",
    0x2004: "THREE_PER_EM",
    0x2005: "FOUR_PER_EM",
    0x2006: "SIX_PER_EM",
    0x2007: "FIGURE_SPACE",
    0x2008: "PUNCT_SPACE",
    0x2009: "THIN_SPACE",
    0x200A: "HAIR_SPACE",
    0x202F: "NNBSP",
    0x205F: "MMSP",
    0x3000: "IDEOGRAPHIC_SPACE",
    # Bidi / isolate controls (Trojan Source class)
    0x202A: "LRE",
    0x202B: "RLE",
    0x202C: "PDF",
    0x202D: "LRO",
    0x202E: "RLO",
    0x2066: "LRI",
    0x2067: "RLI",
    0x2068: "FSI",
    0x2069: "PDI",
}

# Exotic spaces → ASCII space on strip (still credibility hits when present).
_SPACE_TO_ASCII = frozenset({
    0x2000, 0x2001, 0x2002, 0x2003, 0x2004, 0x2005, 0x2006,
    0x2007, 0x2008, 0x2009, 0x200A, 0x202F, 0x205F, 0x3000,
})

# Pure strip (remove, no substitute).
_STRIP_AWAY = frozenset({
    0x200B, 0x200C, 0xFEFF, 0x2060, 0x00AD,
    0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
    0x2066, 0x2067, 0x2068, 0x2069,
})

UTF8_BOM = b"\xef\xbb\xbf"

_NEWLINE_LABELS = {
    "lf": "LF",
    "crlf": "CRLF",
    "cr": "CR",
    "mixed": "mixed",
    "none": "none",
}


@dataclass(frozen=True)
class Finding:
    """One hygiene finding (portability or credibility class)."""

    kind: str          # e.g. "bom", "newlines", "encoding", "unicode"
    label: str         # short machine label (ZWSP, CRLF, …)
    count: int = 1
    detail: str = ""
    credibility: bool = False  # True → counts toward credibility score


@dataclass
class HygieneReport:
    """Result of scanning text or a file."""

    path: Optional[str] = None
    encoding: str = "utf-8"
    encoding_note: str = ""
    has_bom: bool = False
    newlines: str = "none"  # lf | crlf | cr | mixed | none
    findings: list[Finding] = field(default_factory=list)
    credibility: int = 0
    text: str = ""  # decoded body (BOM not included in text if stripped at decode)
    raw_size: int = 0
    binary_suspect: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.binary_suspect

    def summary_lines(self) -> list[str]:
        """Human report lines (no color)."""
        who = self.path or "(text)"
        if self.error:
            return [f"hygiene · {who}", f"  error: {self.error}"]
        if self.binary_suspect:
            return [
                f"hygiene · {who}",
                "  binary-suspect: null bytes — skipped (not text hygiene)",
            ]
        lines = [f"hygiene · {who}"]
        nl = _NEWLINE_LABELS.get(self.newlines, self.newlines)
        lines.append(f"  newlines: {nl}")
        lines.append(f"  bom: {'UTF-8 BOM' if self.has_bom else 'none'}")
        enc = self.encoding
        if self.encoding_note:
            enc = f"{enc} ({self.encoding_note})"
        lines.append(f"  encoding: {enc}")
        lines.append(f"  credibility: {self.credibility}")
        for f in self.findings:
            if not f.credibility:
                continue
            bit = f"    · {f.label}"
            if f.count > 1:
                bit += f" ×{f.count}"
            if f.detail:
                bit += f"  ({f.detail})"
            lines.append(bit)
        if self.credibility == 0 and not any(f.credibility for f in self.findings):
            lines.append("    · (no injection-class Unicode)")
        # portability extras already in newlines/bom; other non-cred findings
        for f in self.findings:
            if f.credibility or f.kind in ("bom", "newlines", "encoding"):
                continue
            bit = f"  note: {f.label}"
            if f.count > 1:
                bit += f" ×{f.count}"
            if f.detail:
                bit += f" — {f.detail}"
            lines.append(bit)
        return lines


@dataclass(frozen=True)
class StripOptions:
    """Default pack + rare exceptions."""

    newlines: str = "lf"       # lf | crlf | keep
    strip_bom: bool = True
    strip_unicode_junk: bool = True
    # Future: encoding conversion stays explicit elsewhere.


@dataclass(frozen=True)
class StripResult:
    text: str
    changed: bool
    report_before: HygieneReport
    applied: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Core scan / strip on str
# ---------------------------------------------------------------------------


def classify_newlines(text: str) -> str:
    """Return lf | crlf | cr | mixed | none."""
    has_crlf = "\r\n" in text
    # lone CR / LF after removing CRLF pairs
    tmp = text.replace("\r\n", "\0")
    has_lf = "\n" in tmp
    has_cr = "\r" in tmp
    kinds = sum([has_crlf, has_lf, has_cr])
    if kinds == 0:
        return "none"
    if kinds > 1:
        return "mixed"
    if has_crlf:
        return "crlf"
    if has_cr:
        return "cr"
    return "lf"


def scan_text(text: str, *, path: Optional[str] = None) -> HygieneReport:
    """Scan already-decoded text (no BOM byte — pass has_bom separately if known)."""
    return _scan_decoded(text, path=path, has_bom=False, encoding="utf-8", encoding_note="")


def _scan_decoded(
    text: str,
    *,
    path: Optional[str],
    has_bom: bool,
    encoding: str,
    encoding_note: str,
    raw_size: int = 0,
) -> HygieneReport:
    findings: list[Finding] = []
    nl = classify_newlines(text)
    if nl not in ("lf", "none"):
        findings.append(Finding(
            kind="newlines",
            label=_NEWLINE_LABELS.get(nl, nl),
            detail="portability — strip defaults to LF",
            credibility=False,
        ))
    if has_bom:
        findings.append(Finding(
            kind="bom",
            label="UTF-8 BOM",
            detail="portability — strip drops BOM by default",
            credibility=False,
        ))

    # Count credibility codepoints
    counts: dict[int, int] = {}
    for ch in text:
        o = ord(ch)
        if o in _CREDIBILITY_CODEPOINTS:
            counts[o] = counts.get(o, 0) + 1

    credibility = 0
    for o, n in sorted(counts.items()):
        label = _CREDIBILITY_CODEPOINTS[o]
        findings.append(Finding(
            kind="unicode",
            label=label,
            count=n,
            detail=f"U+{o:04X}",
            credibility=True,
        ))
        credibility += n

    return HygieneReport(
        path=path,
        encoding=encoding,
        encoding_note=encoding_note,
        has_bom=has_bom,
        newlines=nl,
        findings=findings,
        credibility=credibility,
        text=text,
        raw_size=raw_size or len(text.encode("utf-8", errors="replace")),
    )


def strip_text(text: str, opts: Optional[StripOptions] = None) -> tuple[str, list[str]]:
    """Apply the default strip pack to *text*. Returns (new_text, applied_labels)."""
    opts = opts or StripOptions()
    applied: list[str] = []
    out = text

    if opts.strip_unicode_junk:
        buf: list[str] = []
        stripped_n = 0
        spaced_n = 0
        for ch in out:
            o = ord(ch)
            if o in _STRIP_AWAY:
                stripped_n += 1
                continue
            if o in _SPACE_TO_ASCII:
                buf.append(" ")
                spaced_n += 1
                continue
            buf.append(ch)
        out = "".join(buf)
        if stripped_n:
            applied.append(f"unicode-strip×{stripped_n}")
        if spaced_n:
            applied.append(f"exotic-space→ASCII×{spaced_n}")

    if opts.newlines == "lf":
        before = out
        out = out.replace("\r\n", "\n").replace("\r", "\n")
        if out != before:
            applied.append("newlines→LF")
    elif opts.newlines == "crlf":
        before = out
        mid = out.replace("\r\n", "\n").replace("\r", "\n")
        out = mid.replace("\n", "\r\n")
        if out != before:
            applied.append("newlines→CRLF")
    # keep: no change

    return out, applied


def normalize_text(
    text: str,
    opts: Optional[StripOptions] = None,
    *,
    path: Optional[str] = None,
) -> StripResult:
    """Scan then strip; return before-report + result."""
    before = scan_text(text, path=path)
    new, applied = strip_text(text, opts)
    return StripResult(
        text=new,
        changed=new != text,
        report_before=before,
        applied=applied,
    )


# ---------------------------------------------------------------------------
# Path helpers (bytes in / out)
# ---------------------------------------------------------------------------


def _looks_binary(data: bytes) -> bool:
    if not data:
        return False
    # NUL in first 8KiB → binary
    return b"\x00" in data[:8192]


def decode_bytes(data: bytes) -> tuple[str, bool, str, str]:
    """Decode *data* → (text, has_bom, encoding, encoding_note).

    Prefers UTF-8. Falls back to cp1252 with a note (common Windows paste).
    Does not silently invent encodings beyond that pair for MVP.
    """
    has_bom = data.startswith(UTF8_BOM)
    body = data[len(UTF8_BOM):] if has_bom else data
    try:
        return body.decode("utf-8"), has_bom, "utf-8", ("with BOM" if has_bom else "")
    except UnicodeDecodeError:
        # Not UTF-8 -- fall through to the cp1252 attempt below, then to lossy replace.
        pass
    # Windows-ish fallback — report clearly
    try:
        return body.decode("cp1252"), has_bom, "cp1252", "fallback from non-UTF-8"
    except UnicodeDecodeError:
        text = body.decode("utf-8", errors="replace")
        return text, has_bom, "utf-8", "lossy replace"


def scan_bytes(data: bytes, *, path: Optional[str] = None) -> HygieneReport:
    if _looks_binary(data):
        return HygieneReport(
            path=path,
            binary_suspect=True,
            raw_size=len(data),
            error="",
        )
    text, has_bom, enc, note = decode_bytes(data)
    return _scan_decoded(
        text,
        path=path,
        has_bom=has_bom,
        encoding=enc,
        encoding_note=note,
        raw_size=len(data),
    )


def scan_path(path: Path) -> HygieneReport:
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        return HygieneReport(path=str(path), error=f"{type(e).__name__}: {e}")
    return scan_bytes(data, path=str(path))


def strip_path(
    path: Path,
    opts: Optional[StripOptions] = None,
    *,
    write: bool = True,
) -> tuple[HygieneReport, StripResult, Optional[str]]:
    """Scan + strip a file. If *write*, rewrite as UTF-8 (no BOM) with chosen newlines.

    Returns (before_report, strip_result, error_or_none).
    """
    opts = opts or StripOptions()
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as e:
        err = f"{type(e).__name__}: {e}"
        return HygieneReport(path=str(path), error=err), StripResult(
            text="", changed=False, report_before=HygieneReport(path=str(path), error=err),
        ), err

    before = scan_bytes(data, path=str(path))
    if before.binary_suspect:
        return before, StripResult(
            text="", changed=False, report_before=before,
        ), "binary-suspect"
    if before.error:
        return before, StripResult(
            text="", changed=False, report_before=before,
        ), before.error

    new_text, applied = strip_text(before.text, opts)
    applied = list(applied)
    # BOM on disk: drop by default; --keep-bom preserves a leading UTF-8 BOM.
    want_bom = before.has_bom and not opts.strip_bom
    drop_bom = before.has_bom and opts.strip_bom
    if drop_bom:
        applied.append("bom-drop")
    changed = new_text != before.text or drop_bom

    result = StripResult(
        text=new_text,
        changed=changed,
        report_before=before,
        applied=applied,
    )
    if not write or not changed:
        return before, result, None

    try:
        from xlii.atomicio import write_text_atomic

        if want_bom:
            # Atomic writer is text-mode no-BOM; write bytes to keep BOM.
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(UTF8_BOM + new_text.encode("utf-8"))
        else:
            write_text_atomic(path, new_text, encoding="utf-8")
    except OSError as e:
        return before, result, f"{type(e).__name__}: {e}"
    return before, result, None


def format_report(report: HygieneReport) -> str:
    return "\n".join(report.summary_lines())


def credibility_of(text: str) -> int:
    """Convenience: injection-class count only."""
    return scan_text(text).credibility


def note_ingress(text: str, *, source: str = "") -> str:
    """One-line **take note** for untrusted ingress, or ``""`` if credibility is 0.

    Used at harness capture, inbox drain, and similar seams — operator-visible
    "Danger Will Robinson" / take-note, never auto-strip.
    """
    n = credibility_of(text or "")
    if n <= 0:
        return ""
    src = f" · {source}" if source else ""
    return (
        f"take note · credibility {n}{src} "
        f"— injection-class Unicode · /hygiene scan"
    )


def enrich_meta(meta: Optional[dict], text: str, *, source: str = "") -> dict:
    """Copy *meta* and attach ``credibility`` / ``hygiene_note`` when dirty."""
    out = dict(meta or {})
    n = credibility_of(text or "")
    if n > 0:
        out["credibility"] = n
        out["hygiene_note"] = note_ingress(text, source=source)
    return out


# Confusable / strict helpers reserved for later (--strict). Exported name list
# for docs/tests that assert the public surface.
__all__ = [
    "Finding",
    "HygieneReport",
    "StripOptions",
    "StripResult",
    "classify_newlines",
    "scan_text",
    "scan_bytes",
    "scan_path",
    "strip_text",
    "normalize_text",
    "strip_path",
    "format_report",
    "credibility_of",
    "note_ingress",
    "enrich_meta",
    "decode_bytes",
    "UTF8_BOM",
]
