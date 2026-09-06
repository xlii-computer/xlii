"""serve-public joint seam — every producer and consumer on ONE state dir.

The conductor's cross-vector pin (fleet issues #283/#284): the admin CLI (V3),
the fabric daemon (V5), and the serve process (V2) each read/write the grant
spool, session mirror, and revoke queue. These tests run the full loop offline
at PRODUCTION path defaults (only XLII_STATE_DIR pointed at tmp), so any future
divergence in paths or file shapes fails here, not silently in the field.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from xlii.serve_gate import GateStore
from xlii.serve_spool import default_state_dir, drain_pending, drain_revokes


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path))
    assert default_state_dir() == tmp_path
    return tmp_path


def _cfg(base_url="https://xlii-code.com"):
    from xlii.config import GlobalConfig

    cfg = GlobalConfig()
    cfg.serve = {"public": {"base_url": base_url}}
    return cfg


def test_cli_mint_to_serve_pair_round_trip(state_dir):
    """`xlii serve mint` (V3, default paths) → serve drain (V2) → pair (V1)."""
    from xlii.cmds.serve_admin import mint_pairing_code

    code, line = mint_pairing_code(default_state_dir(), cfg=_cfg(), now=1_000.0)
    assert code in line

    gate = GateStore()
    for item in drain_pending(default_state_dir()):     # serve side, defaults
        gate.add_pending(item["code"], ttl_s=item["ttl_s"],
                         mode=item["mode"], now=item["minted_at"])
    session = gate.consume(code, now=1_010.0, remote="203.0.113.9")
    assert session is not None and session.mode == "full"
    # Single-use: the spool is empty and the code is burned.
    assert drain_pending(default_state_dir()) == []
    assert gate.consume(code, now=1_011.0, remote="203.0.113.9") is None


def test_phone_mint_to_serve_pair_round_trip(state_dir):
    """fabric `webcode` (V5, daemon state dir) → serve drain (V2) → pair."""
    from xlii.daemon_gate import WEBCODE_REPLY_SEP, daemon_state_dir, mint_webcode_to_spool

    daemon_dir = daemon_state_dir(SimpleNamespace(audit_log=state_dir / "elsewhere" / "a.log"))
    assert daemon_dir == default_state_dir()            # the #283-sibling pin

    reply = mint_webcode_to_spool(
        daemon_dir, mode="preview", ttl_s=300, now=2_000.0,
        base_url="https://xlii-code.com",
    )
    assert WEBCODE_REPLY_SEP in reply and "code: " in reply
    code = reply.split(WEBCODE_REPLY_SEP)[0].removeprefix("code: ").strip()

    gate = GateStore()
    for item in drain_pending(default_state_dir()):
        gate.add_pending(item["code"], ttl_s=item["ttl_s"],
                         mode=item["mode"], now=item["minted_at"])
    session = gate.consume(code, now=2_010.0, remote="203.0.113.9")
    assert session is not None and session.mode == "preview"


def test_mirror_written_by_serve_reads_everywhere(state_dir):
    """V2's mirror writer → V3's admin reader AND V5's webcode ls reader."""
    from xlii.cmds import serve_admin
    from xlii.daemon_gate import format_sessions_ls, read_sessions_mirror
    from xlii.serve_public import _mirror_sessions

    gate = GateStore()
    gate.add_pending("X7K2M9Q4", ttl_s=300, mode="full", now=3_000.0)
    session = gate.consume("X7K2-M9Q4", now=3_001.0, remote="203.0.113.9")
    assert session is not None
    _mirror_sessions(default_state_dir(), gate.sessions())

    admin_view = serve_admin.read_sessions_mirror(default_state_dir())
    daemon_view = read_sessions_mirror(default_state_dir())
    assert [s["id"] for s in admin_view] == [session.id]
    assert [s["id"] for s in daemon_view] == [session.id]
    assert session.id in format_sessions_ls(daemon_view, now=3_050.0)


def test_both_kill_paths_land_in_one_queue_and_serve_drains_it(state_dir):
    """V3 `serve revoke` + V5 `webcode kill` → ONE deduped queue → V2 drain."""
    from xlii.cmds import serve_admin
    from xlii.daemon_gate import append_revoke as daemon_append
    from xlii.serve_public import _mirror_sessions

    gate = GateStore()
    gate.add_pending("X7K2M9Q4", ttl_s=300, mode="full", now=4_000.0)
    session = gate.consume("X7K2M9Q4", now=4_001.0, remote="203.0.113.9")
    assert session is not None
    _mirror_sessions(default_state_dir(), gate.sessions())

    # CLI revoke (mirror-verified) + phone kill of the same sid + phone kill all.
    assert serve_admin.revoke_session(default_state_dir(), session.id) is True
    daemon_append(default_state_dir(), session.id)      # dedupes to one entry
    daemon_append(default_state_dir(), "all")

    queued = json.loads((state_dir / "serve-revokes.json").read_text())["pending"]
    assert queued == [session.id, "all"]

    targets = drain_revokes(default_state_dir())        # serve's sweep side
    assert targets == [session.id, "all"]
    for sid in ([s.id for s in gate.sessions()] if "all" in targets
                else [t for t in targets]):
        gate.revoke(sid)
    assert gate.sessions() == []
    assert drain_revokes(default_state_dir()) == []     # consumed, not sticky
