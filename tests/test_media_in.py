"""Inbound media for the XMPP mouth (#3) — URL parsing + AES-GCM decrypt + the
fetch→materialize→attach flow, all against injected fetchers (no network, no
live file-share). Pins that a phone photo/PDF reaches iXaac's eyes as a real
local path, and that a plain text message is left byte-identical.
"""

from __future__ import annotations

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from types import SimpleNamespace

from xlii import media_in


# --------------------------------------------------------------------------- #
#  URL parsing
# --------------------------------------------------------------------------- #

def _aesgcm_url(host_path, iv: bytes, key: bytes):
    return f"aesgcm://{host_path}#{iv.hex()}{key.hex()}"


def test_parses_aesgcm_url_iv_first_then_key():
    iv, key = bytes(range(12)), bytes(range(32))
    ref = media_in.extract_media_refs("here " + _aesgcm_url("box.example/up/x.jpg", iv, key))[0]
    assert ref.encrypted
    assert ref.iv == iv and ref.key == key
    assert ref.https_url == "https://box.example/up/x.jpg"
    assert ref.filename == "x.jpg"


def test_parses_plain_https_media_link_but_not_arbitrary_links():
    refs = media_in.extract_media_refs(
        "pic https://cdn.example/a.png and site https://example.com/page")
    assert [r.https_url for r in refs] == ["https://cdn.example/a.png"]  # only the media file
    assert refs[0].encrypted is False


def test_no_urls_leaves_body_untouched():
    assert media_in.extract_media_refs("just a normal message") == []
    assert media_in.strip_media_urls("just a normal message") == "just a normal message"


def test_strip_media_urls_keeps_the_caption():
    body = "look at this " + _aesgcm_url("h/x.png", bytes(12), bytes(32))
    assert media_in.strip_media_urls(body) == "look at this"


def test_malformed_aesgcm_fragment_is_ignored():
    assert media_in.extract_media_refs("aesgcm://h/x.jpg#deadbeef") == []   # too-short fragment


# --------------------------------------------------------------------------- #
#  OOB extraction — where Conversations actually puts the media link
# --------------------------------------------------------------------------- #

def _stanza_with_oob(url, *, body=None):
    """A minimal slixmpp-like stanza: a .xml ElementTree root with an XEP-0066
    <x xmlns='jabber:x:oob'><url>…</url></x> and an optional <body>."""
    import xml.etree.ElementTree as ET

    root = ET.Element("{jabber:client}message")
    if body is not None:
        b = ET.SubElement(root, "{jabber:client}body")
        b.text = body
    x = ET.SubElement(root, "{jabber:x:oob}x")
    u = ET.SubElement(x, "{jabber:x:oob}url")
    u.text = url
    return SimpleNamespace(xml=root)


def test_oob_urls_reads_the_attachment_link():
    st = _stanza_with_oob("aesgcm://box/up/pic.jpg#abcd", body=None)
    assert media_in.oob_urls(st) == ["aesgcm://box/up/pic.jpg#abcd"]


def test_oob_urls_empty_when_none_present_or_no_xml():
    import xml.etree.ElementTree as ET
    assert media_in.oob_urls(SimpleNamespace(xml=ET.Element("{jabber:client}message"))) == []
    assert media_in.oob_urls(SimpleNamespace(xml=None)) == []
    assert media_in.oob_urls(object()) == []            # no .xml attr → no crash


# --------------------------------------------------------------------------- #
#  AES-GCM decrypt (round-trip with a self-made vector)
# --------------------------------------------------------------------------- #

def test_decrypt_aesgcm_round_trips():
    key, iv, plaintext = bytes(range(32)), bytes(range(12)), b"the CNC part looks fine"
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)   # tag appended, as XEP-0454 uploads
    assert media_in.decrypt_aesgcm(ciphertext, key, iv) == plaintext


# --------------------------------------------------------------------------- #
#  materialize + prepare (injected fetch — no network)
# --------------------------------------------------------------------------- #

def test_materialize_decrypts_and_writes_the_file(tmp_path):
    key, iv, plaintext = bytes(range(32)), bytes(range(12)), b"\x89PNG fake image bytes"
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)
    url = _aesgcm_url("box/up/photo.png", iv, key)
    ref = media_in.extract_media_refs(url)[0]

    path = media_in.materialize(ref, tmp_path, fetch=lambda u, n: ciphertext)
    assert path.read_bytes() == plaintext           # fetched ciphertext → decrypted plaintext
    assert path.name == "photo.png" and path.parent == tmp_path


def test_prepare_returns_caption_and_paths(tmp_path):
    key, iv, plaintext = bytes(range(32)), bytes(range(12)), b"doc"
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)
    body = "what is this? " + _aesgcm_url("box/up/report.pdf", iv, key)

    caption, paths = media_in.prepare(body, tmp_path, fetch=lambda u, n: ciphertext)
    assert caption == "what is this?"
    assert len(paths) == 1 and paths[0].read_bytes() == plaintext


def test_prepare_defaults_the_caption_when_body_is_only_the_attachment(tmp_path):
    key, iv = bytes(range(32)), bytes(range(12))
    ciphertext = AESGCM(key).encrypt(iv, b"x", None)
    body = _aesgcm_url("box/up/pic.jpg", iv, key)
    caption, paths = media_in.prepare(body, tmp_path, fetch=lambda u, n: ciphertext)
    assert caption == media_in.DEFAULT_MEDIA_PROMPT and len(paths) == 1


def test_prepare_skips_a_failing_fetch_without_sinking_the_turn(tmp_path):
    def boom(url, n):
        raise OSError("gone")
    body = "hey " + _aesgcm_url("box/up/pic.jpg", bytes(12), bytes(32))
    caption, paths = media_in.prepare(body, tmp_path, fetch=boom)
    assert caption == "hey" and paths == []          # best-effort: caption survives, no path


def test_prepare_is_identity_for_plain_text(tmp_path):
    assert media_in.prepare("just text", tmp_path) == ("just text", [])


def test_strip_media_urls_keeps_non_media_https_links(tmp_path):
    key, iv = bytes(range(32)), bytes(range(12))
    ciphertext = AESGCM(key).encrypt(iv, b"x", None)
    body = (
        "compare https://example.com/page with "
        + _aesgcm_url("box/up/pic.jpg", iv, key)
    )
    caption, paths = media_in.prepare(body, tmp_path, fetch=lambda u, n: ciphertext)
    assert "https://example.com/page" in caption
    assert len(paths) == 1


def test_prepare_uses_failure_prompt_when_only_attachment_fails(tmp_path):
    body = _aesgcm_url("box/up/pic.jpg", bytes(12), bytes(32))
    caption, paths = media_in.prepare(body, tmp_path, fetch=lambda u, n: (_ for _ in ()).throw(OSError("gone")))
    assert paths == []
    assert caption == media_in.DOWNLOAD_FAILED_PROMPT


def test_materialize_allocates_unique_names_for_same_basename(tmp_path):
    key, iv, plaintext = bytes(range(32)), bytes(range(12)), b"same"
    ciphertext = AESGCM(key).encrypt(iv, plaintext, None)
    ref = media_in.extract_media_refs(_aesgcm_url("box/up/photo.png", iv, key))[0]

    first = media_in.materialize(ref, tmp_path, fetch=lambda u, n: ciphertext)
    second = media_in.materialize(ref, tmp_path, fetch=lambda u, n: ciphertext)
    assert first != second
    assert first.name == "photo.png"
    assert second.name == "photo-2.png"


def test_redact_for_audit_strips_aesgcm_key_material():
    iv, key = bytes(range(12)), bytes(range(32))
    url = _aesgcm_url("box.example/up/x.jpg", iv, key)
    redacted = media_in.redact_for_audit(f"look {url}")
    assert key.hex() not in redacted
    assert iv.hex() not in redacted
    assert "aesgcm://box.example/up/x.jpg#<redacted>" in redacted
    assert "look" in redacted


def test_prepare_enforces_attachment_count_and_byte_budget(tmp_path):
    key, iv = bytes(range(32)), bytes(range(12))
    chunk = b"x" * (1024 * 1024)  # 1 MiB each
    ciphertext = AESGCM(key).encrypt(iv, chunk, None)
    refs = [_aesgcm_url(f"box/up/f{i}.png", iv, key) for i in range(10)]
    body = " ".join(refs)
    caption, paths = media_in.prepare(body, tmp_path, fetch=lambda u, n: ciphertext)
    assert len(paths) == media_in.MAX_MEDIA_ATTACHMENTS
    total = sum(p.stat().st_size for p in paths)
    assert total <= media_in.MAX_MEDIA_BYTES
    assert caption == media_in.DEFAULT_MEDIA_PROMPT
