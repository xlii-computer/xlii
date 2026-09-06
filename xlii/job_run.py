"""Run a claimed advisory job on this box (named kit only).

v0: ``explore`` + the node's configured gig (or home xAI if no gig).
Never bash, never yolo, never nested hire. Brief workplace gets an empty
local-only stub project so file tools have nowhere interesting to go.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

from xlii.config import ProjectConfig
from xlii.job_board import JobBoard
from xlii.farm import (
    PUBLISHED_JOBS,
    WORKPLACE_BRIEF,
    WORKPLACE_LOCAL,
    JobResult,
    Ticket,
    job_gig,
    job_node_name,
    job_projects,
    skip_reason,
    utc_now,
)

CANCELLED_SENTINEL = "(job cancelled)"  # keep in sync with worker_agent.CANCELLED_SENTINEL


def _brief_project(root: Path) -> ProjectConfig:
    (root / ".xlii").mkdir(parents=True, exist_ok=True)
    return ProjectConfig(
        project_root=root,
        name="job-brief",
        collection_id="",
        created_at=utc_now(),
        local_only=True,
        conversation_id="job-brief",
    )


def resolve_workplace(ticket: Ticket, cfg: Any) -> ProjectConfig:
    if ticket.workplace.mode == WORKPLACE_BRIEF:
        import tempfile

        return _brief_project(Path(tempfile.mkdtemp(prefix="xlii-job-brief-")))
    if ticket.workplace.mode != WORKPLACE_LOCAL:
        raise RuntimeError(f"workplace {ticket.workplace.mode!r} is not runnable")
    name = ticket.workplace.project
    path = job_projects(cfg)[name]
    project = ProjectConfig.load(path)
    if project is None:
        # A checkout without .xlii is still a tree explore can read.
        return ProjectConfig(
            project_root=path,
            name=name,
            collection_id="",
            created_at=utc_now(),
            local_only=True,
            conversation_id=f"job-{name}",
        )
    return project


def _gig_backend(cfg: Any):
    name = job_gig(cfg)
    if not name:
        return None
    from xlii.chat_backend import resolve_gig_backend

    return resolve_gig_backend(cfg, name)


def _clients_for_run(cfg: Any, backend: Any):
    """Gig-only boxes have no xAI pool; the worker brain is the backend."""
    if backend is not None:
        return SimpleNamespace(label=getattr(backend, "label", "gig"), chat=None, xai=None)
    from xlii.pool import ClientPool

    return ClientPool.from_config(cfg).primary()


def default_worker_factory(cfg: Any, project: ProjectConfig, backend: Any):
    from xlii.worker_agent import WorkerAgent

    return WorkerAgent(
        clients=_clients_for_run(cfg, backend),
        project=project,
        cfg=cfg,
        role="explore",
        worker_writes=False,
        yolo=False,
        chat_backend=backend,
        subscribed_plugins=[],
    )


def _cancelled_result(ticket: Ticket, node: str) -> JobResult:
    return JobResult(
        id=ticket.id, status="cancelled", node=node, job=ticket.job,
        reason="cancelled", finished_at=utc_now(),
    )


def run_ticket(
    ticket: Ticket,
    cfg: Any,
    *,
    worker_factory: Optional[Callable] = None,
    cancelled: Optional[Callable[[], bool]] = None,
    node: str = "",
) -> JobResult:
    """Execute a ticket this box already claimed. Always returns a result.

    *cancelled* is checked before the worker starts and (when the worker
    honours ``should_stop``) at the start of each iteration, so a board
    cancel aborts within one iteration.

    *node* is the occupancy nick this runtime joined as. Empty falls back
    to ``jobs.node`` (or the literal ``"node"``).
    """
    node = str(node or "").strip() or job_node_name(cfg)
    if cancelled is not None and cancelled():
        return _cancelled_result(ticket, node)
    reason = skip_reason(ticket, cfg)
    if reason:
        return JobResult(
            id=ticket.id, status="skipped", node=node, job=ticket.job,
            reason=reason, finished_at=utc_now(),
        )
    if ticket.job not in PUBLISHED_JOBS:
        return JobResult(
            id=ticket.id, status="skipped", node=node, job=ticket.job,
            reason=f"job {ticket.job!r} is not published", finished_at=utc_now(),
        )
    try:
        project = resolve_workplace(ticket, cfg)
    except Exception as e:
        return JobResult(
            id=ticket.id, status="error", node=node, job=ticket.job,
            reason=f"{type(e).__name__}: {e}", finished_at=utc_now(),
        )
    factory = worker_factory or default_worker_factory
    backend = None
    if worker_factory is None:
        try:
            backend = _gig_backend(cfg)
        except Exception as e:
            return JobResult(
                id=ticket.id, status="error", node=node, job=ticket.job,
                reason=f"{type(e).__name__}: {e}", finished_at=utc_now(),
            )
    worker = factory(cfg, project, backend)
    task = ticket.task
    if ticket.accept:
        task = task + "\n\nAcceptance:\n" + ticket.accept
    context = ticket.context or None
    try:
        try:
            text, call = worker.run(
                task, context=context, max_iterations=ticket.budget.max_iters,
                should_stop=cancelled,
            )
        except TypeError:
            text, call = worker.run(
                task, context=context, max_iterations=ticket.budget.max_iters,
            )
    except Exception as e:
        return JobResult(
            id=ticket.id, status="error", node=node, job=ticket.job,
            reason=f"{type(e).__name__}: {e}", finished_at=utc_now(),
        )
    if cancelled is not None and cancelled():
        return _cancelled_result(ticket, node)
    if text == CANCELLED_SENTINEL:
        return _cancelled_result(ticket, node)
    spent = getattr(call, "cost_usd", None)
    return JobResult(
        id=ticket.id,
        status="done",
        node=node,
        job=ticket.job,
        text=text or "",
        spent_usd=spent,
        finished_at=utc_now(),
    )


def pick_and_run(
    cfg: Any,
    board: JobBoard,
    *,
    worker_factory: Optional[Callable] = None,
) -> Optional[JobResult]:
    """Expire stale claims, claim the first eligible open ad, run it."""
    board.expire_stale()
    node = job_node_name(cfg)
    for ticket in board.list_open():
        if skip_reason(ticket, cfg):
            continue
        claimed = board.claim(ticket.id, node)
        if claimed is None:
            continue
        result = run_ticket(
            claimed, cfg, worker_factory=worker_factory,
            cancelled=lambda: board.is_cancelled(claimed.id),
            node=node,
        )
        board.complete(claimed, result)
        return result
    return None
