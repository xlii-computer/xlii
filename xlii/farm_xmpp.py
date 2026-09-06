"""XMPP MUC adapter for farm tickets. Requires the ``[daemon]`` extra.

Joins a members-only room as the node's daemon JID (same identity as
``xlii daemon``). Groupchat is plaintext on the hub — not OMEMO.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from xlii.daemon_gate import DEFAULT_CONFIG_PATH, DaemonConfig
from xlii.farm import (
    JobResult,
    Ticket,
    job_allowance,
    job_board_root,
    job_gig,
    job_muc_room,
    job_node_name,
    job_offers,
    job_skills,
    job_specialty,
    skip_reason,
)
from xlii.farm_muc import (
    CLAIM_GRACE_S,
    OP_AD,
    OP_BENCH,
    OP_CANCEL,
    OP_CLAIM,
    OP_OFFER,
    OP_RESULT,
    STEER_OPS,
    RoomLedger,
    encode_ad,
    encode_bench,
    encode_cancel,
    encode_claim,
    encode_offer,
    encode_result,
    parse_farm_body,
)
from xlii.job_board import JobBoard
from xlii.job_run import run_ticket


log = logging.getLogger("xlii.farm")


def _creds(config_path: Optional[Path] = None) -> tuple[DaemonConfig, str]:
    """Daemon.toml first; Face ``[remote]`` on the throne if no daemon file."""
    path = Path(config_path or DEFAULT_CONFIG_PATH).expanduser()
    if not path.exists():
        face = Path.home() / ".config" / "xlii" / "face.toml"
        if face.exists():
            import tomllib
            data = tomllib.loads(face.read_text())
            remote = data.get("remote") or {}
            jid = str(remote.get("jid") or "").strip()
            env = str(remote.get("password_env") or "XMPP_THRONE_PASSWORD")
            password = os.environ.get(env, "")
            if not jid or not password:
                raise RuntimeError(
                    f"face.toml [remote] missing jid or env {env} is unset"
                )
            node = str(remote.get("node_name") or "throne").strip() or "throne"
            cfg = DaemonConfig(
                jid=jid,
                password_env=env,
                state_file=Path.home() / ".config" / "xlii" / "face-remote-omemo-state.json",
                verbs_dir=Path.home() / ".config" / "xlii" / "verbs",
                audit_log=Path.home() / ".local" / "share" / "xlii" / "daemon-audit.log",
                allowed_jids=list(remote.get("allowed_jids") or []),
                max_per_minute=10,
                lockout_threshold=5,
                lockout_duration_s=300,
                fallback_enabled=False,
                fallback_workspace="",
                node_name=node,
            )
            return cfg, password
        raise RuntimeError(f"no daemon.toml at {path} and no face.toml")
    cfg = DaemonConfig.load(path)
    password = os.environ.get(cfg.password_env, "")
    if not password:
        raise RuntimeError(
            f"XMPP password env {cfg.password_env} is not set (daemon.toml)"
        )
    return cfg, password


# Ops whose JSON ``node`` names the sender. Only honoured when the MUC
# occupant nick (the resource the service put on the stanza) says the same.
IDENTITY_OPS = frozenset({OP_OFFER, OP_RESULT, OP_CLAIM})


def _claimed_node(data: dict[str, Any], op: str) -> str:
    if op == OP_RESULT:
        raw = data.get("result") if isinstance(data.get("result"), dict) else data
        return str((raw or {}).get("node") or "").strip()
    return str((data or {}).get("node") or "").strip()


def _node_vouched(claimed: str, from_nick: str) -> bool:
    """Occupant nick present and equal to the claimed node. Empty → False."""
    nick = (from_nick or "").strip()
    node = (claimed or "").strip()
    return bool(nick and node and nick == node)


def _trusted_result_ticket(
    result: Any,
    *,
    ledger: Optional[RoomLedger],
    board: JobBoard,
) -> Optional[Ticket]:
    """Kind and identity come from the room ad (ledger), then the file board.

    A missing ad is not an invitation to synthesize an advisory ticket —
    that would send unknown market results into ``done/``.
    """
    if ledger is not None:
        ticket = ledger.ads.get(result.id)
        if ticket is not None:
            return ticket
    found = board.get(result.id)
    if found is not None:
        return found[1]
    return None


def _mirror(
    board: JobBoard,
    op: str,
    data: dict[str, Any],
    *,
    ledger: Optional[RoomLedger] = None,
    from_nick: str = "",
    local: bool = False,
) -> None:
    """Keep the local file board in sync so ``xlii job ls`` works.

    *local* is true only for results this runtime produced itself. A result
    read from the room is never local, whatever nick it arrived under —
    nicks are reusable, so ``from_nick == self.node`` proves nothing about
    who did the work.
    """
    board.ensure()
    try:
        if op == OP_AD:
            ticket = Ticket.from_dict(data.get("ticket") or data)
            if board.get(ticket.id) is None:
                board.post(ticket)
        elif op == OP_RESULT:
            from xlii.farm_market import is_foreign_market_result, quarantine_result

            result = JobResult.from_dict(data.get("result") or data)
            if from_nick and not _node_vouched(result.node, from_nick):
                return
            ticket = _trusted_result_ticket(result, ledger=ledger, board=board)
            if ticket is None:
                log.warning(
                    "farm board mirror: unknown result %s refused (no trusted ad)",
                    result.id,
                )
                return
            found = board.get(ticket.id)
            if found is not None and found[0] == "done":
                # First result settled it. The room's echo of our own result,
                # a replay after restart, or a late spoof never rewrites done/.
                return
            if is_foreign_market_result(ticket, local=local):
                quarantine_result(board, ticket, result)
                return
            board.complete(ticket, result)
        elif op == OP_CANCEL:
            job_id = str(data.get("job_id") or "").strip()
            if job_id:
                board.cancel(job_id)
    except Exception as e:
        log.warning("farm board mirror failed: %s: %s", type(e).__name__, e)


class FarmRuntime:
    """Ledger + pickup. Transport is ``send_body(str)`` (MUC groupchat)."""

    def __init__(
        self,
        gcfg: Any,
        *,
        node: str,
        board: JobBoard,
        send_body,
        pickup: bool,
    ) -> None:
        self.gcfg = gcfg
        self.node = node
        self.board = board
        self.send_body = send_body
        self.pickup = pickup
        self.ledger = RoomLedger()
        self._pending: set[str] = set()
        self._last_offer: str = ""

    def ingest(self, body: str, *, owner: bool = False, from_nick: str = "") -> Optional[str]:
        parsed = parse_farm_body(body)
        if parsed is None:
            return None
        op, data = parsed
        if op in STEER_OPS and not owner:
            log.warning(
                "farm ingest: ignored non-owner %s (by=%r)",
                op, (data or {}).get("by") or (data or {}).get("from"),
            )
            return None
        if op in IDENTITY_OPS and not _node_vouched(
            _claimed_node(data or {}, op), from_nick,
        ):
            log.warning(
                "farm ingest: ignored %s node %r from nick %r (occupancy mismatch)",
                op, _claimed_node(data or {}, op), from_nick,
            )
            return None
        applied = self.ledger.apply_body(body, owner=owner)
        if applied is None:
            return None
        _mirror(self.board, op, data, ledger=self.ledger, from_nick=from_nick)
        if op == OP_BENCH:
            node = str((data or {}).get("node") or "").strip()
            if node and node == self.node:
                self.pickup = False
                log.info("farm bench: %s pickup → observe", self.node)
                self.post_offer()
        return op

    def current_offer_body(self) -> str:
        return encode_offer(
            node=self.node,
            offers=sorted(job_offers(self.gcfg)),
            gig=job_gig(self.gcfg),
            busy=bool(self._pending),
            allowance=job_allowance(self.gcfg).to_dict(),
            specialty=job_specialty(self.gcfg),
            skills=job_skills(self.gcfg),
        )

    def post_offer(self, *, force: bool = False) -> None:
        """Post the offer beacon if it changed (or *force* on join)."""
        body = self.current_offer_body()
        if not force and body == self._last_offer:
            return
        self._last_offer = body
        self.ledger.apply_body(body)
        self.send_body(body)

    def eligible(self) -> list[Ticket]:
        if not self.pickup or self.ledger.is_benched(self.node):
            return []
        out: list[Ticket] = []
        for ticket in self.ledger.open_tickets():
            if skip_reason(ticket, self.gcfg):
                continue
            if ticket.id in self._pending:
                continue
            out.append(ticket)
        return out

    def _emit_own_result(self, result: JobResult) -> JobResult:
        """Stamp occupancy nick, publish to the room, complete locally."""
        result.node = self.node
        rbody = encode_result(result)
        self.ledger.apply_body(rbody)
        self.send_body(rbody)
        _mirror(
            self.board, OP_RESULT, {"result": result.to_dict()},
            ledger=self.ledger, from_nick=self.node, local=True,
        )
        return result

    async def consider(self, ticket: Ticket) -> Optional[Any]:
        if not self.pickup or self.ledger.is_benched(self.node):
            return None
        if skip_reason(ticket, self.gcfg):
            return None
        if self.ledger.is_cancelled(ticket.id):
            return None
        if self.ledger.winner(ticket.id) or ticket.id in self._pending:
            return None
        self._pending.add(ticket.id)
        self.post_offer()
        try:
            body = encode_claim(job_id=ticket.id, node=self.node)
            self.ledger.apply_body(body)
            self.send_body(body)
            await asyncio.sleep(CLAIM_GRACE_S)
            if self.ledger.is_cancelled(ticket.id):
                from xlii.farm import utc_now

                return self._emit_own_result(JobResult(
                    id=ticket.id, status="cancelled", node=self.node,
                    job=ticket.job, reason="cancelled", finished_at=utc_now(),
                ))
            if self.ledger.winner(ticket.id) != self.node:
                log.info("farm claim lost %s (winner %s)", ticket.id,
                         self.ledger.winner(ticket.id))
                return None
            loop = asyncio.get_running_loop()
            node = self.node
            result = await loop.run_in_executor(
                None,
                lambda: run_ticket(
                    ticket, self.gcfg,
                    cancelled=lambda: self.ledger.is_cancelled(ticket.id),
                    node=node,
                ),
            )
            return self._emit_own_result(result)
        finally:
            self._pending.discard(ticket.id)
            self.post_offer()


def _send_groupchat(xmpp: Any, room: str, body: str) -> None:
    xmpp.send_message(mto=room, mbody=body, mtype="groupchat")


# --------------------------------------------------------------------------- #
#  Stanza shapes
#
#  Everything below reads slixmpp stanzas through their ``.xml`` root (or a
#  bare ElementTree element / a ``{"from", "body"}`` dict in tests) so the
#  parse never depends on which stanza plugins the client registered, and
#  never mutates the stanza the way ``stanza['muc']`` does.
# --------------------------------------------------------------------------- #

NS_MUC_USER = "http://jabber.org/protocol/muc#user"
NS_ADDRESS = "http://jabber.org/protocol/address"  # XEP-0033
NS_OCCUPANT_ID = "urn:xmpp:occupant-id:0"  # XEP-0421


def _is_element(obj: Any) -> bool:
    return (
        callable(getattr(obj, "find", None))
        and hasattr(obj, "tag")
        and hasattr(obj, "attrib")
    )


def stanza_xml(stanza: Any) -> Any:
    """ElementTree root of a stanza: slixmpp ``.xml``, or the element itself."""
    if stanza is None or isinstance(stanza, dict):
        return None
    if _is_element(stanza):
        return stanza
    xml = getattr(stanza, "xml", None)
    return xml if _is_element(xml) else None


def _stanza_attr(stanza: Any, key: str) -> str:
    if stanza is None:
        return ""
    if isinstance(stanza, dict):
        return str(stanza.get(key) or "").strip()
    if _is_element(stanza):
        return str(stanza.get(key) or "").strip()
    try:
        value = stanza[key]
    except Exception:
        value = getattr(stanza, key, None)
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "none" else text


def stanza_from(stanza: Any) -> str:
    return _stanza_attr(stanza, "from")


def stanza_type(stanza: Any) -> str:
    return _stanza_attr(stanza, "type").lower()


def stanza_body(stanza: Any) -> str:
    if isinstance(stanza, dict):
        return str(stanza.get("body") or "")
    if _is_element(stanza):
        for child in stanza:
            tag = str(child.tag)
            if tag == "body" or tag.endswith("}body"):
                return str(child.text or "")
        return ""
    try:
        return str(stanza["body"] or "")
    except Exception:
        return str(getattr(stanza, "body", "") or "")


def _split_jid(full: str) -> tuple[str, str]:
    """``user@host/res`` → (lowercased bare, resource). Empty on garbage."""
    text = (full or "").strip()
    if not text or text.lower() == "none":
        return "", ""
    if "/" in text:
        bare, resource = text.split("/", 1)
    else:
        bare, resource = text, ""
    return bare.strip().lower(), resource.strip()


def _bare_jid(value: Any) -> str:
    if value is None or value == "":
        return ""
    bare = getattr(value, "bare", None)
    if callable(bare):
        try:
            bare = bare()
        except Exception:
            bare = None
    if bare not in (None, ""):
        return str(bare).strip().lower()
    return _split_jid(str(value))[0]


def same_room(stanza: Any, room: str) -> bool:
    """True when the stanza's bare ``from`` is *room*. Empty room never matches."""
    want = _bare_jid(room)
    if not want:
        return False
    return _split_jid(stanza_from(stanza))[0] == want


def muc_occupant_nick(stanza: Any, *, room: str = "") -> str:
    """MUC occupant nick from a groupchat stanza (``room@host/nick``).

    Farm joins with the node's name as the nick. That resource is the only
    occupancy identity we trust — JSON ``node`` is unsigned. With *room*
    given, a stanza whose bare ``from`` is not the room yields ``""``: a
    resource on some other origin is not an occupant of our room, however
    it is spelled.
    """
    bare, nick = _split_jid(stanza_from(stanza))
    if not bare:
        return ""
    if room and bare != _bare_jid(room):
        return ""
    return nick


def authentic_occ_node(stanza: Any, data: dict[str, Any], *, room: str = "") -> str:
    """Occupancy node if JSON ``node`` matches the room occupant nick, else ``""``."""
    nick = muc_occupant_nick(stanza, room=room)
    claimed = str((data or {}).get("node") or "").strip()
    if not nick or not claimed or nick != claimed:
        return ""
    return claimed


def _muc_plugin(xmpp: Any) -> Any:
    if not xmpp:
        return None
    try:
        return xmpp["xep_0045"]
    except Exception:
        return None


def _live_property(xmpp: Any, room: str, nick: str, prop: str) -> Any:
    """XEP-0045 occupancy map lookup for a present nick; ``None`` if unknown."""
    muc = _muc_plugin(xmpp)
    if muc is None or not room or not nick:
        return None
    try:
        return muc.get_jid_property(room, nick, prop)
    except Exception:
        return None


def muc_affiliation_is_owner(xmpp: Any, room: str, nick: str) -> bool:
    """True if XEP-0045 presence says *nick* in *room* has affiliation ``owner``.

    Server-vouched. JSON ``by`` is never consulted. Missing plugin / unknown
    or departed nick / any error → False (fail closed).
    """
    aff = _live_property(xmpp, (room or "").strip(), (nick or "").strip(), "affiliation")
    return str(aff or "").strip().lower() == "owner"


def muc_live_real_jid(xmpp: Any, room: str, nick: str) -> str:
    """Bare real JID of a *present* nick (non-anonymous rooms), else ``""``."""
    return _bare_jid(_live_property(xmpp, (room or "").strip(), (nick or "").strip(), "jid"))


@dataclass(frozen=True)
class HistoryIdentity:
    """Who sent a MUC history message, as far as the wire can say.

    ``occupant_id`` is XEP-0421: stamped by the service, which strips any
    client copy first, so it is the one forgery-proof carrier. ``real_jid``
    is the bare JID an in-band carrier claims — XEP-0033 ``ofrom`` (what
    XEP-0313 §5.1.2 specifies for MUC archives) first, then the muc#user
    ``<item jid>`` MAM-backed servers append in non-anonymous rooms. Those
    two are only as trustworthy as the service's sanitising; ``conflict``
    is set when carriers disagree, and a conflicting stanza never vouches.
    """

    occupant_id: str = ""
    real_jid: str = ""
    conflict: bool = False


def history_identity(stanza: Any) -> HistoryIdentity:
    xml = stanza_xml(stanza)
    if xml is None:
        return HistoryIdentity()
    oids: set[str] = set()
    for el in xml.findall(f"{{{NS_OCCUPANT_ID}}}occupant-id"):
        oid = str(el.get("id") or "").strip()
        if oid:
            oids.add(oid)
    ofrom: set[str] = set()
    for addr in xml.findall(f"{{{NS_ADDRESS}}}addresses/{{{NS_ADDRESS}}}address"):
        if str(addr.get("type") or "").strip().lower() != "ofrom":
            continue
        bare = _bare_jid(addr.get("jid"))
        if bare:
            ofrom.add(bare)
    item_jids: set[str] = set()
    for item in xml.findall(f"{{{NS_MUC_USER}}}x/{{{NS_MUC_USER}}}item"):
        bare = _bare_jid(item.get("jid"))
        if bare:
            item_jids.add(bare)
    claimed = ofrom | item_jids
    conflict = len(oids) > 1 or len(claimed) > 1
    return HistoryIdentity(
        occupant_id=next(iter(oids)) if len(oids) == 1 else "",
        real_jid=next(iter(claimed)) if len(claimed) == 1 else "",
        conflict=conflict,
    )


@dataclass(frozen=True)
class Occupant:
    """A present occupant as announced by the join presences."""

    nick: str
    jid: str  # bare; "" in semi-anonymous rooms
    affiliation: str
    occupant_id: str


OccupantIndex = dict[str, Occupant]  # keyed by XEP-0421 occupant-id


def index_occupants(presences: Any, *, room: str = "") -> OccupantIndex:
    """occupant-id → occupant, from the presences a join returned.

    Presences without an occupant-id (service without XEP-0421) are not
    indexed; the nick-keyed XEP-0045 occupancy map still covers them.
    """
    out: OccupantIndex = {}
    for pres in presences or []:
        nick = muc_occupant_nick(pres, room=room)
        if not nick:
            continue
        xml = stanza_xml(pres)
        if xml is None:
            continue
        oid_el = xml.find(f"{{{NS_OCCUPANT_ID}}}occupant-id")
        oid = str(oid_el.get("id") or "").strip() if oid_el is not None else ""
        if not oid:
            continue
        item = xml.find(f"{{{NS_MUC_USER}}}x/{{{NS_MUC_USER}}}item")
        jid = _bare_jid(item.get("jid")) if item is not None else ""
        aff = str(item.get("affiliation") or "").strip().lower() if item is not None else ""
        out[oid] = Occupant(nick=nick, jid=jid, affiliation=aff, occupant_id=oid)
    return out


def _affiliation_item_jid(item: Any) -> str:
    """Bare JID of one XEP-0045 affiliation-list item.

    slixmpp's ``get_affiliation_list`` yields ``item['jid']`` — a string
    (or JID) that may carry a resource and mixed case. Dicts and stanza
    items with a ``jid`` key are accepted too. No ``@`` → not a user.
    """
    if item is None or item == "":
        return ""
    if isinstance(item, str):
        return _bare_jid(item) if "@" in item else ""
    if isinstance(item, dict):
        return _affiliation_item_jid(item.get("jid"))
    if getattr(item, "bare", None) not in (None, ""):
        return _bare_jid(item)
    try:
        return _affiliation_item_jid(item["jid"])
    except Exception:
        return ""


OwnerAffiliations = Optional[frozenset[str]]


async def fetch_owner_affiliations(xmpp: Any, room: str) -> OwnerAffiliations:
    """XEP-0045 owner list as bare JIDs. ``None`` if the fetch failed.

    Fail closed: a missing plugin, missing method, IQ error or
    ``forbidden`` means history cannot vouch departed senders. An empty
    successful list is not a failure. Nicks are never taken from the
    list — they are reusable and slixmpp does not return them anyway.
    """
    room = (room or "").strip()
    muc = _muc_plugin(xmpp)
    if muc is None or not room:
        return None
    getter = getattr(muc, "get_affiliation_list", None)
    if not callable(getter):
        return None
    try:
        raw = getter(room, "owner")
        if hasattr(raw, "__await__"):
            raw = await raw
    except Exception as e:
        log.warning(
            "farm MUC: owner affiliation list fetch failed: %s: %s",
            type(e).__name__, e,
        )
        return None
    if raw is None:
        return None
    try:
        items = list(raw)
    except TypeError:
        return None
    jids: set[str] = set()
    for item in items:
        jid = _affiliation_item_jid(item)
        if jid:
            jids.add(jid)
    return frozenset(jids)


def history_sender_is_owner(
    xmpp: Any,
    room: str,
    nick: str,
    stanza: Any,
    owner_aff: OwnerAffiliations,
    *,
    occupants: Optional[OccupantIndex] = None,
) -> bool:
    """Vouch a MUC history sender as owner. JSON ``by`` is never consulted.

    In order:

    1. The stanza's XEP-0421 occupant-id names a *present* occupant → that
       occupant's presence is the authority (owner affiliation, or real
       JID on the owner list). Nick reuse cannot fake this.
    2. Departed sender with an in-band real JID (``ofrom`` / muc#user
       item) → owner iff that bare JID is on the fetched owner list.
    3. No carrier → live presence for the nick (affiliation owner, or real
       JID on the list) — unless the service stamps occupant-ids and this
       one is not present, in which case the nick holder is someone else.

    ``owner_aff is None`` (fetch failed / forbidden) → only presence
    vouches. Conflicting carriers never vouch.
    """
    room = (room or "").strip()
    nick = (nick or "").strip()
    if not room or not nick:
        return False
    ident = history_identity(stanza)
    if ident.conflict:
        log.warning("farm MUC: history from %r carries conflicting identities", nick)
        return False
    present = (occupants or {}).get(ident.occupant_id) if ident.occupant_id else None
    if present is not None:
        if present.affiliation == "owner":
            return True
        return bool(owner_aff and present.jid and present.jid in owner_aff)
    if ident.real_jid:
        return bool(owner_aff and ident.real_jid in owner_aff)
    if ident.occupant_id and occupants:
        return False
    if muc_affiliation_is_owner(xmpp, room, nick):
        return True
    if owner_aff:
        live = muc_live_real_jid(xmpp, room, nick)
        return bool(live and live in owner_aff)
    return False


def replay_farm_history(
    rt: FarmRuntime,
    history: Any,
    *,
    xmpp: Any,
    room: str,
    owner_aff: OwnerAffiliations,
    occupants: Optional[OccupantIndex] = None,
) -> None:
    """Apply join-time MUC history. Steer ops need an owner vouch.

    Stanzas not from *room* (or from the room itself, no nick) are skipped.
    """
    for msg in history or []:
        try:
            nick = muc_occupant_nick(msg, room=room)
            if not nick:
                continue
            owner = history_sender_is_owner(
                xmpp, room, nick, msg, owner_aff, occupants=occupants,
            )
            rt.ingest(stanza_body(msg), owner=owner, from_nick=nick)
        except Exception:
            continue


async def evict_occupant(
    xmpp: Any, room: str, nick: str, *, reason: str = "evicted",
) -> None:
    """Revoke membership and kick *nick* via XEP-0045. The enforced rung.

    ``set_affiliation(..., 'none')`` is the membership revoke; ``set_role(...,
    'none')`` is the kick. A node that already left still loses membership.
    """
    nick = (nick or "").strip()
    room = (room or "").strip()
    if not xmpp or not room or not nick:
        raise RuntimeError("evict requires xmpp, room, and nick")
    try:
        muc = xmpp["xep_0045"]
    except Exception as e:
        raise RuntimeError(f"evict: no XEP-0045 plugin ({e})") from e
    await muc.set_affiliation(room, "none", nick=nick, reason=reason)
    try:
        await muc.set_role(room, nick, "none", reason=reason)
    except Exception as e:
        log.info("farm evict: kick after revoke skipped for %s: %s", nick, e)


def ingest_occ_stanza(stanza: Any, *, self_node: str, room: str = "") -> bool:
    """Apply occupancy from a MUC stanza. True if the body was an occ envelope.

    With *room* given, an envelope from any other origin is consumed and
    ignored — its resource is not one of our occupants.
    """
    from xlii.glass import apply_remote_claim, apply_remote_release
    from xlii.occ_bus import parse_occ

    occ = parse_occ(stanza_body(stanza))
    if occ is None:
        return False
    op, data = occ
    if room and not same_room(stanza, room):
        log.warning(
            "occ ingest: ignored %s from %r (not %s)", op, stanza_from(stanza), room,
        )
        return True
    n = authentic_occ_node(stanza, data, room=room)
    if not n:
        log.warning(
            "occ ingest: ignored spoofed node %r from nick %r",
            (data or {}).get("node"),
            muc_occupant_nick(stanza, room=room),
        )
        return True
    if op == "claim":
        apply_remote_claim(n, self_node=self_node)
    elif op == "release":
        apply_remote_release(n, self_node=self_node)
    return True


def make_room_handler(rt: FarmRuntime, *, xmpp: Any, room: str):
    """Live groupchat handler bound to one room.

    The occupant nick is only ever the resource of a stanza whose bare
    ``from`` is *room*. Anything else — another room, a bare user JID with
    a resource spelled like the throne or like ourselves — is dropped
    before occupancy or the ledger see it.
    """

    async def _on_muc(stanza: Any) -> None:
        if stanza_type(stanza) != "groupchat":
            return
        if not same_room(stanza, room):
            log.warning(
                "farm MUC: ignored groupchat from %r (not %s)", stanza_from(stanza), room,
            )
            return
        nick = muc_occupant_nick(stanza, room=room)
        if not nick:
            return
        body = stanza_body(stanza)
        try:
            if ingest_occ_stanza(stanza, self_node=rt.node, room=room):
                return
        except Exception as e:
            log.warning("occ ingest: %s: %s", type(e).__name__, e)
        try:
            owner = muc_affiliation_is_owner(xmpp, room, nick)
            op = rt.ingest(body, owner=owner, from_nick=nick)
        except Exception as e:
            log.warning("farm ingest: %s: %s", type(e).__name__, e)
            return
        if op == OP_AD and rt.pickup:
            for t in rt.eligible():
                asyncio.create_task(rt.consider(t))

    return _on_muc


async def attach_farm(
    xmpp: Any, gcfg: Any, *, auto_consider: bool = True,
) -> Optional[FarmRuntime]:
    """Join the jobs room on an already-connected daemon client."""
    room = job_muc_room(gcfg)
    if not room:
        return None
    node = job_node_name(gcfg, getattr(getattr(xmpp, "cfg", None), "node_name", ""))
    pickup = bool(job_offers(gcfg))
    try:
        xmpp["xep_0045"]
    except Exception:
        xmpp.register_plugin("xep_0045")
    muc = xmpp["xep_0045"]
    from slixmpp.jid import JID

    pres, _subj, joined, history = await muc.join_muc_wait(
        JID(room), node, maxstanzas=80, timeout=30,
    )
    board = JobBoard(job_board_root(gcfg))
    board.ensure()
    rt = FarmRuntime(
        gcfg, node=node, board=board,
        send_body=lambda b: _send_groupchat(xmpp, room, b),
        pickup=pickup,
    )
    occupants = index_occupants([pres, *(joined or [])], room=room)
    owner_aff = await fetch_owner_affiliations(xmpp, room)
    if owner_aff is None:
        log.warning(
            "farm MUC: owner affiliation list unavailable; history steer ops "
            "fail closed for departed senders",
        )
    replay_farm_history(
        rt, history, xmpp=xmpp, room=room, owner_aff=owner_aff, occupants=occupants,
    )
    log.info(
        "farm MUC joined %s as %s (%s)",
        room, node, "pickup" if pickup else "observe",
    )
    xmpp.add_event_handler("groupchat_message", make_room_handler(rt, xmpp=xmpp, room=room))
    xmpp._farm_runtime = rt
    try:
        from xlii.occ_bus import bind_sender

        bind_sender(rt.send_body)
    except Exception:
        # Without the occ_bus binding occupancy still persists to disk -- only the fan-out is missing.
        pass
    rt.post_offer(force=True)
    try:
        from xlii.farm_schedule import fire_scheduled_post

        posted = fire_scheduled_post(gcfg)
        if posted is not None:
            rt.ingest(encode_ad(posted), owner=True)
            rt.send_body(encode_ad(posted))
    except Exception as e:
        log.warning("farm scheduled post: %s: %s", type(e).__name__, e)
    if pickup and auto_consider:
        for t in rt.eligible():
            asyncio.create_task(rt.consider(t))
    return rt


def publish_farm_body(gcfg: Any, body: str, *, daemon_toml: Optional[Path] = None) -> int:
    """One-shot: connect, join, post a farm envelope, disconnect."""
    try:
        dcfg, password = _creds(daemon_toml)
    except Exception as e:
        print(f"job: MUC skipped ({e})", file=sys.stderr)
        return 1
    room = job_muc_room(gcfg)
    if not room:
        print("job: jobs.muc is empty", file=sys.stderr)
        return 1

    async def _go() -> None:
        from slixmpp import ClientXMPP
        from slixmpp.jid import JID

        node = job_node_name(gcfg, dcfg.node_name)
        xmpp = ClientXMPP(dcfg.jid, password)
        xmpp.register_plugin("xep_0030")
        xmpp.register_plugin("xep_0045")
        done = asyncio.Event()

        async def _start(_e=None) -> None:
            try:
                xmpp.send_presence()
                muc = xmpp["xep_0045"]
                await muc.join_muc_wait(JID(room), node, maxstanzas=0, timeout=30)
                xmpp.send_message(mto=room, mbody=body, mtype="groupchat")
                await asyncio.sleep(0.4)
            finally:
                xmpp.disconnect()
                done.set()

        xmpp.add_event_handler("session_start", _start)
        xmpp.connect()
        await done.wait()

    asyncio.run(_go())
    return 0


def publish_ad(gcfg: Any, ticket: Ticket, *, daemon_toml: Optional[Path] = None) -> int:
    """One-shot: connect, join, post an ad, disconnect."""
    return publish_farm_body(gcfg, encode_ad(ticket), daemon_toml=daemon_toml)


def publish_cancel(
    gcfg: Any, job_id: str, *, by: str = "", daemon_toml: Optional[Path] = None,
) -> int:
    who = by or job_node_name(gcfg)
    return publish_farm_body(
        gcfg, encode_cancel(job_id=job_id, by=who), daemon_toml=daemon_toml,
    )


def publish_bench(
    gcfg: Any, node: str, *, by: str = "", until: str = "",
    daemon_toml: Optional[Path] = None,
) -> int:
    who = by or job_node_name(gcfg)
    return publish_farm_body(
        gcfg, encode_bench(node=node, by=who, until=until), daemon_toml=daemon_toml,
    )


def evict_node(
    gcfg: Any, nick: str, *, reason: str = "evicted",
    daemon_toml: Optional[Path] = None,
) -> int:
    """Throne verb: XEP-0045 affiliation revoke + kick. Not a room JSON op."""
    try:
        dcfg, password = _creds(daemon_toml)
    except Exception as e:
        print(f"job evict: {e}", file=sys.stderr)
        return 1
    room = job_muc_room(gcfg)
    if not room:
        print("job evict: jobs.muc is empty — evict is a MUC admin act", file=sys.stderr)
        return 1
    nick = (nick or "").strip()
    if not nick:
        print("job evict: node/nick is required", file=sys.stderr)
        return 1

    async def _go() -> int:
        from slixmpp import ClientXMPP
        from slixmpp.jid import JID

        node = job_node_name(gcfg, dcfg.node_name)
        xmpp = ClientXMPP(dcfg.jid, password)
        xmpp.register_plugin("xep_0030")
        xmpp.register_plugin("xep_0045")
        done = asyncio.Event()
        rc = 0

        async def _start(_e=None) -> None:
            nonlocal rc
            try:
                xmpp.send_presence()
                muc = xmpp["xep_0045"]
                await muc.join_muc_wait(JID(room), node, maxstanzas=0, timeout=30)
                if not muc_affiliation_is_owner(xmpp, room, node):
                    print(
                        "job evict: this occupant is not a MUC owner — "
                        "affiliation revoke refused",
                        file=sys.stderr,
                    )
                    rc = 1
                    return
                await evict_occupant(xmpp, room, nick, reason=reason)
            except Exception as e:
                print(f"job evict: {type(e).__name__}: {e}", file=sys.stderr)
                rc = 1
            finally:
                xmpp.disconnect()
                done.set()

        xmpp.add_event_handler("session_start", _start)
        xmpp.connect()
        await done.wait()
        return rc

    return asyncio.run(_go())


def run_watch(gcfg: Any, *, once: bool = False, daemon_toml: Optional[Path] = None) -> int:
    """Long-lived MUC watch (node). ``--once``: consider history, one pickup or idle."""
    dcfg, password = _creds(daemon_toml)
    room = job_muc_room(gcfg)
    if not room:
        raise RuntimeError("jobs.muc is empty")

    async def _go() -> int:
        from slixmpp import ClientXMPP

        node = job_node_name(gcfg, dcfg.node_name)
        xmpp = ClientXMPP(dcfg.jid, password)
        xmpp.cfg = dcfg  # job_node_name fallback shape
        xmpp.register_plugin("xep_0030")
        xmpp.register_plugin("xep_0045")
        xmpp.auto_reconnect = not once
        produced = asyncio.Event()
        exit_code = 0

        async def _start(_e=None) -> None:
            xmpp.send_presence(pstatus=node)
            rt = await attach_farm(xmpp, gcfg, auto_consider=not once)
            if rt is None:
                return
            orig_send = rt.send_body

            def _send(body: str) -> None:
                orig_send(body)
                parsed = parse_farm_body(body)
                if parsed and parsed[0] == OP_RESULT:
                    produced.set()

            rt.send_body = _send
            if once:
                tasks = [asyncio.create_task(rt.consider(t)) for t in rt.eligible()]
                if not tasks:
                    xmpp.disconnect()
                    return
                results = await asyncio.gather(*tasks, return_exceptions=True)
                nonlocal exit_code
                if not any(getattr(r, "status", None) == "done" for r in results if r and not isinstance(r, Exception)):
                    exit_code = 1
                xmpp.disconnect()

        xmpp.add_event_handler("session_start", _start)
        xmpp.connect()
        if once:
            await xmpp.disconnected
            return exit_code
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            xmpp.disconnect()
            return 0
        return 0

    return asyncio.run(_go())
