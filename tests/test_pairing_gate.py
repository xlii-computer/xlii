"""qr-pair P1 — pure pairing window (offline, no [daemon] extra)."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from xlii.pairing_gate import (
    DEFAULT_TTL_S,
    RAILS,
    REASON_EXPIRED,
    REASON_GRANT,
    REASON_INVITE,
    REASON_INVITE_MISMATCH,
    REASON_LOCKOUT,
    REASON_NO_WINDOW,
    REASON_NOT_ALLOWLISTED,
    REASON_REPLAY,
    REASON_WRONG_CODE,
    PairingStore,
    PairingWindow,
    codes_match,
    consume,
    hash_code,
    mint_window,
    omemo_verify_uri,
    own_identity_from_omemo_state,
    qr_ansi,
    verify_code,
)
from xlii.serve_gate import group_code, mint_code


NOW = 1_700_000_000.0


def test_mint_window_hashes_code_never_stores_plaintext():
    window, grouped = mint_window(now=NOW)
    assert grouped == group_code(grouped)
    assert window.code_hash == hash_code(grouped)
    assert grouped.lower() not in json.dumps(_window_json(window)).lower()
    assert window.rail == "daemon"
    assert window.expires_at == NOW + DEFAULT_TTL_S
    assert window.invite_jid == ""
    assert not window.consumed_by


def _window_json(w: PairingWindow) -> dict:
    return {
        "code_hash": w.code_hash, "rail": w.rail, "opened_at": w.opened_at,
        "expires_at": w.expires_at, "invite_jid": w.invite_jid,
        "consumed_by": w.consumed_by,
    }


def test_verify_accepts_grouped_and_confusable_forms():
    window, grouped = mint_window(now=NOW, code="X7K2-M9Q4")
    assert verify_code(window, "X7K2-M9Q4", now=NOW) == REASON_GRANT
    assert verify_code(window, "x7k2m9q4", now=NOW) == REASON_GRANT
    assert verify_code(window, " x7k2 m9q4 ", now=NOW) == REASON_GRANT


def test_verify_rejects_wrong_expired_replay():
    window, grouped = mint_window(now=NOW, code="X7K2-M9Q4", ttl_s=10)
    assert verify_code(window, "AAAA-AAAA", now=NOW) == REASON_WRONG_CODE
    assert verify_code(window, grouped, now=NOW + 10) == REASON_EXPIRED
    taken = consume(window, "dev-1", now=NOW)
    assert verify_code(taken, grouped, now=NOW) == REASON_REPLAY


def test_codes_match_is_hash_equality():
    window, grouped = mint_window(now=NOW)
    assert codes_match(window, grouped)
    assert not codes_match(window, mint_code())


def test_mint_rejects_bad_rail_and_ttl():
    with pytest.raises(ValueError, match="rail"):
        mint_window(rail="sms", now=NOW)
    with pytest.raises(ValueError, match="ttl"):
        mint_window(ttl_s=0, now=NOW)


def test_rails_are_the_three_omemo_paths():
    assert RAILS == {"daemon", "notify", "face"}


# --------------------------------------------------------------------------- #
#  Store
# --------------------------------------------------------------------------- #


def _store(tmp_path: Path) -> PairingStore:
    return PairingStore(tmp_path / "xlii-pairing.json")


def test_store_round_trip_and_corrupt_is_closed(tmp_path):
    store = _store(tmp_path)
    window, grouped = mint_window(now=NOW, code="X7K2-M9Q4")
    store.put(window)
    loaded = store.get("daemon")
    assert loaded is not None
    assert loaded.code_hash == window.code_hash
    assert verify_code(loaded, grouped, now=NOW) == REASON_GRANT

    store.path.write_text("{not json", encoding="utf-8")
    assert store.get("daemon") is None
    d = store.evaluate(
        rail="daemon", sender_jid="a@x", body=grouped, device_id="1",
        allowlisted=True, now=NOW,
    )
    assert d.reason == REASON_NO_WINDOW and not d.granted


def test_grant_consumes_single_use(tmp_path):
    store = _store(tmp_path)
    window, grouped = mint_window(now=NOW, code="X7K2-M9Q4")
    store.put(window)
    first = store.evaluate(
        rail="daemon", sender_jid="me@x", body=grouped, device_id="42",
        allowlisted=True, now=NOW,
    )
    assert first.granted and first.reason == REASON_GRANT
    assert first.device_id == "42" and first.window_closed
    second = store.evaluate(
        rail="daemon", sender_jid="me@x", body=grouped, device_id="99",
        allowlisted=True, now=NOW + 1,
    )
    assert not second.granted and second.reason == REASON_REPLAY


def test_expiry_mid_flight_closes_window(tmp_path):
    store = _store(tmp_path)
    window, grouped = mint_window(now=NOW, ttl_s=30, code="X7K2-M9Q4")
    store.put(window)
    d = store.evaluate(
        rail="daemon", sender_jid="me@x", body=grouped, device_id="1",
        allowlisted=True, now=NOW + 30,
    )
    assert not d.granted and d.reason == REASON_EXPIRED and d.window_closed
    assert store.get("daemon") is None


def test_non_allowlisted_denied_even_with_code(tmp_path):
    store = _store(tmp_path)
    window, grouped = mint_window(now=NOW, code="X7K2-M9Q4")
    store.put(window)
    d = store.evaluate(
        rail="daemon", sender_jid="evil@x", body=grouped, device_id="1",
        allowlisted=False, now=NOW,
    )
    assert not d.granted and d.reason == REASON_NOT_ALLOWLISTED
    # Window stays open for the real phone.
    ok = store.evaluate(
        rail="daemon", sender_jid="me@x", body=grouped, device_id="1",
        allowlisted=True, now=NOW,
    )
    assert ok.granted


def test_invite_enrolls_matching_jid_only(tmp_path):
    store = _store(tmp_path)
    window, grouped = mint_window(now=NOW, code="X7K2-M9Q4", invite_jid="new@x")
    store.put(window)
    miss = store.evaluate(
        rail="daemon", sender_jid="other@x", body=grouped, device_id="1",
        allowlisted=False, now=NOW,
    )
    assert not miss.granted and miss.reason == REASON_INVITE_MISMATCH
    hit = store.evaluate(
        rail="daemon", sender_jid="new@x", body=grouped, device_id="7",
        allowlisted=False, now=NOW,
    )
    assert hit.granted and hit.reason == REASON_INVITE
    assert hit.enroll_jid == "new@x"


def test_invite_does_not_enroll_already_allowlisted(tmp_path):
    store = _store(tmp_path)
    window, grouped = mint_window(now=NOW, code="X7K2-M9Q4", invite_jid="me@x")
    store.put(window)
    d = store.evaluate(
        rail="daemon", sender_jid="me@x", body=grouped, device_id="1",
        allowlisted=True, now=NOW,
    )
    assert d.granted and d.reason == REASON_GRANT
    assert d.enroll_jid == ""


def test_wrong_code_lockout_closes_window(tmp_path):
    store = PairingStore(
        tmp_path / "p.json",
        lockout_threshold=3,
        lockout_window_s=300,
        lockout_duration_s=60,
    )
    window, grouped = mint_window(now=NOW, code="X7K2-M9Q4")
    store.put(window)
    reasons = []
    for i in range(3):
        d = store.evaluate(
            rail="daemon", sender_jid="me@x", body="AAAA-AAAA", device_id="1",
            allowlisted=True, now=NOW + i,
        )
        reasons.append(d.reason)
        assert not d.granted
    assert REASON_WRONG_CODE in reasons
    assert reasons[-1] == REASON_LOCKOUT
    assert store.get("daemon") is None
    # Still locked after a fresh mint
    fresh, code = mint_window(now=NOW + 1, code="B1C2-D3E4")
    store.put(fresh)
    locked = store.evaluate(
        rail="daemon", sender_jid="me@x", body=code, device_id="1",
        allowlisted=True, now=NOW + 2,
    )
    assert not locked.granted and locked.reason == REASON_LOCKOUT


def test_no_window_is_byte_identical_deny(tmp_path):
    store = _store(tmp_path)
    d = store.evaluate(
        rail="daemon", sender_jid="me@x", body="X7K2-M9Q4", device_id="1",
        allowlisted=True, now=NOW,
    )
    assert not d.granted and d.reason == REASON_NO_WINDOW


def test_mode_0600_on_store_file(tmp_path):
    store = _store(tmp_path)
    window, _ = mint_window(now=NOW)
    store.put(window)
    assert store.path.stat().st_mode & 0o777 == 0o600


def test_unconsume_restores_live_window(tmp_path):
    store = _store(tmp_path)
    window, grouped = mint_window(now=NOW, code="X7K2-M9Q4")
    store.put(window)
    granted = store.evaluate(
        rail="daemon", sender_jid="me@x", body=grouped, device_id="42",
        allowlisted=True, now=NOW,
    )
    assert granted.granted
    store.unconsume("daemon")
    live = store.get("daemon")
    assert live is not None and not live.consumed_by
    again = store.evaluate(
        rail="daemon", sender_jid="me@x", body=grouped, device_id="42",
        allowlisted=True, now=NOW + 1,
    )
    assert again.granted


# --------------------------------------------------------------------------- #
#  Identity + URI (S0)
# --------------------------------------------------------------------------- #


def test_omemo_verify_uri_conversations_shape():
    uri = omemo_verify_uri("throne@desk.tailnet", 812345, "0a1b" + "c" * 60)
    assert uri.startswith("xmpp:throne@desk.tailnet?omemo-sid-812345=")
    assert "omemo-sid-812345=" in uri
    assert "0a1b" in uri
    # Code is NOT in the URI.
    assert "X7K2" not in uri


def test_own_identity_from_omemo_state_reads_python_omemo_keys():
    # Ed25519 identity key whose Curve25519 form we don't need to hard-code
    # beyond "32-byte key → 64 hex". A low-order point: use a known vector.
    # 32 zero bytes is an invalid ed point for the map? Use a random-looking
    # 32-byte value that survives the birational map (y != 1).
    ik = bytes(range(32))
    b64 = base64.urlsafe_b64encode(ik).decode("ascii")
    data = {
        "/own_device_id": 4242,
        "/devices/me@x/4242/identity_key": b64,
    }
    got = own_identity_from_omemo_state(data)
    assert got is not None
    sid, fp = got
    assert sid == 4242
    assert len(fp) == 64 and all(c in "0123456789abcdef" for c in fp)


def test_own_identity_missing_or_corrupt_is_none():
    assert own_identity_from_omemo_state({}) is None
    assert own_identity_from_omemo_state({"/own_device_id": 1}) is None
    assert own_identity_from_omemo_state({"/own_device_id": "nope"}) is None


def test_qr_ansi_empty_without_encoder_or_draws_when_present():
    uri = omemo_verify_uri("a@b", 1, "ab" * 32)
    try:
        import qrcode  # noqa: F401
    except ImportError:
        assert qr_ansi(uri) == ""
    else:
        art = qr_ansi(uri)
        assert art and (("█" in art) or ("▀" in art) or ("▄" in art))
