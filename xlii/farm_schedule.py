"""Scheduled farm poster (classifieds C7).

Workers stay residents. The only cron sits on the *poster*: a
``jobs.startup_ad`` spec fires at most once per calendar day (stamp file),
from daemon join or an explicit call. pr-watch stays an inbox producer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from xlii.farm import (
    DEFAULT_MAX_ITERS,
    Budget,
    Ticket,
    job_board_root,
    job_node_name,
    jobs_cfg,
)
from xlii.job_board import JobBoard


def startup_ad_spec(cfg: Any) -> Optional[dict[str, Any]]:
    raw = jobs_cfg(cfg).get("startup_ad")
    return dict(raw) if isinstance(raw, dict) and raw else None


def fire_scheduled_post(
    cfg: Any,
    *,
    now: Optional[datetime] = None,
    stamp_path: Optional[Path] = None,
) -> Optional[Ticket]:
    """Post the configured startup ad once per calendar day. None if idle."""
    spec = startup_ad_spec(cfg)
    if spec is None:
        return None
    task = str(spec.get("task") or "").strip()
    job = str(spec.get("job") or "explore").strip()
    if not task:
        return None
    now = now or datetime.now(timezone.utc)
    stamp = Path(stamp_path) if stamp_path is not None else (
        job_board_root(cfg) / "startup-ad.day"
    )
    day = now.date().isoformat()
    try:
        if stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == day:
            return None
    except OSError:
        pass
    usd = spec.get("max_usd")
    try:
        max_usd = float(usd) if usd is not None and usd != "" else None
    except (TypeError, ValueError):
        max_usd = None
    try:
        iters = int(spec.get("max_iters") or DEFAULT_MAX_ITERS)
    except (TypeError, ValueError):
        iters = DEFAULT_MAX_ITERS
    ticket = Ticket.make(
        job=job,
        task=task,
        budget=Budget(max_usd=max_usd, max_iters=iters),
        posted_by=job_node_name(cfg),
    )
    board = JobBoard(job_board_root(cfg))
    board.post(ticket)
    try:
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(day + "\n", encoding="utf-8")
    except OSError:
        pass
    return ticket
