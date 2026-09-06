"""Daemon `_dispatch` × elevation — the real wiring (needs the [daemon] extra).

Drives the live `_dispatch`: /xsu elevates, destructive verbs (kill/webcode)
require it when the gate is armed, and 3 bad codes lock the daemon. Time is
pinned by monkeypatching the daemon module's clock so the TOTP is deterministic.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("slixmpp")

import xlii.daemon as daemon_mod
from xlii import totp
from xlii.daemon import CommandDaemon
from xlii.daemon_gate import DaemonConfig, ElevationGate

_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
_NOW = 50_000.0


def _code(now: float = _NOW) -> str:
    return totp._hotp(totp._decode_secret(_SECRET), int(now // 30), 6)


class _ElevDaemon(CommandDaemon):
    def __init__(self, *, secret: str, admin_jids: list[str] | None = None) -> None:
        self.cfg = DaemonConfig(
            jid="daemon@example.test", password_env="PW",
            state_file=Path("/tmp/s"), verbs_dir=Path("/tmp/verbs"),
            audit_log=Path("/tmp/a"), allowed_jids=["me@x", "chat@x"],
            max_per_minute=10, lockout_threshold=5, lockout_duration_s=300,
            fallback_enabled=True, fallback_workspace="", grammar="slash",
            admin_jids=admin_jids or [],
        )
        self.elevation = ElevationGate(secret)
        self.shutdown_requested = False
        self.audit: list = []
        self.webcode_runs = 0
        self.agent_runs = 0

    def __del__(self):
        pass

    def _audit(self, s, b, st):
        self.audit.append((s, b, st))

    def _run_webcode(self, sender, decision):
        self.webcode_runs += 1
        return "[webcode minted]"

    async def _run_agent(self, prompt, ws, sess, attachments=None, outbox="",
                         force_lab=False):
        self.agent_runs += 1
        return "[agent reply]"

    async def _run_verb(self, path, args):
        return "[verb]"


@pytest.fixture(autouse=True)
def _pin_clock(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon_mod.time, "time", lambda: _NOW)
    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))


def _dispatch(d, body, sender="me@x"):
    return asyncio.run(d._dispatch(sender, body))


# --------------------------------------------------------------------------- #
#  Gate ARMED (TOTP configured)
# --------------------------------------------------------------------------- #

def test_xsu_good_code_then_kill_shuts_down():
    d = _ElevDaemon(secret=_SECRET)
    assert _dispatch(d, f"/xsu {_code()}") == "[daemon] elevated."
    assert _dispatch(d, "/kill") == "[daemon] shutting down."
    assert d.shutdown_requested is True


def test_kill_without_elevation_is_refused():
    d = _ElevDaemon(secret=_SECRET)
    assert _dispatch(d, "/kill") == "[daemon] elevation required"
    assert d.shutdown_requested is False


def test_webcode_requires_elevation_the_mfa_unlocks_the_magic_link():
    d = _ElevDaemon(secret=_SECRET)
    assert _dispatch(d, "/webcode") == "[daemon] elevation required"
    assert d.webcode_runs == 0
    _dispatch(d, f"/xsu {_code()}")
    assert _dispatch(d, "/webcode") == "[webcode minted]"
    assert d.webcode_runs == 1


def test_elevation_is_spent_on_one_destructive_act():
    d = _ElevDaemon(secret=_SECRET)
    _dispatch(d, f"/xsu {_code()}")
    assert _dispatch(d, "/webcode") == "[webcode minted]"      # consumes it
    assert _dispatch(d, "/webcode") == "[daemon] elevation required"  # gone


def test_three_bad_codes_lock_the_whole_daemon():
    d = _ElevDaemon(secret=_SECRET)
    assert _dispatch(d, "/xsu 000000") == "[daemon] denied."
    assert _dispatch(d, "/xsu 000000") == "[daemon] denied — one more attempt."
    assert _dispatch(d, "/xsu 000000") == "[daemon] locked — restart required"
    # Locked → EVERYTHING is refused, even chat, until an SSH/console restart.
    assert _dispatch(d, "hello") == "[daemon] locked — restart required"
    assert _dispatch(d, f"/xsu {_code()}") == "[daemon] locked — restart required"
    assert d.agent_runs == 0


def test_non_admin_xsu_attempts_do_not_lock_the_daemon():
    d = _ElevDaemon(secret=_SECRET, admin_jids=["me@x"])
    for _ in range(3):
        assert _dispatch(d, "/xsu 000000", sender="chat@x") == "[daemon] not permitted."
    assert d.elevation.is_locked() is False
    assert _dispatch(d, "hello", sender="me@x") == "[agent reply]"


def test_chat_still_flows_while_armed_but_unelevated():
    d = _ElevDaemon(secret=_SECRET)
    assert _dispatch(d, "hello iXaac") == "[agent reply]"
    assert d.agent_runs == 1


# --------------------------------------------------------------------------- #
#  Gate DISABLED (no TOTP) — no regression, /xsu stays hidden
# --------------------------------------------------------------------------- #

def test_unconfigured_remote_control_opens_without_totp():
    d = _ElevDaemon(secret="")
    reply = _dispatch(d, "/remote-control")
    assert "sitting open" in reply.lower()
    from xlii.occupancy_store import load_live
    assert load_live(now=_NOW).remote_lab.open


def test_unconfigured_kill_still_works_no_regression():
    d = _ElevDaemon(secret="")
    assert _dispatch(d, "/kill") == "[daemon] shutting down."
    assert d.shutdown_requested is True


def test_remote_control_requires_elevation_when_sitting_closed():
    d = _ElevDaemon(secret=_SECRET)
    assert _dispatch(d, "/remote-control") == "[daemon] elevation required"
    _dispatch(d, f"/xsu {_code()}")
    reply = _dispatch(d, "/remote-control")
    assert "sitting open" in reply.lower()
    from xlii.occupancy_store import load_live
    occ = load_live(now=_NOW)
    assert occ.remote_lab.open and not occ.remote_lab.locked
    assert occ.remote_lab.source == "phone"


def test_phone_remote_control_consumes_elevation():
    d = _ElevDaemon(secret=_SECRET)
    _dispatch(d, f"/xsu {_code()}")
    assert "sitting open" in _dispatch(d, "/remote-control").lower()
    assert _dispatch(d, "/webcode") == "[daemon] elevation required"


def test_face_sitting_skips_xsu_for_daemon_remote_control():
    from xlii.occupancy_store import load_live, mutate

    mutate(lambda o: o.open_remote_lab(now=_NOW, source="face"), now=_NOW)
    d = _ElevDaemon(secret=_SECRET)
    reply = _dispatch(d, "/remote-control")
    assert "sitting open" in reply.lower()
    assert d.elevation.is_elevated("me@x", now=_NOW) is False
    occ = load_live(now=_NOW)
    assert occ.remote_lab.source == "face"


def test_remote_control_drop_does_not_need_elevation():
    from xlii.occupancy_store import load_live, mutate

    mutate(lambda o: o.open_remote_lab(now=_NOW, source="phone"), now=_NOW)
    d = _ElevDaemon(secret=_SECRET)
    reply = _dispatch(d, "/remote-control drop")
    assert "sitting closed" in reply.lower()
    assert not load_live(now=_NOW).remote_lab.open


def test_unconfigured_xsu_looks_like_an_unknown_command_and_never_locks():
    d = _ElevDaemon(secret="")
    r = _dispatch(d, "/xsu 000000")
    assert "unknown command" in r and "xsu" not in r.lower()
    # Spamming it never locks a disabled gate.
    for _ in range(5):
        _dispatch(d, "/xsu 000000")
    assert d.elevation.is_locked() is False
    assert _dispatch(d, "hello") == "[agent reply]"
