"""Offline receive-path tests for the optional XMPP daemon module."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("omemo")
pytest.importorskip("slixmpp_omemo")

from slixmpp_omemo import TrustLevel

from xlii.daemon import CommandDaemon
from xlii.daemon_gate import DaemonConfig, RateLimiter
from xlii.pairing_gate import PairingStore, mint_window


@pytest.fixture(autouse=True)
def _pairing_isolation(tmp_path, monkeypatch):
    monkeypatch.setenv("XLII_PAIRING_PATH", str(tmp_path / "xlii-pairing.json"))


class _FakeStanza:
    def __init__(
        self,
        *,
        sender: str = "phone@example.test/resource",
        body: str | None = None,
    ) -> None:
        self._values = {"type": "chat", "from": sender}
        # No "body" key at all unless one is given — the daemon must survive a
        # stanza whose body raises (the OMEMO-only case).
        if body is not None:
            self._values["body"] = body

    def __getitem__(self, key: str) -> str:
        return self._values[key]


class _FakeSessionManager:
    """Records set_trust calls so a test can assert the blind-trust promotion —
    and can be told to raise, to prove a failed write still rejects safely."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, object]] = []

    async def set_trust(self, bare_jid: str, identity_key: str, level: object) -> None:
        if self.fail:
            raise RuntimeError("trust store unavailable")
        self.calls.append((bare_jid, identity_key, level))


class _FakeXEP:
    def __init__(
        self,
        *,
        trust_level: str,
        promote_fails: bool = False,
        body: str = "kill",
        encrypted: bool = True,
        decrypt_fails: bool = False,
    ) -> None:
        self.trust_level = trust_level
        self.session_manager = _FakeSessionManager(fail=promote_fails)
        self.body = body
        self.encrypted = encrypted
        self.decrypt_fails = decrypt_fails
        self.decrypt_calls = 0

    def is_encrypted(self, _stanza: object) -> set[str]:
        return {"eu.siacs.conversations.axolotl"} if self.encrypted else set()

    async def get_session_manager(self) -> "_FakeSessionManager":
        return self.session_manager

    async def decrypt_message(self, stanza: object):
        self.decrypt_calls += 1
        if self.decrypt_fails:
            raise RuntimeError("cannot decrypt")
        sender = stanza["from"] if isinstance(stanza, _FakeStanza) else "phone@example.test/res"
        bare = sender.split("/")[0]
        return (
            {"body": self.body},
            SimpleNamespace(
                trust_level_name=self.trust_level,
                device_id=42,
                bare_jid=bare,
                identity_key="deadbeef",
            ),
        )


class _FakeDaemon(CommandDaemon):
    def __init__(
        self,
        *,
        trust_level: str,
        blind_trust: bool = False,
        promote_fails: bool = False,
        body: str = "kill",
        encrypted: bool = True,
        decrypt_fails: bool = False,
        extra_allowed: list[str] | None = None,
    ) -> None:
        self.cfg = DaemonConfig(
            jid="daemon@example.test",
            password_env="PW",
            state_file=Path("/tmp/state"),
            verbs_dir=Path("/tmp/verbs"),
            audit_log=Path("/tmp/audit"),
            allowed_jids=["phone@example.test"] + list(extra_allowed or []),
            max_per_minute=10,
            lockout_threshold=5,
            lockout_duration_s=300,
            fallback_enabled=False,
            fallback_workspace="",
            blind_trust=blind_trust,
        )
        self.rate_limiter = RateLimiter(10, 5, 300)
        self.shutdown_requested = False
        self.config_path = None
        self._xep = _FakeXEP(
            trust_level=trust_level,
            promote_fails=promote_fails,
            body=body,
            encrypted=encrypted,
            decrypt_fails=decrypt_fails,
        )
        self.audit: list[tuple[str, str | None, str]] = []
        self.dispatched: list[tuple[str, str]] = []
        self.replies: list[tuple[str, str]] = []

    def __del__(self) -> None:
        pass

    def __getitem__(self, key: str) -> _FakeXEP:
        assert key == "xep_0384"
        return self._xep

    def _audit(self, sender: str, body: str | None, status: str) -> None:
        self.audit.append((sender, body, status))

    def _send_plain(self, *_args, **_kwargs) -> None:
        pass

    def _send_chat_state(self, *_args, **_kwargs) -> None:
        pass

    async def _encrypted_reply(self, to_jid: str, body: str) -> None:
        self.replies.append((to_jid, body))

    async def _dispatch(self, sender: str, body: str, attachments=None) -> str:
        self.dispatched.append((sender, body))
        return ""


def test_untrusted_omemo_device_is_rejected_before_dispatch():
    daemon = _FakeDaemon(trust_level="UNDECIDED")
    asyncio.run(daemon._on_message(_FakeStanza()))

    assert daemon.dispatched == []
    assert daemon.audit == [
        ("phone@example.test", None, "rejected: untrusted device 42 (UNDECIDED)")
    ]
    assert daemon.replies and "device not trusted" in daemon.replies[0][1]


def test_trusted_omemo_device_reaches_dispatch():
    daemon = _FakeDaemon(trust_level="TRUSTED")
    asyncio.run(daemon._on_message(_FakeStanza()))

    assert daemon.dispatched == [("phone@example.test", "kill")]
    assert ("phone@example.test", "kill", "received") in daemon.audit


def test_blind_trust_promotes_undecided_allowlisted_device():
    """BTBV first contact: with blind_trust on, an allowlisted sender's UNDECIDED
    device is promoted at the *incoming* gate (python-omemo only resolves trust on
    the encrypt path, so a receive-first device would otherwise stay UNDECIDED
    forever) and the message reaches dispatch."""
    daemon = _FakeDaemon(trust_level="UNDECIDED", blind_trust=True)
    asyncio.run(daemon._on_message(_FakeStanza()))

    # the promotion was persisted to the trust store via the session manager…
    assert daemon._xep.session_manager.calls == [
        ("phone@example.test", "deadbeef", TrustLevel.TRUSTED.value)
    ]
    # …the audit records the first-contact trust and the delivered body…
    assert ("phone@example.test", None, "blind-trusted device 42 (first contact)") in daemon.audit
    assert ("phone@example.test", "kill", "received") in daemon.audit
    # …and the message reached dispatch instead of being rejected.
    assert daemon.dispatched == [("phone@example.test", "kill")]


def test_blind_trust_off_still_rejects_undecided_device():
    """The secure default (blind_trust=false) rejects UNDECIDED devices without
    ever touching the trust store — pinning stays a deliberate `xlii daemon trust`."""
    daemon = _FakeDaemon(trust_level="UNDECIDED", blind_trust=False)
    asyncio.run(daemon._on_message(_FakeStanza()))

    assert daemon._xep.session_manager.calls == []
    assert daemon.dispatched == []
    assert daemon.audit == [
        ("phone@example.test", None, "rejected: untrusted device 42 (UNDECIDED)")
    ]
    assert daemon.replies and "xlii daemon trust" in daemon.replies[0][1]


def test_blind_trust_promotion_failure_rejects_safely():
    """If the trust-store write raises, the daemon rejects (never dispatches) and
    the message loop survives — a failed promotion must not open the gate."""
    daemon = _FakeDaemon(trust_level="UNDECIDED", blind_trust=True, promote_fails=True)
    asyncio.run(daemon._on_message(_FakeStanza()))

    assert daemon.dispatched == []
    assert daemon.audit == [
        ("phone@example.test", None, "rejected: untrusted device 42 (UNDECIDED)")
    ]


def test_pairing_grant_pins_untrusted_device_without_dispatch():
    store = PairingStore()
    window, grouped = mint_window(now=time.time())
    store.put(window)
    daemon = _FakeDaemon(trust_level="UNDECIDED", body=grouped)
    asyncio.run(daemon._on_message(_FakeStanza()))

    assert daemon.dispatched == []
    assert daemon._xep.session_manager.calls == [
        ("phone@example.test", "deadbeef", TrustLevel.TRUSTED.value)
    ]
    assert any("pair-grant" in status for _, _, status in daemon.audit)
    loaded = store.get("daemon")
    assert loaded is not None and loaded.consumed_by == "42"
    assert daemon.replies and "paired" in daemon.replies[0][1].lower()


def test_not_allowlisted_jid_is_dropped_before_decrypt():
    """An open window is not an open door: a stranger it does not name is
    dropped before any OMEMO decrypt (no crypto work, no trust-store side
    effects), exactly as it was before pairing existed."""
    store = PairingStore()
    window, grouped = mint_window(now=time.time())
    store.put(window)
    daemon = _FakeDaemon(trust_level="UNDECIDED", body=grouped)
    asyncio.run(daemon._on_message(_FakeStanza(sender="stranger@example.test/phone")))

    loaded = store.get("daemon")
    assert loaded is not None and loaded.consumed_by == ""
    assert daemon.dispatched == []
    assert daemon._xep.decrypt_calls == 0
    assert daemon.audit == [
        ("stranger@example.test", None, "rejected: not in whitelist")
    ]


def test_uninvited_jid_is_dropped_before_decrypt_on_invite_window():
    """An ``--invite`` window only earns a decrypt for the JID it names."""
    store = PairingStore()
    window, grouped = mint_window(now=time.time(), invite_jid="new@example.test")
    store.put(window)
    daemon = _FakeDaemon(trust_level="UNDECIDED", body=grouped)
    asyncio.run(daemon._on_message(_FakeStanza(sender="stranger@example.test/phone")))

    loaded = store.get("daemon")
    assert loaded is not None and loaded.consumed_by == ""
    assert daemon._xep.decrypt_calls == 0
    assert daemon.audit == [
        ("stranger@example.test", None, "rejected: not in whitelist")
    ]


def test_invite_grant_enrolls_jid_and_pins(tmp_path):
    store = PairingStore()
    window, grouped = mint_window(now=time.time(), invite_jid="new@example.test")
    store.put(window)
    toml = tmp_path / "daemon.toml"
    toml.write_text(
        '[daemon]\njid = "daemon@example.test"\n'
        '[whitelist]\nallowed_jids = ["phone@example.test"]\n',
        encoding="utf-8",
    )
    daemon = _FakeDaemon(trust_level="UNDECIDED", body=grouped)
    daemon.config_path = toml
    asyncio.run(daemon._on_message(_FakeStanza(sender="new@example.test/phone")))

    assert daemon.dispatched == []
    assert "new@example.test" in daemon.cfg.allowed_jids
    assert "new@example.test" in toml.read_text()
    assert any("pair-invite" in status for _, _, status in daemon.audit)
    assert daemon._xep.session_manager.calls == [
        ("new@example.test", "deadbeef", TrustLevel.TRUSTED.value)
    ]


def test_plaintext_pairing_code_does_not_consume_window():
    store = PairingStore()
    window, grouped = mint_window(now=time.time())
    store.put(window)
    daemon = _FakeDaemon(
        trust_level="UNDECIDED", body=grouped, encrypted=False,
    )
    asyncio.run(daemon._on_message(_FakeStanza(body=grouped)))

    loaded = store.get("daemon")
    assert loaded is not None and loaded.consumed_by == ""
    assert daemon.dispatched == []
    assert any("pair-need-omemo" in status for _, _, status in daemon.audit)


def test_pairing_code_is_not_dispatched_as_a_verb():
    store = PairingStore()
    window, grouped = mint_window(now=time.time())
    store.put(window)
    daemon = _FakeDaemon(trust_level="TRUSTED", body=grouped)
    asyncio.run(daemon._on_message(_FakeStanza()))

    assert daemon.dispatched == []
    loaded = store.get("daemon")
    assert loaded is not None and loaded.consumed_by == "42"


def test_pairing_trust_write_failure_restores_window():
    store = PairingStore()
    window, grouped = mint_window(now=time.time())
    store.put(window)
    daemon = _FakeDaemon(trust_level="UNDECIDED", body=grouped, promote_fails=True)
    asyncio.run(daemon._on_message(_FakeStanza()))

    loaded = store.get("daemon")
    assert loaded is not None and loaded.consumed_by == ""
    assert daemon.dispatched == []
    assert any("pair-grant-failed" in status for _, _, status in daemon.audit)
