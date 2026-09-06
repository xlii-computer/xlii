"""Text hygiene kernel — portability + credibility (injection-class Unicode)."""

from __future__ import annotations

from pathlib import Path

from xlii.text_hygiene import (
    UTF8_BOM,
    StripOptions,
    classify_newlines,
    credibility_of,
    decode_bytes,
    format_report,
    normalize_text,
    scan_bytes,
    scan_path,
    scan_text,
    strip_path,
    strip_text,
)


# --- newlines ---------------------------------------------------------------


def test_classify_newlines_variants():
    assert classify_newlines("a\nb\n") == "lf"
    assert classify_newlines("a\r\nb\r\n") == "crlf"
    assert classify_newlines("a\rb\r") == "cr"
    assert classify_newlines("a\r\nb\nc") == "mixed"
    assert classify_newlines("no breaks") == "none"


def test_strip_newlines_to_lf():
    out, applied = strip_text("a\r\nb\rc\n", StripOptions(newlines="lf"))
    assert out == "a\nb\nc\n"
    assert any("LF" in a for a in applied)


def test_strip_newlines_to_crlf():
    out, applied = strip_text("a\nb\n", StripOptions(newlines="crlf"))
    assert out == "a\r\nb\r\n"
    assert any("CRLF" in a for a in applied)


def test_strip_newlines_keep():
    raw = "a\r\nb"
    out, applied = strip_text(raw, StripOptions(newlines="keep", strip_unicode_junk=False))
    assert out == raw
    assert not any("newline" in a for a in applied)


# --- BOM / encoding ---------------------------------------------------------


def test_decode_utf8_bom():
    data = UTF8_BOM + b"hello"
    text, has_bom, enc, note = decode_bytes(data)
    assert text == "hello" and has_bom and enc == "utf-8"
    assert "BOM" in note


def test_scan_bytes_reports_bom_not_as_credibility():
    rep = scan_bytes(UTF8_BOM + b"clean\n")
    assert rep.has_bom
    assert rep.credibility == 0
    assert any(f.kind == "bom" for f in rep.findings)


def test_decode_cp1252_fallback():
    # 0x80 is undefined in latin-1 strict paths; cp1252 has euro at 0x80
    data = b"price \x80 5"
    text, has_bom, enc, note = decode_bytes(data)
    assert not has_bom
    assert enc == "cp1252"
    assert "fallback" in note
    assert "\u20ac" in text or text  # decoded something


def test_binary_suspect_null():
    rep = scan_bytes(b"abc\x00def")
    assert rep.binary_suspect
    assert rep.credibility == 0


# --- credibility / stego ----------------------------------------------------


def test_zwsp_raises_credibility():
    text = f"ignore{chr(0x200B)}previous"
    rep = scan_text(text)
    assert rep.credibility == 1
    assert any(f.label == "ZWSP" for f in rep.findings if f.credibility)


def test_bidi_rlo_raises_credibility():
    text = f"safe{chr(0x202E)}evil"
    assert credibility_of(text) == 1


def test_exotic_space_counts_and_normalizes_to_ascii():
    text = f"a{chr(0x2003)}b"  # EM SPACE
    rep = scan_text(text)
    assert rep.credibility == 1
    out, applied = strip_text(text)
    assert out == "a b"
    assert any("exotic-space" in a for a in applied)


def test_soft_hyphen_stripped():
    text = f"com{chr(0x00AD)}mand"
    out, _ = strip_text(text)
    assert out == "command"
    assert credibility_of(text) == 1


def test_zwj_emoji_not_stripped_and_not_credibility():
    """ZWJ is intentional for emoji graphemes — leave alone, no danger counter."""
    # woman + ZWJ + woman (simplified family piece)
    text = f"ok{chr(0x200D)}ok"
    rep = scan_text(text)
    assert rep.credibility == 0
    out, _ = strip_text(text)
    assert chr(0x200D) in out


def test_crlf_alone_zero_credibility():
    """Windows line endings are portability, not injection-class."""
    rep = scan_text("line1\r\nline2\r\n")
    assert rep.newlines == "crlf"
    assert rep.credibility == 0


def test_strip_default_pack_clears_credibility():
    dirty = f"x{chr(0x200B)}y\r\n"
    result = normalize_text(dirty)
    assert result.changed
    assert result.report_before.credibility == 1
    assert credibility_of(result.text) == 0
    assert "\r" not in result.text


# --- path I/O ---------------------------------------------------------------


def test_scan_path_and_strip_path_roundtrip(tmp_path: Path):
    p = tmp_path / "note.txt"
    # BOM + CRLF + ZWSP
    p.write_bytes(UTF8_BOM + f"hello{chr(0x200B)}\r\nworld\r\n".encode("utf-8"))
    before = scan_path(p)
    assert before.has_bom and before.credibility == 1 and before.newlines == "crlf"

    b2, result, err = strip_path(p, write=True)
    assert err is None and result.changed
    after = scan_path(p)
    assert after.credibility == 0
    assert after.has_bom is False
    assert after.newlines == "lf"
    assert after.text == "hello\nworld\n"


def test_strip_path_keep_bom_flag(tmp_path: Path):
    p = tmp_path / "b.txt"
    p.write_bytes(UTF8_BOM + b"hi\r\n")
    opts = StripOptions(strip_bom=False, newlines="lf", strip_unicode_junk=True)
    before, result, err = strip_path(p, opts, write=True)
    assert err is None
    assert "bom-drop" not in result.applied
    data = p.read_bytes()
    assert data.startswith(UTF8_BOM)
    assert data[len(UTF8_BOM):] == b"hi\n"


def test_strip_path_skips_binary(tmp_path: Path):
    p = tmp_path / "x.bin"
    p.write_bytes(b"\x00\x01\x02")
    before, result, err = strip_path(p, write=True)
    assert before.binary_suspect and err == "binary-suspect"
    assert p.read_bytes() == b"\x00\x01\x02"


def test_scan_missing_path(tmp_path: Path):
    rep = scan_path(tmp_path / "nope.txt")
    assert rep.error


def test_format_report_includes_credibility_block():
    rep = scan_text(f"a{chr(0x200B)}b\r\n")
    body = format_report(rep)
    assert "credibility: 1" in body
    assert "ZWSP" in body
    assert "newlines: CRLF" in body or "CRLF" in body


def test_multi_zwsp_counts():
    text = f"{chr(0x200B)}{chr(0x200B)}{chr(0x200B)}"
    assert credibility_of(text) == 3


def test_note_ingress_empty_when_clean():
    from xlii.text_hygiene import note_ingress

    assert note_ingress("plain text") == ""
    assert note_ingress("") == ""


def test_note_ingress_and_enrich_meta():
    from xlii.text_hygiene import enrich_meta, note_ingress

    dirty = f"a{chr(0x200B)}b"
    note = note_ingress(dirty, source="harness:cursor")
    assert "take note" in note and "credibility 1" in note and "cursor" in note
    meta = enrich_meta({"x": 1}, dirty, source="harness")
    assert meta["x"] == 1 and meta["credibility"] == 1
    assert "take note" in meta["hygiene_note"]
    assert enrich_meta({}, "clean") == {}
