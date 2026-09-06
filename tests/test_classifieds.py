"""Classifieds Part I — house board authority, cancel rungs, later beacon/pane.

Phase 1: owner-affiliation gate, cancel / bench / evict. No live XMPP.
"""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from argparse import Namespace
from types import SimpleNamespace

import pytest

from xlii.farm import (
    KIND_MARKET,
    WORKPLACE_LOCAL,
    Allowance,
    JobError,
    JobResult,
    Ticket,
    Workplace,
    job_node_name,
    skip_reason,
    utc_now,
)
from xlii.farm_muc import (
    OP_AD,
    OP_BENCH,
    OP_CANCEL,
    OP_OFFER,
    OP_RESULT,
    RoomLedger,
    encode_ad,
    encode_bench,
    encode_cancel,
    encode_claim,
    encode_offer,
    encode_result,
    parse_farm_body,
)
from xlii.farm_xmpp import (
    FarmRuntime,
    evict_occupant,
    fetch_owner_affiliations,
    history_identity,
    history_sender_is_owner,
    index_occupants,
    ingest_occ_stanza,
    make_room_handler,
    muc_affiliation_is_owner,
    muc_occupant_nick,
    replay_farm_history,
)
from xlii.job_board import JobBoard
from xlii.job_run import run_ticket


def _cfg(tmp_path, **jobs):
    block = {
        "offers": ["explore"],
        "gig": "kimi",
        "node": "kimi-laptop",
        "board": str(tmp_path / "board"),
        **jobs,
    }
    return SimpleNamespace(jobs=block, max_worker_iterations=4)


def _ticket(**kw) -> Ticket:
    return Ticket.make(job="explore", task="look at this", **kw)


# --------------------------------------------------------------------------- #
#  Protocol
# --------------------------------------------------------------------------- #


def test_cancel_and_bench_roundtrip_parse():
    op, data = parse_farm_body(encode_cancel(job_id="abc", by="throne"))
    assert op == OP_CANCEL and data["job_id"] == "abc" and data["by"] == "throne"
    op, data = parse_farm_body(encode_bench(node="kimi-laptop", by="throne", until="soon"))
    assert op == OP_BENCH and data["node"] == "kimi-laptop" and data["until"] == "soon"
    op, data = parse_farm_body(encode_bench(node="kimi-laptop", by="throne"))
    assert "until" not in data


def test_non_owner_cancel_bench_post_ignored():
    t = _ticket()
    led = RoomLedger()
    assert led.apply_body(encode_ad(t), owner=False) is None
    assert t.id not in led.ads
    assert led.apply_body(encode_ad(t), owner=True) == OP_AD
    assert t.id in led.ads

    assert led.apply_body(encode_cancel(job_id=t.id, by="compromised"), owner=False) is None
    assert not led.is_cancelled(t.id)
    assert t.id in [x.id for x in led.open_tickets()]

    assert led.apply_body(encode_cancel(job_id=t.id, by="throne"), owner=True) == OP_CANCEL
    assert led.is_cancelled(t.id)
    assert led.open_tickets() == []

    led2 = RoomLedger()
    assert led2.apply_body(encode_bench(node="kimi-laptop", by="spoof"), owner=False) is None
    assert not led2.is_benched("kimi-laptop")
    assert led2.apply_body(encode_bench(node="kimi-laptop", by="throne"), owner=True) == OP_BENCH
    assert led2.is_benched("kimi-laptop")
    assert led2.benched["kimi-laptop"] == ""


def test_owner_cancel_marks_ticket_not_open():
    t = _ticket()
    led = RoomLedger()
    led.apply_body(encode_ad(t), owner=True)
    led.apply_body(encode_claim(job_id=t.id, node="kimi-laptop"))
    # claimed tickets are already not open; cancel still records so a later
    # expiry cannot resurrect them.
    led.apply_body(encode_cancel(job_id=t.id, by="throne"), owner=True)
    assert led.is_cancelled(t.id)
    assert led.open_tickets() == []


def test_spoofed_by_field_is_informational():
    t = _ticket()
    led = RoomLedger()
    led.apply_body(encode_ad(t), owner=True)
    # A non-owner can write by="throne"; affiliation is what counts.
    assert led.apply_body(encode_cancel(job_id=t.id, by="throne"), owner=False) is None
    assert not led.is_cancelled(t.id)


# --------------------------------------------------------------------------- #
#  Runtime honor
# --------------------------------------------------------------------------- #


def test_benched_node_does_not_claim(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.farm_xmpp.CLAIM_GRACE_S", 0)
    sent: list[str] = []
    cfg = _cfg(tmp_path)
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="kimi-laptop", board=board, send_body=sent.append, pickup=True,
    )
    t = _ticket()
    rt.ingest(encode_ad(t), owner=True)
    assert rt.eligible()
    rt.ingest(encode_bench(node="kimi-laptop", by="throne"), owner=True)
    assert rt.pickup is False
    assert rt.ledger.is_benched("kimi-laptop")
    assert rt.eligible() == []
    result = asyncio.run(rt.consider(t))
    assert result is None
    assert not any('"op": "claim"' in s or '"op":"claim"' in s for s in sent)


def test_bench_other_node_does_not_flip_self(tmp_path):
    cfg = _cfg(tmp_path)
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="kimi-laptop", board=board, send_body=lambda b: None, pickup=True,
    )
    rt.ingest(encode_bench(node="other-box", by="throne"), owner=True)
    assert rt.pickup is True
    assert rt.ledger.is_benched("other-box")
    assert not rt.ledger.is_benched("kimi-laptop")


def test_non_owner_ingest_does_not_bench(tmp_path):
    cfg = _cfg(tmp_path)
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="kimi-laptop", board=board, send_body=lambda b: None, pickup=True,
    )
    assert rt.ingest(encode_bench(node="kimi-laptop", by="throne"), owner=False) is None
    assert rt.pickup is True
    assert not rt.ledger.is_benched("kimi-laptop")


# --------------------------------------------------------------------------- #
#  Cancel aborts within one iteration
# --------------------------------------------------------------------------- #


class _LoopWorker:
    """Worker that runs *max_iterations* steps and honours should_stop."""

    def __init__(self, on_iter):
        self.on_iter = on_iter
        self.iters = 0

    def run(self, task, context=None, max_iterations=None, should_stop=None, **kw):
        n = max_iterations or 8
        for _ in range(n):
            if should_stop is not None and should_stop():
                from xlii.job_run import CANCELLED_SENTINEL
                return CANCELLED_SENTINEL, SimpleNamespace(cost_usd=None, iterations=self.iters)
            self.iters += 1
            self.on_iter(self.iters)
        return "done", SimpleNamespace(cost_usd=0.0, iterations=self.iters)


def test_cancelled_ticket_aborts_within_one_iteration(tmp_path):
    cfg = _cfg(tmp_path)
    t = _ticket()
    flag = {"cancel": False}

    def on_iter(n):
        # After the first completed iteration, the board says cancelled.
        if n >= 1:
            flag["cancel"] = True

    worker = _LoopWorker(on_iter)
    result = run_ticket(
        t, cfg,
        worker_factory=lambda *a: worker,
        cancelled=lambda: flag["cancel"],
    )
    assert result.status == "cancelled"
    assert worker.iters == 1


def test_already_cancelled_does_not_start_worker(tmp_path):
    cfg = _cfg(tmp_path)
    t = _ticket()
    started = {"n": 0}

    class Boom:
        def run(self, *a, **k):
            started["n"] += 1
            raise AssertionError("should not run")

    result = run_ticket(
        t, cfg, worker_factory=lambda *a: Boom(), cancelled=lambda: True,
    )
    assert result.status == "cancelled"
    assert started["n"] == 0


def test_board_cancel_removes_from_open(tmp_path):
    board = JobBoard(tmp_path / "jobs")
    t = _ticket()
    board.post(t)
    assert board.cancel(t.id)
    assert board.list_open() == []
    assert board.is_cancelled(t.id)
    lane, ticket, _ = board.get(t.id)
    assert lane == "cancelled" and ticket.id == t.id


# --------------------------------------------------------------------------- #
#  Affiliation helper + evict (mocked XEP-0045)
# --------------------------------------------------------------------------- #


class _FakeMuc:
    """XEP-0045 plugin stand-in: the occupancy map slixmpp fills from presence."""

    def __init__(self, affiliations=None, occupant_jids=None):
        self.affiliations = dict(affiliations or {})
        self.occupant_jids = dict(occupant_jids or {})
        self.affiliation_calls = []
        self.role_calls = []

    def get_jid_property(self, room, nick, prop):
        if prop == "affiliation":
            return self.affiliations.get((room, nick))
        if prop == "jid":
            return self.occupant_jids.get((room, nick))
        return None

    async def set_affiliation(self, room, affiliation, *, nick=None, jid=None, reason=""):
        self.affiliation_calls.append({
            "room": room, "affiliation": affiliation, "nick": nick,
            "jid": jid, "reason": reason,
        })

    async def set_role(self, room, nick, role, *, reason=""):
        self.role_calls.append({
            "room": room, "nick": nick, "role": role, "reason": reason,
        })


class _AffListMuc(_FakeMuc):
    """Adds ``get_affiliation_list`` shaped like slixmpp 1.17: a list of the
    admin-query ``item['jid']`` values (bare strings, resource and case as the
    service sent them) — never nicks."""

    def __init__(self, affiliations=None, owner_list=None, occupant_jids=None):
        super().__init__(affiliations, occupant_jids)
        self.owner_list = list(owner_list or [])
        self.fail_list = False

    async def get_affiliation_list(self, room, affiliation):
        if self.fail_list:
            from xlii.farm import JobError

            raise JobError("forbidden")
        if affiliation != "owner":
            return []
        return list(self.owner_list)


class _FakeXmpp:
    def __init__(self, muc):
        self._muc = muc
        self.handlers = {}
        self.sent = []

    def __getitem__(self, key):
        if key == "xep_0045":
            return self._muc
        raise KeyError(key)

    def add_event_handler(self, name, handler):
        self.handlers[name] = handler

    def send_message(self, mto, mbody, mtype):
        self.sent.append((mto, mbody, mtype))


ROOM = "jobs@conf.house"
NS_MUC_USER = "http://jabber.org/protocol/muc#user"
NS_ADDRESS = "http://jabber.org/protocol/address"
NS_OCCUPANT_ID = "urn:xmpp:occupant-id:0"
NS_DELAY = "urn:xmpp:delay"


def _has_slixmpp() -> bool:
    try:
        import slixmpp  # noqa: F401
    except ImportError:
        return False
    return True


def _as_stanza(wire: str):
    """Documented wire XML → a real slixmpp stanza when the extra is installed
    (the shape the daemon sees), else the ElementTree root of the same XML."""
    root = ET.fromstring(wire)
    if not _has_slixmpp():
        return root
    from slixmpp import Message, Presence
    from slixmpp.plugins.xep_0045 import stanza as s45
    from slixmpp.xmlstream import register_stanza_plugin

    register_stanza_plugin(Message, s45.MUCMessage)
    register_stanza_plugin(Presence, s45.MUCPresence)
    register_stanza_plugin(s45.MUCMessage, s45.MUCUserItem)
    register_stanza_plugin(s45.MUCPresence, s45.MUCUserItem)
    if root.tag.endswith("}presence"):
        return Presence(xml=root)
    return Message(xml=root)


def _wire_message(
    *, frm, body, mtype="groupchat", occupant_id="", item_jid="", item_aff="",
    ofrom="", delayed=True, extra="",
) -> str:
    """A MUC groupchat message as Prosody 13 / any XEP-0045 service puts it on
    the wire. ``delayed`` adds the XEP-0203 history stamp; ``occupant_id`` the
    XEP-0421 stamp; ``item_jid`` the muc#user item MAM-backed non-anonymous
    rooms append; ``ofrom`` the XEP-0033 address XEP-0313 §5.1.2 specifies."""
    parts = [
        f"<message xmlns='jabber:client' from='{frm}' type='{mtype}' id='m1'>",
        f"<body>{body.replace('&', '&amp;').replace('<', '&lt;')}</body>",
    ]
    if occupant_id:
        parts.append(f"<occupant-id xmlns='{NS_OCCUPANT_ID}' id='{occupant_id}'/>")
    if item_jid or item_aff:
        aff = f" affiliation='{item_aff}'" if item_aff else ""
        jid = f" jid='{item_jid}'" if item_jid else ""
        parts.append(f"<x xmlns='{NS_MUC_USER}'><item{aff} role='participant'{jid}/></x>")
    if ofrom:
        parts.append(
            f"<addresses xmlns='{NS_ADDRESS}'><address type='ofrom' jid='{ofrom}'/></addresses>"
        )
    if delayed:
        parts.append(f"<delay xmlns='{NS_DELAY}' from='{ROOM}' stamp='2026-09-05T12:00:00Z'/>")
    parts.append(extra)
    parts.append("</message>")
    return "".join(parts)


def _wire_presence(nick, *, jid="", aff="member", occupant_id="", room=ROOM) -> str:
    jid_attr = f" jid='{jid}'" if jid else ""
    oid = f"<occupant-id xmlns='{NS_OCCUPANT_ID}' id='{occupant_id}'/>" if occupant_id else ""
    return (
        f"<presence xmlns='jabber:client' from='{room}/{nick}'>"
        f"<x xmlns='{NS_MUC_USER}'><item affiliation='{aff}' role='participant'{jid_attr}/></x>"
        f"{oid}</presence>"
    )


def _hist(nick, body, **kw):
    """A history stanza from ``ROOM/nick``."""
    return _as_stanza(_wire_message(frm=f"{ROOM}/{nick}", body=body, **kw))


def _live(frm, body, **kw):
    """A live groupchat stanza (no delay) from an arbitrary origin."""
    kw.setdefault("delayed", False)
    return _as_stanza(_wire_message(frm=frm, body=body, **kw))


def _runtime(tmp_path, node="kimi-laptop", board_name="board", pickup=True):
    cfg = _cfg(tmp_path, muc=ROOM, node=node)
    board = JobBoard(tmp_path / board_name)
    rt = FarmRuntime(cfg, node=node, board=board, send_body=lambda b: None, pickup=pickup)
    return rt, board


def test_muc_affiliation_is_owner_uses_xep_0045():
    muc = _FakeMuc({
        (ROOM, "throne"): "owner",
        (ROOM, "kimi-laptop"): "member",
    })
    xmpp = _FakeXmpp(muc)
    assert muc_affiliation_is_owner(xmpp, ROOM, "throne") is True
    assert muc_affiliation_is_owner(xmpp, ROOM, "kimi-laptop") is False
    assert muc_affiliation_is_owner(xmpp, ROOM, "ghost") is False
    assert muc_affiliation_is_owner(None, ROOM, "throne") is False


# --------------------------------------------------------------------------- #
#  B2 — history owner vouch on real XEP-0045 shapes
# --------------------------------------------------------------------------- #


def test_fetch_owner_affiliations_parses_slixmpp_jid_strings():
    """slixmpp's get_affiliation_list yields item['jid'] strings — resource and
    case included, nicks absent. Only bare JIDs come out; nick-shaped junk is
    dropped, not promoted to an identity."""
    muc = _AffListMuc(owner_list=[
        "Throne@House/desk",
        "co-owner@house",
        "throne",  # not a JID: never a vouch
        "",
    ])
    owners = asyncio.run(fetch_owner_affiliations(_FakeXmpp(muc), ROOM))
    assert owners == frozenset({"throne@house", "co-owner@house"})


def test_fetch_owner_affiliations_accepts_jid_objects_and_items():
    slixmpp = pytest.importorskip("slixmpp")
    from slixmpp.plugins.xep_0045 import stanza as s45
    from slixmpp.xmlstream import register_stanza_plugin

    register_stanza_plugin(s45.MUCAdminQuery, s45.MUCAdminItem, iterable=True)
    # the IQ result body XEP-0045 §10.5 sends back, as slixmpp parses it
    query = s45.MUCAdminQuery(xml=ET.fromstring(
        "<query xmlns='http://jabber.org/protocol/muc#admin'>"
        "<item affiliation='owner' jid='Second@House/res'/>"
        "<item affiliation='owner' jid='third@house'/>"
        "</query>"
    ))
    wire_items = [item["jid"] for item in query]  # what get_affiliation_list returns
    assert wire_items == ["Second@House/res", "third@house"]
    muc = _AffListMuc(owner_list=[slixmpp.JID("Throne@House/desk"), *wire_items, *query])
    owners = asyncio.run(fetch_owner_affiliations(_FakeXmpp(muc), ROOM))
    assert owners == frozenset({"throne@house", "second@house", "third@house"})


def test_fetch_owner_affiliations_fail_closed_on_error():
    muc = _AffListMuc()
    muc.fail_list = True
    assert asyncio.run(fetch_owner_affiliations(_FakeXmpp(muc), ROOM)) is None
    assert asyncio.run(fetch_owner_affiliations(None, ROOM)) is None
    assert asyncio.run(fetch_owner_affiliations(_FakeXmpp(_FakeMuc()), ROOM)) is None
    empty = _AffListMuc(owner_list=[])
    assert asyncio.run(fetch_owner_affiliations(_FakeXmpp(empty), ROOM)) == frozenset()


def test_history_identity_reads_wire_carriers():
    msg = _hist(
        "throne", "hi", occupant_id="OID-T", item_jid="Throne@House/desk",
        item_aff="owner",
    )
    ident = history_identity(msg)
    assert ident.occupant_id == "OID-T"
    assert ident.real_jid == "throne@house"
    assert ident.conflict is False

    only_ofrom = _hist("throne", "hi", ofrom="throne@house/mam")
    assert history_identity(only_ofrom).real_jid == "throne@house"

    agreeing = _hist("throne", "hi", ofrom="throne@house", item_jid="throne@house")
    assert history_identity(agreeing).real_jid == "throne@house"

    bare = _hist("throne", "hi")
    assert history_identity(bare) == history_identity(None)
    assert history_identity(bare).real_jid == ""


def test_history_identity_conflicting_carriers_flagged():
    forged = _hist(
        "throne", "hi", ofrom="throne@house", item_jid="mallory@house",
    )
    assert history_identity(forged).conflict is True
    two_items = _hist(
        "throne", "hi", item_jid="throne@house",
        extra=f"<x xmlns='{NS_MUC_USER}'><item jid='mallory@house'/></x>",
    )
    assert history_identity(two_items).conflict is True
    two_oids = _hist(
        "throne", "hi", occupant_id="A",
        extra=f"<occupant-id xmlns='{NS_OCCUPANT_ID}' id='B'/>",
    )
    assert history_identity(two_oids).conflict is True


def test_history_departed_owner_vouches_ad_cancel_bench_by_real_jid(tmp_path):
    """The one-shot publisher joined, posted, and left. Presence has no
    ``throne``. The MAM-backed history carries the service's muc#user item
    with the owner's bare JID; that JID is on the fetched owner list."""
    t = _ticket()
    t2 = _ticket()
    owner_kw = dict(occupant_id="OID-THRONE", item_jid="throne@house/desk", item_aff="owner")
    history = [
        _hist("throne", encode_ad(t), **owner_kw),
        _hist("throne", encode_ad(t2), **owner_kw),
        _hist("throne", encode_cancel(job_id=t2.id, by="throne"), **owner_kw),
        _hist("throne", encode_bench(node="kimi-laptop", by="throne"), **owner_kw),
    ]
    muc = _AffListMuc()  # nobody but us present → live affiliation misses
    xmpp = _FakeXmpp(muc)
    assert muc_affiliation_is_owner(xmpp, ROOM, "throne") is False
    owners = frozenset({"throne@house"})
    occupants = index_occupants(
        [_as_stanza(_wire_presence("kimi-laptop", jid="kimi@house/d", occupant_id="OID-ME"))],
        room=ROOM,
    )
    assert history_sender_is_owner(
        xmpp, ROOM, "throne", history[0], owners, occupants=occupants,
    ) is True

    rt, board = _runtime(tmp_path)
    replay_farm_history(
        rt, history, xmpp=xmpp, room=ROOM, owner_aff=owners, occupants=occupants,
    )
    assert t.id in rt.ledger.ads and board.get(t.id) is not None
    assert rt.ledger.is_cancelled(t2.id)
    assert rt.ledger.is_benched("kimi-laptop")
    assert rt.pickup is False


def test_history_departed_owner_vouches_by_xep_0033_ofrom(tmp_path):
    t = _ticket()
    history = [_hist("throne", encode_ad(t), ofrom="throne@house")]
    rt, board = _runtime(tmp_path)
    replay_farm_history(
        rt, history, xmpp=_FakeXmpp(_AffListMuc()), room=ROOM,
        owner_aff=frozenset({"throne@house"}),
    )
    assert t.id in rt.ledger.ads


def test_history_departed_owner_fails_closed_without_owner_list(tmp_path):
    t = _ticket()
    history = [
        _hist("throne", encode_ad(t), item_jid="throne@house", item_aff="owner"),
        _hist("throne", encode_bench(node="kimi-laptop", by="throne"), ofrom="throne@house"),
    ]
    rt, _board = _runtime(tmp_path)
    replay_farm_history(
        rt, history, xmpp=_FakeXmpp(_AffListMuc()), room=ROOM, owner_aff=None,
    )
    assert t.id not in rt.ledger.ads
    assert not rt.ledger.is_benched("kimi-laptop")
    assert rt.pickup is True


def test_history_departed_member_carrier_does_not_vouch(tmp_path):
    """A departed member's history under the throne nick carries the member's
    JID (the service wrote it); the affiliation attribute it also carries is
    not consulted."""
    t = _ticket()
    history = [
        _hist("throne", encode_ad(t), item_jid="mallory@house", item_aff="owner"),
        _hist("throne", encode_cancel(job_id=t.id, by="throne"), ofrom="mallory@house"),
    ]
    rt, _board = _runtime(tmp_path)
    replay_farm_history(
        rt, history, xmpp=_FakeXmpp(_AffListMuc()), room=ROOM,
        owner_aff=frozenset({"throne@house"}),
    )
    assert t.id not in rt.ledger.ads
    assert not rt.ledger.is_cancelled(t.id)


def test_history_departed_nick_alone_never_vouches(tmp_path):
    """No carrier, nick gone from presence: the nick spelling is not identity,
    even when the owner list is present and non-empty."""
    t = _ticket()
    history = [_hist("throne", encode_ad(t))]
    rt, _board = _runtime(tmp_path)
    replay_farm_history(
        rt, history, xmpp=_FakeXmpp(_AffListMuc()), room=ROOM,
        owner_aff=frozenset({"throne@house"}),
    )
    assert t.id not in rt.ledger.ads


def test_history_conflicting_carriers_fail_closed(tmp_path):
    t = _ticket()
    history = [_hist(
        "throne", encode_ad(t), ofrom="throne@house", item_jid="mallory@house",
    )]
    rt, _board = _runtime(tmp_path)
    replay_farm_history(
        rt, history, xmpp=_FakeXmpp(_AffListMuc()), room=ROOM,
        owner_aff=frozenset({"throne@house", "mallory@house"}),
    )
    assert t.id not in rt.ledger.ads


def test_history_occupant_id_binds_to_present_occupant(tmp_path):
    """XEP-0421: the service stamps every message and strips client copies.
    When the stamp names someone present, their presence is the authority —
    a forged owner carrier or a reused throne nick cannot override it."""
    t = _ticket()
    t_ok = _ticket()
    presences = [
        _as_stanza(_wire_presence("throne", jid="throne@house/desk", aff="owner", occupant_id="OID-THRONE")),
        _as_stanza(_wire_presence("kimi-laptop", jid="kimi@house/d", aff="member", occupant_id="OID-KIMI")),
        _as_stanza(_wire_presence("acer", jid="acer@house/d", aff="member", occupant_id="OID-ACER")),
    ]
    occupants = index_occupants(presences, room=ROOM)
    assert set(occupants) == {"OID-THRONE", "OID-KIMI", "OID-ACER"}
    assert occupants["OID-THRONE"].affiliation == "owner"
    assert occupants["OID-KIMI"].jid == "kimi@house"

    muc = _AffListMuc({(ROOM, "throne"): "owner"})
    xmpp = _FakeXmpp(muc)
    owners = frozenset({"throne@house"})
    history = [
        # acer sent this while holding the throne nick, then renamed; it
        # carries a forged owner item. The stamp says acer.
        _hist("throne", encode_ad(t), occupant_id="OID-ACER",
              item_jid="throne@house", item_aff="owner"),
        # a departed stranger under the reused nick: stamp is unknown, the
        # nick is currently the owner's — presence must not vouch it.
        _hist("throne", encode_bench(node="kimi-laptop", by="throne"), occupant_id="OID-GONE"),
        # the present owner's own history: stamp matches their presence.
        _hist("throne", encode_ad(t_ok), occupant_id="OID-THRONE"),
    ]
    rt, _board = _runtime(tmp_path)
    replay_farm_history(
        rt, history, xmpp=xmpp, room=ROOM, owner_aff=owners, occupants=occupants,
    )
    assert t.id not in rt.ledger.ads
    assert not rt.ledger.is_benched("kimi-laptop")
    assert t_ok.id in rt.ledger.ads


def test_history_replay_json_by_does_not_vouch_departed_nick(tmp_path):
    t = _ticket()
    history = [_hist("throne", encode_cancel(job_id=t.id, by="throne"))]
    rt, _board = _runtime(tmp_path)
    rt.ledger.apply_body(encode_ad(t), owner=True)
    replay_farm_history(
        rt, history, xmpp=_FakeXmpp(_FakeMuc()), room=ROOM, owner_aff=None,
    )
    assert not rt.ledger.is_cancelled(t.id)


def test_history_replay_live_presence_owner_counts_without_list(tmp_path):
    """Service without XEP-0421 and no owner list: a nick present with owner
    affiliation still vouches its own history (the legacy live path)."""
    t = _ticket()
    muc = _FakeMuc({(ROOM, "throne"): "owner"})
    rt, _board = _runtime(tmp_path)
    replay_farm_history(
        rt, [_hist("throne", encode_ad(t))],
        xmpp=_FakeXmpp(muc), room=ROOM, owner_aff=None,
    )
    assert t.id in rt.ledger.ads


def test_history_live_real_jid_on_owner_list_vouches(tmp_path):
    """Non-anonymous room: presence carries the nick's real JID; that JID on
    the owner list vouches even when the affiliation field is missing."""
    t = _ticket()
    muc = _AffListMuc(occupant_jids={(ROOM, "throne"): "Throne@House/desk"})
    rt, _board = _runtime(tmp_path)
    replay_farm_history(
        rt, [_hist("throne", encode_ad(t))],
        xmpp=_FakeXmpp(muc), room=ROOM, owner_aff=frozenset({"throne@house"}),
    )
    assert t.id in rt.ledger.ads


def test_history_replay_skips_non_room_origin(tmp_path):
    t = _ticket()
    muc = _FakeMuc({(ROOM, "throne"): "owner"})
    rt, _board = _runtime(tmp_path)
    history = [
        _as_stanza(_wire_message(frm="mallory@other.host/throne", body=encode_ad(t))),
        _as_stanza(_wire_message(frm=ROOM, body="room notice")),
    ]
    replay_farm_history(rt, history, xmpp=_FakeXmpp(muc), room=ROOM, owner_aff=None)
    assert t.id not in rt.ledger.ads


# --------------------------------------------------------------------------- #
#  Room-origin binding — the occupant nick is a resource *of the room*
# --------------------------------------------------------------------------- #


def test_muc_occupant_nick_requires_room_origin():
    assert muc_occupant_nick(_live(f"{ROOM}/throne", "x"), room=ROOM) == "throne"
    assert muc_occupant_nick(_live(f"{ROOM.upper()}/throne", "x"), room=ROOM) == "throne"
    assert muc_occupant_nick(_live("mallory@other.host/throne", "x"), room=ROOM) == ""
    assert muc_occupant_nick(_live("other@conf.house/throne", "x"), room=ROOM) == ""
    assert muc_occupant_nick(_live(ROOM, "x"), room=ROOM) == ""
    assert muc_occupant_nick({"from": f"{ROOM}/node1", "body": ""}, room=ROOM) == "node1"
    assert muc_occupant_nick({"from": "x@y/node1", "body": ""}, room=ROOM) == ""
    # without a room the resource is still returned (callers that own the check)
    assert muc_occupant_nick(_live("x@y/node1", "")) == "node1"


def test_room_handler_non_room_throne_resource_is_not_owner(tmp_path):
    """Fable probe: a groupchat stanza from ``mallory@other/throne`` must not
    become owner-vouched just because the live throne is an owner."""
    t = _ticket()
    muc = _FakeMuc({(ROOM, "throne"): "owner"})
    xmpp = _FakeXmpp(muc)
    rt, board = _runtime(tmp_path)
    handler = make_room_handler(rt, xmpp=xmpp, room=ROOM)

    asyncio.run(handler(_live("mallory@other.host/throne", encode_ad(t))))
    assert t.id not in rt.ledger.ads
    assert board.get(t.id) is None
    asyncio.run(handler(_live("throne@house/throne", encode_bench(node="kimi-laptop", by="throne"))))
    assert not rt.ledger.is_benched("kimi-laptop")

    # the real room-origin throne still steers
    asyncio.run(handler(_live(f"{ROOM}/throne", encode_ad(t))))
    assert t.id in rt.ledger.ads
    assert board.get(t.id) is not None


def test_room_handler_non_room_self_nick_result_never_lands_in_done(tmp_path):
    """Fable probe: ``evil@x/<self.node>`` with a market result for a known ad
    must not skip quarantine. Room-origin foreign results still quarantine;
    room-origin member ads are still refused."""
    from xlii.farm_market import quarantine_dir

    muc = _FakeMuc({(ROOM, "throne"): "owner", (ROOM, "kimi-laptop"): "member"})
    xmpp = _FakeXmpp(muc)
    rt, board = _runtime(tmp_path, node="throne")
    handler = make_room_handler(rt, xmpp=xmpp, room=ROOM)

    market = Ticket.make(job="explore", task="look", kind=KIND_MARKET)
    asyncio.run(handler(_live(f"{ROOM}/throne", encode_ad(market))))
    assert market.id in rt.ledger.ads

    spoof = JobResult(
        id=market.id, status="done", node="throne", job="explore",
        text="injected", finished_at=utc_now(),
    )
    asyncio.run(handler(_live("evil@other.host/throne", encode_result(spoof))))
    assert not (board.root / "done" / f"{market.id}.json").is_file()
    assert not (quarantine_dir(board.root) / f"{market.id}.json").is_file()
    assert market.id not in rt.ledger.results

    foreign = JobResult(
        id=market.id, status="done", node="jo-badge", job="explore",
        text="foreign work", finished_at=utc_now(),
    )
    asyncio.run(handler(_live(f"{ROOM}/jo-badge", encode_result(foreign))))
    assert (quarantine_dir(board.root) / f"{market.id}.json").is_file()
    assert not (board.root / "done" / f"{market.id}.json").is_file()

    house = _ticket()
    asyncio.run(handler(_live(f"{ROOM}/kimi-laptop", encode_ad(house))))
    assert house.id not in rt.ledger.ads


def test_room_handler_ignores_non_groupchat_and_room_bare(tmp_path):
    t = _ticket()
    muc = _FakeMuc({(ROOM, "throne"): "owner"})
    rt, _board = _runtime(tmp_path)
    handler = make_room_handler(rt, xmpp=_FakeXmpp(muc), room=ROOM)
    asyncio.run(handler(_live(f"{ROOM}/throne", encode_ad(t), mtype="chat")))
    asyncio.run(handler(_live(ROOM, encode_ad(t))))
    assert t.id not in rt.ledger.ads


def test_occ_stanza_non_room_origin_is_ignored(tmp_path, monkeypatch):
    from xlii.occ_bus import encode_claim as occ_claim
    from xlii.occupancy_store import load_live

    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    spoof = _live("mallory@other.host/node1", occ_claim("node1"))
    assert ingest_occ_stanza(spoof, self_node="throne", room=ROOM) is True
    assert load_live().via == ""
    real = _live(f"{ROOM}/node1", occ_claim("node1"))
    assert ingest_occ_stanza(real, self_node="throne", room=ROOM) is True
    assert load_live().via == "node1"


def test_room_handler_routes_occ_claims_only_from_room(tmp_path, monkeypatch):
    from xlii.occ_bus import encode_claim as occ_claim
    from xlii.occupancy_store import load_live

    monkeypatch.setenv("XLII_OCCUPANCY_PATH", str(tmp_path / "occ.json"))
    rt, _board = _runtime(tmp_path, node="throne")
    handler = make_room_handler(rt, xmpp=_FakeXmpp(_FakeMuc()), room=ROOM)
    asyncio.run(handler(_live("mallory@other.host/node1", occ_claim("node1"))))
    assert load_live().via == ""
    asyncio.run(handler(_live(f"{ROOM}/node1", occ_claim("node1"))))
    assert load_live().via == "node1"


def test_attach_farm_binds_room_and_vouches_departed_owner(tmp_path):
    """End to end on the real slixmpp stanza classes: join returns presences
    and history; the departed owner's ad replays; the handler registered for
    ``groupchat_message`` drops foreign-origin throne resources."""
    pytest.importorskip("slixmpp")
    from xlii.farm_xmpp import attach_farm

    t = _ticket()
    presences = [
        _as_stanza(_wire_presence("kimi-laptop", jid="kimi@house/d", occupant_id="OID-ME")),
    ]
    history = [
        _hist("throne", encode_ad(t), occupant_id="OID-THRONE",
              item_jid="throne@house/desk", item_aff="owner"),
    ]

    class Muc(_AffListMuc):
        async def join_muc_wait(self, room, nick, **kw):
            self.joined = (str(room), nick)
            return presences[0], None, presences, history

    muc = Muc(owner_list=["throne@house/desk"])
    xmpp = _FakeXmpp(muc)
    cfg = _cfg(tmp_path, muc=ROOM, node="kimi-laptop")
    rt = asyncio.run(attach_farm(xmpp, cfg, auto_consider=False))
    assert rt is not None
    assert muc.joined == (ROOM, "kimi-laptop")
    assert t.id in rt.ledger.ads
    handler = xmpp.handlers["groupchat_message"]

    other = _ticket()
    asyncio.run(handler(_live("mallory@other.host/throne", encode_ad(other))))
    assert other.id not in rt.ledger.ads


# --------------------------------------------------------------------------- #
#  Identity ops: claim / offer / result need the occupant nick
# --------------------------------------------------------------------------- #


def test_claim_requires_matching_occupant_nick(tmp_path):
    from xlii.farm_muc import OP_CLAIM

    rt, _board = _runtime(tmp_path, node="acer")
    t = _ticket()
    rt.ingest(encode_ad(t), owner=True)
    assert rt.ingest(encode_claim(job_id=t.id, node="other"), from_nick="acer") is None
    assert rt.ingest(encode_claim(job_id=t.id, node="other"), from_nick="") is None
    assert rt.ingest(encode_claim(job_id=t.id, node="other")) is None
    assert rt.ledger.winner(t.id) is None
    assert rt.ingest(encode_claim(job_id=t.id, node="other"), from_nick="other") == OP_CLAIM
    assert rt.ledger.winner(t.id) == "other"


def test_offer_and_result_fail_closed_without_nick(tmp_path):
    rt, _board = _runtime(tmp_path, node="throne")
    assert rt.ingest(encode_offer(node="kimi-laptop", offers=["explore"])) is None
    assert "kimi-laptop" not in rt.ledger.offers
    t = _ticket()
    rt.ingest(encode_ad(t), owner=True)
    result = JobResult(
        id=t.id, status="done", node="kimi-laptop", job="explore",
        text="x", finished_at=utc_now(),
    )
    assert rt.ingest(encode_result(result)) is None
    assert t.id not in rt.ledger.results


def test_market_result_under_own_nick_from_room_is_quarantined(tmp_path):
    """Nicks are reusable: a room result arriving under our own nick is not
    ours. It is a document in quarantine, never done/."""
    from xlii.farm_market import quarantine_dir

    rt, board = _runtime(tmp_path, node="throne")
    market = Ticket.make(job="explore", task="look", kind=KIND_MARKET)
    rt.ingest(encode_ad(market), owner=True)
    result = JobResult(
        id=market.id, status="done", node="throne", job="explore",
        text="squatter", finished_at=utc_now(),
    )
    assert rt.ingest(encode_result(result), from_nick="throne") == OP_RESULT
    assert (quarantine_dir(board.root) / f"{market.id}.json").is_file()
    assert not (board.root / "done" / f"{market.id}.json").is_file()


def test_own_result_echo_and_late_spoof_do_not_rewrite_done(tmp_path, monkeypatch):
    monkeypatch.setattr("xlii.farm_xmpp.CLAIM_GRACE_S", 0)
    sent: list[str] = []
    cfg = _cfg(tmp_path, muc=ROOM, node="acer")
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(cfg, node="acer", board=board, send_body=sent.append, pickup=True)
    t = _ticket()
    rt.ingest(encode_ad(t), owner=True)

    def fake_run(ticket, cfg_arg, **kw):
        return JobResult(
            id=ticket.id, status="done", node="acer", job="explore",
            text="from acer", finished_at=utc_now(),
        )

    monkeypatch.setattr("xlii.farm_xmpp.run_ticket", fake_run)
    asyncio.run(rt.consider(t))
    lane, _ticket_, first = board.get(t.id)
    assert lane == "done" and first.text == "from acer"

    echo = next(s for s in sent if '"op": "result"' in s or '"op":"result"' in s)
    rt.ingest(echo, from_nick="acer")
    late = JobResult(
        id=t.id, status="done", node="acer", job="explore",
        text="rewritten", finished_at=utc_now(),
    )
    rt.ingest(encode_result(late), from_nick="acer")
    lane, _ticket_, kept = board.get(t.id)
    assert lane == "done" and kept.text == "from acer"


class _DoneWorker:
    def __init__(self, reply="ok"):
        self.reply = reply

    def run(self, task, context=None, max_iterations=None, should_stop=None, **kw):
        return self.reply, SimpleNamespace(cost_usd=0.0, iterations=1)


def _through_run_ticket(reply: str):
    from xlii.job_run import run_ticket as real_run_ticket

    def _run(ticket, cfg_arg, **kw):
        kw.setdefault("worker_factory", lambda *a: _DoneWorker(reply=reply))
        return real_run_ticket(ticket, cfg_arg, **kw)

    return _run


def test_consider_stamps_runtime_nick_when_jobs_node_unset(tmp_path, monkeypatch):
    """jobs.node unset would stamp result.node='node'; occupancy nick is shop-a."""
    monkeypatch.setattr("xlii.farm_xmpp.CLAIM_GRACE_S", 0)
    sent: list[str] = []
    cfg = _cfg(tmp_path)
    del cfg.jobs["node"]
    assert job_node_name(cfg) == "node"
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="shop-a", board=board, send_body=sent.append, pickup=True,
    )
    t = _ticket()
    rt.ingest(encode_ad(t), owner=True)
    monkeypatch.setattr("xlii.farm_xmpp.run_ticket", _through_run_ticket("from shop-a"))
    result = asyncio.run(rt.consider(t))
    assert result is not None and result.node == "shop-a"
    lane, _ticket_, stored = board.get(t.id)
    assert lane == "done" and stored is not None
    assert stored.node == "shop-a"
    assert stored.text == "from shop-a"

    peer_board = JobBoard(tmp_path / "peer-board")
    peer = FarmRuntime(
        cfg, node="shop-b", board=peer_board, send_body=lambda b: None, pickup=True,
    )
    peer.ingest(encode_ad(t), owner=True)
    rbody = next(s for s in sent if '"op": "result"' in s or '"op":"result"' in s)
    assert peer.ingest(rbody, from_nick="shop-a") == OP_RESULT
    plane, _pt, pres = peer_board.get(t.id)
    assert plane == "done" and pres is not None and pres.node == "shop-a"


def test_consider_local_market_done_room_origin_quarantines(tmp_path, monkeypatch):
    """Own local market result lands in done/; the same nick from the room
    is still foreign (nicks are reusable) and goes to quarantine."""
    from xlii.farm_market import quarantine_dir

    monkeypatch.setattr("xlii.farm_xmpp.CLAIM_GRACE_S", 0)
    sent: list[str] = []
    cfg = _cfg(tmp_path)
    del cfg.jobs["node"]
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="shop-a", board=board, send_body=sent.append, pickup=True,
    )
    market = Ticket.make(job="explore", task="look", kind=KIND_MARKET)
    rt.ingest(encode_ad(market), owner=True)
    monkeypatch.setattr("xlii.farm_xmpp.run_ticket", _through_run_ticket("own market"))
    result = asyncio.run(rt.consider(market))
    assert result is not None and result.node == "shop-a"
    assert (board.root / "done" / f"{market.id}.json").is_file()
    assert not (quarantine_dir(board.root) / f"{market.id}.json").is_file()

    other = Ticket.make(job="explore", task="look", kind=KIND_MARKET)
    rt.ingest(encode_ad(other), owner=True)
    foreign = JobResult(
        id=other.id, status="done", node="shop-a", job="explore",
        text="from the room", finished_at=utc_now(),
    )
    assert rt.ingest(encode_result(foreign), from_nick="shop-a") == OP_RESULT
    assert (quarantine_dir(board.root) / f"{other.id}.json").is_file()
    assert not (board.root / "done" / f"{other.id}.json").is_file()


def test_evict_issues_affiliation_change():
    muc = _FakeMuc()
    xmpp = _FakeXmpp(muc)
    asyncio.run(evict_occupant(xmpp, "jobs@conf", "kimi-laptop", reason="benched too long"))
    assert muc.affiliation_calls == [{
        "room": "jobs@conf",
        "affiliation": "none",
        "nick": "kimi-laptop",
        "jid": None,
        "reason": "benched too long",
    }]
    assert muc.role_calls[0]["role"] == "none"
    assert muc.role_calls[0]["nick"] == "kimi-laptop"


def test_cli_cancel_and_parser(tmp_path, monkeypatch):
    from xlii.cmds import jobs as job_cmd
    from xlii.cli import build_parser
    from xlii.config import GlobalConfig

    cfg = GlobalConfig()
    cfg.jobs = {
        "offers": ["explore"],
        "node": "throne",
        "board": str(tmp_path / "board"),
    }
    monkeypatch.setattr(job_cmd, "GlobalConfig", SimpleNamespace(load=lambda: cfg))
    board = JobBoard(tmp_path / "board")
    t = _ticket()
    board.post(t)

    rc = job_cmd.cmd_job_cancel(Namespace(id=t.id))
    assert rc == 0
    assert board.is_cancelled(t.id)

    p = build_parser()
    assert p.parse_args(["job", "cancel", "abc"]).job_cmd == "cancel"
    assert p.parse_args(["job", "bench", "kimi-laptop"]).job_cmd == "bench"
    assert p.parse_args(["job", "evict", "kimi-laptop"]).job_cmd == "evict"


def test_cli_bench_without_muc_fails_clearly(tmp_path, monkeypatch, capsys):
    from xlii.cmds import jobs as job_cmd
    from xlii.config import GlobalConfig

    cfg = GlobalConfig()
    cfg.jobs = {"node": "throne", "board": str(tmp_path / "board"), "muc": ""}
    monkeypatch.setattr(job_cmd, "GlobalConfig", SimpleNamespace(load=lambda: cfg))
    rc = job_cmd.cmd_job_bench(Namespace(node="kimi-laptop", until=""))
    assert rc == 1
    err = capsys.readouterr().err
    assert "jobs.muc" in err


def test_cli_evict_without_muc_fails_clearly(tmp_path, monkeypatch, capsys):
    from xlii.cmds import jobs as job_cmd
    from xlii.config import GlobalConfig

    cfg = GlobalConfig()
    cfg.jobs = {"node": "throne", "board": str(tmp_path / "board")}
    monkeypatch.setattr(job_cmd, "GlobalConfig", SimpleNamespace(load=lambda: cfg))
    rc = job_cmd.cmd_job_evict(Namespace(node="kimi-laptop"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "jobs.muc" in err or "MUC" in err


# --------------------------------------------------------------------------- #
#  Phase 2 — offer beacon + pane
# --------------------------------------------------------------------------- #


def test_offer_beacon_roundtrip_parse_apply():
    body = encode_offer(
        node="kimi-laptop",
        offers=["explore"],
        gig="kimi",
        busy=False,
        allowance={"usd_left": 2.5, "per_iter_est": 0.05},
        specialty="Python code review, terse",
        skills=["review"],
    )
    op, data = parse_farm_body(body)
    assert op == OP_OFFER
    assert data["node"] == "kimi-laptop"
    assert data["offers"] == ["explore"]
    assert data["gig"] == "kimi"
    assert data["busy"] is False
    assert data["specialty"] == "Python code review, terse"
    assert data["skills"] == ["review"]
    allow = Allowance.from_dict(data["allowance"])
    assert allow.usd_left == 2.5 and allow.per_iter_est == 0.05

    led = RoomLedger()
    assert led.apply_body(body) == OP_OFFER  # offer is not a steer op
    assert led.offers["kimi-laptop"]["gig"] == "kimi"


def test_offer_spoofed_node_ignored(tmp_path):
    cfg = _cfg(tmp_path)
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="acer", board=board, send_body=lambda b: None, pickup=True,
    )
    body = encode_offer(node="kimi-laptop", offers=["explore"], gig="kimi")
    assert rt.ingest(body, from_nick="acer") is None
    assert "kimi-laptop" not in rt.ledger.offers
    assert rt.ingest(body, from_nick="kimi-laptop") == OP_OFFER
    assert "kimi-laptop" in rt.ledger.offers


def test_post_offer_on_change(tmp_path):
    sent: list[str] = []
    cfg = _cfg(tmp_path, allowance={"usd_left": 1.0, "per_iter_est": 0.02})
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="kimi-laptop", board=board, send_body=sent.append, pickup=True,
    )
    rt.post_offer(force=True)
    rt.post_offer()  # unchanged — no second send
    assert sum(1 for s in sent if '"op": "offer"' in s or '"op":"offer"' in s) == 1
    parsed = parse_farm_body(sent[0])
    assert parsed is not None and parsed[0] == OP_OFFER
    assert parsed[1]["allowance"]["usd_left"] == 1.0


def test_pane_renders_both_sides_from_ledger():
    from xlii.farm_view import build_farm_view
    from xlii.panes.farm import FarmPane

    t = _ticket()
    led = RoomLedger()
    led.apply_body(encode_ad(t), owner=True)
    led.apply_body(encode_offer(
        node="kimi-laptop", offers=["explore"], gig="kimi", busy=True,
        allowance={"usd_left": 3, "per_iter_est": 0.1},
    ))
    view = build_farm_view(
        ledgers={"jobs@conf": led},
        joined_pools={"jobs@conf"},
        is_owner=True,
    )
    pane = FarmPane("farm://", view=view)
    pane.mount("farm://")
    front = pane.render()
    text = "\n".join(r.text for r in front.rows)
    assert t.id[:8] in text
    assert "open" in text
    assert "jobs" in text  # pool label from jobs@conf
    names = [a.name for a in pane.actions()]
    assert "cancel" in names and "post" in names and "flip" in names

    pane.mount("farm://nodes")
    flip = pane.render()
    ftext = "\n".join(r.text for r in flip.rows)
    assert "kimi-laptop" in ftext
    assert "explore" in ftext
    assert "busy" in ftext
    fnames = [a.name for a in pane.actions()]
    assert "bench" in fnames and "evict" in fnames


def test_non_throne_pane_has_no_control_buttons():
    from xlii.farm_view import build_farm_view
    from xlii.panes.farm import FarmPane

    t = _ticket()
    led = RoomLedger()
    led.apply_body(encode_ad(t), owner=True)
    led.apply_body(encode_offer(node="kimi-laptop", offers=["explore"]))
    view = build_farm_view(
        ledgers={"jobs@conf": led},
        joined_pools={"jobs@conf"},
        is_owner=False,
    )
    pane = FarmPane("farm://", view=view)
    pane.mount("farm://")
    names = [a.name for a in pane.actions()]
    assert names == ["flip"]
    pane.mount("farm://nodes")
    names = [a.name for a in pane.actions()]
    assert names == ["flip"]
    assert "cancel" not in names and "bench" not in names and "evict" not in names


def test_ticket_in_unjoined_pool_is_absent_not_hidden():
    from xlii.farm_view import build_farm_view
    from xlii.panes.farm import FarmPane

    here = _ticket()
    other = _ticket()
    led_here = RoomLedger()
    led_here.apply_body(encode_ad(here), owner=True)
    led_away = RoomLedger()
    led_away.apply_body(encode_ad(other), owner=True)
    view = build_farm_view(
        ledgers={"jobs@house": led_here, "jobs@strangers": led_away},
        joined_pools={"jobs@house"},
        is_owner=True,
    )
    assert any(r.ticket_id == here.id for r in view.ads)
    assert all(r.ticket_id != other.id for r in view.ads)
    pane = FarmPane("farm://", view=view)
    pane.mount("farm://")
    text = "\n".join(r.text for r in pane.render().rows)
    assert here.id[:8] in text
    assert other.id[:8] not in text
    assert "hidden" not in text.lower()
    assert "strangers" not in text


# --------------------------------------------------------------------------- #
#  Phase 3 — posters
# --------------------------------------------------------------------------- #


def test_remote_post_auto_deny_writes_no_ad(tmp_path, monkeypatch):
    from xlii.farm_post import post_to_pool, resolve_where
    from xlii.farm import Budget, JobError
    from xlii.tools import auto_deny

    monkeypatch.setattr("xlii.tools._confirm", auto_deny)
    cfg = _cfg(tmp_path, muc="jobs@conf")
    pool = resolve_where(cfg, "jobs")
    assert pool == "jobs@conf"
    with pytest.raises(JobError, match="not confirmed"):
        post_to_pool(
            cfg, job="explore", task="look at auth", pool=pool,
            budget=Budget(max_usd=1.5, max_iters=4), publish_muc=False,
        )
    board = JobBoard(tmp_path / "board")
    assert board.list_open() == []


def test_remote_post_confirm_text_names_pool_and_budget(tmp_path, monkeypatch):
    from xlii.farm import Budget
    from xlii.farm_post import post_to_pool, remote_post_prompt, resolve_where

    prompts: list[str] = []

    def yes(prompt: str) -> str:
        prompts.append(prompt)
        return "y"

    monkeypatch.setattr("xlii.tools._confirm", yes)
    cfg = _cfg(tmp_path, muc="jobs@conf")
    pool = resolve_where(cfg, "jobs")
    budget = Budget(max_usd=2.0, max_iters=6)
    prompt = remote_post_prompt(
        job="explore", pool=pool, budget=budget, task="wiki drift",
    )
    assert "jobs@conf" in prompt
    assert "max_usd=2" in prompt
    assert "max_iters=6" in prompt
    ticket = post_to_pool(
        cfg, job="explore", task="wiki drift", pool=pool,
        budget=budget, publish_muc=False,
    )
    assert prompts and "jobs@conf" in prompts[0] and "max_usd=2" in prompts[0]
    assert "context: none" in prompts[0]
    board = JobBoard(tmp_path / "board")
    assert board.get(ticket.id)[0] == "open"


def test_remote_post_confirm_shows_context_that_rides_the_room(tmp_path, monkeypatch):
    """A yes ships ``context`` plaintext to the pool, so the prompt must show
    it — sized and sampled — not just the task."""
    from xlii.farm import Budget
    from xlii.farm_post import post_to_pool, remote_post_prompt

    secret = "parent notes: " + "S" * 400
    prompt = remote_post_prompt(
        job="explore", pool="jobs@conf", budget=Budget(max_usd=1.0, max_iters=3),
        task="t" * 300, context=secret,
    )
    assert f"context ({len(secret)} chars" in prompt
    assert "parent notes: SSSS" in prompt
    assert "task (300 chars)" in prompt
    assert len(prompt) < 700  # sampled, not dumped

    prompts: list[str] = []
    monkeypatch.setattr("xlii.tools._confirm", lambda p: prompts.append(p) or "y")
    cfg = _cfg(tmp_path, muc="jobs@conf")
    ticket = post_to_pool(
        cfg, job="explore", task="look", pool="jobs@conf",
        budget=Budget(max_usd=1.0, max_iters=3), context=secret, publish_muc=False,
    )
    assert ticket.context == secret
    assert prompts and "parent notes: SSSS" in prompts[0]
    assert f"context ({len(secret)} chars" in prompts[0]


def test_dispatch_where_pool_confirms(tmp_path, monkeypatch):
    from tests.helpers import make_agent
    from xlii.tools import auto_deny

    agent = make_agent(tmp_path)
    agent.cfg.jobs = {
        "offers": ["explore"],
        "node": "throne",
        "board": str(tmp_path / "board"),
        "muc": "jobs@conf",
    }
    monkeypatch.setattr("xlii.tools._confirm", auto_deny)
    monkeypatch.setattr("xlii.farm_xmpp.publish_ad", lambda *a, **k: 0)
    text, _call = agent._run_worker({"task": "look at this", "where": "jobs"})
    assert "not confirmed" in text
    assert JobBoard(tmp_path / "board").list_open() == []

    monkeypatch.setattr("xlii.tools._confirm", lambda p: "y")
    text, _call = agent._run_worker({
        "task": "look at this", "where": "jobs", "budget": 1.25,
    })
    assert "posted ad" in text
    assert "jobs@conf" in text
    assert "max_usd=1.25" in text
    assert JobBoard(tmp_path / "board").list_open()


def test_post_job_door_auto_deny(tmp_path, monkeypatch):
    from tests.helpers import make_tool_ctx
    from xlii.door_tools import t_post_job
    from xlii.tools import auto_deny

    monkeypatch.setattr("xlii.tools._confirm", auto_deny)
    ctx = make_tool_ctx(tmp_path)
    ctx.cfg = _cfg(tmp_path, muc="jobs@conf")
    result = t_post_job(ctx, {
        "task": "nightly sweep", "pool": "jobs", "budget": 0.5,
    })
    assert result.is_error
    assert "not confirmed" in result.content
    assert JobBoard(tmp_path / "board").list_open() == []


def test_post_job_door_confirm_names_pool_budget(tmp_path, monkeypatch):
    from tests.helpers import make_tool_ctx
    from xlii.door_tools import t_post_job

    prompts: list[str] = []
    monkeypatch.setattr("xlii.tools._confirm", lambda p: prompts.append(p) or "y")
    ctx = make_tool_ctx(tmp_path)
    ctx.cfg = _cfg(tmp_path, muc="jobs@conf")
    result = t_post_job(ctx, {
        "task": "nightly sweep", "pool": "jobs", "budget": 0.5, "max_iters": 5,
    })
    assert not result.is_error
    assert prompts
    assert "jobs@conf" in prompts[0]
    assert "max_usd=0.5" in prompts[0]
    assert "max_iters=5" in prompts[0]
    assert "posted" in result.content


def test_scheduled_post_fires_once_per_day(tmp_path):
    from datetime import datetime, timezone

    from xlii.farm_schedule import fire_scheduled_post

    cfg = _cfg(
        tmp_path,
        startup_ad={"job": "explore", "task": "nightly wiki-drift sweep", "max_usd": 1},
    )
    stamp = tmp_path / "stamp.day"
    day = datetime(2026, 9, 5, tzinfo=timezone.utc)
    first = fire_scheduled_post(cfg, now=day, stamp_path=stamp)
    assert first is not None and first.task == "nightly wiki-drift sweep"
    second = fire_scheduled_post(cfg, now=day, stamp_path=stamp)
    assert second is None
    nxt = fire_scheduled_post(
        cfg, now=datetime(2026, 9, 6, tzinfo=timezone.utc), stamp_path=stamp,
    )
    assert nxt is not None and nxt.id != first.id
    board = JobBoard(tmp_path / "board")
    assert len(board.list_open()) == 2


# --------------------------------------------------------------------------- #
#  Phase 4 — market venue
# --------------------------------------------------------------------------- #


def test_market_ticket_refuses_context_and_write_shaped():
    with pytest.raises(JobError, match="refuse context"):
        Ticket.make(job="explore", task="look", context="secret notes", kind=KIND_MARKET)
    with pytest.raises(JobError, match="refused across thrones"):
        Ticket.make(job="stage", task="pull project", kind=KIND_MARKET)
    with pytest.raises(JobError, match="refused across thrones"):
        Ticket.make(job="lab", task="edit files", kind=KIND_MARKET)
    with pytest.raises(JobError, match="local-project"):
        Ticket.make(
            job="explore", task="look", kind=KIND_MARKET,
            workplace=Workplace(mode=WORKPLACE_LOCAL, project="iXaac-lab"),
        )
    t = Ticket.make(job="explore", task="look at this", kind=KIND_MARKET)
    assert t.kind == KIND_MARKET and t.context == ""


def test_market_stage_lab_refused_at_claim(tmp_path):
    cfg = _cfg(tmp_path)
    t = Ticket.make(job="explore", task="look", kind=KIND_MARKET)
    t.job = "stage"
    assert "refused across thrones" in (skip_reason(t, cfg) or "")
    t.job = "lab"
    assert "refused across thrones" in (skip_reason(t, cfg) or "")
    t.job = "explore"
    t.context = "leaked"
    assert "refuse context" in (skip_reason(t, cfg) or "")
    t.context = ""
    t.workplace = Workplace(mode=WORKPLACE_LOCAL, project="x")
    assert "local-project" in (skip_reason(t, cfg) or "")


def test_foreign_market_result_goes_to_quarantine_not_done(tmp_path):
    from xlii.farm_market import load_quarantine, quarantine_dir
    from xlii.farm_xmpp import _mirror

    board = JobBoard(tmp_path / "board")
    t = Ticket.make(job="explore", task="look", kind=KIND_MARKET)
    board.post(t)
    result = JobResult(
        id=t.id, status="done", node="stranger-badge", job="explore",
        text="injected prompt", finished_at=utc_now(),
    )
    _mirror(board, "result", {"result": result.to_dict()})
    assert (quarantine_dir(board.root) / f"{t.id}.json").is_file()
    found = load_quarantine(board, t.id)
    assert found is not None and found[1].text == "injected prompt"
    # not fused into done/
    lane = board.get(t.id)
    assert lane is not None and lane[0] != "done"


def test_unknown_market_shaped_result_does_not_land_in_done(tmp_path):
    from xlii.farm_market import quarantine_dir
    from xlii.farm_xmpp import _mirror

    board = JobBoard(tmp_path / "board")
    t = Ticket.make(job="explore", task="look", kind=KIND_MARKET)
    result = JobResult(
        id=t.id, status="done", node="throne", job="explore",
        text="injected prompt", finished_at=utc_now(),
    )
    _mirror(board, "result", {"result": result.to_dict()})
    assert board.get(t.id) is None
    assert not (board.root / "done" / f"{t.id}.json").is_file()
    assert not (quarantine_dir(board.root) / f"{t.id}.json").is_file()


def test_result_node_self_is_not_enough_without_nick_vouch(tmp_path):
    from xlii.farm_market import quarantine_dir

    cfg = _cfg(tmp_path)
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="throne", board=board, send_body=lambda b: None, pickup=True,
    )
    t = Ticket.make(job="explore", task="look", kind=KIND_MARKET)
    rt.ingest(encode_ad(t), owner=True)
    result = JobResult(
        id=t.id, status="done", node="throne", job="explore",
        text="injected prompt", finished_at=utc_now(),
    )
    assert rt.ingest(encode_result(result), from_nick="stranger-badge") is None
    lane = board.get(t.id)
    assert lane is not None and lane[0] != "done"
    assert not (quarantine_dir(board.root) / f"{t.id}.json").is_file()
    assert t.id not in rt.ledger.results


def test_result_nick_mismatch_ignored(tmp_path):
    cfg = _cfg(tmp_path)
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="throne", board=board, send_body=lambda b: None, pickup=True,
    )
    t = _ticket()
    rt.ingest(encode_ad(t), owner=True)
    result = JobResult(
        id=t.id, status="done", node="kimi-laptop", job="explore",
        text="nope", finished_at=utc_now(),
    )
    assert rt.ingest(encode_result(result), from_nick="acer") is None
    assert t.id not in rt.ledger.results
    lane = board.get(t.id)
    assert lane is not None and lane[0] != "done"


def test_known_market_foreign_result_quarantines_from_ledger(tmp_path):
    from xlii.farm_market import load_quarantine, quarantine_dir

    cfg = _cfg(tmp_path)
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="throne", board=board, send_body=lambda b: None, pickup=True,
    )
    t = Ticket.make(job="explore", task="look", kind=KIND_MARKET)
    rt.ingest(encode_ad(t), owner=True)
    result = JobResult(
        id=t.id, status="done", node="jo-badge", job="explore",
        text="foreign work", finished_at=utc_now(),
    )
    assert rt.ingest(encode_result(result), from_nick="jo-badge") == OP_RESULT
    qpath = quarantine_dir(board.root) / f"{t.id}.json"
    assert qpath.is_file()
    found = load_quarantine(board, t.id)
    assert found is not None and found[1].text == "foreign work"
    lane = board.get(t.id)
    assert lane is not None and lane[0] != "done"


def test_result_kind_prefers_ledger_ad_over_file_board(tmp_path):
    from xlii.farm_market import quarantine_dir

    cfg = _cfg(tmp_path)
    board = JobBoard(tmp_path / "board")
    rt = FarmRuntime(
        cfg, node="throne", board=board, send_body=lambda b: None, pickup=True,
    )
    market = Ticket.make(job="explore", task="look", kind=KIND_MARKET)
    advisory = Ticket(id=market.id, job=market.job, task=market.task)
    board.post(advisory)
    rt.ledger.ads[market.id] = market
    result = JobResult(
        id=market.id, status="done", node="jo-badge", job="explore",
        text="from the fair", finished_at=utc_now(),
    )
    assert rt.ingest(encode_result(result), from_nick="jo-badge") == OP_RESULT
    assert (quarantine_dir(board.root) / f"{market.id}.json").is_file()
    assert not (board.root / "done" / f"{market.id}.json").is_file()


def test_rep_increments_only_on_poster_accept(tmp_path):
    from xlii.farm_market import (
        RepStore, accept_quarantined, quarantine_result, rep_path,
    )

    board = JobBoard(tmp_path / "jobs")
    t = Ticket.make(job="explore", task="look", kind=KIND_MARKET)
    board.post(t)
    result = JobResult(
        id=t.id, status="done", node="jo-badge", job="explore",
        text="ok", finished_at=utc_now(),
    )
    quarantine_result(board, t, result)
    store = RepStore(rep_path(board.root))
    assert store.get("abcd" * 16)["accepted"] == 0
    counts = accept_quarantined(board, t.id, fingerprint="abcd" * 16, rep=store)
    assert counts["accepted"] == 1
    assert store.get("abcd" * 16)["accepted"] == 1
    # completing on the house board must not bump rep
    assert store.get("abcd" * 16)["disputed"] == 0


def test_market_wall_shows_beacons_never_tickets():
    from xlii.farm_market import build_market_wall
    from xlii.panes.market import MarketPane

    offers = {
        "jo-badge": {
            "node": "jo-badge",
            "offers": ["explore"],
            "gig": "kimi",
            "allowance": {"per_iter_est": 0.04},
            "fingerprint": "ffff" * 16,
        }
    }
    rows = build_market_wall(offers, rep=None, fingerprint_for={"jo-badge": "ffff" * 16})
    assert len(rows) == 1
    assert rows[0].offers == "explore"
    pane = MarketPane("market://", rows=rows)
    pane.mount("market://")
    text = "\n".join(r.text for r in pane.render().rows)
    assert "explore" in text
    assert "ticket" not in text.lower()
    assert pane.actions() == []


def test_invite_issues_muc_create_and_invite():
    from xlii.farm_market import create_deal_room

    class Muc:
        def __init__(self):
            self.joined = []
            self.invited = []

        async def join_muc_wait(self, room, nick, maxstanzas=0, timeout=30):
            self.joined.append((room, nick))

        def invite(self, room, jid):
            self.invited.append((room, jid))

    class Xmpp:
        def __init__(self, muc):
            self._muc = muc

        def __getitem__(self, k):
            if k == "xep_0045":
                return self._muc
            raise KeyError(k)

    muc = Muc()
    room = asyncio.run(create_deal_room(
        Xmpp(muc), "conference.market.xlii.computer", "deadbeef" * 8,
        nick="throne",
    ))
    assert room.endswith("@conference.market.xlii.computer")
    assert muc.joined and muc.invited
    assert muc.invited[0][1] == "deadbeef" * 8
