"""Post a farm ad to a pool (classifieds C6).

Remote posts always confirm; the prompt names the pool and the budget.
``auto_deny`` → no ad.
"""

from __future__ import annotations

from typing import Any, Optional

from xlii.farm import (
    Budget,
    JobError,
    Ticket,
    job_board_root,
    job_muc_room,
    job_node_name,
)
from xlii.job_board import JobBoard


LOCAL_WHERE = frozenset({
    "", "this", "here", "local", "this body", "this-body", "this_body",
})


def resolve_where(cfg: Any, where: str) -> str:
    """Return a pool (room JID) to post to, or ``\"\"`` for this body.

    Unknown pool names fail — we do not post to a room this node is not in.
    """
    raw = (where or "").strip()
    if raw.lower() in LOCAL_WHERE:
        return ""
    room = job_muc_room(cfg)
    if not room:
        raise JobError(
            f"unknown pool {raw!r} — jobs.muc is empty on this body"
        )
    localpart = room.split("@", 1)[0]
    if raw in {room, localpart} or raw.lower() == "jobs":
        return room
    raise JobError(f"unknown pool {raw!r} (this body sits in {room})")


def _snippet(text: str, limit: int = 200) -> str:
    flat = " ".join((text or "").split())
    if len(flat) > limit:
        flat = flat[: limit - 3] + "…"
    return flat


def remote_post_prompt(
    *, job: str, pool: str, budget: Budget, task: str, context: str = "",
) -> str:
    """Name the pool, the budget, and everything that will ride the room.

    The ad is plaintext on the hub. ``context`` is part of it, so a yes must
    be a yes to the context too — it is shown (sized and sampled), never
    silently attached.
    """
    usd = "none" if budget.max_usd is None else f"{budget.max_usd:g}"
    task_text = task or ""
    lines = [
        f"post {job} job to pool {pool} with budget "
        f"max_usd={usd} max_iters={budget.max_iters}?",
        f"task ({len(task_text)} chars): {_snippet(task_text)}",
    ]
    if (context or "").strip():
        lines.append(
            f"context ({len(context)} chars, plaintext on the room): "
            f"{_snippet(context)}"
        )
    else:
        lines.append("context: none")
    lines.append("[y/N] ")
    return "\n".join(lines)


def confirm_remote_post(
    *, job: str, pool: str, budget: Budget, task: str, context: str = "",
) -> bool:
    """Always confirm. ``auto_deny`` returns a non-yes answer → False."""
    from xlii.tools import _confirm

    prompt = remote_post_prompt(
        job=job, pool=pool, budget=budget, task=task, context=context,
    )
    ans = _confirm(prompt)
    return str(ans or "").strip().lower() in {"y", "yes"}


def post_to_pool(
    cfg: Any,
    *,
    job: str,
    task: str,
    pool: str,
    budget: Optional[Budget] = None,
    context: str = "",
    accept: str = "",
    publish_muc: bool = True,
) -> Ticket:
    """Confirm, then write the ad to the file board (and MUC when configured)."""
    budget = budget or Budget()
    if not confirm_remote_post(
        job=job, pool=pool, budget=budget, task=task, context=context,
    ):
        raise JobError("remote post not confirmed — no ad")
    ticket = Ticket.make(
        job=job,
        task=task,
        context=context,
        accept=accept,
        budget=budget,
        posted_by=job_node_name(cfg),
    )
    board = JobBoard(job_board_root(cfg))
    board.post(ticket)
    if publish_muc and job_muc_room(cfg):
        try:
            from xlii.farm_xmpp import publish_ad
            publish_ad(cfg, ticket)
        except Exception:
            pass
    return ticket
