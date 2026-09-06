"""serve-public P1 — the ``webcode`` phone verb (V5).

Exit gate (proposals/serve-public-fleet.md):
  - classification of all four sub-verbs (mint / preview / ls / kill)
  - spool append is atomic
  - OMEMO reply format pinned
  - unpinned-device ``webcode`` does not dispatch (F-series re-assert)
  - preview mode maps to ``xlii code --preview --tui``

open-Q4: mint limited to 3 successful mints / 300s / JID (WebcodeMintLimiter).

Run:  ./venv/bin/python -m pytest tests/test_webcode_verb.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from xlii.daemon_gate import (
    REVOKES_SPOOL_NAME,
    WEBCODE_MINT_MAX,
    WEBCODE_MINT_WINDOW_S,
    WebcodeMintLimiter,
    append_revoke,
    classify_dispatch,
    format_sessions_ls,
    format_webcode_reply,
    mint_webcode_to_spool,
    read_sessions_mirror,
    webcode_serve_cmd,
)
from xlii.serve_gate import is_valid_format, normalize_code
from xlii.serve_spool import drain_pending, spool_path


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def _classify(body: str, verbs_dir: Path | None = None, **kw):
    return classify_dispatch(
        body,
        verbs_dir=verbs_dir or Path("/tmp/no-verbs"),
        fallback_enabled=kw.pop("fallback_enabled", True),
        fallback_workspace=kw.pop("fallback_workspace", ""),
        sender=kw.pop("sender", ""),
    )


# --------------------------------------------------------------------------- #
#  Classification — all four sub-verbs
# --------------------------------------------------------------------------- #

def test_webcode_bare_is_mint():
    d = _classify("webcode")
    assert d.kind == "webcode"
    assert d.webcode_action == "mint"
    assert d.webcode_mode == "full"
    assert d.audit == "webcode: mint"


def test_webcode_preview():
    d = _classify("webcode preview")
    assert d.kind == "webcode"
    assert d.webcode_action == "preview"
    assert d.webcode_mode == "preview"
    assert d.audit == "webcode: preview"


def test_webcode_ls():
    d = _classify("webcode ls")
    assert d.kind == "webcode"
    assert d.webcode_action == "ls"
    assert d.audit == "webcode: ls"


def test_webcode_kill_id():
    d = _classify("webcode kill abc123")
    assert d.kind == "webcode"
    assert d.webcode_action == "kill"
    assert d.webcode_kill_target == "abc123"


def test_webcode_kill_all():
    d = _classify("webcode kill all")
    assert d.kind == "webcode"
    assert d.webcode_action == "kill"
    assert d.webcode_kill_target == "all"


def test_webcode_kill_missing_id_usage():
    d = _classify("webcode kill")
    assert d.kind == "webcode"
    assert d.webcode_action == "kill"
    assert d.webcode_kill_target is None
    assert "usage" in (d.reply or "")


def test_webcode_case_insensitive():
    assert _classify("WEBCODE").webcode_action == "mint"
    assert _classify("Webcode Preview").webcode_mode == "preview"


def test_webcode_last_is_a_query():
    d = _classify("webcode last")
    assert d.kind == "webcode"
    assert d.webcode_action == "last"


def test_webcode_email_is_a_mint_plus_mail():
    d = _classify("webcode email")
    assert d.kind == "webcode"
    assert d.webcode_action == "email"
    assert d.webcode_mode == "full"


def test_webcode_unknown_subverb_stays_webcode_kind():
    # Must not fall through to the agent with a half-parsed pairing intent.
    d = _classify("webcode frobnicate")
    assert d.kind == "webcode"
    assert d.webcode_action is None
    assert "usage" in (d.reply or "")


def test_webcode_beats_verb_script(tmp_path):
    # Even if verbs/webcode.sh exists, the built-in wins (like kill).
    verbs = tmp_path / "verbs"
    verbs.mkdir()
    script = verbs / "webcode.sh"
    script.write_text("#!/bin/sh\necho no\n")
    script.chmod(0o755)
    d = _classify("webcode", verbs_dir=verbs)
    assert d.kind == "webcode"
    assert d.webcode_action == "mint"


# --------------------------------------------------------------------------- #
#  Preview → serve command mapping
# --------------------------------------------------------------------------- #

def test_preview_mode_maps_to_preview_tui():
    assert webcode_serve_cmd("preview") == ("xlii", "code", "--preview", "--tui")
    assert webcode_serve_cmd("full") == ("xlii", "code", "--tui")


# --------------------------------------------------------------------------- #
#  Pinned OMEMO reply format
# --------------------------------------------------------------------------- #

def test_webcode_reply_format_pinned():
    # The URL field is the magic link — it carries ?code= (closed-door S1).
    reply = format_webcode_reply("X7K2-M9Q4", "https://xlii-code.com", 300)
    assert reply == (
        "code: X7K2-M9Q4  ·  https://xlii-code.com/?code=X7K2-M9Q4"
        "  ·  expires in 5m"
    )


def test_webcode_reply_format_non_minute_ttl():
    reply = format_webcode_reply("ABCD-EFGH", "https://example.test", 90)
    assert reply == (
        "code: ABCD-EFGH  ·  https://example.test/?code=ABCD-EFGH"
        "  ·  expires in 90s"
    )


# --------------------------------------------------------------------------- #
#  Mint limiter (open-Q4)
# --------------------------------------------------------------------------- #

def test_webcode_mint_limiter_allows_then_blocks():
    lim = WebcodeMintLimiter()
    jid = "phone@test"
    now = 1_000_000.0
    for i in range(WEBCODE_MINT_MAX):
        ok, _ = lim.check(jid, now=now + i)
        assert ok
        lim.record(jid, now=now + i)
    ok, reason = lim.check(jid, now=now + WEBCODE_MINT_MAX)
    assert not ok
    assert "rate limit" in (reason or "")
    # After the window rolls, minting works again.
    ok2, _ = lim.check(jid, now=now + WEBCODE_MINT_WINDOW_S + 1)
    assert ok2


# --------------------------------------------------------------------------- #
#  Spool append (atomic) — offline via daemon_gate helper
# --------------------------------------------------------------------------- #

def test_webcode_mint_appends_atomic_spool(tmp_path):
    reply = mint_webcode_to_spool(
        tmp_path,
        mode="full",
        ttl_s=300,
        now=1_700_000_000.0,
        base_url="https://xlii-code.com",
    )
    assert reply.startswith("code: ")
    assert "https://xlii-code.com" in reply
    assert "expires in 5m" in reply
    # Pinned separators (double-space · double-space).
    assert "  ·  " in reply

    path = spool_path(tmp_path)
    assert path.exists()
    assert (path.stat().st_mode & 0o777) == 0o600
    pending = drain_pending(tmp_path)
    assert len(pending) == 1
    assert pending[0]["mode"] == "full"
    assert is_valid_format(pending[0]["code"])
    assert pending[0]["ttl_s"] == 300
    # After drain, spool is empty.
    assert drain_pending(tmp_path) == []


def test_webcode_preview_mint_writes_preview_mode(tmp_path):
    mint_webcode_to_spool(
        tmp_path,
        mode="preview",
        ttl_s=300,
        now=1.0,
        base_url="https://xlii-code.com",
    )
    pending = drain_pending(tmp_path)
    assert pending[0]["mode"] == "preview"
    assert len(normalize_code(pending[0]["code"])) == 8


def test_webcode_mint_requires_base_url(tmp_path):
    with pytest.raises(ValueError, match="base_url"):
        mint_webcode_to_spool(
            tmp_path, mode="full", ttl_s=300, now=1.0, base_url="",
        )


def test_webcode_kill_queues_revoke(tmp_path):
    append_revoke(tmp_path, "sess-1")
    append_revoke(tmp_path, "all")
    path = tmp_path / REVOKES_SPOOL_NAME
    assert (path.stat().st_mode & 0o777) == 0o600
    revokes = json.loads(path.read_text())
    assert revokes["pending"] == ["sess-1", "all"]


def test_webcode_ls_reads_mirror(tmp_path):
    mirror = {
        "version": 1,
        "sessions": [
            {
                "id": "abc",
                "paired_at": 1000.0,
                "last_activity": 1010.0,
                "mode": "full",
                "remote": "1.2.3.4",
            }
        ],
    }
    (tmp_path / "serve-sessions.json").write_text(json.dumps(mirror))
    sessions = read_sessions_mirror(tmp_path)
    assert len(sessions) == 1
    text = format_sessions_ls(sessions, now=1060.0)
    assert "abc" in text
    assert "idle=50s" in text
    assert format_sessions_ls([], now=1.0) == "[daemon] no active webcode sessions"


# --------------------------------------------------------------------------- #
#  Unpinned device — F-series negative re-assert for webcode
# --------------------------------------------------------------------------- #

def test_unpinned_device_webcode_does_not_dispatch():
    """Untrusted OMEMO device never reaches _dispatch, even for webcode."""
    pytest.importorskip("omemo")
    pytest.importorskip("slixmpp_omemo")

    from xlii.daemon import CommandDaemon
    from xlii.daemon_gate import DaemonConfig, RateLimiter

    class _FakeStanza:
        def __init__(self):
            self._values = {"type": "chat", "from": "phone@example.test/resource"}

        def __getitem__(self, key: str) -> str:
            return self._values[key]

    class _FakeXEP:
        def is_encrypted(self, _stanza: object) -> set[str]:
            return {"eu.siacs.conversations.axolotl"}

        async def decrypt_message(self, _stanza: object):
            return (
                {"body": "webcode"},
                SimpleNamespace(trust_level_name="UNDECIDED", device_id=42),
            )

    class _FakeDaemon(CommandDaemon):
        def __init__(self) -> None:
            self.cfg = DaemonConfig(
                jid="daemon@example.test",
                password_env="PW",
                state_file=Path("/tmp/state"),
                verbs_dir=Path("/tmp/verbs"),
                audit_log=Path("/tmp/audit"),
                allowed_jids=["phone@example.test"],
                max_per_minute=10,
                lockout_threshold=5,
                lockout_duration_s=300,
                fallback_enabled=False,
                fallback_workspace="",
                blind_trust=False,
            )
            self.rate_limiter = RateLimiter(10, 5, 300)
            self.shutdown_requested = False
            self._xep = _FakeXEP()
            self.audit: list[tuple[str, str | None, str]] = []
            self.dispatched: list[tuple[str, str]] = []

        def __del__(self) -> None:
            pass

        def __getitem__(self, key: str) -> _FakeXEP:
            assert key == "xep_0384"
            return self._xep

        def _audit(self, sender: str, body: str | None, status: str) -> None:
            self.audit.append((sender, body, status))

        def _send_plain(self, *_a, **_k) -> None:
            pass

        def _send_chat_state(self, *_a, **_k) -> None:
            pass

        async def _encrypted_reply(self, *_a, **_k) -> None:
            pass

        async def _dispatch(self, sender: str, body: str,
                            attachments: list[str] | None = None) -> str:
            self.dispatched.append((sender, body))
            return ""

    daemon = _FakeDaemon()
    asyncio.run(daemon._on_message(_FakeStanza()))

    assert daemon.dispatched == []
    assert daemon.audit == [
        ("phone@example.test", None, "rejected: untrusted device 42 (UNDECIDED)")
    ]


# --------------------------------------------------------------------------- #
#  Conductor fix pins: shared queue dedupe · kill cap · mirror coercion · dir
# --------------------------------------------------------------------------- #


def test_webcode_kill_queue_dedupes_via_shared_helper(tmp_path):
    """append_revoke now rides serve_spool's single implementation — a phone
    double-tap must not queue the same sid twice."""
    append_revoke(tmp_path, "sess-1")
    append_revoke(tmp_path, "sess-1")
    revokes = json.loads((tmp_path / REVOKES_SPOOL_NAME).read_text())
    assert revokes["pending"] == ["sess-1"]


def test_webcode_kill_oversized_target_gets_usage_reply():
    from xlii.daemon_gate import WEBCODE_KILL_TARGET_MAX

    decision = _classify("webcode kill " + "x" * (WEBCODE_KILL_TARGET_MAX + 1))
    assert decision.kind == "webcode"
    assert decision.reply is not None and "usage" in decision.reply
    assert decision.webcode_kill_target is None
    # An in-bounds target still classifies as a real kill.
    ok = _classify("webcode kill sess-1")
    assert ok.webcode_kill_target == "sess-1"


def test_webcode_ls_survives_mangled_mirror_entry(tmp_path):
    """One hand-mangled mirror entry must not turn the whole ls reply into a
    daemon error — numerics are coerced at read."""
    mirror = {
        "version": 1,
        "sessions": [
            {"id": "good", "paired_at": 1000.0, "last_activity": 1010.0,
             "mode": "full", "remote": ""},
            {"id": "mangled", "paired_at": "yesterday", "last_activity": None,
             "mode": "full", "remote": ""},
        ],
    }
    (tmp_path / "serve-sessions.json").write_text(json.dumps(mirror))
    sessions = read_sessions_mirror(tmp_path)
    assert [s["id"] for s in sessions] == ["good", "mangled"]
    text = format_sessions_ls(sessions, now=1060.0)
    assert "good" in text and "mangled" in text     # no exception, both listed


def test_daemon_state_dir_is_canonical_not_audit_parent(monkeypatch, tmp_path):
    """#283's sibling: a relocated daemon.toml audit_log must NOT drag the
    grant spool away from where serve drains; XLII_STATE_DIR is honored."""
    from types import SimpleNamespace

    from xlii.daemon_gate import daemon_state_dir

    monkeypatch.setenv("XLII_STATE_DIR", str(tmp_path))
    cfg = SimpleNamespace(audit_log=Path("/var/log/elsewhere/audit.log"))
    assert daemon_state_dir(cfg) == tmp_path
