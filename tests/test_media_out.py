"""Outbound media crypto+URL (`xlii/media_out.py`) — pinned against media_in.

The critical property: what media_out produces, media_in (and every OMEMO
client) can parse and decrypt — the two halves are inverses.
"""

from __future__ import annotations

import pytest

from xlii import media_in, media_out


def test_encrypt_round_trips_through_media_in_decrypt():
    data = b"\x89PNG a generated render"
    ciphertext, iv, key = media_out.encrypt_for_share(data)
    assert len(iv) == 12 and len(key) == 32
    assert ciphertext != data and len(ciphertext) == len(data) + 16   # tag appended
    assert media_in.decrypt_aesgcm(ciphertext, key, iv) == data


def test_fresh_key_and_iv_per_file():
    _, iv1, key1 = media_out.encrypt_for_share(b"a")
    _, iv2, key2 = media_out.encrypt_for_share(b"a")
    assert (iv1, key1) != (iv2, key2)


def test_aesgcm_url_layout_parses_back_via_media_in():
    _, iv, key = media_out.encrypt_for_share(b"x")
    url = media_out.aesgcm_url("https://home.example/file_share/abc/render.png", iv, key)
    assert url.startswith("aesgcm://home.example/file_share/abc/render.png#")
    ref = media_in.extract_media_refs(url)[0]           # the inverse parser
    assert ref.encrypted and ref.iv == iv and ref.key == key
    assert ref.https_url == "https://home.example/file_share/abc/render.png"
    assert ref.filename == "render.png"


def test_aesgcm_url_refuses_non_https_and_strips_fragments():
    _, iv, key = media_out.encrypt_for_share(b"x")
    with pytest.raises(ValueError):
        media_out.aesgcm_url("http://host/f.png", iv, key)
    url = media_out.aesgcm_url("https://host/f.png#oldfrag", iv, key)
    assert url.count("#") == 1 and "oldfrag" not in url


def test_mime_for_common_reply_types():
    assert media_out.mime_for("render.png") == "image/png"
    assert media_out.mime_for("doc.pdf") == "application/pdf"
    assert media_out.mime_for("say.m4a") == "audio/mp4"
    assert media_out.mime_for("mystery.bin") == "application/octet-stream"


def test_prepare_upload_is_the_daemon_ready_bundle(tmp_path):
    p = tmp_path / "render.png"
    p.write_bytes(b"\x89PNG payload")
    name, mime, ciphertext, iv, key = media_out.prepare_upload(p)
    assert (name, mime) == ("render.png", "image/png")
    assert media_in.decrypt_aesgcm(ciphertext, key, iv) == b"\x89PNG payload"


# --------------------------------------------------------------------------- #
#  send_file — the agent's hand into the outbox
# --------------------------------------------------------------------------- #

def _ctx(tmp_path, outbox=None):
    from types import SimpleNamespace

    from xlii.tool_context import ToolContext
    return ToolContext(
        project=SimpleNamespace(project_root=tmp_path),
        clients=SimpleNamespace(),
        cfg=SimpleNamespace(),
        outbox_dir=outbox,
    )


def test_send_file_queues_a_byte_identical_copy(tmp_path):
    from xlii.tool_handlers import t_send_file

    src = tmp_path / "sign.png"
    src.write_bytes(b"\x89PNG shop open")
    ob = tmp_path / "ob"
    res = t_send_file(_ctx(tmp_path, ob), {"path": str(src)})
    assert not res.is_error and "queued sign.png" in res.content
    assert (ob / "sign.png").read_bytes() == b"\x89PNG shop open"


def test_send_file_resolves_project_relative_paths(tmp_path):
    from xlii.tool_handlers import t_send_file

    (tmp_path / ".xlii" / "artifacts").mkdir(parents=True)
    (tmp_path / ".xlii" / "artifacts" / "a.png").write_bytes(b"x")
    ob = tmp_path / "ob"
    res = t_send_file(_ctx(tmp_path, ob), {"path": ".xlii/artifacts/a.png"})
    assert not res.is_error and (ob / "a.png").exists()


def test_send_file_refuses_paths_outside_project(tmp_path):
    from xlii.tool_handlers import t_send_file

    outside = tmp_path.parent / "secret.txt"
    outside.write_text("host secret")
    ob = tmp_path / "ob"

    res = t_send_file(_ctx(tmp_path, ob), {"path": str(outside)})
    assert res.is_error and "escapes project root" in res.content
    assert not (ob / "secret.txt").exists()

    link = tmp_path / "linked-secret.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")
    res = t_send_file(_ctx(tmp_path, ob), {"path": "linked-secret.txt"})
    assert res.is_error and "escapes project root" in res.content
    assert not (ob / "linked-secret.txt").exists()


def test_send_file_same_name_twice_keeps_both(tmp_path):
    from xlii.tool_handlers import t_send_file

    src = tmp_path / "a.png"
    src.write_bytes(b"one")
    ob = tmp_path / "ob"
    t_send_file(_ctx(tmp_path, ob), {"path": str(src)})
    src.write_bytes(b"two")
    t_send_file(_ctx(tmp_path, ob), {"path": str(src)})
    assert (ob / "a.png").read_bytes() == b"one"
    assert (ob / "1-a.png").read_bytes() == b"two"


def test_send_file_refusals(tmp_path):
    from xlii.tool_handlers import t_send_file

    src = tmp_path / "a.png"
    src.write_bytes(b"x")
    # No outbox → no delivery channel (belt under the advertise gate).
    res = t_send_file(_ctx(tmp_path, None), {"path": str(src)})
    assert res.is_error and "no delivery channel" in res.content
    ob = tmp_path / "ob"
    assert t_send_file(_ctx(tmp_path, ob), {"path": ""}).is_error
    assert t_send_file(_ctx(tmp_path, ob), {"path": str(tmp_path / "ghost.png")}).is_error


def test_send_file_respects_the_media_size_cap(tmp_path, monkeypatch):
    import xlii.tool_handlers as th

    src = tmp_path / "big.bin"
    src.write_bytes(b"toolarge")
    monkeypatch.setattr("xlii.media_in.MAX_MEDIA_BYTES", 4)
    res = th.t_send_file(_ctx(tmp_path, tmp_path / "ob"), {"path": str(src)})
    assert res.is_error and "delivery cap" in res.content


# --------------------------------------------------------------------------- #
#  apply_outbox_gate — the palette exists exactly when a mouth can deliver
# --------------------------------------------------------------------------- #

def _names(schemas):
    return [s["function"]["name"] for s in schemas]


def test_gate_strips_send_file_without_an_outbox():
    from xlii.tool_schemas import apply_outbox_gate, tool_schemas

    out = apply_outbox_gate(tool_schemas(), None)
    assert "send_file" not in _names(out)
    assert "generate_image" in _names(out)      # unrelated tools untouched


def test_gate_grants_the_delivery_palette_past_chat_blind(tmp_path):
    from xlii.mode_contract import chat_tools_policy
    from xlii.tool_schemas import apply_outbox_gate, tool_schemas

    # The daemon persona surface: chat-blind allowlist first, then the grant.
    policy = chat_tools_policy()
    blind = [s for s in tool_schemas() if policy.permits(s["function"]["name"])]
    assert "send_file" not in _names(blind)

    out = apply_outbox_gate(blind, tmp_path)
    assert "send_file" in _names(out) and "generate_image" in _names(out)
    # And no duplicates when the palette is already present (full surface).
    full = apply_outbox_gate(tool_schemas(), tmp_path)
    assert _names(full).count("send_file") == 1
    assert _names(full).count("generate_image") == 1


def test_gate_never_grants_past_a_mode_controller(tmp_path):
    """With every LOCAL session now carrying an outbox (tui-media-delivery P0),
    the grant must not inject paid tools into a mode controller's curated
    palette — agent.run_turn passes None to the gate when a mode is active, so
    /plan's read-only palette never gains generate_image/send_file."""
    from xlii.mode_contract import chat_tools_policy
    from xlii.tool_schemas import apply_outbox_gate, tool_schemas

    # The agent-side contract: a mode turn calls the gate with None even when
    # session.outbox_dir is set — assert the None path only ever strips.
    policy = chat_tools_policy()
    blind = [s for s in tool_schemas() if policy.permits(s["function"]["name"])]
    out = apply_outbox_gate(blind, None)
    assert "send_file" not in _names(out) and "generate_image" not in _names(out)
    # And the caller passes None exactly when active_mode is not None:
    import inspect

    from xlii import agent as agent_mod
    src = inspect.getsource(agent_mod.Agent.run_turn)
    assert "if self.active_mode is None else None" in src


def test_outbox_dir_threads_from_flat_session_state(tmp_path):
    from xlii.session_state import SessionState
    from xlii.tool_context import ToolContext

    s = SessionState.from_flat(yolo=True, conversational=True, outbox_dir=tmp_path)
    assert s.outbox_dir == tmp_path
    assert SessionState.from_flat().outbox_dir is None
    assert ToolContext.__dataclass_fields__["outbox_dir"].default is None
