"""Read-only projection of the house job board (classifieds C4/C5).

Pools are rooms. A ticket in a room this node is not in is *absent*,
not labelled hidden.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from xlii.farm import Ticket
from xlii.farm_muc import RoomLedger
from xlii.job_board import JobBoard


def _age(created_at: str, now: datetime) -> str:
    if not created_at:
        return ""
    try:
        dt = datetime.fromisoformat(created_at)
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def _budget(ticket: Ticket) -> str:
    b = ticket.budget
    parts = []
    if b.max_usd is not None:
        parts.append(f"${b.max_usd:g}")
    parts.append(f"{b.max_iters}it")
    return " ".join(parts)


def _ad_state(ledger: RoomLedger, ticket: Ticket) -> str:
    if ledger.is_cancelled(ticket.id):
        return "cancelled"
    if ticket.id in ledger.results:
        return "done"
    winner = ledger.winner(ticket.id)
    if winner:
        return "claimed"
    return "open"


@dataclass(frozen=True)
class FarmAdRow:
    pool: str
    ticket_id: str
    job: str
    state: str
    poster: str
    budget: str
    claimant: str
    age: str
    task: str = ""


@dataclass(frozen=True)
class FarmNodeRow:
    pool: str
    node: str
    offers: str
    gig: str
    allowance: str
    busy: str


@dataclass
class FarmView:
    """Both sides of the board. *joined_pools* is the rooms this node sits in."""

    pools: tuple[str, ...] = ()
    joined_pools: tuple[str, ...] = ()
    ads: tuple[FarmAdRow, ...] = ()
    nodes: tuple[FarmNodeRow, ...] = ()
    is_owner: bool = False


def build_farm_view(
    *,
    ledgers: dict[str, RoomLedger],
    joined_pools: Optional[set[str]] = None,
    is_owner: bool = False,
    now: Optional[datetime] = None,
    boards: Optional[dict[str, JobBoard]] = None,
) -> FarmView:
    """Project ledgers (and optional file boards) for rooms this node is in.

    *ledgers* keys are pool names (room JIDs). A pool not in *joined_pools*
    contributes nothing — the ticket is absent, not hidden.
    """
    now = now or datetime.now(timezone.utc)
    joined = set(joined_pools) if joined_pools is not None else set(ledgers)
    ads: list[FarmAdRow] = []
    nodes: list[FarmNodeRow] = []
    pools: list[str] = []
    for pool in sorted(ledgers):
        if pool not in joined:
            continue
        pools.append(pool)
        led = ledgers[pool]
        tickets = dict(led.ads)
        if boards and pool in boards:
            board = boards[pool]
            for t in list(board.list_open()) + list(board.list_claimed()):
                tickets.setdefault(t.id, t)
        for ticket in tickets.values():
            winner = led.winner(ticket.id) or ticket.claimed_by
            ads.append(FarmAdRow(
                pool=pool,
                ticket_id=ticket.id,
                job=ticket.job,
                state=_ad_state(led, ticket),
                poster=ticket.posted_by,
                budget=_budget(ticket),
                claimant=winner or "",
                age=_age(ticket.created_at, now),
                task=" ".join(ticket.task.split()),
            ))
        seen_nodes: set[str] = set()
        for node, data in led.offers.items():
            seen_nodes.add(node)
            allow = data.get("allowance") or {}
            usd = allow.get("usd_left")
            est = allow.get("per_iter_est")
            allow_s = ""
            if usd is not None:
                allow_s = f"${usd:g} left"
            if est is not None:
                allow_s = (allow_s + " " if allow_s else "") + f"${est:g}/it"
            busy = "benched" if led.is_benched(node) else (
                "busy" if data.get("busy") else "idle"
            )
            offers = data.get("offers") or []
            if isinstance(offers, str):
                offers = [offers]
            nodes.append(FarmNodeRow(
                pool=pool,
                node=node,
                offers=",".join(str(x) for x in offers),
                gig=str(data.get("gig") or ""),
                allowance=allow_s,
                busy=busy,
            ))
        for node in led.benched:
            if node not in seen_nodes:
                nodes.append(FarmNodeRow(
                    pool=pool, node=node, offers="", gig="",
                    allowance="", busy="benched",
                ))
    ads.sort(key=lambda r: (r.pool, r.age, r.ticket_id))
    nodes.sort(key=lambda r: (r.pool, r.node))
    return FarmView(
        pools=tuple(pools),
        joined_pools=tuple(sorted(joined)),
        ads=tuple(ads),
        nodes=tuple(nodes),
        is_owner=is_owner,
    )


def ambient_farm_view(*, is_owner: bool = False) -> FarmView:
    """Best-effort view from the local file board + live runtime if attached."""
    from xlii.config import GlobalConfig
    from xlii.farm import job_board_root, job_muc_room

    cfg = GlobalConfig.load()
    pool = job_muc_room(cfg) or "file"
    board = JobBoard(job_board_root(cfg))
    ledger = RoomLedger()
    try:
        board.ensure()
        for t in list(board.list_open()) + list(board.list_claimed()):
            ledger.ads[t.id] = t
            if t.claimed_by:
                ledger.claims.setdefault(t.id, []).append(
                    (t.claimed_at or "", t.claimed_by),
                )
    except Exception:
        pass
    return build_farm_view(
        ledgers={pool: ledger},
        joined_pools={pool},
        is_owner=is_owner,
        boards={pool: board},
    )
