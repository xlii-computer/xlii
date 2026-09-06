"""U0 of the upload locker (proposals/upload-locker.md): folding files into a
user turn. Pure `prepare_user_turn` coverage + a `run_turn` integration check
that an image rides the turn but does NOT linger as base64 in history.
"""

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

import xlii.multimodal as M
from tests.helpers import make_agent, make_cfg


def _minimal_pdf(text: str = "Hello PDF World") -> bytes:
    """A tiny valid PDF with one line of extractable text (verified vs pypdf)."""
    content = b"BT /F1 24 Tf 72 700 Td (%s) Tj ET" % text.encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
         b"/Resources << /Font << /F1 5 0 R >> >> >>"),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_pos = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offsets:
        pdf += b"%010d 00000 n \n" % off
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref_pos)
    return pdf


def _img(path: Path, data: bytes = b"PNGBYTES") -> Path:
    # prepare_user_turn keys off the suffix + size, not PNG magic, so any bytes do.
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------------- #
#  prepare_user_turn — the pure fold
# --------------------------------------------------------------------------- #

def test_text_only_turn_is_byte_identical():
    p = M.prepare_user_turn("hello world")
    assert p.outgoing == "hello world"      # same string, no parts array
    assert p.compact == "hello world"
    assert p.has_images is False
    assert M.prepare_user_turn("hi", []).outgoing == "hi"   # empty list == none


def test_image_becomes_parts_array_with_base64_data_url(tmp_path):
    img = _img(tmp_path / "shot.png", b"PNGBYTES")
    p = M.prepare_user_turn("what is this?", [img])
    assert p.has_images is True
    assert isinstance(p.outgoing, list)
    assert p.outgoing[0] == {"type": "text", "text": "what is this?"}
    part = p.outgoing[1]
    assert part["type"] == "image_url"
    url = part["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"PNGBYTES"
    assert part["image_url"]["detail"] == "high"
    # the history form drops the blob, keeps a readable placeholder
    assert "shot.png" in p.compact and "base64" not in p.compact


def test_jpg_mime_and_multiple_images_no_text(tmp_path):
    a = _img(tmp_path / "a.jpg", b"A")
    b = _img(tmp_path / "b.jpeg", b"B")
    p = M.prepare_user_turn("", [a, b])
    assert p.has_images
    assert [part["type"] for part in p.outgoing] == ["image_url", "image_url"]
    assert p.outgoing[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_text_file_folds_into_string_not_parts(tmp_path):
    f = tmp_path / "notes.md"
    f.write_text("# Title\nbody")
    p = M.prepare_user_turn("read this", [f])
    assert p.has_images is False
    assert isinstance(p.outgoing, str)
    assert "notes.md" in p.outgoing and "# Title" in p.outgoing


def test_oversize_image_skipped_with_note(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "MAX_IMAGE_BYTES", 4)
    big = _img(tmp_path / "big.png", b"toolarge")
    p = M.prepare_user_turn("look", [big])
    assert p.has_images is False            # nothing sent as an image
    assert isinstance(p.outgoing, str)
    assert "too large" in p.outgoing and "big.png" in p.outgoing


def test_missing_and_unsupported_files_degrade_to_notes(tmp_path):
    missing = tmp_path / "ghost.png"        # never created
    weird = tmp_path / "data.bin"
    weird.write_bytes(b"\x00\x01\x02")
    p = M.prepare_user_turn("x", [missing, weird])
    assert p.has_images is False
    assert "not found" in p.outgoing
    assert "unsupported type" in p.outgoing


# --------------------------------------------------------------------------- #
#  run_turn integration — image in, compacted out
# --------------------------------------------------------------------------- #

def test_run_turn_sends_image_then_compacts_history(tmp_path):
    agent = make_agent(tmp_path, model_override="grok-4")   # vision-capable
    img = _img(tmp_path / "pic.png", b"IMG")
    seen = {}

    def fake(*a, **k):
        user = [m for m in agent.history if m["role"] == "user"][-1]
        seen["content"] = user["content"]   # what the model receives this turn
        return (SimpleNamespace(content="done", tool_calls=None), None, False)

    agent._stream_orchestrator_iteration = fake
    agent.run_turn("describe", attachments=[img])

    # during the turn: a parts array carrying the image
    assert isinstance(seen["content"], list)
    assert any(part.get("type") == "image_url" for part in seen["content"])
    # after the turn: compacted back to a plain string (no base64 lingering)
    user_after = [m for m in agent.history if m["role"] == "user"][-1]
    assert isinstance(user_after["content"], str)
    assert "pic.png" in user_after["content"]
    assert "base64" not in user_after["content"]


def test_run_turn_text_only_history_untouched(tmp_path):
    agent = make_agent(tmp_path)
    agent._stream_orchestrator_iteration = lambda *a, **k: (
        SimpleNamespace(content="ok", tool_calls=None), None, False
    )
    agent.run_turn("just text")
    user = [m for m in agent.history if m["role"] == "user"][-1]
    assert user["content"] == "just text"   # plain string, untouched


# --------------------------------------------------------------------------- #
#  vision gate: don't send images to a model that can't see them (the hang fix)
# --------------------------------------------------------------------------- #

def test_model_supports_vision():
    assert M.model_supports_vision("grok-4")
    assert M.model_supports_vision("grok-4-1-fast-non-reasoning")
    assert M.model_supports_vision("grok-4.3")
    assert M.model_supports_vision("grok-2-vision-1212")
    assert not M.model_supports_vision("grok-build-0.1")
    assert not M.model_supports_vision("grok-code-fast-1")
    assert not M.model_supports_vision("grok-3")
    assert not M.model_supports_vision(None)


def test_image_omitted_for_non_vision_model(tmp_path):
    agent = make_agent(tmp_path)   # cfg default "orch-model" → not vision-capable
    img = _img(tmp_path / "p.png", b"IMG")
    seen = {}

    def fake(*a, **k):
        user = [m for m in agent.history if m["role"] == "user"][-1]
        seen["content"] = user["content"]
        return (SimpleNamespace(content="ok", tool_calls=None), None, False)

    agent._stream_orchestrator_iteration = fake
    agent.run_turn("what is this?", attachments=[img])

    # the image is NOT sent as a parts array — it's text with an explicit note,
    # so the model can't stall on input it can't read (and won't hallucinate)
    assert isinstance(seen["content"], str)
    assert "omitted" in seen["content"].lower() and "p.png" in seen["content"]
    assert "can't see images" in agent.console.text     # the user was warned
    # code surface resolves the orchestrator role — point the fix there
    assert "--orchestrator" in agent.console.text and "--chat" not in agent.console.text


def test_image_omission_hint_targets_chat_role_on_chat_surface(tmp_path):
    # On a chat/howto surface the vision model comes from the `chat` role, so the
    # fix hint must say `--chat`, not `--orchestrator` (which would be a dead end
    # — setting the orchestrator slot wouldn't change the chat model in play).
    # (Bugbot PR#75: "Chat vision hints wrong role")
    agent = make_agent(tmp_path)            # chat-model is non-vision by default
    agent.session.conversational = True
    img = _img(tmp_path / "p.png", b"IMG")
    agent._stream_orchestrator_iteration = lambda *a, **k: (
        SimpleNamespace(content="ok", tool_calls=None), None, False
    )
    agent.run_turn("what is this?", attachments=[img])

    txt = agent.console.text
    assert "can't see images" in txt
    assert "--chat" in txt and "--orchestrator" not in txt


def test_image_omission_hint_targets_pinned_model_override(tmp_path):
    # A loadout/persona/skill pin shadows the role config, so `xlii models set`
    # would not change the active model for the next turn.
    cfg = make_cfg(orchestrator="grok-4", chat="grok-4")
    agent = make_agent(tmp_path, cfg=cfg, model_override="grok-build-0.1")
    agent.session.conversational = True
    img = _img(tmp_path / "p.png", b"IMG")
    agent._stream_orchestrator_iteration = lambda *a, **k: (
        SimpleNamespace(content="ok", tool_calls=None), None, False
    )
    agent.run_turn("what is this?", attachments=[img])

    txt = agent.console.text
    assert "can't see images" in txt
    assert "`/model grok-4`" in txt
    assert "--chat" not in txt and "--orchestrator" not in txt


# --------------------------------------------------------------------------- #
#  U3 — kind handlers: PDF text, oversize, graceful refusal
# --------------------------------------------------------------------------- #

def test_pdf_branch_inlines_extracted_text(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setattr(M, "_extract_pdf_text", lambda p, **k: "EXTRACTED BODY")
    p = M.prepare_user_turn("read this", [pdf])
    assert p.has_images is False
    assert isinstance(p.outgoing, str)
    assert "EXTRACTED BODY" in p.outgoing and "doc.pdf" in p.outgoing


def test_pdf_branch_note_when_no_extractor(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setattr(M, "_extract_pdf_text", lambda p, **k: None)
    p = M.prepare_user_turn("read", [pdf])
    assert "files extra" in p.outgoing or "xlii[files]" in p.outgoing


def test_pdf_branch_note_when_scanned(tmp_path, monkeypatch):
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    monkeypatch.setattr(M, "_extract_pdf_text", lambda p, **k: "")
    p = M.prepare_user_turn("read", [pdf])
    assert "no extractable text" in p.outgoing


def test_pdf_oversize_not_read(tmp_path, monkeypatch):
    pdf = tmp_path / "big.pdf"
    pdf.write_bytes(b"%PDF-1.4" + b"x" * 200)
    monkeypatch.setattr(M, "_MAX_PDF_BYTES", 8)
    # extractor must NOT even be called for an oversize file
    monkeypatch.setattr(M, "_extract_pdf_text",
                        lambda p, **k: pytest.fail("extractor called on oversize PDF"))
    p = M.prepare_user_turn("x", [pdf])
    assert "too large" in p.outgoing and "big.pdf" in p.outgoing


def test_oversize_image_and_pdf_dont_crash(tmp_path, monkeypatch):
    # The U3 gate: a too-big file and a PDF are handled, not crashed.
    monkeypatch.setattr(M, "MAX_IMAGE_BYTES", 4)
    monkeypatch.setattr(M, "_MAX_PDF_BYTES", 4)
    big_img = tmp_path / "huge.png"
    big_img.write_bytes(b"x" * 100)
    pdf = tmp_path / "d.pdf"
    pdf.write_bytes(b"%PDF-1.4" + b"x" * 100)
    p = M.prepare_user_turn("here", [big_img, pdf])   # must not raise
    assert p.has_images is False
    assert "too large" in p.outgoing


def test_pdf_real_extraction_end_to_end(tmp_path):
    pytest.importorskip("pypdf")
    pdf = tmp_path / "hello.pdf"
    pdf.write_bytes(_minimal_pdf("Hello PDF World"))
    assert "Hello PDF World" in (M._extract_pdf_text(pdf) or "")
    p = M.prepare_user_turn("what does this say?", [pdf])
    assert p.has_images is False
    assert "Hello PDF World" in p.outgoing
