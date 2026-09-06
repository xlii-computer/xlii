"""`/jobs` — list · inspect · cancel session-owned background jobs (Vector J).

The engine + registry live in :mod:`xlii.jobs`; this is the REPL surface only.
Jobs are dispatched by their producers (``/tasks --background`` today; III's
Conductor registers ``fleet`` jobs on the same registry tomorrow) and notify on
completion via the live console. Session-scoped — they die with the session,
never a daemon.

    /jobs            list active + recent jobs
    /jobs show <id>  one job's detail (+ result preview)
    /jobs cancel <id>  request cancellation (cooperative)
    /jobs clear      forget finished jobs
"""

from __future__ import annotations

from typing import Any, Optional

from rich.text import Text

from xlii import jobs as J
from xlii.commands import REPLCommand, register_repl_command


def _short(text: str, limit: int = 120) -> str:
    one = " ".join((text or "").split())
    return one if len(one) <= limit else one[: limit - 1] + "…"


def _result_text(result: Any) -> str:
    """The human-useful bit of a job result — a pipeline's carry, else ``str``."""
    carry = getattr(result, "carry", None)
    if carry is not None:
        return str(carry)
    return str(result) if result is not None else ""


def _job_text(job: J.BackgroundJob) -> Text:
    """One glanceable line: ``⠹ t1  task build  running  4s · A1 ✓ A2 ⠹``."""
    status_style = J._STATUS_STYLE.get(job.status, "")
    t = Text("  ")
    t.append(f"{job.glyph} ", style=status_style)
    t.append(job.job_id, style="cyan")
    t.append(f"  {job.kind}", style=J._KIND_STYLE.get(job.kind, ""))
    if job.name and job.name != job.kind:
        t.append(f" {job.name}", style="bold")
    t.append(f"  {job.status}", style=status_style or "dim")
    t.append(f"  {job.elapsed():.0f}s", style="dim")
    if job.progress:
        done, total = job.progress
        t.append(f"  {done}/{total}", style="dim")
    if job.detail:
        t.append(f"  · {job.detail}", style="dim")
    if job.error:
        t.append(f"  — {job.error}", style="red")
    return t


def _do_list(console: Any, reg: J.JobRegistry) -> None:
    jobs = reg.jobs()
    if not jobs:
        console.print("[dim]no background jobs — /tasks run --background '<pipe>' to start one[/dim]")
        return
    active = [j for j in jobs if j.active]
    head = f"[bold]background jobs[/bold] [dim]({len(active)} active · session-scoped)[/dim]"
    console.print(head)
    for job in jobs:
        console.print(_job_text(job))


def _do_show(console: Any, reg: J.JobRegistry, rest: str) -> None:
    job_id = rest.split()[0] if rest.split() else ""
    if not job_id:
        console.print("[dim]usage:[/dim] [cyan]/jobs show <id>[/cyan]")
        return
    job = reg.get(job_id)
    if job is None:
        console.print(f"[yellow]no job {job_id}[/yellow] [dim](/jobs to list)[/dim]")
        return
    console.print(_job_text(job))
    if job.status == J.DONE:
        preview = _short(_result_text(job.result))
        if preview:
            console.print(f"  [dim]result:[/dim] {preview}")


def _do_cancel(console: Any, reg: J.JobRegistry, rest: str) -> None:
    job_id = rest.split()[0] if rest.split() else ""
    if not job_id:
        console.print("[dim]usage:[/dim] [cyan]/jobs cancel <id>[/cyan]")
        return
    if reg.cancel(job_id):
        console.print(f"[dim][jobs] cancel requested for {job_id} "
                      "(running work finishes; its result is discarded)[/dim]")
    else:
        console.print(f"[yellow]nothing to cancel for {job_id}[/yellow] "
                      "[dim](unknown id, or already finished)[/dim]")


def _do_clear(console: Any, reg: J.JobRegistry) -> None:
    n = reg.clear_finished()
    console.print(f"[dim][jobs] cleared {n} finished job{'' if n == 1 else 's'}[/dim]")


_USAGE = (
    "[dim]usage:[/dim] [cyan]/jobs[/cyan] · [cyan]show <id>[/cyan] · "
    "[cyan]cancel <id>[/cyan] · [cyan]clear[/cyan]"
)


def _jobs_handler(line: str, ctx: dict[str, Any]) -> bool:
    console = ctx.get("console")
    if console is None:
        return True
    state = ctx.get("state")
    reg: Optional[J.JobRegistry] = J.get_registry(state)
    if reg is None:
        console.print("[dim](no live session — background jobs need a running REPL)[/dim]")
        return True

    parts = line.split(maxsplit=2)
    sub = parts[1].strip().lower() if len(parts) > 1 else "list"
    rest = parts[2].strip() if len(parts) > 2 else ""

    if sub in ("", "list", "ls"):
        _do_list(console, reg)
    elif sub in ("show", "info", "status"):
        _do_show(console, reg, rest)
    elif sub == "cancel":
        _do_cancel(console, reg, rest)
    elif sub == "clear":
        _do_clear(console, reg)
    elif sub in ("help", "-h", "--help"):
        console.print(_USAGE)
    else:
        console.print(f"[yellow]unknown /jobs subcommand: {sub}[/yellow]")
        console.print(_USAGE)
    return True


def register() -> None:
    # Visibility (Q4): register the frame-tab fallback through A1's published seam
    # so jobs show in the input-frame tabs even before S places the profile-bar
    # call line (J4). Best-effort no-op if the seam isn't present — safe day 1.
    J.register_job_seams()
    register_repl_command(
        REPLCommand(
            name="jobs",
            handler=_jobs_handler,
            description="List, inspect, or cancel session-owned background jobs (/tasks --background, fleets).",
            usage="/jobs [show <id> | cancel <id> | clear]",
            category="general",
            repls=["code", "chat"],
        )
    )
