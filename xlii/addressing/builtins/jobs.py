"""JobsProvider provider."""
from __future__ import annotations


from xlii.addressing import (
    Address,
    Node,
    Resolution,
    ShellExport,
)


class JobsProvider:
    """``jobs://`` — the session's background jobs as a browseable list (the live ``[jobs N]`` chip's
    Pane-2 destination).

    ``jobs://`` lists every job in dispatch order (glyph · id · status · label); ``jobs://<id>`` reads
    that job's detail (kind, status, elapsed, sub-status, and result or error). Read-only: jobs are
    spawned by tasks / the Conductor and cancelled via ``/jobs``. Reaches the live registry through the
    ambient session (:mod:`xlii.active_session`); outside a session it lists empty. The ``[jobs N]``
    chip's *count* comes from the same registry (see ``xlii.tui.status._jobs_doorway``); this provider
    is what clicking it opens. Nodes carry ``type=job``.
    """

    scheme = "jobs"

    def resolve(self, address: Address) -> Resolution:
        jid = address.key.strip()
        if not jid:
            return Resolution(ok=True, address=address, kind="jobs")  # the browseable root
        job = self._job(jid)
        return Resolution(ok=job is not None, address=address, kind="jobs",
                          reason="" if job is not None else f"no job {jid!r}")

    def stat(self, address: Address) -> Node:
        jid = address.key.strip()
        return Node(address=str(address), name=jid or "jobs",
                    kind="container" if not jid else "leaf", extra={"type": "job"})

    def list(self, address: Address) -> "list[Node]":
        if address.key.strip():
            return []
        from xlii.active_session import active_registry

        reg = active_registry()
        if reg is None:
            return []
        out: list[Node] = []
        for j in reg.jobs():
            label = j.name or j.kind
            out.append(Node(
                address=f"jobs://{j.job_id}",
                name=f"{j.glyph} {j.job_id}  {label}  ·  {j.status}",
                kind="leaf",
                extra={"type": "job", "status": j.status, "job_kind": j.kind},
            ))
        return out

    def read(self, address: Address) -> bytes:
        jid = address.key.strip()
        if not jid:
            raise IsADirectoryError("jobs://: a jobs root — use ls")
        job = self._job(jid)
        if job is None:
            raise FileNotFoundError(f"jobs://{jid}: no such job")
        return _render_job(job).encode()

    def exists(self, address: Address) -> bool:
        jid = address.key.strip()
        return True if not jid else self._job(jid) is not None

    def shell_export(self, address: Address) -> ShellExport:
        if not address.key.strip():
            return ShellExport(kind="address")  # the jobs root — a live registry, no file behind it
        # A job lives in the session registry — a subprocess can't re-resolve the
        # address, so snapshot the rendered report (a freeze-frame, not a live view).
        return ShellExport(kind="content", content=self.read(address), suffix=".md")

    @staticmethod
    def _job(jid: str):
        from xlii.active_session import active_registry

        reg = active_registry()
        return reg.get(jid) if reg is not None else None


def _render_job(job) -> str:
    """Render one :class:`~xlii.jobs.BackgroundJob` as readable markdown for the view pane.

    Captures the job's result for Story #1 — a ``PipelineOutcome`` gets a structured
    carry/ok block instead of a bare ``repr``."""
    lines = [
        f"# job {job.job_id} — {job.name or job.kind}",
        "",
        f"- kind: {job.kind}",
        f"- status: {job.glyph} {job.status}",
        f"- elapsed: {job.elapsed():.0f}s",
    ]
    if job.progress:
        done, total = job.progress
        lines.append(f"- progress: {done}/{total}")
    if job.detail:
        lines.append(f"- detail: {job.detail}")
    if job.error:
        lines += ["", "## error", "", str(job.error)]
    elif job.result is not None:
        lines += ["", "## result", ""] + _format_job_result(job.result)
    return "\n".join(lines).rstrip() + "\n"


def _format_job_result(result: object) -> list[str]:
    """Prefer a structured PipelineOutcome dump; otherwise ``str(result)``."""
    # Duck-type xlii.tasks.PipelineOutcome without importing the module at load time.
    if (
        hasattr(result, "ok")
        and hasattr(result, "carry")
        and hasattr(result, "steps")
        and hasattr(result, "had_errors")
    ):
        out = [
            f"- ok: {bool(result.ok)}",
            f"- had_errors: {bool(result.had_errors)}",
            f"- steps: {len(getattr(result, 'steps', ()) or ())}",
        ]
        failed = getattr(result, "failed_index", None)
        if failed is not None:
            out.append(f"- failed_at_step: {int(failed) + 1}")
        carry = getattr(result, "carry", "") or ""
        out += ["", "### carry", "", carry if carry else "(empty)"]
        return out
    return [str(result)]


