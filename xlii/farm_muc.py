"""Farm MUC envelopes + in-room claim ledger.

MUC is not OMEMO. Trust is members-only on the hub. The file board remains
the local mirror so ``xlii job ls`` works on any box that saw the room.

Claim is first-writer-wins by ``at`` then node name, with a short grace
window on the wire (see ``CLAIM_GRACE_S``) so two joiners don't both run.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from xlii.farm import JobResult, Ticket, utc_now


FARM_KIND = "xlii.farm"
OP_AD = "ad"
OP_CLAIM = "claim"
OP_RESULT = "result"
OP_CANCEL = "cancel"
OP_BENCH = "bench"
OP_OFFER = "offer"
CLAIM_GRACE_S = 2.0

# Board-steering ops. Honored only when the caller vouches that the sender
# is a MUC owner. JSON ``by`` / ``from`` / ``posted_by`` are informational.
STEER_OPS = frozenset({OP_AD, OP_CANCEL, OP_BENCH})

KNOWN_OPS = frozenset({OP_AD, OP_CLAIM, OP_RESULT, OP_CANCEL, OP_BENCH, OP_OFFER})


def encode_ad(ticket: Ticket) -> str:
    return json.dumps({"xlii": FARM_KIND, "op": OP_AD, "ticket": ticket.to_dict()})


def encode_claim(*, job_id: str, node: str, at: str = "") -> str:
    return json.dumps({
        "xlii": FARM_KIND,
        "op": OP_CLAIM,
        "id": job_id,
        "node": node,
        "at": at or utc_now(),
    })


def encode_result(result: JobResult) -> str:
    return json.dumps({"xlii": FARM_KIND, "op": OP_RESULT, "result": result.to_dict()})


def encode_cancel(*, job_id: str, by: str = "") -> str:
    return json.dumps({
        "xlii": FARM_KIND,
        "op": OP_CANCEL,
        "job_id": job_id,
        "by": by,
    })


def encode_bench(*, node: str, by: str = "", until: str = "") -> str:
    data: dict[str, Any] = {
        "xlii": FARM_KIND,
        "op": OP_BENCH,
        "node": node,
        "by": by,
    }
    if until:
        data["until"] = until
    return json.dumps(data)


def encode_offer(
    *,
    node: str,
    offers: list[str],
    gig: str = "",
    busy: bool = False,
    allowance: Optional[dict[str, Any]] = None,
    specialty: str = "",
    skills: Optional[list[str]] = None,
) -> str:
    data: dict[str, Any] = {
        "xlii": FARM_KIND,
        "op": OP_OFFER,
        "node": node,
        "offers": list(offers),
        "gig": gig,
        "busy": bool(busy),
        "allowance": dict(allowance or {}),
    }
    if specialty:
        data["specialty"] = specialty
    if skills:
        data["skills"] = list(skills)
    return json.dumps(data)


def parse_farm_body(text: str) -> Optional[tuple[str, dict[str, Any]]]:
    raw = (text or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("xlii") != FARM_KIND:
        return None
    op = str(data.get("op") or "").strip()
    if op not in KNOWN_OPS:
        return None
    return op, data


def _parse_at(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class RoomLedger:
    """In-memory view of ads / claims / results seen in the room (or tests)."""

    def __init__(self) -> None:
        self.ads: dict[str, Ticket] = {}
        self.claims: dict[str, list[tuple[str, str]]] = {}  # id -> [(at, node)]
        self.results: dict[str, JobResult] = {}
        self.cancelled: set[str] = set()
        self.benched: dict[str, str] = {}  # node -> until ("" = until further notice)
        self.offers: dict[str, dict[str, Any]] = {}  # node -> last offer envelope

    def apply_body(self, text: str, *, owner: bool = False) -> Optional[str]:
        """Apply a farm envelope.

        Steer ops (``ad`` / ``cancel`` / ``bench``) are no-ops unless *owner*
        is true. The caller must have checked MUC affiliation — JSON ``by``
        is never authority.
        """
        parsed = parse_farm_body(text)
        if parsed is None:
            return None
        op, data = parsed
        if op in STEER_OPS and not owner:
            return None
        if op == OP_AD:
            ticket = Ticket.from_dict(data.get("ticket") or data)
            self.ads[ticket.id] = ticket
        elif op == OP_CLAIM:
            job_id = str(data.get("id") or "").strip()
            node = str(data.get("node") or "").strip()
            at = str(data.get("at") or utc_now())
            if job_id and node:
                self.claims.setdefault(job_id, []).append((at, node))
        elif op == OP_RESULT:
            result = JobResult.from_dict(data.get("result") or data)
            if result.id:
                self.results[result.id] = result
        elif op == OP_CANCEL:
            job_id = str(data.get("job_id") or "").strip()
            if job_id:
                self.cancelled.add(job_id)
        elif op == OP_BENCH:
            node = str(data.get("node") or "").strip()
            if node:
                self.benched[node] = str(data.get("until") or "")
        elif op == OP_OFFER:
            node = str(data.get("node") or "").strip()
            if node:
                self.offers[node] = data
        return op

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self.cancelled

    def is_benched(self, node: str) -> bool:
        return node in self.benched

    def winner(self, job_id: str, *, now: Optional[datetime] = None) -> Optional[str]:
        ticket = self.ads.get(job_id)
        rows = list(self.claims.get(job_id) or [])
        if not rows:
            return None
        now = now or datetime.now(timezone.utc)
        ttl = ticket.claim_ttl_s if ticket is not None else 300
        live: list[tuple[datetime, str]] = []
        for at, node in rows:
            when = _parse_at(at)
            if (now - when).total_seconds() <= ttl:
                live.append((when, node))
        if not live:
            return None
        live.sort(key=lambda r: (r[0], r[1]))
        return live[0][1]

    def open_tickets(self) -> list[Ticket]:
        out: list[Ticket] = []
        for ticket in self.ads.values():
            if ticket.id in self.cancelled:
                continue
            if ticket.id in self.results:
                continue
            if self.winner(ticket.id):
                continue
            out.append(ticket)
        return out
