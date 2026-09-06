"""Remote-control: me@ talks to THIS Face window while a sitting is open.

Phone XMPP is a different mouth from Tauri. Occupancy sitting is the gate.
While Face is up, throne@ stays **online** so the server cannot store a queue.
Sitting still gates ingest. Delayed / offline stanzas (XEP-0203) are discarded
and never run — a DM sent before sitting must not fire later.

Face logs in as **its own** JID from ``face.toml`` ``[remote]`` — never
``daemon@`` (that is the node). Bare lines run as Mojo on this desk.
No ``?`` prefix. Mojo may ``dispatch_subagent`` with ``role=lab`` onto the
current Face project (writes stay on the code agent).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from xlii.occupancy_store import load_live

_log = logging.getLogger("xlii.face_remote")

_bridge_lock = threading.Lock()
_bridge: Any = None  # _XmppThread | None
_bridge_note = ""
_last_start_attempt = 0.0

# After presence, Prosody dumps the offline box in a burst. Those stanzas
# may lack a delay stamp. Ignore the burst; live DMs after this are fine.
LIVE_GRACE_S = 3.0
_DELAY_NS = "urn:xmpp:delay"
_DELAY_LEGACY_NS = "jabber:x:delay"


def stanza_delay_stamp(stanza: Any) -> Optional[float]:
    """Unix time of an XEP-0203 / XEP-0091 delay on *stanza*, or None if live."""
    xml = getattr(stanza, "xml", None)
    if xml is None:
        return None
    return _xml_delay_stamp(xml)


def _xml_delay_stamp(el: Any) -> Optional[float]:
    tag = getattr(el, "tag", "") or ""
    if isinstance(tag, str):
        local = tag.rsplit("}", 1)[-1]
        ns = tag[1:].split("}", 1)[0] if tag.startswith("{") else ""
        if (local == "delay" and ns in ("", _DELAY_NS)) or (
            local == "x" and ns in ("", _DELAY_LEGACY_NS)
        ):
            stamp = el.get("stamp") if hasattr(el, "get") else None
            parsed = _parse_xmpp_stamp(stamp)
            if parsed is not None:
                return parsed
    for child in list(el):
        hit = _xml_delay_stamp(child)
        if hit is not None:
            return hit
    return None


def _parse_xmpp_stamp(raw: Optional[str]) -> Optional[float]:
    from datetime import datetime, timezone

    s = (raw or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def drop_reason_live_only(
    *,
    delay_stamp: Optional[float],
    live_since: float,
    now: float,
    grace_s: float = LIVE_GRACE_S,
) -> str:
    """Why a Face sidecar must not ingest this stanza. Empty = live, ok."""
    if delay_stamp is not None:
        return "offline-delay"
    if live_since > 0 and now < live_since + grace_s:
        return "connect-drain"
    return ""


def sitting_allows_remote(*, now: Optional[float] = None) -> bool:
    clock = time.time() if now is None else now
    try:
        occ = load_live(now=clock)
    except Exception:
        return False
    return bool(occ.remote_lab_allows_dollar(now=clock))


def bridge_status() -> str:
    """One line for /remote-control status."""
    with _bridge_lock:
        note = _bridge_note
        alive = _bridge is not None and _bridge.is_alive()
    if alive:
        return note or "phone bridge listening"
    return note or "phone bridge off"


def ingest_face_turn(
    server: Any, text: str, *, claim_mouth: bool = True, surface: str | None = None,
) -> str:
    """Paint a user turn on Face, run Mojo, paint the reply, return it.

    Always talk — remote-control does not follow the glass [$] flip.
    Requires an open sitting (the HTTP/XMPP doors check too).
    ``claim_mouth=False`` when the tailnet glass already holds me@ via
    ``tailnet-glass`` (G4) — don't overwrite that with this_node().
    """
    t = (text or "").strip()
    if not t:
        return ""
    if not sitting_allows_remote():
        return (
            "[face] sitting closed — desk /remote-control open "
            "then text this window"
        )
    try:
        from xlii.occupancy_store import mutate

        mutate(lambda o: o.record_remote_lab_agent(now=time.time()))
    except Exception:
        pass
    if claim_mouth:
        try:
            from xlii.glass import claim_this_limb

            claim_this_limb()
        except Exception:
            # Occupancy bookkeeping is advisory — it must never block ingesting
            # the turn the user just sent.
            pass
    from xlii.cmds.sessions.ask import PersonaProjectError, run_persona_oneshot
    from xlii.cmds.sessions.resolve import _lookup_persona, ensure_default_persona
    from xlii.persona import DEFAULT_PERSONA_ID, talk_persona_id
    from xlii.repl_cmds.mojo import build_mojo_ambient
    from xlii.turn_events import AssistantAnswer, UserTurn
    from xlii.ws_protocol import serialize_event

    state = server.state
    persona_id = talk_persona_id(state=state, project=state.project, cfg=state.cfg)
    persona = _lookup_persona(persona_id)
    if persona is None and persona_id == DEFAULT_PERSONA_ID:
        persona = ensure_default_persona()
    if persona is None:
        return f"[face] persona {persona_id!r} not found"
    from xlii.bearings import face_surface

    if not surface:
        surface = face_surface(server)
    ambient = build_mojo_ambient(state, t, surface=surface)
    begin = getattr(server, "_begin_talk_turn", None)
    if callable(begin):
        begin(t)
    try:
        server.send(serialize_event(UserTurn(text=t, kind="talk")))
    except Exception:
        # Echoing the turn back to the Face is cosmetic; the turn still runs.
        pass

    def _hold(agent: Any) -> None:
        server._oneshot_agent = agent
        desk = getattr(state, "project", None)
        if desk is not None:
            agent.lab_project = desk
        agent.sitting = state
        try:
            agent.session.door_surface = surface
            emit = getattr(server, "_emit_door", None)
            if callable(emit):
                agent.session.emit_door = emit
        except Exception:
            pass

    from xlii.project_paths import is_home_desk_project

    desk = getattr(state, "project", None)
    hire = (
        "write" if desk is not None and not is_home_desk_project(desk)
        else "read"
    )

    try:
        from xlii.chat_tiers import session_chat_tier

        reply = run_persona_oneshot(
            persona, t, pool=state.pool, cfg=state.cfg,
            console=state.console, ambient_context=ambient,
            persist=True, drain=False,
            yolo=bool(getattr(server, "yolo", False)),
            chat_tier=session_chat_tier(state),
            cancelled=(
                server._turn_cancelled.is_set
                if getattr(server, "_turn_cancelled", None) is not None
                else None
            ),
            desk_xli_dir=getattr(desk, "xli_dir", None),
            on_agent=_hold,
            hire=hire,
            surface=surface,
        ) or ""
    except PersonaProjectError:
        if hasattr(server, "_talk_in_flight"):
            server._talk_in_flight = None
        return f"[face] couldn't open {persona.name}'s memory"
    finally:
        server._oneshot_agent = None
    finish = getattr(server, "_finish_talk_turn", None)
    if callable(finish):
        finish(reply)
    try:
        server.send(serialize_event(AssistantAnswer(
            markdown=reply, streamed=True, mode="chat",
            mode_color="magenta")))
        drain = getattr(server, "drain_outbox_to_wire", None)
        if callable(drain):
            drain()
    except Exception:
        # The reply is already recorded in the transcript — a dead Face socket
        # only costs this delivery.
        pass
    return reply


def reconcile_bridge(server: Any) -> None:
    """Keep throne@ online while Face is up so Prosody cannot backlog.

    Sitting still gates ingest. Disconnecting when the sitting closes is what
    stored the pre-open test message and ran it on the next connect.
    """
    with _bridge_lock:
        alive = _bridge is not None and _bridge.is_alive()
        if not alive:
            _start_bridge_locked(server)


def _start_bridge_locked(server: Any) -> None:
    global _bridge, _bridge_note, _last_start_attempt
    now = time.time()
    if now - _last_start_attempt < 15:
        return
    _last_start_attempt = now
    cfg, err = load_bridge_config()
    if cfg is None:
        _bridge_note = err or "no XMPP config for phone bridge"
        _log.warning(_bridge_note)
        return
    password = os.environ.get(cfg.password_env) or ""
    if not password:
        _bridge_note = f"set ${cfg.password_env} so Face can log in as {cfg.jid}"
        _log.warning(_bridge_note)
        return
    try:
        thread = _XmppThread(cfg, password, server)
        thread.start()
    except Exception as e:  # noqa: BLE001
        _bridge_note = f"phone bridge failed: {type(e).__name__}: {e}"
        _log.exception("face remote xmpp start")
        return
    _bridge = thread
    _bridge_note = f"phone bridge on — text {cfg.jid} from me@ (this Face window)"


def _stop_bridge_locked() -> None:
    global _bridge, _bridge_note
    t = _bridge
    _bridge = None
    if t is not None:
        t.request_stop()
    _bridge_note = "phone bridge off"


def bind_face_daemon(xmpp: Any, server: Any) -> None:
    """Face sidecar: live desk ingest, never an offline backlog."""
    xmpp.live_only = True
    from xlii.bearings import SURFACE_XMPP

    xmpp.turn_handler = lambda prompt, **_k: ingest_face_turn(
        server, prompt, surface=SURFACE_XMPP,
    )
    xmpp.presence_priority = 0


_NODE_LOCALPARTS = frozenset({"daemon", "xlii-daemon"})


def is_node_daemon_jid(jid: str) -> bool:
    """True for the node command daemon — Face must never log in as this."""
    local = (jid or "").split("@", 1)[0].strip().lower()
    return local in _NODE_LOCALPARTS


def _face_journal_name() -> str:
    """Throne/limb Mojo nickname — never hardcode, never the iXaac costume."""
    try:
        from xlii.config import GlobalConfig
        from xlii.persona import factory_persona_id

        return factory_persona_id(GlobalConfig.load())
    except Exception:
        return "mojo"


def load_bridge_config():
    """DaemonConfig for the Face sidecar from ``face.toml`` ``[remote]`` only.

    Never ``daemon@``. If this box already runs ``xlii daemon``, that process
    owns the body JID — Face sitting is occupancy, not a second account.
    """
    from xlii.daemon_gate import (
        DEFAULT_AUDIT_LOG,
        DEFAULT_CONFIG_PATH,
        DEFAULT_OMEMO_STATE,
        DEFAULT_VERBS_DIR,
        DaemonConfig,
    )
    from xlii.occupancy import _face_toml_path

    daemon_toml = DEFAULT_CONFIG_PATH.expanduser()
    if daemon_toml.is_file():
        return None, (
            "this box already has xlii daemon — phone DMs that JID. "
            "/remote-control open is occupancy ($ on that contact)."
        )

    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]

    path = _face_toml_path()
    if not path.is_file():
        return None, (
            "no [remote] jid — mint a Face account (not daemon@) and put "
            "jid + password_env in ~/.config/xlii/face.toml [remote]"
        )
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
        return None, f"face.toml: {type(e).__name__}: {e}"
    remote = raw.get("remote") if isinstance(raw.get("remote"), dict) else {}
    jid = str(remote.get("jid") or "").strip().lower()
    if not jid or "@" not in jid:
        return None, (
            "face.toml [remote] jid missing — mint a new @ for this desk, "
            "not daemon@"
        )
    if is_node_daemon_jid(jid):
        return None, (
            f"refused {jid} — daemon@ is the node. Give Face its own account."
        )
    me = str(remote.get("allowed") or remote.get("from") or "").strip().lower()
    if not me:
        me = _notify_jid()
    allowed = [me] if me else []
    extra = remote.get("allowed_jids")
    if isinstance(extra, list):
        allowed = [str(x).strip().lower() for x in extra if str(x).strip()]
    if not allowed:
        return None, "face.toml [remote] needs allowed_jids (me@)"
    pw_env = str(remote.get("password_env") or "XMPP_FACE_PASSWORD").strip()
    cfg = DaemonConfig(
        jid=jid,
        password_env=pw_env,
        state_file=Path(str(DEFAULT_OMEMO_STATE)).with_name(
            "face-remote-omemo-state.json"
        ),
        verbs_dir=DEFAULT_VERBS_DIR,
        audit_log=DEFAULT_AUDIT_LOG,
        allowed_jids=allowed,
        max_per_minute=10,
        lockout_threshold=5,
        lockout_duration_s=300,
        fallback_enabled=True,
        fallback_workspace="",
        fallback_persona=_face_journal_name(),
        node_name=str(remote.get("node_name") or "throne").strip() or "throne",
        blind_trust=bool(remote.get("blind_trust", False)),
        keyed=False,
        grammar="slash",
        progress_after_s=int(remote.get("progress_after_s") or 20),
    )
    return cfg, ""


def _notify_jid() -> str:
    from xlii.daemon_gate import DEFAULT_NOTIFY_CONFIG

    path = DEFAULT_NOTIFY_CONFIG
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    import re

    m = re.search(r'(?m)^[ \t]*jid[ \t]*=[ \t]*"([^"]+)"', text)
    return (m.group(1) if m else "").strip().lower()


def load_tailnet_allowlist(path: Optional[Path] = None) -> list[str]:
    """``face.toml [tailnet] allowed_devices`` — empty means no WhoIs door."""
    from xlii.occupancy import _face_toml_path

    cfg_path = path or _face_toml_path()
    if not cfg_path.is_file():
        return []
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]
    try:
        raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, tomllib.TOMLDecodeError):
        return []
    section = raw.get("tailnet") if isinstance(raw.get("tailnet"), dict) else {}
    extra = section.get("allowed_devices")
    if not isinstance(extra, list):
        return []
    return [str(x).strip() for x in extra if str(x).strip()]


_tailnet_limiter = None


def _rate_limiter():
    global _tailnet_limiter
    if _tailnet_limiter is None:
        from xlii.daemon_gate import RateLimiter

        _tailnet_limiter = RateLimiter(
            max_per_minute=10, lockout_threshold=5, lockout_duration_s=300,
        )
    return _tailnet_limiter


def _json_err(status: int, msg: str) -> tuple[int, bytes, str]:
    return status, json.dumps({"ok": False, "error": msg}).encode(), "application/json"


def _peer_tuple(peer: Any) -> tuple[str, int]:
    if not peer:
        return ("", 0)
    try:
        return (str(peer[0]), int(peer[1]))
    except (TypeError, ValueError, IndexError):
        return ("", 0)


def auth_tailnet_peer(
    peer: Any, *, rate_limit: bool = True,
) -> tuple[str, Optional[tuple[int, bytes, str]]]:
    """WhoIs + allowlist + sitting ``--for``. Returns ``(device_key, error)``.

    ``rate_limit`` is for ``/remote-turn`` (10/min). Glass asset GETs skip it
    — a page load is many files, not a turn.
    """
    from xlii.tailnet import names_match, whois

    ip, port = _peer_tuple(peer)
    peer_info = whois(ip, port) if ip else None
    if peer_info is None:
        _log.warning("tailnet door deny: no whois for %s:%s", ip, port)
        return "", _json_err(403, "not a tailnet peer")
    allowed = load_tailnet_allowlist()
    if not any(names_match(name, peer_info) for name in allowed):
        _log.warning(
            "tailnet door deny: unknown device %s (%s)",
            peer_info.node_name, peer_info.stable_id,
        )
        return "", _json_err(403, "device not allowlisted")
    bound = ""
    try:
        occ = load_live()
        bound = str(getattr(occ.remote_lab, "device", "") or "")
    except Exception:
        bound = ""
    if bound and not names_match(bound, peer_info):
        _log.warning(
            "tailnet door deny: sitting bound to %s, got %s",
            bound, peer_info.node_name,
        )
        return "", _json_err(403, f"sitting is bound to {bound}")
    key = peer_info.node_name or peer_info.stable_id or ip
    if rate_limit:
        ok, reason = _rate_limiter().check(key)
        if not ok:
            _log.warning("tailnet door deny: rate limit %s (%s)", key, reason)
            return "", _json_err(403, reason or "rate limit")
    return key, None


def _auth_http_remote(
    *,
    path: str,
    token: str,
    peer: Any,
    on_tailnet_bind: bool,
) -> tuple[str, Optional[tuple[int, bytes, str]]]:
    """Return (device_id, error_response). device_id is 'loopback' or a node name."""
    from urllib.parse import parse_qs, urlparse
    import secrets as _secrets

    parsed = urlparse(path)
    params = {k: (v[0] if v else "") for k, v in parse_qs(parsed.query).items()}
    got = params.get("token") or ""

    if not on_tailnet_bind:
        if not _secrets.compare_digest(got, token):
            return "", _json_err(403, "bad token")
        return "loopback", None

    return auth_tailnet_peer(peer, rate_limit=True)


def handle_http_remote_turn(
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    body: bytes,
    token: str,
    server: Any,
    peer: Any = None,
    on_tailnet_bind: bool = False,
) -> tuple[int, bytes, str]:
    """``POST /remote-turn`` → JSON ``{ok, reply}``.

    Loopback + token (unchanged), or tailnet bind + WhoIs + allowlisted device.
    Sitting is still the gate. Never trust X-Forwarded-For.
    """
    _device, denied = _auth_http_remote(
        path=path, token=token, peer=peer, on_tailnet_bind=on_tailnet_bind,
    )
    if denied is not None:
        return denied
    if method != "POST":
        return _json_err(405, "POST only")
    if not sitting_allows_remote():
        return _json_err(403, "sitting closed — /remote-control open")
    try:
        payload = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return _json_err(400, "invalid json")
    text = str((payload or {}).get("text") or "").strip()
    if not text:
        return _json_err(400, "need text")
    reply = ingest_face_turn(server, text)
    return 200, json.dumps({"ok": True, "reply": reply}).encode(), "application/json"


class _XmppThread(threading.Thread):
    def __init__(self, cfg, password: str, server: Any) -> None:
        super().__init__(name="face-remote-xmpp", daemon=True)
        self.cfg = cfg
        self.password = password
        self.server = server
        self._xmpp = None

    def request_stop(self) -> None:
        x = self._xmpp
        if x is None:
            return
        try:
            x.shutdown_requested = True
            x.disconnect()
        except Exception:
            # An already-disconnected daemon is the state we were asking for.
            pass

    def run(self) -> None:
        try:
            from xlii.daemon import CommandDaemon
            import sys as _sys

            xmpp = CommandDaemon(self.cfg, self.password)
            bind_face_daemon(xmpp, self.server)
            xmpp.register_plugin("xep_0030")
            xmpp.register_plugin("xep_0060")
            xmpp.register_plugin("xep_0163")
            xmpp.register_plugin("xep_0054")
            xmpp.register_plugin("xep_0153")
            xmpp.register_plugin("xep_0084")
            xmpp.register_plugin("xep_0199")
            xmpp.register_plugin("xep_0085")
            xmpp.register_plugin("xep_0363")
            xmpp.register_plugin("xep_0380")
            xmpp.register_plugin(
                "xep_0384",
                {
                    "json_file_path": str(self.cfg.state_file),
                    "blind_trust": self.cfg.blind_trust,
                },
                module=_sys.modules["xlii.daemon"],
            )
            xmpp.auto_reconnect = True
            self._xmpp = xmpp
            self.cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
            if not self.cfg.state_file.exists():
                self.cfg.state_file.touch(mode=0o600)
            xmpp.connect()
            xmpp.loop.run_forever()
        except Exception:
            _log.exception("face remote xmpp loop")
            global _bridge_note
            _bridge_note = "phone bridge died — see log"
