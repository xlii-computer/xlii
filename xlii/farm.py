"""Named advisory farm tickets (P0).

The ad is the product. A node offers *names* (``explore``), never a tool
list and never a guest shell. Transport (file board, later MUC) only
carries these documents. Distinct from :mod:`xlii.jobs` (session background
jobs / ``/jobs``).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


PUBLISHED_JOBS = frozenset({"explore"})
REFUSED_JOBS = frozenset({"bash", "lab", "general", "cursor", "claude", "harness"})
# M6: sellable across thrones. Write-shaped names never cross.
MARKET_JOBS = frozenset({"explore", "summarize", "review", "translate", "render"})
MARKET_REFUSED_JOBS = REFUSED_JOBS | {"stage", "lab"}

KIND_ADVISORY = "advisory"
KIND_MARKET = "market"
WORKPLACE_BRIEF = "brief"
WORKPLACE_LOCAL = "local-project"
TICKET_VERSION = 1

DEFAULT_CLAIM_TTL_S = 300
DEFAULT_MAX_ITERS = 8


class JobError(ValueError):
    """Malformed or refused ticket."""


# Board filenames are `{id}.json` under open/claimed/done. Refuse anything
# that can walk out of those lanes (slash, `..`, NUL). uuid4.hex matches.
_JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")


def valid_job_id(job_id: str) -> bool:
    """True if *job_id* is a safe board filename stem (no path escape)."""
    if not job_id or ".." in job_id or "/" in job_id or "\\" in job_id:
        return False
    if "\x00" in job_id:
        return False
    return _JOB_ID_RE.fullmatch(job_id) is not None


def new_job_id() -> str:
    return uuid.uuid4().hex


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def jobs_cfg(cfg: Any) -> dict[str, Any]:
    raw = getattr(cfg, "jobs", None) or {}
    return dict(raw) if isinstance(raw, dict) else {}


def job_offers(cfg: Any) -> frozenset[str]:
    raw = jobs_cfg(cfg).get("offers") or []
    if isinstance(raw, str):
        raw = [raw]
    names = [str(x).strip() for x in raw if str(x).strip()]
    return frozenset(n for n in names if n in PUBLISHED_JOBS)


def job_gig(cfg: Any) -> str:
    return str(jobs_cfg(cfg).get("gig") or "").strip()


def job_projects(cfg: Any) -> dict[str, Path]:
    raw = jobs_cfg(cfg).get("projects") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Path] = {}
    for name, path in raw.items():
        key = str(name).strip()
        if not key or path in (None, ""):
            continue
        out[key] = Path(str(path)).expanduser()
    return out


def job_node_name(cfg: Any, fallback: str = "") -> str:
    return (
        str(jobs_cfg(cfg).get("node") or "").strip()
        or str(fallback or "").strip()
        or "node"
    )


def job_muc_room(cfg: Any) -> str:
    """Members-only MUC on the existing hub. Empty = file board only."""
    return str(jobs_cfg(cfg).get("muc") or "").strip()


def job_board_root(cfg: Any) -> Path:
    raw = str(jobs_cfg(cfg).get("board") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".xlii" / "jobs"


@dataclass
class Workplace:
    mode: str = WORKPLACE_BRIEF
    project: str = ""
    rev: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"mode": self.mode}
        if self.project:
            d["project"] = self.project
        if self.rev:
            d["rev"] = self.rev
        return d

    @classmethod
    def from_dict(cls, raw: Any) -> "Workplace":
        data = raw if isinstance(raw, dict) else {}
        mode = str(data.get("mode") or WORKPLACE_BRIEF).strip() or WORKPLACE_BRIEF
        return cls(
            mode=mode,
            project=str(data.get("project") or "").strip(),
            rev=str(data.get("rev") or "").strip(),
        )


@dataclass
class Allowance:
    """What a node says it has left to spend — mirrors :class:`Budget` vocabulary.

    Comparison is ``ad.max_usd ≤ node.usd_left``, not an auction.
    """

    usd_left: Optional[float] = None
    per_iter_est: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.usd_left is not None:
            d["usd_left"] = self.usd_left
        if self.per_iter_est is not None:
            d["per_iter_est"] = self.per_iter_est
        return d

    @classmethod
    def from_dict(cls, raw: Any) -> "Allowance":
        data = raw if isinstance(raw, dict) else {}
        def _f(key: str) -> Optional[float]:
            val = data.get(key)
            try:
                return float(val) if val is not None and val != "" else None
            except (TypeError, ValueError):
                return None
        return cls(usd_left=_f("usd_left"), per_iter_est=_f("per_iter_est"))


def job_allowance(cfg: Any) -> Allowance:
    raw = jobs_cfg(cfg).get("allowance") or {}
    return Allowance.from_dict(raw)


def job_specialty(cfg: Any) -> str:
    return str(jobs_cfg(cfg).get("specialty") or "").strip()


def job_skills(cfg: Any) -> list[str]:
    raw = jobs_cfg(cfg).get("skills") or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(x).strip() for x in raw if str(x).strip()]


@dataclass
class Budget:
    max_usd: Optional[float] = None
    max_iters: int = DEFAULT_MAX_ITERS

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"max_iters": self.max_iters}
        if self.max_usd is not None:
            d["max_usd"] = self.max_usd
        return d

    @classmethod
    def from_dict(cls, raw: Any) -> "Budget":
        data = raw if isinstance(raw, dict) else {}
        max_usd = data.get("max_usd")
        try:
            usd = float(max_usd) if max_usd is not None and max_usd != "" else None
        except (TypeError, ValueError):
            usd = None
        try:
            iters = int(data.get("max_iters") or DEFAULT_MAX_ITERS)
        except (TypeError, ValueError):
            iters = DEFAULT_MAX_ITERS
        return cls(max_usd=usd, max_iters=max(1, min(iters, 20)))


@dataclass
class Ticket:
    id: str
    job: str
    task: str
    kind: str = KIND_ADVISORY
    v: int = TICKET_VERSION
    context: str = ""
    accept: str = ""
    workplace: Workplace = field(default_factory=Workplace)
    budget: Budget = field(default_factory=Budget)
    claim_ttl_s: int = DEFAULT_CLAIM_TTL_S
    posted_by: str = "throne"
    created_at: str = ""
    claimed_by: str = ""
    claimed_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "v": self.v,
            "id": self.id,
            "kind": self.kind,
            "job": self.job,
            "task": self.task,
            "context": self.context,
            "accept": self.accept,
            "workplace": self.workplace.to_dict(),
            "budget": self.budget.to_dict(),
            "claim_ttl_s": self.claim_ttl_s,
            "from": self.posted_by,
            "created_at": self.created_at,
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at,
        }

    def dump(self) -> str:
        return json.dumps(self.to_dict(), indent=2) + "\n"

    @classmethod
    def from_dict(cls, raw: Any) -> "Ticket":
        if not isinstance(raw, dict):
            raise JobError("ticket must be a JSON object")
        try:
            v = int(raw.get("v") or 0)
        except (TypeError, ValueError):
            v = 0
        if v != TICKET_VERSION:
            raise JobError(f"unsupported ticket version {raw.get('v')!r}")
        tid = str(raw.get("id") or "").strip()
        job = str(raw.get("job") or "").strip()
        task = str(raw.get("task") or "").strip()
        if not tid:
            raise JobError("ticket id is required")
        if not valid_job_id(tid):
            raise JobError(f"invalid ticket id {tid!r}")
        if not job:
            raise JobError("ticket job is required")
        if not task:
            raise JobError("ticket task is required")
        kind = str(raw.get("kind") or KIND_ADVISORY).strip() or KIND_ADVISORY
        try:
            ttl = int(raw.get("claim_ttl_s") or DEFAULT_CLAIM_TTL_S)
        except (TypeError, ValueError):
            ttl = DEFAULT_CLAIM_TTL_S
        return cls(
            v=v,
            id=tid,
            kind=kind,
            job=job,
            task=task,
            context=str(raw.get("context") or ""),
            accept=str(raw.get("accept") or ""),
            workplace=Workplace.from_dict(raw.get("workplace")),
            budget=Budget.from_dict(raw.get("budget")),
            claim_ttl_s=max(30, min(ttl, 3600)),
            posted_by=str(raw.get("from") or "throne").strip() or "throne",
            created_at=str(raw.get("created_at") or ""),
            claimed_by=str(raw.get("claimed_by") or "").strip(),
            claimed_at=str(raw.get("claimed_at") or "").strip(),
        )

    @classmethod
    def from_json(cls, text: str) -> "Ticket":
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            raise JobError(f"invalid JSON: {e}") from e
        return cls.from_dict(raw)

    @classmethod
    def make(
        cls,
        *,
        job: str,
        task: str,
        context: str = "",
        accept: str = "",
        workplace: Optional[Workplace] = None,
        budget: Optional[Budget] = None,
        posted_by: str = "throne",
        claim_ttl_s: int = DEFAULT_CLAIM_TTL_S,
        kind: str = KIND_ADVISORY,
    ) -> "Ticket":
        job = job.strip()
        task = task.strip()
        kind = (kind or KIND_ADVISORY).strip() or KIND_ADVISORY
        if kind == KIND_MARKET:
            _refuse_market(job=job, task=task, context=context, workplace=workplace)
        elif job not in PUBLISHED_JOBS:
            raise JobError(
                f"job {job!r} is not a published farm job "
                f"(v0: {', '.join(sorted(PUBLISHED_JOBS))})"
            )
        if not task:
            raise JobError("task is required")
        wp = workplace or Workplace()
        if wp.mode not in (WORKPLACE_BRIEF, WORKPLACE_LOCAL):
            raise JobError(f"workplace.mode {wp.mode!r} is not allowed in v0")
        if wp.mode == WORKPLACE_LOCAL and not wp.project:
            raise JobError("local-project workplace requires a project name")
        return cls(
            id=new_job_id(),
            job=job,
            task=task,
            kind=kind,
            context=context,
            accept=accept,
            workplace=wp,
            budget=budget or Budget(),
            posted_by=posted_by,
            created_at=utc_now(),
            claim_ttl_s=claim_ttl_s,
        )


@dataclass
class JobResult:
    id: str
    status: str
    node: str
    job: str
    text: str = ""
    reason: str = ""
    spent_usd: Optional[float] = None
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "v": TICKET_VERSION,
            "id": self.id,
            "status": self.status,
            "node": self.node,
            "job": self.job,
            "text": self.text,
            "reason": self.reason,
            "finished_at": self.finished_at or utc_now(),
        }
        if self.spent_usd is not None:
            d["spent_usd"] = self.spent_usd
        return d

    def dump(self) -> str:
        return json.dumps(self.to_dict(), indent=2) + "\n"

    @classmethod
    def from_dict(cls, raw: Any) -> "JobResult":
        if not isinstance(raw, dict):
            raise JobError("result must be a JSON object")
        spent = raw.get("spent_usd")
        try:
            spent_f = float(spent) if spent is not None else None
        except (TypeError, ValueError):
            spent_f = None
        rid = str(raw.get("id") or "").strip()
        if rid and not valid_job_id(rid):
            raise JobError(f"invalid result id {rid!r}")
        return cls(
            id=rid,
            status=str(raw.get("status") or "").strip() or "error",
            node=str(raw.get("node") or "").strip(),
            job=str(raw.get("job") or "").strip(),
            text=str(raw.get("text") or ""),
            reason=str(raw.get("reason") or ""),
            spent_usd=spent_f,
            finished_at=str(raw.get("finished_at") or ""),
        )


def _refuse_market(
    *, job: str, task: str, context: str, workplace: Optional[Workplace],
) -> None:
    if not task.strip():
        raise JobError("task is required")
    if (context or "").strip():
        raise JobError("market tickets refuse context")
    if job in MARKET_REFUSED_JOBS:
        raise JobError(f"job {job!r} is refused across thrones")
    if job not in MARKET_JOBS:
        raise JobError(
            f"job {job!r} is not a market good "
            f"({', '.join(sorted(MARKET_JOBS))})"
        )
    wp = workplace or Workplace()
    if wp.mode == WORKPLACE_LOCAL:
        raise JobError("market tickets refuse local-project workplace")
    if wp.mode != WORKPLACE_BRIEF:
        raise JobError(f"workplace.mode {wp.mode!r} is not allowed on the market")


def skip_reason(ticket: Ticket, cfg: Any) -> Optional[str]:
    """Why this box must not claim *ticket*. None = eligible."""
    if ticket.kind == KIND_MARKET:
        return market_skip_reason(ticket, cfg)
    if ticket.kind != KIND_ADVISORY:
        return f"kind {ticket.kind!r} is not advisory"
    if ticket.job in REFUSED_JOBS:
        return f"job {ticket.job!r} is never offerable"
    if ticket.job not in PUBLISHED_JOBS:
        return f"job {ticket.job!r} is not published"
    offers = job_offers(cfg)
    if ticket.job not in offers:
        return f"job {ticket.job!r} is not in this box's offers"
    mode = ticket.workplace.mode
    if mode == WORKPLACE_BRIEF:
        return None
    if mode != WORKPLACE_LOCAL:
        return f"workplace.mode {mode!r} is not allowed"
    name = ticket.workplace.project
    projects = job_projects(cfg)
    path = projects.get(name)
    if path is None:
        return f"project {name!r} is not in jobs.projects"
    if not path.is_dir():
        return f"project {name!r} path does not exist: {path}"
    return None


def market_skip_reason(ticket: Ticket, cfg: Any) -> Optional[str]:
    """Claim-time refusals for a cross-throne ticket (M6)."""
    if (ticket.context or "").strip():
        return "market tickets refuse context"
    if ticket.job in MARKET_REFUSED_JOBS:
        return f"job {ticket.job!r} is refused across thrones"
    if ticket.job not in MARKET_JOBS:
        return f"job {ticket.job!r} is not a market good"
    if ticket.workplace.mode == WORKPLACE_LOCAL:
        return "market tickets refuse local-project workplace"
    if ticket.workplace.mode != WORKPLACE_BRIEF:
        return f"workplace.mode {ticket.workplace.mode!r} is not allowed on the market"
    raw = jobs_cfg(cfg).get("offers") or []
    if isinstance(raw, str):
        raw = [raw]
    names = {str(x).strip() for x in raw if str(x).strip()}
    if ticket.job not in names:
        return f"job {ticket.job!r} is not in this box's offers"
    return None
