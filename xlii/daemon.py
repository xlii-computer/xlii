"""XMPP command daemon — Phase 2 of the xlii multi-machine fabric.

Listens for OMEMO-encrypted DMs from a JID whitelist and dispatches messages.
Under the default slash grammar (`[policy] grammar = "slash"`), bare text is a
chat/agent turn and commands wear the REPL's own sigil:
- Built-in `/kill` shuts the daemon down cleanly; `/webcode` mints a pairing
  code; `/remote-control` opens sitting (admin-gated via `[trust] admin_jids`;
  empty = every allowlisted JID is an admin). Phone `/remote-control` needs
  `/xsu` unless Face already opened sitting. Both idle out after 5 minutes.
- Verb scripts in `~/.config/xlii/verbs/<name>.sh` run as `/name`; stdout
  becomes the OMEMO-encrypted reply
- Anything else falls back to `xlii ask <workspace> <prompt>`, which runs a
  one-shot xlii agent turn in the named (or most-recently-active) workspace
(`[policy] grammar = "bare"` restores the legacy first-word-verb routing.)

A workspace prefix `[alias] <message>` overrides the agent fallback target so
"[isaac2] grep me the auth module" runs the agent in that specific workspace.

Designed as a long-lived process. Tailscale-only substrate is assumed (Prosody
binds to the tailnet IP in `/etc/prosody/prosody.cfg.lua`). Every inbound
message is audit-logged in append-only JSONL.

This module mirrors the OMEMO setup in `~/.config/xlii/bin/xmpp_send.py` but
runs its own dedicated state file (`daemon-omemo-state.json`) — daemon and
sender JIDs are different identities and must not share OMEMO state.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from xlii.atomicio import write_text_atomic
from xlii.daemon_avatar import publish_daemon_avatar
from xlii.daemon_gate import (  # noqa: F401  (DEFAULT_* re-exported for cmds/daemon.py)
    DEFAULT_AUDIT_LOG,
    DEFAULT_CONFIG_PATH,
    DEFAULT_OMEMO_STATE,
    DEFAULT_VERBS_DIR,
    KEY_ENV,
    DaemonConfig,
    RateLimiter,
    WebcodeMintLimiter,
    append_revoke,
    ask_command_line,
    chunk_reply,
    daemon_agent_persona,
    classify_dispatch,
    daemon_state_dir,
    evaluate_daemon_launch,
    format_sessions_ls,
    is_admin_jid,
    list_verbs,
    mint_webcode_to_spool,
    omemo_device_trusted,
    read_sessions_mirror,
)
from xlii.omemo_payload import sealed_recipient_keys
from xlii.serve_spool import spool_path

# OMEMO / slixmpp deps come from the optional `[daemon]` extra
# (`pip install "xlii[daemon]"`). They are imported at module level, so this
# whole module only loads when the extra is installed — `xlii daemon`
# (xlii/cmds/daemon.py) imports it lazily and prints a friendly install hint on
# ImportError, exactly like `xlii mcp deep-contexts`.
from omemo.storage import Just, Maybe, Nothing, Storage
from omemo.types import JSONType
from slixmpp.clientxmpp import ClientXMPP
from slixmpp.jid import JID
from slixmpp.plugins import register_plugin
from slixmpp.stanza import Message
from slixmpp_omemo import TrustLevel, XEP_0384


# --------------------------------------------------------------------------- #
#  Defaults & limits
# --------------------------------------------------------------------------- #

MAX_REPLY_CHARS = 1500       # Conversations renders more, but huge replies are unwieldy
VERB_TIMEOUT_S = 30          # Per-verb hard timeout
AGENT_TIMEOUT_S = 300        # Agent-fallback hard timeout (5 min)

# Defaults until V3's [serve.public] accessors land (getattr-with-default).
_DEFAULT_CODE_TTL_S = 300
_DEFAULT_BASE_URL = ""


# --------------------------------------------------------------------------- #
#  OMEMO storage (mirrors xmpp_send.py)
# --------------------------------------------------------------------------- #

class JsonStorage(Storage):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        self._data: dict[str, JSONType] = {}
        if path.exists():
            try:
                self._data = json.loads(path.read_text("utf8"))
            except (json.JSONDecodeError, OSError, TypeError):
                # Corrupt or unreadable ledger — start with empty state
                pass

    async def _load(self, key: str) -> Maybe[JSONType]:
        if key in self._data:
            return Just(self._data[key])
        return Nothing()

    async def _store(self, key: str, value: JSONType) -> None:
        self._data[key] = value
        # Atomic + fsync: a crash mid-write must never corrupt the OMEMO ledger.
        write_text_atomic(self._path, json.dumps(self._data))

    async def _delete(self, key: str) -> None:
        self._data.pop(key, None)
        write_text_atomic(self._path, json.dumps(self._data))


class XEP_0384Impl(XEP_0384):
    default_config = {
        "json_file_path": None,
        "fallback_message": "This message is OMEMO encrypted.",
        # Blind-trust-before-verification. OFF by default (secure): an unknown
        # device is NOT trusted. Wired from DaemonConfig.blind_trust in run().
        "blind_trust": False,
    }

    def plugin_init(self) -> None:
        self._json_storage = JsonStorage(Path(self.json_file_path))
        super().plugin_init()

    @property
    def storage(self) -> Storage:
        return self._json_storage

    @property
    def _btbv_enabled(self) -> bool:
        # Default-secure: only blind-trust when the operator explicitly opts in
        # via `[trust] blind_trust = true` (run() warns loudly when they do).
        return bool(self.blind_trust)

    async def _devices_blindly_trusted(self, blindly_trusted, identifier):
        for d in blindly_trusted:
            logging.info(f"BTBV trusted device: {d.bare_jid}/{d.device_id}")

    async def _prompt_manual_trust(self, manually_trusted, identifier):
        # BTBV routes a device here (rather than blindly trusting it) once the
        # JID already has a trusted device. For note-to-self (me@ -> me@) that
        # anchor is the sender's OWN device, so this fires on the very first
        # message to a second device of the same account. We must not block on
        # stdin in a long-running daemon, so the decision is config-driven:
        # when the operator opted into blind_trust, honor it here too (trusting
        # new devices is exactly what blind_trust means) — distrusting would
        # silently defeat it. Only the secure default (blind_trust off)
        # distrusts unknown manual-tier devices.
        level = TrustLevel.TRUSTED if self.blind_trust else TrustLevel.DISTRUSTED
        sm = await self.get_session_manager()
        for d in manually_trusted:
            await sm.set_trust(d.bare_jid, d.identity_key, level.value)


register_plugin(XEP_0384Impl)


# --------------------------------------------------------------------------- #
#  webcode helpers — config lookup (V3 accessors when present)
# --------------------------------------------------------------------------- #

def _serve_public_settings() -> tuple[str, int]:
    """``(base_url, code_ttl_s)`` from GlobalConfig when V3 accessors exist.

    Falls back to empty base_url + 300s TTL so V5 can land before V3 merges.
    """
    try:
        from xlii.config import GlobalConfig
        g = GlobalConfig.load()
        base = getattr(g, "serve_public_base_url", None) or _DEFAULT_BASE_URL
        ttl = int(getattr(g, "serve_public_code_ttl_s", _DEFAULT_CODE_TTL_S) or _DEFAULT_CODE_TTL_S)
        if ttl <= 0:
            ttl = _DEFAULT_CODE_TTL_S
        return (str(base).strip(), ttl)
    except Exception:
        return (_DEFAULT_BASE_URL, _DEFAULT_CODE_TTL_S)


# --------------------------------------------------------------------------- #
#  The daemon
# --------------------------------------------------------------------------- #


class CommandDaemon(ClientXMPP):
    def __init__(self, cfg: DaemonConfig, password: str):
        super().__init__(cfg.jid, password)
        self.cfg = cfg
        self.rate_limiter = RateLimiter(
            cfg.max_per_minute, cfg.lockout_threshold, cfg.lockout_duration_s
        )
        self.webcode_mint_limiter = WebcodeMintLimiter()
        self.shutdown_requested = False
        # Fabric elevation (hidden /xsu): the TOTP secret is env-only (never a
        # file — same posture as the daemon password / management key). Absent →
        # the gate is disabled and destructive verbs keep their admin-tier gate,
        # so nothing regresses until an owner provisions a secret.
        import os as _os
        from xlii.daemon_gate import ElevationGate
        self.elevation = ElevationGate(
            _os.environ.get(getattr(cfg, "totp_secret_env", "XLII_DAEMON_TOTP_SECRET"), "")
        )
        self.add_event_handler("session_start", self._on_session_start)
        self.add_event_handler("message", self._on_message)
        self.add_event_handler("presence_subscribe", self._on_presence_subscribe)
        # Face remote-control: run the live desk instead of `xlii ask`.
        self.turn_handler = None  # Optional[Callable[..., str]]
        self.presence_priority = 0
        # Face sidecar only: discard offline/delayed stanzas (never a backlog).
        self.live_only = False
        self._live_since = 0.0
        self.config_path: Optional[Path] = None

    async def _on_session_start(self, _event: Any) -> None:
        # Roster first, then the 42-brain photo (XEP-0153 / XEP-0084), then
        # presence so the PHOTO hash rides the same available stanza as the
        # node_name status. A failed IQ must not keep the daemon offline.
        await self.get_roster()
        await self._publish_avatar()
        # Surface this body's fabric name in presence so the phone's roster
        # shows WHICH node it's talking to (throne, node1, …); "" = no status.
        pri = int(getattr(self, "presence_priority", 0) or 0)
        self.send_presence(
            pstatus=self.cfg.node_name or None,
            ppriority=pri,
        )
        # Directed available so the phone roster can show this contact online
        # even before a bidirectional subscribe finishes.
        for jid in self.cfg.allowed_jids:
            if jid and jid != self._bare_jid(self.cfg.jid):
                self.send_presence(
                    pto=jid,
                    pstatus=self.cfg.node_name or None,
                    ppriority=pri,
                )
        self._live_since = time.time()
        logging.info(
            f"daemon online as {self.cfg.jid}; "
            f"whitelist={self.cfg.allowed_jids or '(empty — all messages will be rejected)'}"
        )
        # Allowlisted phones must be able to fetch OMEMO PEP. Auto-complete
        # presence so a brand-new Face JID isn't stuck on "error fetching omemo".
        for jid in self.cfg.allowed_jids:
            if jid and jid != self._bare_jid(self.cfg.jid):
                self.send(self.make_presence(pto=jid, ptype="subscribed"))
                self.send(self.make_presence(pto=jid, ptype="subscribe"))
        try:
            from xlii.panic_mail import check_on_wake

            r = check_on_wake()
            if r.status in ("kill", "destroy_run"):
                logging.warning("panic mail on wake: %s", r.status)
                self.shutdown_requested = True
        except Exception as e:
            logging.warning("panic wake failed: %s: %s", type(e).__name__, e)
        try:
            from xlii.config import GlobalConfig
            from xlii.farm_xmpp import attach_farm

            await attach_farm(self, GlobalConfig.load())
        except Exception as e:
            logging.warning("farm MUC join skipped: %s: %s", type(e).__name__, e)
        try:
            from xlii.persona import DEFAULT_PERSONA_ID, ensure_default_persona

            ensure_default_persona(DEFAULT_PERSONA_ID)
        except Exception as e:
            logging.warning("mojo seed skipped: %s: %s", type(e).__name__, e)
        asyncio.create_task(self._watch_sitting())

    def _on_presence_subscribe(self, presence: Any) -> None:
        sender = JID(presence["from"]).bare
        if sender not in (self.cfg.allowed_jids or []):
            return
        self.send(self.make_presence(pto=sender, ptype="subscribed"))
        self.send(self.make_presence(pto=sender, ptype="subscribe"))

    async def _publish_avatar(self) -> None:
        try:
            status = await publish_daemon_avatar(self)
            logging.info("xmpp avatar: %s", status)
        except Exception as e:  # never let a photo IQ crash session start
            logging.warning(
                "xmpp avatar publish failed: %s: %s", type(e).__name__, e
            )

    async def _maybe_blind_trust(self, sender: str, device_info: Any) -> bool:
        """Promote an UNDECIDED sender device to TRUSTED when ``blind_trust`` is
        on (BTBV, first contact). Caller has already whitelist-checked ``sender``.
        Returns False (reject) when blind_trust is off or the promotion errors."""
        if not self.cfg.blind_trust:
            return False
        try:
            sm = await self["xep_0384"].get_session_manager()
            await sm.set_trust(device_info.bare_jid, device_info.identity_key, TrustLevel.TRUSTED.value)
        except Exception as e:  # never let a trust write crash the message loop
            logging.warning(f"blind-trust promotion failed for {sender}: {type(e).__name__}: {e}")
            return False
        logging.info(f"blind-trust: promoted {sender}/{getattr(device_info, 'device_id', '?')} "
                     "→ TRUSTED (BTBV, first contact)")
        return True

    async def _try_pair_grant(
        self,
        sender: str,
        body: str,
        device_info: Any,
        *,
        allowlisted: bool,
    ) -> bool:
        """Bound-TOFU grant. Returns True when this stanza was consumed as a
        pairing attempt (grant *or* deny) so the caller must not dispatch the
        code as a verb. Returns False when there is no live window / the body
        is not pairing-shaped — fall through to the normal receive path."""
        from xlii.pairing_gate import PairingStore, REASON_NO_WINDOW
        from xlii.serve_gate import is_valid_format

        if not is_valid_format(body or ""):
            return False
        store = PairingStore()
        if store.get("daemon") is None:
            return False
        if device_info is None:
            # Window is open and this looks like the code, but there is no
            # identity to pin. Leave the window live; ask for an OMEMO send.
            self._audit(sender, None, "pair-need-omemo")
            return False
        device_id = str(getattr(device_info, "device_id", "") or "")
        decision = store.evaluate(
            rail="daemon",
            sender_jid=sender,
            body=body or "",
            device_id=device_id,
            allowlisted=allowlisted,
        )
        if decision.reason == REASON_NO_WINDOW:
            return False
        self._audit(sender, None, decision.reason)
        if not decision.granted:
            # Swallow pairing-shaped bodies while a window is open so a
            # Crockford-8 code never runs as a verb (trusted or not).
            if allowlisted:
                await self._encrypted_reply(
                    sender,
                    f"[daemon] pairing failed ({decision.reason}).",
                )
            return True
        try:
            sm = await self["xep_0384"].get_session_manager()
            await sm.set_trust(
                getattr(device_info, "bare_jid", sender),
                device_info.identity_key,
                TrustLevel.TRUSTED.value,
            )
        except Exception as e:
            logging.warning("pair-grant trust write failed: %s: %s", type(e).__name__, e)
            self._audit(sender, None, "pair-grant-failed")
            store.unconsume("daemon")
            return True
        if decision.enroll_jid:
            if decision.enroll_jid not in self.cfg.allowed_jids:
                self.cfg.allowed_jids.append(decision.enroll_jid)
            path = getattr(self, "config_path", None)
            if path is not None:
                from xlii.daemon_toml import whitelist_add_jid

                whitelist_add_jid(path, decision.enroll_jid)
        logging.info(
            "pair-grant: pinned %s/%s → TRUSTED (window closed)", sender, device_id,
        )
        await self._encrypted_reply(
            sender,
            f"[daemon] paired. device {device_id} pinned TRUSTED.",
        )
        return True

    def _pair_invited(self, sender: str) -> bool:
        """True when a live ``--invite`` window names ``sender``.

        Cheap, pre-decrypt gate: only an allowlisted sender or the invited JID
        is worth an OMEMO decrypt (decrypt has device/trust-store side effects
        and is unbounded work), so every other JID is dropped before any crypto
        exactly as it was before pairing existed.
        """
        from xlii.pairing_gate import PairingStore

        store = PairingStore()
        window = store.get("daemon")
        now = time.time()
        if window is None or not window.live(now):
            return False
        if (window.invite_jid or "").strip() != sender:
            return False
        return store.locked_until(sender) <= now

    async def _on_message(self, stanza: Message) -> None:
        if stanza["type"] not in ("chat", "normal"):
            return
        sender = JID(stanza["from"]).bare

        xep_0384: XEP_0384 = self["xep_0384"]
        ns = xep_0384.is_encrypted(stanza)
        try:
            plain_body = (stanza["body"] or "").strip()
        except Exception:
            plain_body = ""
        allowlisted = sender in (self.cfg.allowed_jids or [])
        # Who may reach the decrypt / pairing path at all.
        pairable = allowlisted or self._pair_invited(sender)

        if not ns:
            if pairable and await self._try_pair_grant(
                sender, plain_body, None, allowlisted=allowlisted,
            ):
                return
            self._audit(sender, None, "rejected: unencrypted")
            self._send_plain(sender, "[daemon] OMEMO required; message rejected.")
            return

        if not pairable:
            self._audit(sender, None, "rejected: not in whitelist")
            return  # silent — an unknown JID never gets a decrypt

        if not allowlisted:
            # Invited but not yet enrolled: bound the decrypt work the open
            # window can be made to do before the code is even looked at.
            ok, reason = self.rate_limiter.check(sender)
            if not ok:
                self._audit(sender, None, f"rate-limited: {reason}")
                return

        try:
            inner, device_info = await xep_0384.decrypt_message(stanza)
            body = (inner["body"] or "").strip()
        except Exception as e:
            logging.exception("decrypt failed")
            if await self._try_pair_grant(
                sender, plain_body, None, allowlisted=allowlisted,
            ):
                return
            if not allowlisted:
                self._audit(sender, None, "rejected: not in whitelist")
                return
            self._audit(sender, None, f"decrypt error: {type(e).__name__}: {e}")
            await self._encrypted_reply(sender, f"[daemon] decryption failed: {type(e).__name__}")
            return

        if await self._try_pair_grant(
            sender, body, device_info, allowlisted=allowlisted,
        ):
            return

        if not allowlisted:
            self._audit(sender, None, "rejected: not in whitelist")
            return  # silent — don't ack to non-whitelisted JIDs

        ok, reason = self.rate_limiter.check(sender)
        if not ok:
            self._audit(sender, None, f"rate-limited: {reason}")
            await self._encrypted_reply(sender, f"[daemon] {reason}")
            return

        if not omemo_device_trusted(device_info):
            device_id = getattr(device_info, "device_id", "?")
            # BTBV at the INCOMING gate. python-omemo's blind-trust callbacks
            # only resolve trust on the ENCRYPT path, but the daemon rejects
            # here (decrypt) before it ever replies — so a receive-first
            # device from an allowlisted JID would stay UNDECIDED forever and
            # blind_trust would never fire. With blind_trust on, promote it on
            # first contact (the sender was already whitelist-checked above);
            # the secure default still rejects and requires `xlii pair` /
            # `xlii daemon trust`.
            if not await self._maybe_blind_trust(sender, device_info):
                trust = getattr(device_info, "trust_level_name", "unknown")
                self._audit(sender, None, f"rejected: untrusted device {device_id} ({trust})")
                await self._encrypted_reply(
                    sender,
                    "[daemon] device not trusted. Pair with `xlii pair`, or pin: "
                    f"xlii daemon trust {sender} <omemo-fingerprint>",
                )
                return
            self._audit(sender, None, f"blind-trusted device {device_id} (first contact)")

        from xlii import media_in

        # Media-in (#3): a photo/file arrives as an aesgcm:// URL in an XEP-0066
        # OOB <url> element — usually with an EMPTY body — not the text. Collect
        # those (from the decrypted stanza, else the raw one) so an image message
        # isn't silently dropped by the empty-body gate below.
        oob = media_in.oob_urls(inner) or media_in.oob_urls(stanza)
        media_body = "\n".join(t for t in (body, *oob) if t).strip()

        if getattr(self, "live_only", False):
            from xlii.face_remote import drop_reason_live_only, stanza_delay_stamp

            delay = stanza_delay_stamp(stanza) or stanza_delay_stamp(inner)
            reason = drop_reason_live_only(
                delay_stamp=delay,
                live_since=float(getattr(self, "_live_since", 0) or 0),
                now=time.time(),
            )
            if reason:
                self._audit(sender, media_in.redact_for_audit(body) or "(empty)",
                            f"dropped: {reason} (live-only)")
                note = (
                    "[face] discarded (was not live). "
                    "send again with sitting open."
                    if reason == "offline-delay"
                    else "[face] discarded (throne just came online). send again."
                )
                await self._encrypted_reply(sender, note)
                return

        if not media_body:
            # No text AND no recognizable attachment. Log the stanza SHAPE (tag
            # names only — never content) so an unsupported attachment form
            # (e.g. XEP-0447 stateless file sharing) shows up in the journal
            # instead of vanishing without a trace.
            try:
                tags = sorted(
                    {el.tag for el in inner.xml.iter()}
                    | {el.tag for el in stanza.xml.iter()}
                )
                logging.info("empty-body message from %s dropped; elements: %s",
                             sender, ", ".join(tags))
            except Exception as e:
                logging.debug("stanza-shape logging failed: %s", e)
            return

        self._audit(sender, media_in.redact_for_audit(body) or "(media)", "received")

        # Fetch + decrypt any shared-file URL (aesgcm:// or plain media https://)
        # to a temp file and hand it to the turn so iXaac SEES it. No media URL ⇒
        # body unchanged, no fetch.
        media_paths: list[str] = []
        temp_paths: list[str] = []
        try:
            body, media_paths = await asyncio.to_thread(self._materialize_media, media_body)
            temp_paths = list(media_paths)
        except Exception as e:
            logging.warning("media-in failed: %s", e)

        # The media inbox (tauri-face V5): persist inbound files to the
        # persona's durable media store BEFORE the turn — never unlink an
        # ear's input. The turn attaches the PERSISTED paths; the finally
        # below cleans only the temp copies (already moved on success). On a
        # persist failure the turn still runs off the temp files, exactly the
        # old lifecycle.
        if media_paths:
            try:
                media_paths = await asyncio.to_thread(
                    self._persist_media, media_paths, body, sender, media_body
                )
            except Exception as e:
                logging.warning("media persist failed (serving from temp): %s", e)

        # X2 chat states: `composing` while the dispatch runs, `active` with
        # the reply — turns dead air into "it's working" on Conversations/Monal.
        self._send_chat_state(sender, "composing")
        progress_task: Optional[asyncio.Task] = None
        if self.cfg.progress_after_s > 0:
            progress_task = asyncio.create_task(self._progress_ping(sender))
        try:
            reply = await self._dispatch(sender, body, attachments=media_paths or None)
        except Exception as e:
            logging.exception("dispatch error")
            reply = f"[daemon] error: {type(e).__name__}: {e}"
            self._audit(sender, body, f"dispatch error: {e}")
        finally:
            if progress_task is not None:
                progress_task.cancel()
            self._send_chat_state(sender, "active")
            # Temp copies only: on a successful persist the files were MOVED
            # (unlink is a no-op miss); on a failed persist this is the old
            # cleanup unchanged. The persisted store is never touched here.
            for p in temp_paths:
                try:
                    os.unlink(p)
                except OSError as e:
                    logging.debug("best-effort temp file cleanup failed for %s: %s", p, e)
            for d in {os.path.dirname(p) for p in temp_paths}:
                try:
                    os.rmdir(d)
                except OSError as e:
                    logging.debug("best-effort temp dir cleanup failed for %s: %s", d, e)

        if reply:
            # Chunk, don't truncate (X2): long replies arrive as ordered
            # ` (i/n)`-suffixed messages instead of being silently cut.
            for chunk in chunk_reply(reply, MAX_REPLY_CHARS):
                await self._encrypted_reply(sender, chunk)

        if self.shutdown_requested:
            await asyncio.sleep(2)  # let final reply flush
            self.disconnect()

    async def _dispatch(self, sender: str, body: str,
                        attachments: "Optional[list[str]]" = None) -> str:
        # Elevation lockout (fabric): 3 bad /xsu codes in a minute locked the
        # daemon — refuse EVERYTHING (even chat) until an SSH/console restart.
        # The lock is in-memory, so the restart is the recovery.
        if self.elevation.is_locked():
            self._audit(sender, body, "rejected: daemon locked (elevation lockout)")
            return "[daemon] locked — restart required"

        now = time.time()
        if self.elevation.configured:
            self.elevation.touch(sender, now=now)

        # The routing *decision* (kill / webcode / remote-control / verb / agent /
        # unknown / elevate) is pure and lives in daemon_gate.classify_dispatch
        # (unit-tested without the [daemon] extra). This method only performs
        # the side effect.
        decision = classify_dispatch(
            body,
            verbs_dir=self.cfg.verbs_dir,
            fallback_enabled=self.cfg.fallback_enabled,
            fallback_workspace=self.cfg.fallback_workspace,
            sender=sender,
            grammar=self.cfg.grammar,
            is_admin=is_admin_jid(self.cfg, sender),
        )
        self._audit(sender, body, decision.audit)

        if decision.kind == "elevate":
            return self._run_elevate(sender, decision.elevate_code, verbs_dir=self.cfg.verbs_dir)
        if decision.kind == "kill":
            if not self._elevated_or_refuse(sender, "kill"):
                return "[daemon] elevation required"
            from xlii.daemon_kill import disable_systemd_restart

            note = disable_systemd_restart()
            self._audit(sender, body, f"kill systemd: {note}")
            self.shutdown_requested = True
            return decision.reply
        if decision.kind == "webcode":
            # Minting a webcode grants a shell on the terminal — a high-risk act,
            # so the MFA unlocks the magic link (both /webcode and /kill gated).
            if not self._elevated_or_refuse(sender, "webcode"):
                return "[daemon] elevation required"
            return self._run_webcode(sender, decision)
        if decision.kind == "remote_lab":
            return self._run_remote_lab(sender, decision)
        if decision.kind == "verb":
            return await self._run_verb(decision.verb_path, decision.verb_args)
        if decision.kind == "node":
            return self._whoami_reply()
        if decision.kind == "unknown":
            return decision.reply
        if decision.kind == "agent":
            from xlii.glass import rider_agent_allowed

            rider = self._bare_jid(sender) != self._bare_jid(self.cfg.jid)
            persona = daemon_agent_persona(self.cfg)
            allowed, kind = rider_agent_allowed(
                rider=rider, persona=persona, node=self.cfg.node_name,
            )
            if not allowed:
                self._audit(sender, body, "rejected: $ sitting closed")
                return (
                    "[daemon] $ sitting closed — desk /remote-control open "
                    "(mojo still works when a persona is bound)"
                )
            # Face remote-control: sitting means THIS window, not a lab oneshot.
            if callable(getattr(self, "turn_handler", None)):
                force_lab = False
            else:
                force_lab = kind == "dollar"
        else:
            force_lab = False
        # Media-out: every agent turn gets a fresh outbox — the delivery channel
        # send_file queues into. Whatever lands there is encrypted, uploaded
        # (XEP-0363) and sent to the SENDER as inline media, files first, text
        # reply after (how a human sends a photo with a comment).
        import shutil
        import tempfile

        outbox = Path(tempfile.mkdtemp(prefix="xlii-outbox-"))
        try:
            reply = await self._run_agent(decision.prompt, decision.workspace,
                                          decision.session, attachments=attachments,
                                          outbox=str(outbox), force_lab=force_lab)
            await self._deliver_outbox(sender, outbox)
        finally:
            shutil.rmtree(outbox, ignore_errors=True)
        return reply

    @staticmethod
    def _bare_jid(jid: str) -> str:
        return (jid or "").split("/", 1)[0].strip().lower()

    async def _watch_sitting(self) -> None:
        """When Face on this box opens remote-control, ping me@ (whoami + $)."""
        from xlii.occupancy_store import load_live

        prev = False
        while not getattr(self, "shutdown_requested", False):
            await asyncio.sleep(1.2)
            try:
                occ = load_live()
                live = bool(occ.remote_lab.open and not occ.remote_lab.locked)
            except Exception:
                continue
            if live and not prev:
                try:
                    await self._announce_sitting()
                except Exception:
                    logging.warning("sitting announce failed", exc_info=True)
            prev = live

    async def _announce_sitting(self) -> None:
        from xlii.daemon_gate import daemon_agent_persona
        from xlii.repl_cmds.remote_lab import sitting_open_notice

        me = ""
        own = self._bare_jid(self.cfg.jid)
        for jid in self.cfg.allowed_jids or []:
            if jid and self._bare_jid(jid) != own:
                me = jid
                break
        if not me:
            return
        node = self.cfg.node_name or own.split("@", 1)[0]
        persona = daemon_agent_persona(self.cfg) or "mojo"
        text = sitting_open_notice(node=node, persona=persona, jid=self.cfg.jid)
        await self._encrypted_reply(me, text)

    def _whoami_reply(self) -> str:
        """Answer /whoami: which body + which persona is speaking (fabric)."""
        from xlii.config import GlobalConfig
        from xlii.daemon_gate import daemon_agent_persona
        from xlii.persona import DEFAULT_PERSONA_DISPLAY, DEFAULT_PERSONA_ID

        node = self.cfg.node_name or "(unnamed node)"
        # Same resolver the mouth uses — never DaemonConfig-as-GlobalConfig,
        # and never the iXaac chat costume as the journal.
        try:
            gcfg = GlobalConfig.load()
        except Exception:
            gcfg = None
        pid = daemon_agent_persona(self.cfg, global_cfg=gcfg) or DEFAULT_PERSONA_ID
        persona = DEFAULT_PERSONA_DISPLAY if pid == DEFAULT_PERSONA_ID else pid
        return f"[node] {node} · persona: {persona} · {self.cfg.jid}"

    def _elevated_or_refuse(self, sender: str, verb: str) -> bool:
        """Gate a destructive verb on a live elevation when the TOTP gate is armed.

        Unconfigured → True (the classify-layer admin gate already applied — no
        regression). Configured → require a live elevation, and SPEND it on use
        (one code, one destructive act)."""
        if not self.elevation.configured:
            return True
        if not self.elevation.is_elevated(sender, now=time.time()):
            self._audit(sender, verb, f"{verb} blocked: not elevated")
            return False
        self.elevation.consume_elevation(sender)
        return True

    def _sitting_live(self, *, now: float) -> bool:
        from xlii.occupancy_store import load_live
        from xlii.repl_cmds.remote_lab import sitting_live

        try:
            return sitting_live(load_live(now=now), now=now)
        except Exception:
            return False

    def _run_remote_lab(self, sender: str, decision) -> str:
        """Execute a classified ``/remote-control`` (open/lock/unlock/drop/status).

        Phone mint (open/unlock when sitting is not live) spends ``/xsu``.
        Face already-open sitting skips the TOTP — the user is at the glass.
        """
        from xlii.repl_cmds.remote_lab import (
            apply_remote_control,
            format_daemon_sitting_reply,
        )

        action = decision.remote_lab_action
        if action is None:
            return decision.reply or (
                "[daemon] usage: /remote-control "
                "[open|lock|unlock|drop|status]"
            )
        now = time.time()
        live = self._sitting_live(now=now)
        if action in ("open", "unlock") and not live:
            if not self._elevated_or_refuse(sender, "remote-control"):
                return "[daemon] elevation required"
        if action == "open" and live:
            from xlii.occupancy_store import load_live

            return format_daemon_sitting_reply(load_live(now=now), action)
        occ = apply_remote_control(action, now=now, source="phone")
        return format_daemon_sitting_reply(occ, action)

    def _run_elevate(self, sender: str, code: Optional[str], *, verbs_dir) -> str:
        """Perform a hidden `/xsu` elevation attempt.

        When the gate is unconfigured, `/xsu` is indistinguishable from any other
        unknown command (it reveals nothing and never touches the lockout
        counter). Otherwise map the ElevationGate result to a terse reply — no
        detail that would help a prober."""
        if not self.elevation.configured:
            return f"[daemon] unknown command. verbs: {list_verbs(verbs_dir)}"
        result = self.elevation.attempt(sender, code or "", now=time.time())
        if result == "elevated":
            return "[daemon] elevated."
        if result == "grace":
            return "[daemon] denied — one more attempt."
        if result == "locked":
            return "[daemon] locked — restart required"
        return "[daemon] denied."

    def _run_webcode(self, sender: str, decision) -> str:
        """Execute a classified webcode decision (mint / ls / kill).

        CLI parity for on-box operators is V3's ``xlii serve mint|sessions|revoke``
        — same spool + mirror; this is the phone/fabric arm only.
        """
        # Canned usage / unknown-sub-verb replies from classify.
        if decision.webcode_action is None:
            return decision.reply or (
                "[daemon] usage: webcode | webcode preview | webcode ls | "
                "webcode last | webcode email | webcode kill <id>|all"
            )
        if decision.webcode_action == "kill" and not decision.webcode_kill_target:
            return decision.reply or "[daemon] usage: webcode kill <id>|all"

        action = decision.webcode_action
        state_dir = daemon_state_dir(self.cfg)
        now = time.time()

        if action in ("mint", "preview", "email"):
            return self._webcode_mint(
                sender, mode=decision.webcode_mode, now=now, state_dir=state_dir,
                email=action == "email",
            )
        if action == "ls":
            sessions = read_sessions_mirror(state_dir)
            return format_sessions_ls(sessions, now=now)
        if action == "last":
            from xlii.face_receipt import format_face_receipt, read_face_receipt

            return format_face_receipt(read_face_receipt(state_dir), now=now)
        if action == "kill":
            target = decision.webcode_kill_target
            append_revoke(state_dir, target)
            self._audit(sender, f"webcode kill {target}", f"webcode: revoke queued ({target})")
            return f"[daemon] webcode kill queued: {target}"
        return (
            "[daemon] usage: webcode | webcode preview | webcode ls | "
            "webcode last | webcode email | webcode kill <id>|all"
        )

    def _webcode_mint(
        self,
        sender: str,
        *,
        mode: str,
        now: float,
        state_dir: Path,
        email: bool = False,
    ) -> str:
        ok, reason = self.webcode_mint_limiter.check(sender, now=now)
        if not ok:
            self._audit(sender, "webcode", f"webcode mint denied: {reason}")
            return f"[daemon] {reason}"

        base_url, ttl_s = _serve_public_settings()
        try:
            reply = mint_webcode_to_spool(
                state_dir, mode=mode, ttl_s=ttl_s, now=now, base_url=base_url,
            )
        except ValueError as e:
            return f"[daemon] {e}"
        except Exception as e:
            self._audit(sender, "webcode", f"webcode spool error: {e}")
            return f"[daemon] webcode spool error: {type(e).__name__}: {e}"

        self.webcode_mint_limiter.record(sender, now=now)
        spool = spool_path(state_dir)
        self._audit(
            sender,
            f"webcode {mode}",
            f"webcode minted mode={mode} spool={spool}",
        )
        if not email:
            return reply
        from xlii.webcode_mail import code_from_webcode_reply, mint_reply_and_email

        code = code_from_webcode_reply(reply)
        mailed = mint_reply_and_email(
            code=code, base_url=base_url, ttl_s=ttl_s,
            cfg=getattr(self, "cfg", None),
        )
        # mint_reply_and_email rebuilds the same reply + a status line.
        extra = mailed[len(reply):] if mailed.startswith(reply) else mailed
        if extra.strip():
            self._audit(sender, "webcode email", extra.strip())
        return mailed

    async def _run_verb(self, path: Path, args: list[str]) -> str:
        try:
            proc = await asyncio.create_subprocess_exec(
                str(path), *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=VERB_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"[daemon] verb timed out after {VERB_TIMEOUT_S}s"
        except Exception as e:
            # Verb execution failure (subprocess, timeout, etc.)
            return f"[daemon] verb error: {type(e).__name__}: {e}"

        out = stdout.decode("utf-8", "replace").rstrip()
        err = stderr.decode("utf-8", "replace").rstrip()
        if proc.returncode != 0 and not out:
            return f"[verb exit {proc.returncode}] {err[:500]}" if err else f"[verb exit {proc.returncode}]"
        if err:
            out = f"{out}\n[stderr] {err[:200]}" if out else f"[stderr] {err[:500]}"
        return out or f"(verb returned no output, exit {proc.returncode})"

    def _persist_media(self, paths: "list[str]", caption: str, sender: str,
                       source_body: str) -> "list[str]":
        """(media inbox) Move fetched files into the daemon persona's durable
        media store with metadata sidecars; return the persisted paths. The
        fabric pull carries this dir to the throne, where /media surfaces it
        in a live session. Blocking I/O — call via ``asyncio.to_thread``."""
        import time as _time

        from xlii import media_in
        from xlii.persona import CHAT_STATE_DIR, DEFAULT_PERSONA_ID

        pid = daemon_agent_persona(self.cfg) or DEFAULT_PERSONA_ID
        media_dir = CHAT_STATE_DIR / pid / "media"
        persisted = media_in.persist_inbound(
            [Path(p) for p in paths],
            caption=caption,
            sender=sender,
            ts=_time.time(),
            source_redacted=media_in.redact_for_audit(source_body),
            media_dir=media_dir,
        )
        persisted_paths = [str(p) for p in persisted]
        temp_fallbacks = [p for p in paths if Path(p).exists()]
        return persisted_paths + temp_fallbacks or list(paths)

    def _materialize_media(self, body: str) -> "tuple[str, list[str]]":
        """(#3 media-in) Fetch + decrypt any shared-file URLs in the body into a
        fresh temp dir; return ``(caption, [paths])``. Blocking I/O — call via
        ``asyncio.to_thread``. No media URL ⇒ ``(body, [])`` and no temp dir left."""
        import tempfile

        from xlii import media_in

        tmp = Path(tempfile.mkdtemp(prefix="xlii-media-"))
        caption, paths = media_in.prepare(body, tmp)
        if not paths:
            try:
                tmp.rmdir()
            except OSError as e:
                logging.debug("best-effort empty media temp dir cleanup failed for %s: %s", tmp, e)
        return caption, [str(p) for p in paths]

    async def _run_agent(self, prompt: str, workspace_key: str, session_id: str = "",
                         attachments: "Optional[list[str]]" = None,
                         outbox: str = "", force_lab: bool = False) -> str:
        """Spawn `xlii ask <prompt>` and capture its reply.

        We rely on `xlii` being on PATH. When a persona is configured
        (`[agent_fallback] persona`, fabric F2) the turn runs AS that persona
        over its own memory — every surface becomes the same entity, and the
        persona is the continuity, so the per-sender `--session` is dropped.
        Otherwise `--workspace` + `--session` (X1) carry the project context and
        the per-sender conversation id. Either way the subprocess boundary — the
        fabric's audited seam — stays.
        """
        handler = getattr(self, "turn_handler", None)
        if callable(handler):
            try:
                return await asyncio.to_thread(
                    handler, prompt,
                    workspace=workspace_key, session=session_id,
                    attachments=attachments, outbox=outbox,
                    force_lab=force_lab,
                )
            except Exception as e:
                return f"[daemon] face turn failed: {type(e).__name__}: {e}"
        persona = daemon_agent_persona(self.cfg, force_lab=force_lab)
        cmd = ask_command_line(
            prompt,
            persona=persona,
            workspace=workspace_key,
            session=session_id,
            attachments=attachments,
            outbox=outbox,
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=AGENT_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return f"[daemon] agent timed out after {AGENT_TIMEOUT_S}s"
        except FileNotFoundError:
            return "[daemon] `xlii` not on PATH; agent fallback unavailable"
        except Exception as e:
            # Agent fallback spawn failure
            return f"[daemon] agent error: {type(e).__name__}: {e}"

        out = stdout.decode("utf-8", "replace").rstrip()
        err = stderr.decode("utf-8", "replace").rstrip()
        if proc.returncode != 0:
            return f"[agent exit {proc.returncode}] {(err or out)[:800]}"
        return out or "(agent returned no output)"

    async def _deliver_outbox(self, to_jid: str, outbox: Path) -> int:
        """Ship every file the turn queued (media-out, the inverse of
        ``_materialize_media``): encrypt (XEP-0454), upload to the server's
        file share (XEP-0363 — rides the same 443 path inbound photos use),
        send the ``aesgcm://`` link as an OMEMO body per file — Conversations
        renders it inline. A failed file is reported, never raised: the text
        reply must still go out."""
        from xlii import media_out

        try:
            files = sorted(p for p in outbox.iterdir() if p.is_file())
        except OSError:
            return 0
        sent = 0
        for p in files:
            try:
                name, mime, ciphertext, iv, key = await asyncio.to_thread(
                    media_out.prepare_upload, p)
                url = await self["xep_0363"].upload_file(
                    filename=name,
                    size=len(ciphertext),
                    content_type=mime,
                    input_file=io.BytesIO(ciphertext),
                    timeout=60,
                )
                await self._encrypted_reply(to_jid, media_out.aesgcm_url(url, iv, key))
                sent += 1
                self._audit(to_jid, f"(file: {name}, {len(ciphertext)} bytes)", "sent media")
            except Exception as e:
                logging.exception("media-out delivery failed for %s", p.name)
                await self._encrypted_reply(
                    to_jid, f"[daemon] couldn't deliver {p.name}: {type(e).__name__}: {e}")
        return sent

    def _send_chat_state(self, to_jid: str, state: str) -> None:
        """XEP-0085 typing indicator (X2). A standalone body-less stanza —
        chat states are metadata and ride plaintext even in OMEMO chats
        (standard client behavior). Best-effort: never blocks a dispatch."""
        try:
            msg = self.make_message(mto=to_jid, mtype="chat")
            msg["chat_state"] = state
            msg.send()
        except Exception:
            logging.debug("chat state send failed", exc_info=True)

    async def _progress_ping(self, to_jid: str) -> None:
        """One encrypted "still working…" after cfg.progress_after_s (X2).
        Cancelled when the dispatch finishes first; deliberately a single
        ping, not a stream — XMPP messages are discrete."""
        try:
            await asyncio.sleep(self.cfg.progress_after_s)
            await self._encrypted_reply(
                to_jid,
                f"[daemon] still working… ({self.cfg.progress_after_s}s and counting)",
            )
        except asyncio.CancelledError:
            # Expected when dispatch finishes before the delayed ping fires.
            pass
        except Exception:
            logging.debug("progress ping failed", exc_info=True)

    def _send_plain(self, to_jid: str, body: str) -> None:
        """Plaintext reply — only for cases where OMEMO isn't viable
        (e.g. unencrypted-rejected). Whitelisted senders always get
        encrypted replies via _encrypted_reply()."""
        msg = self.make_message(mto=JID(to_jid), mtype="chat")
        msg["body"] = body
        msg.send()

    async def _encrypted_reply(self, to_jid: str, body: str) -> None:
        xep_0384: XEP_0384 = self["xep_0384"]
        recipient = JID(to_jid)
        try:
            await xep_0384.refresh_device_lists({recipient}, force_download=True)
        except Exception:
            # OMEMO device list refresh is best-effort
            logging.exception("device list refresh failed")

        msg = self.make_message(mto=recipient, mtype="chat")
        msg["body"] = body
        msg.set_to(recipient)
        msg.set_from(self.boundjid)
        try:
            # slixmpp-omemo >= 2: ONE ready-to-send stanza (or None), plus a
            # non-critical error set — not the old per-namespace dict.
            encrypted, errors = await xep_0384.encrypt_message(msg, {recipient})
        except Exception:
            # Encryption failure — drop reply rather than send plaintext
            logging.exception("encrypt_message failed; reply dropped")
            return
        if errors:
            logging.warning("OMEMO encryption warnings: %s", errors)
        if encrypted is None:
            logging.error("OMEMO produced no sendable payload; reply dropped")
            return
        try:
            rids = sealed_recipient_keys(encrypted)
        except Exception:
            logging.exception("OMEMO payload introspection failed; reply dropped")
            return
        if not rids:
            logging.error(
                "OMEMO sealed no recipient keys; undecryptable reply dropped")
            return
        encrypted.send()

    def _audit(self, sender: str, body: Optional[str], status: str) -> None:
        try:
            self.cfg.audit_log.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            line = json.dumps({
                "ts": ts,
                "from": sender,
                "body": (body or "")[:500],
                "status": status,
            })
            # Plaintext OMEMO message bodies land here, so own-only perms.
            # O_CREAT honours umask, so chmod after open to force 0o600.
            fd = os.open(
                self.cfg.audit_log,
                os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                0o600,
            )
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                # The file was already opened 0o600; a filesystem that refuses fchmod keeps that mode.
                pass
            with os.fdopen(fd, "a") as f:
                f.write(line + "\n")
        except Exception:
            logging.exception("audit log write failed")


# --------------------------------------------------------------------------- #
#  Entry point — invoked by `xlii daemon`
# --------------------------------------------------------------------------- #

def run(config_path: Path = DEFAULT_CONFIG_PATH) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not config_path.exists():
        print(f"error: daemon config not found at {config_path}", file=sys.stderr)
        print("see ~/.config/xlii/daemon.toml.example for a template", file=sys.stderr)
        return 3

    try:
        cfg = DaemonConfig.load(config_path)
    except (KeyError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"error: invalid daemon config at {config_path}: {e}", file=sys.stderr)
        return 3

    password = os.environ.get(cfg.password_env)
    if not password:
        print("error: configured XMPP password environment variable is not set", file=sys.stderr)
        return 3

    if not cfg.allowed_jids:
        print(
            "error: whitelist.allowed_jids is empty — daemon would reject all "
            "messages. Refusing to start.", file=sys.stderr,
        )
        return 3

    # Keyed launch gate (Vector E). Starting the daemon is the privileged act, so
    # the gate lives here at the function chokepoint — `xlii daemon` from the CLI
    # or cron would bypass a REPL-only check. The admin secret in the vault is the
    # key; the operator presents it out-of-band via $XLII_DAEMON_KEY. The decision
    # is pure (daemon_gate.evaluate_daemon_launch); we just wire in the vault here.
    from xlii.vault import admin_secret_is_set, verify_admin_secret

    try:
        key_is_set = admin_secret_is_set()
    except Exception:
        key_is_set = False  # fail closed — unreadable vault means no key
    launch = evaluate_daemon_launch(
        keyed=cfg.keyed,
        key_is_set=key_is_set,
        provided_key=os.environ.get(KEY_ENV),
        verify_key=verify_admin_secret,
        blind_trust=cfg.blind_trust,
        autostart=cfg.autostart,
        always_on=cfg.always_on,
    )
    for w in launch.warnings:
        logging.warning(w)
    if not launch.allowed:
        print(f"error: {launch.reason}", file=sys.stderr)
        return 3
    logging.info("launch gate: %s", launch.reason)

    cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
    if not cfg.state_file.exists():
        cfg.state_file.touch(mode=0o600)
    cfg.audit_log.parent.mkdir(parents=True, exist_ok=True)

    xmpp = CommandDaemon(cfg, password)
    xmpp.config_path = config_path
    xmpp.register_plugin("xep_0030")
    xmpp.register_plugin("xep_0060")
    xmpp.register_plugin("xep_0163")
    xmpp.register_plugin("xep_0054")  # vCard — PHOTO for the roster
    xmpp.register_plugin("xep_0153")  # vCard-based avatars (Conversations)
    xmpp.register_plugin("xep_0084")  # PEP user avatar
    xmpp.register_plugin("xep_0199")
    try:
        from xlii.config import GlobalConfig
        from xlii.farm import job_muc_room

        if job_muc_room(GlobalConfig.load()):
            xmpp.register_plugin("xep_0045")
    except Exception:
        # No job MUC configured, or config unreadable -- the daemon runs without the MUC plugin.
        pass
    xmpp.register_plugin("xep_0085")  # chat states — typing indicator (X2)
    xmpp.register_plugin("xep_0363")  # HTTP upload — media-out delivery
    xmpp.register_plugin("xep_0380")
    xmpp.register_plugin(
        "xep_0384",
        {"json_file_path": str(cfg.state_file), "blind_trust": cfg.blind_trust},
        module=sys.modules[__name__],
    )

    # Survive transient disconnects. The old handler stopped the loop on *any*
    # disconnect, so a wifi blip or server restart killed the daemon — silently,
    # with exit 0. Now we only stop on an explicit shutdown (`kill` verb sets
    # shutdown_requested → self.disconnect()); an unexpected drop falls through
    # to slixmpp's auto-reconnect.
    xmpp.auto_reconnect = True

    def _on_disconnected(_event=None) -> None:
        if xmpp.shutdown_requested:
            logging.info("shutdown requested — stopping the event loop")
            xmpp.loop.stop()
        else:
            logging.warning(
                "disconnected unexpectedly — awaiting slixmpp auto-reconnect "
                "(the daemon stays up)"
            )

    xmpp.add_event_handler("disconnected", _on_disconnected)

    # slixmpp >= 1.9: connect() schedules on xmpp.loop and returns a Future —
    # there is no truthy success value to check; failures surface as
    # connection_failed/disconnected events and ride auto_reconnect.
    xmpp.connect()

    try:
        xmpp.loop.run_forever()
    except KeyboardInterrupt:
        logging.info("interrupted; disconnecting")
        xmpp.shutdown_requested = True
        try:
            xmpp.disconnect()
        except Exception:
            # Interrupt teardown: shutdown_requested is already set, so a failed disconnect changes nothing.
            pass
        return 0

    return 0


# --------------------------------------------------------------------------- #
#  Device-trust pinning — invoked by `xlii daemon trust <jid> <fingerprint>`
# --------------------------------------------------------------------------- #

def set_manual_trust(
    config_path: Path,
    jid: str,
    fingerprint: str,
    *,
    distrust: bool = False,
) -> int:
    """Pin (default) or un-pin (`distrust=True`) a sender device's OMEMO key.

    This is the answer to "blind_trust = false needs another way to establish
    trust" (proposals/xmpp-fabric.md F4): the operator verifies a device's
    fingerprint out-of-band, then runs `xlii daemon trust <jid> <fingerprint>`.

    EXPERIMENTAL — like the rest of the fabric, the connected OMEMO path can only
    be exercised against a live Prosody server on your tailnet; building the
    SessionManager itself publishes the daemon's bundle, so this *must* connect
    (it can't be a purely-local edit). Flow: connect as the daemon identity →
    `get_session_manager()` → `get_device_information(jid)` → match the device
    whose `format_identity_key` equals `fingerprint` (already normalized to 64
    lowercase hex) → `set_trust(...)`. Returns a process exit code:
    0 ok · 2 connect failed · 3 config/precondition · 4 no such fingerprint ·
    5 OMEMO operation error.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s"
    )

    if not config_path.exists():
        print(f"error: daemon config not found at {config_path}", file=sys.stderr)
        return 3
    try:
        cfg = DaemonConfig.load(config_path)
    except (KeyError, ValueError, tomllib.TOMLDecodeError) as e:
        print(f"error: invalid daemon config at {config_path}: {e}", file=sys.stderr)
        return 3

    password = os.environ.get(cfg.password_env)
    if not password:
        print(f"error: ${cfg.password_env} (the XMPP password) is not set",
              file=sys.stderr)
        return 3
    if not cfg.state_file.exists():
        print(
            f"error: OMEMO state {cfg.state_file} doesn't exist yet — the daemon "
            "must have run and received at least one message from this device "
            "before you can pin it.", file=sys.stderr,
        )
        return 3

    target = "".join(fingerprint.split()).lower()  # defensive; caller normalizes
    level = TrustLevel.DISTRUSTED.value if distrust else TrustLevel.TRUSTED.value
    outcome = {"code": 4}

    xmpp = ClientXMPP(cfg.jid, password)
    xmpp.register_plugin("xep_0030")
    xmpp.register_plugin("xep_0060")
    xmpp.register_plugin("xep_0163")
    xmpp.register_plugin("xep_0380")
    xmpp.register_plugin(
        "xep_0384",
        {"json_file_path": str(cfg.state_file), "blind_trust": cfg.blind_trust},
        module=sys.modules[__name__],
    )

    async def _do_trust(_event: Any = None) -> None:
        try:
            xep_0384: XEP_0384 = xmpp["xep_0384"]
            sm = await xep_0384.get_session_manager()
            try:
                await xep_0384.refresh_device_lists({JID(jid)}, force_download=True)
            except Exception:
                logging.exception("device-list refresh failed; using cached state")

            devices = await sm.get_device_information(jid)
            match = next(
                (d for d in devices
                 if "".join(sm.format_identity_key(d.identity_key)) == target),
                None,
            )
            if match is None:
                known = ", ".join(
                    sorted("".join(sm.format_identity_key(d.identity_key))
                           for d in devices)
                ) or "(none — daemon has not seen a device for this JID)"
                print(
                    f"error: no device of {jid} has fingerprint\n  {target}\n"
                    f"known fingerprints: {known}", file=sys.stderr,
                )
                outcome["code"] = 4
            else:
                await sm.set_trust(jid, match.identity_key, level)
                verb = "distrusted" if distrust else "trusted"
                print(f"✓ {verb} {jid} device {match.device_id}\n  {target}")
                outcome["code"] = 0
        except Exception as e:
            logging.exception("trust operation failed")
            print(f"error: trust operation failed: {type(e).__name__}: {e}",
                  file=sys.stderr)
            outcome["code"] = 5
        finally:
            xmpp.disconnect()

    xmpp.add_event_handler("session_start", _do_trust)
    # slixmpp >= 1.9 removed XMLStream.process() and connect() has no truthy
    # return; run the loop until _do_trust disconnects, signalled by the
    # `disconnected` future. Failure events must resolve it too.
    xmpp.add_event_handler("connection_failed", lambda _e: xmpp.abort())
    xmpp.add_event_handler("failed_auth", lambda _e: xmpp.disconnect())
    xmpp.connect()
    done, _pending = xmpp.loop.run_until_complete(
        asyncio.wait({xmpp.disconnected}, timeout=90)
    )
    if not done:
        xmpp.abort()
        print("error: timed out talking to the XMPP server", file=sys.stderr)
        return 2
    return outcome["code"]


if __name__ == "__main__":
    # Direct invocation: `python3 -m xlii.daemon [config.toml]`. Normally you run
    # it via `xlii daemon` (xlii/cmds/daemon.py), which adds the friendly
    # extra-missing hint and the experimental-safety banner.
    cfg_arg = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else DEFAULT_CONFIG_PATH
    sys.exit(run(cfg_arg))
