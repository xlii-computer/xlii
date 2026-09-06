"""serve-public V3 — config accessors + on-box admin CLI (mint/sessions/revoke).

Pinned against the merge contract in proposals/serve-public-fleet.md.
Run:  pytest tests/test_serve_admin.py
"""

from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from xlii.cmds import serve_admin as SA
from xlii.config import (
    DEFAULT_SERVE_PUBLIC_CODE_TTL_S,
    DEFAULT_SERVE_PUBLIC_IDLE_TIMEOUT_S,
    DEFAULT_SERVE_PUBLIC_MAX_SESSIONS,
    DEFAULT_SERVE_PUBLIC_SESSION_TTL_S,
    MISSING_SERVE_PUBLIC_BASE_URL,
    GlobalConfig,
)
from xlii.serve_gate import mint_code, normalize_code
from xlii.serve_spool import drain_pending, spool_path


def _cfg(**public_kw) -> GlobalConfig:
    cfg = GlobalConfig()
    cfg.serve = {"public": dict(public_kw)}
    return cfg


def _fake_mirror(state_dir: Path, sessions: list[dict]) -> None:
    SA.write_sessions_mirror(state_dir, sessions)


# --------------------------------------------------------------------------- #
#  Config — [serve.public] accessors + --public without base_url
# --------------------------------------------------------------------------- #


def test_serve_public_defaults_when_block_absent():
    cfg = GlobalConfig()
    assert cfg.serve_public_base_url is None
    assert cfg.serve_public_code_ttl_s == DEFAULT_SERVE_PUBLIC_CODE_TTL_S
    assert cfg.serve_public_session_ttl_s == DEFAULT_SERVE_PUBLIC_SESSION_TTL_S
    assert cfg.serve_public_idle_timeout_s == DEFAULT_SERVE_PUBLIC_IDLE_TIMEOUT_S
    assert cfg.serve_public_max_sessions == DEFAULT_SERVE_PUBLIC_MAX_SESSIONS
    assert cfg.serve_public_face_default is True


def test_serve_public_accessors_read_nested_block():
    cfg = _cfg(
        base_url="https://xlii-code.com",
        code_ttl_s=120,
        session_ttl_s=3600,
        idle_timeout_s=600,
        max_sessions=2,
    )
    assert cfg.serve_public_base_url == "https://xlii-code.com"
    assert cfg.serve_public_code_ttl_s == 120
    assert cfg.serve_public_session_ttl_s == 3600
    assert cfg.serve_public_idle_timeout_s == 600
    assert cfg.serve_public_max_sessions == 2


def test_serve_public_base_url_strips_and_empty_is_none():
    assert _cfg(base_url="  https://a.example  ").serve_public_base_url == "https://a.example"
    assert _cfg(base_url="").serve_public_base_url is None
    assert _cfg(base_url="   ").serve_public_base_url is None


def test_serve_public_closed_door_knobs():
    # Defaults: off / unset.
    cfg = GlobalConfig()
    assert cfg.serve_public_closed_door is False
    assert cfg.serve_public_redirect_off_url is None
    # Strict bool — only JSON true counts (inverse of the is-int trap).
    assert _cfg(closed_door=True).serve_public_closed_door is True
    assert _cfg(closed_door=1).serve_public_closed_door is False
    assert _cfg(closed_door="yes").serve_public_closed_door is False
    # redirect_off_url strips; empty / non-str is None.
    assert (
        _cfg(redirect_off_url=" https://example.org ").serve_public_redirect_off_url
        == "https://example.org"
    )
    assert _cfg(redirect_off_url="").serve_public_redirect_off_url is None
    assert _cfg(redirect_off_url=7).serve_public_redirect_off_url is None


def test_require_serve_public_base_url_friendly_error():
    cfg = _cfg()  # no base_url
    with pytest.raises(ValueError) as ei:
        cfg.require_serve_public_base_url()
    assert str(ei.value) == MISSING_SERVE_PUBLIC_BASE_URL
    assert "base_url" in str(ei.value)
    assert "serve.public" in str(ei.value)


def test_require_serve_public_base_url_ok():
    assert _cfg(base_url="https://xlii-code.com").require_serve_public_base_url() == (
        "https://xlii-code.com"
    )


def test_serve_public_round_trips_through_save(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_DIR", tmp_path)
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_FILE", tmp_path / "config.json")
    cfg = GlobalConfig()
    cfg.serve = {
        "public": {
            "base_url": "https://example.test",
            "code_ttl_s": 90,
            "session_ttl_s": 7200,
            "idle_timeout_s": 900,
            "max_sessions": 5,
        },
    }
    cfg.save()
    data = json.loads((tmp_path / "config.json").read_text())
    assert data["serve"]["public"]["base_url"] == "https://example.test"
    assert data["serve"]["public"]["code_ttl_s"] == 90
    loaded = GlobalConfig.load()
    assert loaded.serve_public_base_url == "https://example.test"
    assert loaded.serve_public_code_ttl_s == 90
    assert loaded.serve_public_max_sessions == 5


# --------------------------------------------------------------------------- #
#  mint — spool write
# --------------------------------------------------------------------------- #


def test_mint_writes_well_formed_spool_entry(tmp_path):
    cfg = _cfg(base_url="https://xlii-code.com", code_ttl_s=300)
    code, line = SA.mint_pairing_code(tmp_path, cfg=cfg, mode="full", now=1_700_000_000.0)

    assert "-" in code and len(normalize_code(code)) == 8
    assert "code:" in line
    assert "https://xlii-code.com" in line
    assert "expires in 5m" in line

    path = spool_path(tmp_path)
    assert path.is_file()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert len(data["pending"]) == 1
    entry = data["pending"][0]
    assert entry["code"] == normalize_code(code)
    assert entry["ttl_s"] == 300
    assert entry["mode"] == "full"
    assert entry["minted_at"] == 1_700_000_000.0


def test_mint_preview_mode(tmp_path):
    cfg = _cfg(base_url="https://xlii-code.com")
    code, _ = SA.mint_pairing_code(tmp_path, cfg=cfg, mode="preview", now=10.0)
    pending = drain_pending(tmp_path)
    assert len(pending) == 1
    assert pending[0]["code"] == normalize_code(code)
    assert pending[0]["mode"] == "preview"


def test_mint_without_base_url_errors(tmp_path):
    cfg = _cfg()
    with pytest.raises(ValueError) as ei:
        SA.mint_pairing_code(tmp_path, cfg=cfg)
    assert "base_url" in str(ei.value)


def test_cmd_serve_mint_prints_and_exits_clean(tmp_path, monkeypatch, capsys):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps({"serve": {"public": {"base_url": "https://xlii-code.com"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_FILE", cfg_dir / "config.json")

    state = tmp_path / "state"
    state.mkdir()
    rc = SA.cmd_serve_mint(SimpleNamespace(preview=False, state_dir=str(state), email=False))
    assert rc == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 2
    assert out[0].startswith("code:")
    assert "https://xlii-code.com" in out[0]
    assert len(normalize_code(out[1])) == 8
    pending = drain_pending(state)
    assert len(pending) == 1


def test_cmd_serve_mint_email_sends_the_link(tmp_path, monkeypatch, capsys):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(
        json.dumps({
            "serve": {"public": {"base_url": "https://xlii-code.com"}},
            "email_accounts": {
                "personal": {
                    "imap_host": "imap.test",
                    "smtp_host": "smtp.test",
                    "user": "me@test.com",
                    "default": True,
                }
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_FILE", cfg_dir / "config.json")
    sent = []
    monkeypatch.setattr(
        "xlii.email.send_message",
        lambda account, *, to, subject, body: sent.append((to, subject, body)),
    )
    state = tmp_path / "state"
    state.mkdir()
    rc = SA.cmd_serve_mint(SimpleNamespace(preview=False, state_dir=str(state), email=True))
    assert rc == 0
    assert sent and sent[0][0] == "me@test.com"
    assert "?code=" in sent[0][2]
    assert "emailed me@test.com" in capsys.readouterr().err
    assert len(drain_pending(state)) == 1


def test_cmd_serve_mint_missing_base_url_exits_1(tmp_path, monkeypatch, capsys):
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_DIR", cfg_dir)
    monkeypatch.setattr("xlii.config.GLOBAL_CONFIG_FILE", cfg_dir / "config.json")

    rc = SA.cmd_serve_mint(SimpleNamespace(preview=False, state_dir=str(tmp_path)))
    assert rc == 1
    err = capsys.readouterr().err
    assert "base_url" in err


# --------------------------------------------------------------------------- #
#  sessions / revoke — fake mirror
# --------------------------------------------------------------------------- #


def test_sessions_lists_fake_mirror(tmp_path, capsys):
    _fake_mirror(tmp_path, [
        {
            "id": "sess-aaa",
            "paired_at": 100.0,
            "last_activity": 150.0,
            "mode": "full",
            "remote": "203.0.113.9",
        },
        {
            "id": "sess-bbb",
            "paired_at": 110.0,
            "last_activity": 110.0,
            "mode": "preview",
            "remote": "",
        },
    ])
    rc = SA.cmd_serve_sessions(SimpleNamespace(state_dir=str(tmp_path)))
    assert rc == 0
    out = capsys.readouterr().out
    assert "sess-aaa" in out and "sess-bbb" in out
    assert "full" in out and "preview" in out
    assert "203.0.113.9" in out


def test_sessions_empty_mirror(tmp_path, capsys):
    rc = SA.cmd_serve_sessions(SimpleNamespace(state_dir=str(tmp_path)))
    assert rc == 0
    assert "(none)" in capsys.readouterr().out


def test_revoke_queues_without_editing_mirror(tmp_path, capsys):
    """Queue-only: revoke writes the queue and leaves the mirror alone — serve
    owns the mirror and would clobber any edit made here on its next touch."""
    _fake_mirror(tmp_path, [
        {"id": "keep", "paired_at": 1.0, "last_activity": 1.0, "mode": "full", "remote": "a"},
        {"id": "kill", "paired_at": 2.0, "last_activity": 2.0, "mode": "full", "remote": "b"},
    ])
    rc = SA.cmd_serve_revoke(SimpleNamespace(session_id="kill", state_dir=str(tmp_path)))
    assert rc == 0
    assert "kill revoked — applies at next sweep" in capsys.readouterr().out

    # Mirror untouched; serve rewrites it once the revoke lands at sweep.
    assert [s["id"] for s in SA.read_sessions_mirror(tmp_path)] == ["keep", "kill"]

    revokes = json.loads(SA.revokes_spool_path(tmp_path).read_text(encoding="utf-8"))
    assert revokes["pending"] == ["kill"]
    assert stat.S_IMODE(SA.revokes_spool_path(tmp_path).stat().st_mode) == 0o600


def test_revoke_all_queues_sentinel(tmp_path, capsys):
    """'all' queues the shared sentinel (same shape webcode kill all writes),
    even when the mirror is stale — serve kills whatever is actually live."""
    _fake_mirror(tmp_path, [
        {"id": "a", "paired_at": 1.0, "last_activity": 1.0, "mode": "full", "remote": ""},
        {"id": "b", "paired_at": 2.0, "last_activity": 2.0, "mode": "preview", "remote": ""},
    ])
    rc = SA.cmd_serve_revoke(SimpleNamespace(session_id="all", state_dir=str(tmp_path)))
    assert rc == 0
    revokes = json.loads(SA.revokes_spool_path(tmp_path).read_text(encoding="utf-8"))
    assert revokes["pending"] == ["all"]
    assert "2 session" in capsys.readouterr().out

    # Re-queue dedupes to a single sentinel.
    rc = SA.cmd_serve_revoke(SimpleNamespace(session_id="all", state_dir=str(tmp_path)))
    assert rc == 0
    revokes = json.loads(SA.revokes_spool_path(tmp_path).read_text(encoding="utf-8"))
    assert revokes["pending"] == ["all"]

    # Stale/empty mirror: the sentinel still queues (explicit operator intent).
    empty = tmp_path / "empty"
    empty.mkdir()
    rc = SA.cmd_serve_revoke(SimpleNamespace(session_id="all", state_dir=str(empty)))
    assert rc == 0
    assert "mirror lists none" in capsys.readouterr().out
    revokes = json.loads(SA.revokes_spool_path(empty).read_text(encoding="utf-8"))
    assert revokes["pending"] == ["all"]


def test_revoke_unknown_exits_1(tmp_path, capsys):
    _fake_mirror(tmp_path, [
        {"id": "only", "paired_at": 1.0, "last_activity": 1.0, "mode": "full", "remote": ""},
    ])
    rc = SA.cmd_serve_revoke(SimpleNamespace(session_id="nope", state_dir=str(tmp_path)))
    assert rc == 1
    assert "no live session" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
#  register_serve_admin hook (conductor wiring surface)
# --------------------------------------------------------------------------- #


def test_register_serve_admin_exposes_mint_sessions_revoke():
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="serve_action")
    SA.register_serve_admin(sub)
    assert set(sub.choices) == {"mint", "sessions", "revoke"}

    mint_args = root.parse_args(["mint", "--preview", "--state-dir", "/tmp/x"])
    assert mint_args.func is SA.cmd_serve_mint
    assert mint_args.preview is True
    assert mint_args.state_dir == "/tmp/x"
    assert mint_args.email is False
    mailed = root.parse_args(["mint", "--email"])
    assert mailed.email is True

    sess_args = root.parse_args(["sessions"])
    assert sess_args.func is SA.cmd_serve_sessions

    rev_args = root.parse_args(["revoke", "all"])
    assert rev_args.func is SA.cmd_serve_revoke
    assert rev_args.session_id == "all"


def test_format_mint_line_pinned():
    # Same shape V5 pins for the OMEMO reply (fleet brief) — the URL is the
    # magic link carrying ?code= (closed-door S1).
    code = mint_code()
    line = SA.format_mint_line(code, "https://xlii-code.com", 300)
    assert f"  ·  https://xlii-code.com/?code={code}  ·  expires in 5m" in line
    assert line.startswith("code: ")


# --------------------------------------------------------------------------- #
#  #283 pin — mint and drain agree on the state dir at PRODUCTION defaults
# --------------------------------------------------------------------------- #


def test_mint_drain_round_trip_at_production_defaults(tmp_path, monkeypatch):
    """The bug class behind #283: `xlii serve mint` (no --state-dir) must write
    where serve's drain (no override) reads. Both sides resolve through
    serve_spool.default_state_dir(), whose XLII_STATE_DIR seam we point at tmp —
    the two calls must land on the SAME file with no path passed by the test."""
    from xlii.serve_spool import default_state_dir

    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path))

    cfg = _cfg(base_url="https://xlii-code.com")
    monkeypatch.setattr(SA.GlobalConfig, "load", classmethod(lambda cls: cfg))

    rc = SA.cmd_serve_mint(SimpleNamespace(state_dir=None, preview=False))
    assert rc == 0

    drained = drain_pending(default_state_dir())      # serve's side, defaults
    assert len(drained) == 1
    assert drained[0]["mode"] == "full"
    assert spool_path(tmp_path).exists()              # and it's the SAME dir


def test_resolve_state_dir_reads_env_at_call_time(monkeypatch, tmp_path):
    """Call-time resolution: a module-level constant would freeze the env at
    import and silently re-open #283 for any embedder that sets it later."""
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "one"))
    assert SA._resolve_state_dir(SimpleNamespace(state_dir=None)) == tmp_path / "one"
    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path / "two"))
    assert SA._resolve_state_dir(SimpleNamespace(state_dir=None)) == tmp_path / "two"


def test_serve_public_int_rejects_booleans():
    from xlii.config import _serve_public_int
    assert _serve_public_int({"max_sessions": True}, "max_sessions", 3) == 3
    assert _serve_public_int({"max_sessions": False}, "max_sessions", 3) == 3
    assert _serve_public_int({"max_sessions": 5}, "max_sessions", 3) == 5
