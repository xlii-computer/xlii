"""Market venue helpers (classifieds Part II).

The venue is a room server and a wall. Agreement and payment happen
before the door. xlii never custodies value. Reputation is a count
against an OMEMO fingerprint: accepted / disputed / abandoned.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from xlii.atomicio import write_text_atomic
from xlii.farm import KIND_MARKET, JobError, JobResult, Ticket, valid_job_id
from xlii.job_board import JobBoard


REP_FIELDS = ("accepted", "disputed", "abandoned")


def quarantine_dir(root: Path) -> Path:
    return Path(root) / "quarantine"


def rep_path(root: Path) -> Path:
    return Path(root) / "rep.json"


class RepStore:
    """accepted / disputed / abandoned per fingerprint."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._data: dict[str, dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            self._data = {}
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._data = {}
            return
        out: dict[str, dict[str, int]] = {}
        if isinstance(raw, dict):
            for fp, row in raw.items():
                if not isinstance(row, dict):
                    continue
                out[str(fp)] = {k: int(row.get(k) or 0) for k in REP_FIELDS}
        self._data = out

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.path, json.dumps(self._data, indent=2) + "\n")

    def get(self, fingerprint: str) -> dict[str, int]:
        fp = (fingerprint or "").strip()
        row = self._data.get(fp) or {}
        return {k: int(row.get(k) or 0) for k in REP_FIELDS}

    def bump(self, fingerprint: str, field: str) -> dict[str, int]:
        fp = (fingerprint or "").strip()
        if not fp:
            raise JobError("reputation needs a fingerprint")
        if field not in REP_FIELDS:
            raise JobError(f"unknown rep field {field!r}")
        row = self._data.setdefault(fp, {k: 0 for k in REP_FIELDS})
        row[field] = int(row.get(field) or 0) + 1
        self._save()
        return self.get(fp)


def quarantine_result(board: JobBoard, ticket: Ticket, result: JobResult) -> Path:
    """Land a foreign result as a document. Never auto-fuse into done/."""
    board.ensure()
    qdir = quarantine_dir(board.root)
    qdir.mkdir(parents=True, exist_ok=True)
    if not valid_job_id(ticket.id):
        raise JobError(f"invalid job id {ticket.id!r}")
    dest = qdir / f"{ticket.id}.json"
    doc = result.to_dict()
    doc["ticket"] = ticket.to_dict()
    doc["quarantine"] = True
    write_text_atomic(dest, json.dumps(doc, indent=2) + "\n")
    return dest


def is_foreign_market_result(ticket: Ticket, *, local: bool = False) -> bool:
    """True when a market result did not come out of this runtime.

    *local* is the producing code path's word (the node ran the ticket
    itself). Nothing on the wire — not JSON ``result.node``, not the MUC
    nick it arrived under — can make a market result local: nicks are
    reusable and JSON is unsigned. House results are never foreign.
    """
    if ticket.kind != KIND_MARKET:
        return False
    return not local


def load_quarantine(board: JobBoard, job_id: str) -> Optional[tuple[Ticket, JobResult]]:
    if not valid_job_id(job_id):
        return None
    path = quarantine_dir(board.root) / f"{job_id}.json"
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    result = JobResult.from_dict(raw)
    ticket_raw = raw.get("ticket") if isinstance(raw.get("ticket"), dict) else raw
    ticket = Ticket.from_dict(ticket_raw)
    return ticket, result


def accept_quarantined(
    board: JobBoard,
    job_id: str,
    *,
    fingerprint: str,
    rep: Optional[RepStore] = None,
) -> dict[str, int]:
    """Poster accept: bump *accepted* for *fingerprint*. Does not ambient-fuse."""
    found = load_quarantine(board, job_id)
    if found is None:
        raise JobError(f"no quarantined result {job_id}")
    store = rep or RepStore(rep_path(board.root))
    return store.bump(fingerprint, "accepted")


@dataclass(frozen=True)
class MarketWallRow:
    fingerprint: str
    offers: str
    gig: str
    rate: str
    accepted: int = 0
    disputed: int = 0
    abandoned: int = 0


def build_market_wall(
    offers: dict[str, dict[str, Any]],
    rep: Optional[RepStore] = None,
    *,
    fingerprint_for: Optional[dict[str, str]] = None,
) -> tuple[MarketWallRow, ...]:
    """Read-only wall: beacons + rep. Never tickets."""
    rows: list[MarketWallRow] = []
    fp_map = fingerprint_for or {}
    for node, data in sorted(offers.items()):
        fp = fp_map.get(node) or str(data.get("fingerprint") or node)
        allow = data.get("allowance") or {}
        est = allow.get("per_iter_est")
        rate = f"${est:g}/it" if est is not None else ""
        names = data.get("offers") or []
        if isinstance(names, str):
            names = [names]
        counts = rep.get(fp) if rep is not None else {k: 0 for k in REP_FIELDS}
        rows.append(MarketWallRow(
            fingerprint=fp,
            offers=",".join(str(x) for x in names),
            gig=str(data.get("gig") or ""),
            rate=rate,
            accepted=int(counts.get("accepted") or 0),
            disputed=int(counts.get("disputed") or 0),
            abandoned=int(counts.get("abandoned") or 0),
        ))
    return tuple(rows)


async def create_deal_room(
    xmpp: Any, venue: str, fingerprint: str, *, nick: str = "throne",
) -> str:
    """Open a two-party deal room and invite the badge (XEP-0045).

    *fingerprint* is the invitee's OMEMO fp; the JID to invite is looked up
    on the client when possible, otherwise the fp is used as the invite token
    the test/mock records.
    """
    fp = (fingerprint or "").strip()
    venue = (venue or "").strip()
    if not xmpp or not venue or not fp:
        raise RuntimeError("invite requires xmpp, venue, and fingerprint")
    try:
        muc = xmpp["xep_0045"]
    except Exception as e:
        raise RuntimeError(f"invite: no XEP-0045 plugin ({e})") from e
    local = "deal-" + "".join(c for c in fp.lower() if c.isalnum())[:12]
    if "@" in venue:
        host = venue.split("@", 1)[1]
    else:
        host = venue
    room = f"{local}@{host}"
    await muc.join_muc_wait(room, nick, maxstanzas=0, timeout=30)
    invite = getattr(muc, "invite", None)
    if callable(invite):
        invite(room, fp)
    return room
