"""Send-only OMEMO XMPP — the Phase-1 notification rail (`xlii notify`).

One-shot: connect as the configured sender, OMEMO-encrypt a single message to
the recipient, send it, disconnect. Mirrors the daemon's encrypted-send path but
has **no inbound channel** — zero remote-execution surface, the lowest-risk slice
of the fabric.

Behind the optional `[daemon]` extra (same slixmpp/OMEMO deps); reuses the
daemon's secure-default OMEMO plugin (XEP_0384Impl) so trust + storage behave
identically. The crypto/network path needs a live XMPP server to verify — the
config + CLI surface around it are unit-tested in tests/.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import tomllib
from pathlib import Path

from slixmpp import ClientXMPP
from slixmpp.jid import JID

# Reuse the daemon's secure-default OMEMO plugin + atomic JsonStorage. Importing
# this also runs daemon.py's register_plugin(XEP_0384Impl) at module load.
from xlii import daemon as _daemon  # noqa: F401  (registers XEP_0384Impl)
from xlii.daemon_gate import DEFAULT_NOTIFY_CONFIG, NotifyConfig
from xlii.omemo_payload import sealed_recipient_keys


class _Sender(ClientXMPP):
    def __init__(self, cfg: NotifyConfig, password: str, message: str):
        super().__init__(cfg.jid, password)
        self._cfg = cfg
        self._message = message
        self.sent_ok = False
        self.register_plugin("xep_0030")
        self.register_plugin("xep_0060")
        self.register_plugin("xep_0163")
        self.register_plugin("xep_0380")
        self.register_plugin(
            "xep_0384",
            {"json_file_path": str(cfg.state_file), "blind_trust": cfg.blind_trust},
            module=sys.modules["xlii.daemon"],
        )
        self.add_event_handler("session_start", self._on_start)

    async def _on_start(self, _event) -> None:
        try:
            self.send_presence()
            await self.get_roster()
            recipient = JID(self._cfg.recipient)
            xep_0384 = self["xep_0384"]
            try:
                await xep_0384.refresh_device_lists({recipient}, force_download=True)
            except Exception:
                logging.exception("device list refresh failed")
            # Trust reconciliation (+ optional bring-up introspection via
            # XLII_OMEMO_DEBUG). slixmpp-omemo only encrypts via oldmemo
            # (eu.siacs.conversations.axolotl); a recipient device active ONLY
            # under urn:xmpp:omemo:2 is invisible to us → keyless envelope →
            # "This message is OMEMO encrypted" fallback on the recipient.
            debug = bool(os.environ.get("XLII_OMEMO_DEBUG"))
            try:
                sm = await xep_0384.get_session_manager()
                own, _others = await sm.get_own_device_information()
                devs = await sm.get_device_information(recipient.bare)
                peers = [d for d in devs if d.device_id != own.device_id]
                if debug:
                    print(f"OMEMO: {len(peers)} recipient device(s) for "
                          f"{recipient.bare} (my device {own.device_id}):",
                          file=sys.stderr)
                    for d in peers:
                        ns = ",".join(sorted(n.rsplit(":", 1)[-1].rsplit(".", 1)[-1]
                                             for n in d.namespaces)) or "(none)"
                        act = ",".join(sorted(f"{k.rsplit(':',1)[-1].rsplit('.',1)[-1]}"
                                              f"={'on' if v else 'off'}"
                                              for k, v in d.active)) or "(none)"
                        print(f"  · device {d.device_id} …{d.identity_key.hex()[-8:]} "
                              f"[{d.trust_level_name}] ns={ns} active={act}",
                              file=sys.stderr)
                # Self-heal: when the operator opted into blind_trust, promote
                # any non-trusted recipient device to TRUSTED before encrypting.
                # This repairs the note-to-self BTBV trap (a device stuck at
                # DISTRUSTED because the sender's own device anchored BTBV into
                # manual mode) in-band, on the same run — no state-file surgery.
                if self._cfg.blind_trust:
                    for d in peers:
                        if d.trust_level_name != "TRUSTED":
                            await sm.set_trust(d.bare_jid, d.identity_key, "TRUSTED")
                            logging.info("blind-trusting device %s (was %s)",
                                         d.device_id, d.trust_level_name)
            except Exception:
                logging.exception("OMEMO device introspection failed")
            msg = self.make_message(mto=recipient, mtype="chat")
            msg["body"] = self._message
            msg.set_to(recipient)
            msg.set_from(self.boundjid)
            # slixmpp-omemo >= 2: encrypt_message returns ONE ready-to-send
            # stanza (fallback body + store hint + per-version OMEMO payloads,
            # to/type carried over) or None when nothing was encryptable; the
            # returned error set is non-critical by contract.
            encrypted, errors = await xep_0384.encrypt_message(msg, {recipient})
            if errors:
                logging.warning("OMEMO encryption warnings: %s", errors)
            if encrypted is None:
                logging.error("OMEMO produced no sendable payload for %s", recipient)
            else:
                # Which recipient device-ids did we seal a key for? Empty means
                # a keyless envelope → the recipient sees only the fallback
                # body. Surfaced under XLII_OMEMO_DEBUG; a truly empty set is
                # also a hard error regardless (nothing to deliver).
                try:
                    rids = sealed_recipient_keys(encrypted)
                    if debug:
                        print(f"OMEMO payload sealed for device(s): "
                              f"{rids or '(NONE)'}", file=sys.stderr)
                    if not rids:
                        logging.error(
                            "OMEMO sealed no recipient keys — the message would "
                            "arrive undecryptable; not marking as sent")
                        return
                except Exception:
                    logging.exception(
                        "payload introspection failed; not marking as sent")
                    return
                encrypted.send()
                self.sent_ok = True
        except Exception:
            logging.exception("notify send failed")
        finally:
            self.disconnect()


def send(cfg: NotifyConfig, password: str, message: str) -> bool:
    """Connect, encrypt + send one message, disconnect. Returns success."""
    xmpp = _Sender(cfg, password, message)
    # slixmpp >= 1.9 removed XMLStream.process(); connect() schedules on
    # xmpp.loop and the session end is signalled by the `disconnected` future.
    # Failure events must resolve that future too, or a bad password /
    # unreachable server would ride out the full timeout.
    def _fail(reason: str):
        def _handler(_event=None) -> None:
            print(f"error: {reason}", file=sys.stderr)
            xmpp.abort()
        return _handler

    xmpp.add_event_handler(
        "connection_failed", _fail("could not connect to the XMPP server"))
    xmpp.add_event_handler(
        "failed_auth",
        _fail("authentication failed — check the password in the "
              "configured password_env"))
    xmpp.connect()
    done, _pending = xmpp.loop.run_until_complete(
        asyncio.wait({xmpp.disconnected}, timeout=90)
    )
    if not done:
        xmpp.abort()
        return False
    # One-shot process: reap slixmpp's internal tasks so loop teardown doesn't
    # emit "Task was destroyed but it is pending!" warnings.
    leftovers = [t for t in asyncio.all_tasks(xmpp.loop) if not t.done()]
    for task in leftovers:
        task.cancel()
    if leftovers:
        xmpp.loop.run_until_complete(
            asyncio.gather(*leftovers, return_exceptions=True)
        )
    return xmpp.sent_ok


def run(config_path: Path = DEFAULT_NOTIFY_CONFIG, message: str = "") -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    if not message.strip():
        print("error: empty notification message", file=sys.stderr)
        return 3
    if not config_path.exists():
        print(f"error: notify config not found at {config_path}", file=sys.stderr)
        print("write a [notify] config (jid / recipient / password_env); see "
              "proposals/xmpp-fabric.md", file=sys.stderr)
        return 3
    try:
        cfg = NotifyConfig.load(config_path)
    except (KeyError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"error: invalid notify config at {config_path}: {e}", file=sys.stderr)
        return 3

    password = os.environ.get(cfg.password_env)
    if not password:
        print(f"error: notify password env var {cfg.password_env} is not set", file=sys.stderr)
        return 3

    if send(cfg, password, message):
        print(f"✓ sent (OMEMO) → {cfg.recipient}")
        return 0
    print("error: send failed — see log output above", file=sys.stderr)
    return 2
