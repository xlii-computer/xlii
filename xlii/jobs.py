"""Vector J — session-owned background jobs + the live status surface.

A *background job* is a unit of long work — a ``/tasks`` pipeline, a ``/loop``, a
harness session, or (Program III) a *fleet* of vector-sessions — run off the REPL
thread so the prompt stays responsive, tracked on one in-memory registry, and
surfaced live in the profile bar. **This is the substrate Program III's Conductor
drives** (``proposals/vector-orchestration.md``): II shipped the Fleet (launch N
sessions), III ships the brain, and this is the job bus between them — the
Conductor registers each fleet-vector session as a ``fleet`` job here, so a
"vectoring pass" shows up in the same profile-bar segment as a
``/tasks --background`` run. One engine, many callers.

Design contract (locked in ``proposals/interaction-layer-iii-parallel-build.md``):

* **Session-lived, never a daemon.** The :class:`JobRegistry` lives in-memory on
  ``REPLState.job_registry`` (J's half of the two-pointer; S adds ``no_sync``) and
  dies with the session — no cross-session reload. Per-pipeline durability stays
  ``/tasks resume``'s on-disk ``TaskRun``; only the *registry* is ephemeral.
* **Front-end-agnostic.** Jobs run on a :class:`~concurrent.futures.ThreadPoolExecutor`
  (the ``swarm.dispatch_writers`` pattern), capped by ``cfg.max_parallel_workers``
  — NOT Textual's ``@work`` — so the inline REPL, ``--tui``, and a headless
  ``xlii ask`` share one engine.
* **Two published seams (J3, day 1):**
    - :func:`set_job_listener` — a module-global repaint hook the TUI installs
      (``launch()``'s J6 line, placed by P), mirroring
      ``terminal_image.set_renderable_sink`` / ``preview.set_surface_host``. Fired
      on every job state change so the profile bar repaints live.
    - :func:`summary_segment` — the ``fleet · A1 ✓ A2 ⠹ B … · 3/6`` indicator S
      places in ``profile_bar`` (J4). A pure read: never creates the registry,
      never raises.
* **Visible standalone (Q4).** :func:`register_job_seams` also registers a
  frame-tab provider through A1's published ``register_frame_tab``, so jobs show
  in the input-frame folder tabs even before S places the profile-bar call.
"""

from __future__ import annotations

import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from rich.text import Text

# --------------------------------------------------------------------------- #
#  Vocabulary — job kinds (open set) + the status lifecycle
# --------------------------------------------------------------------------- #

# The four named clients. `kind` is deliberately a plain string, not an Enum, so
# III's Conductor can register a `fleet` job (and a community caller a new kind)
# on this same registry without J changing — the contract is "keep kind open".
KIND_TASK = "task"
KIND_LOOP = "loop"
KIND_HARNESS = "harness"
KIND_FLEET = "fleet"
KIND_TURN = "turn"      # bg-default P1: a normal agent turn, tracked (not pooled)
KIND_JOURNAL = "journal"  # fast exit: open-time catch-up of a deferred journal tail

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

_ACTIVE = (PENDING, RUNNING)

# Glyphs for the at-a-glance status surface (braille spinner for in-flight).
_GLYPH = {PENDING: "◦", RUNNING: "⠹", DONE: "✓", FAILED: "✗", CANCELLED: "⊘"}
_STATUS_STYLE = {
    PENDING: "dim",
    RUNNING: "yellow",
    DONE: "green",
    FAILED: "bold red",
    CANCELLED: "dim",
}
_KIND_STYLE = {
    KIND_TASK: "cyan",
    KIND_LOOP: "magenta",
    KIND_HARNESS: "blue",
    KIND_FLEET: "bold cyan",
    KIND_TURN: "green",
    KIND_JOURNAL: "yellow",
}


@dataclass
class BackgroundJob:
    """One tracked unit of background work.

    ``kind`` ∈ {task, loop, harness, fleet} (open), ``status`` walks
    pending → running → done|failed|cancelled. ``result`` / ``error`` hold the
    fn's return / failure; ``notify`` gates the on-completion console line.
    ``detail`` + ``progress`` let a *composite* job (a fleet) render its
    sub-status — ``A1 ✓ A2 ⠹ B …`` and ``3/6`` — in the status segment.
    """

    job_id: str
    kind: str
    name: str
    status: str = PENDING
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    result: Any = None
    error: Optional[str] = None
    notify: bool = True
    # Free-form sub-status line for a composite job (e.g. a fleet's per-vector
    # glyph row); None for a plain task/loop. Callers update it as work moves.
    detail: Optional[str] = None
    # (done, total) for a composite job, rendered as `3/6`; None otherwise.
    progress: Optional[tuple[int, int]] = None

    # Cancel is cooperative: we mark intent and best-effort cancel a not-yet-
    # started future, but never force-kill a running thread (we don't own fn's
    # internals). A long fn that wants to honour cancellation can poll the job.
    cancel_requested: bool = field(default=False, repr=False)
    _future: Any = field(default=None, repr=False, compare=False)

    @property
    def active(self) -> bool:
        return self.status in _ACTIVE

    @property
    def glyph(self) -> str:
        return _GLYPH.get(self.status, "?")

    def elapsed(self) -> float:
        """Seconds spent (running window, or since creation while pending)."""
        start = self.started_at if self.started_at is not None else self.created_at
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - start)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe view (drops the live future / result object)."""
        return {
            "job_id": self.job_id,
            "kind": self.kind,
            "name": self.name,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "detail": self.detail,
            "progress": list(self.progress) if self.progress else None,
            "notify": self.notify,
        }


# --------------------------------------------------------------------------- #
#  Seam J3a — the repaint listener (module-global, the set_renderable_sink shape)
# --------------------------------------------------------------------------- #

_JOB_LISTENER: Optional[Callable[[], None]] = None


def set_job_listener(cb: Optional[Callable[[], None]]) -> Optional[Callable[[], None]]:
    """Install the repaint hook fired on every job state change; return the
    previous one so the front-end can restore it on exit (the
    ``set_renderable_sink`` contract).

    The TUI installs ``lambda: app.call_from_thread(app._refresh_status)`` (the J6
    line P places in ``launch()``), so async job progress repaints the profile
    bar from the worker thread safely. A raising / absent listener is ignored —
    a repaint hook must never kill a job thread.
    """
    global _JOB_LISTENER
    prev = _JOB_LISTENER
    _JOB_LISTENER = cb
    return prev


def _fire_listener() -> None:
    cb = _JOB_LISTENER
    if cb is None:
        return
    try:
        cb()
    except Exception:
        # A failing surface listener must not break job bookkeeping.
        pass


# --------------------------------------------------------------------------- #
#  Background execution must be non-interactive (never hang a detached job)
# --------------------------------------------------------------------------- #

# Marks the current thread as a background job worker (thread-local, so a
# concurrent foreground gate on another thread is unaffected).
_BG = threading.local()

_confirm_lock = threading.Lock()
_confirm_depth = 0
_confirm_prev: Optional[Callable[..., str]] = None


@contextlib.contextmanager
def _noninteractive_confirm():
    """While active, a confirm raised **on a background job thread** fails closed
    (returns ``"n"``) instead of blocking on ``input()`` / popping a modal — a
    detached job must never hang waiting for a human, and an unconfirmed step
    must never run unsupervised. Confirms on *other* threads (a concurrent
    foreground gate) still delegate to the live ``xlii.tools._confirm``, so the
    interactive REPL is unaffected.

    Reference-counted: concurrent background jobs share one swap, and the live
    confirm captured at first-entry is restored only when the last finishes
    (composing with — not clobbering — the TUI's modal-backed confirm, which is
    installed at ``launch()`` time, before any job runs).
    """
    global _confirm_depth, _confirm_prev
    import xlii.tools as _tools

    with _confirm_lock:
        if _confirm_depth == 0:
            _confirm_prev = _tools._confirm
            captured = _confirm_prev

            def _shim(prompt: str = "") -> str:
                if getattr(_BG, "active", False):
                    return "n"
                return captured(prompt)

            _tools._confirm = _shim
        _confirm_depth += 1
    try:
        yield
    finally:
        with _confirm_lock:
            _confirm_depth -= 1
            if _confirm_depth == 0:
                _tools._confirm = _confirm_prev
                _confirm_prev = None


# --------------------------------------------------------------------------- #
#  The registry (J3) — the one session-owned home for every background job
# --------------------------------------------------------------------------- #

class JobRegistry:
    """In-memory registry of background jobs for one session.

    Lives on ``REPLState.job_registry`` (lazily created by :func:`get_registry`).
    Jobs run on a shared :class:`ThreadPoolExecutor` capped by the session's
    ``max_parallel_workers`` ceiling. Never persisted: it dies with the session
    (the "never an unsupervised daemon" contract).
    """

    def __init__(
        self,
        *,
        max_workers: int = 8,
        on_complete: Optional[Callable[[BackgroundJob], None]] = None,
    ) -> None:
        self._jobs: dict[str, BackgroundJob] = {}
        self._order: list[str] = []          # insertion order, for stable listing
        self._unseen_done: list[str] = []    # terminal jobs not yet viewed (done-tab)
        self._lock = threading.RLock()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._max_workers = max(1, int(max_workers or 1))
        self._seq = 0
        # Completion sink: writes the "✓ job done" line to the live console. Set
        # by get_registry so it reads state.console at fire time (the TUI swaps
        # the console in/out — reading late keeps the notice on the right surface).
        self.on_complete = on_complete

    # --- dispatch ----------------------------------------------------------

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=self._max_workers, thread_name_prefix="xlii-job"
            )
        return self._executor

    def dispatch(
        self,
        kind: str,
        name: str,
        fn: Callable[[], Any],
        *,
        notify: bool = True,
    ) -> str:
        """Run ``fn`` off-thread as a tracked job; return its short id (``t1`` …).

        ``fn`` takes no args and returns the job's result (anything). Its return
        value lands in ``job.result``; an exception is captured into
        ``job.error`` + status ``failed`` (a job never crashes the pool). The
        listener fires on dispatch (pending), start (running), and finish.
        """
        with self._lock:
            self._seq += 1
            job_id = f"{(kind or 'job')[:1]}{self._seq}"
            job = BackgroundJob(job_id=job_id, kind=kind, name=name, notify=notify)
            self._jobs[job_id] = job
            self._order.append(job_id)
            ex = self._ensure_executor()

        try:
            future = ex.submit(self._run, job, fn)
        except RuntimeError:
            # Executor already shut down (session tearing down) — run inline so
            # the work still completes and the job still resolves, never hangs.
            self._run(job, fn)
            return job_id
        job._future = future
        _fire_listener()  # surface it as pending immediately
        return job_id

    def _run(self, job: BackgroundJob, fn: Callable[[], Any]) -> None:
        if job.cancel_requested:
            self._finalize(job, CANCELLED)
            return
        job.status = RUNNING
        job.started_at = time.time()
        _fire_listener()
        status, result, error = DONE, None, None
        try:
            with _noninteractive_confirm():
                _BG.active = True
                try:
                    result = fn()
                finally:
                    _BG.active = False
            if job.cancel_requested:
                status = CANCELLED
        except Exception as e:  # noqa: BLE001 — a job's failure is data, not a crash
            status, error = FAILED, f"{type(e).__name__}: {e}"
        job.result = result if status == DONE else job.result
        job.error = error
        self._finalize(job, status)

    # --- externally-driven lifecycle (bg-default P1) -------------------------
    # A normal agent turn runs on the SURFACE's own thread (a Textual worker) so
    # trust gates keep prompting and test harnesses can await it — dispatch()'s
    # pool wraps fn in _noninteractive_confirm, which would auto-deny those
    # prompts. adopt/begin/finish give such work a first-class registry record
    # (visible in /jobs, the status fleet segment, and pane projections)
    # without moving its execution here.

    def adopt(self, kind: str, name: str, *, notify: bool = True) -> str:
        """Register a job whose execution the caller owns. Returns its id."""
        with self._lock:
            self._seq += 1
            job_id = f"{(kind or 'job')[:1]}{self._seq}"
            self._jobs[job_id] = BackgroundJob(
                job_id=job_id, kind=kind, name=name, notify=notify
            )
            self._order.append(job_id)
        _fire_listener()
        return job_id

    def begin(self, job_id: str) -> None:
        """Mark an adopted job running (caller is about to do the work)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != PENDING:
                return
            job.status = RUNNING
            job.started_at = time.time()
        _fire_listener()

    def finish(self, job_id: str, *, error: Optional[str] = None,
               cancelled: bool = False, result: Any = None) -> None:
        """Resolve an adopted job (done / failed / cancelled)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in _ACTIVE:
                return
        if error is not None:
            job.error = error
            self._finalize(job, FAILED)
        elif cancelled or job.cancel_requested:
            self._finalize(job, CANCELLED)
        else:
            job.result = result
            self._finalize(job, DONE)

    def _finalize(self, job: BackgroundJob, status: str) -> None:
        job.status = status
        job.finished_at = time.time()
        with self._lock:
            # Done-tab: a completed-unseen chip rides the input frame until the
            # user opens jobs://<id> (mark_seen). Cancelled counts as finished too.
            if job.job_id not in self._unseen_done:
                self._unseen_done.append(job.job_id)
        _fire_listener()
        if job.notify and self.on_complete is not None:
            try:
                self.on_complete(job)
            except Exception:
                # The job is already finalized; only the completion callback is lost.
                pass

    # --- reads -------------------------------------------------------------

    def get(self, job_id: str) -> Optional[BackgroundJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def jobs(
        self,
        *,
        kind: Optional[str] = None,
        active: Optional[bool] = None,
    ) -> list[BackgroundJob]:
        """All jobs in dispatch order, optionally filtered by ``kind`` /
        ``active`` (True = pending|running, False = terminal)."""
        with self._lock:
            out = [self._jobs[i] for i in self._order if i in self._jobs]
        if kind is not None:
            out = [j for j in out if j.kind == kind]
        if active is True:
            out = [j for j in out if j.active]
        elif active is False:
            out = [j for j in out if not j.active]
        return out

    def active_jobs(self) -> list[BackgroundJob]:
        return self.jobs(active=True)

    def unseen_done(self) -> list[BackgroundJob]:
        """Terminal jobs the user hasn't opened yet (Story #1 done-tab chips)."""
        with self._lock:
            ids = list(self._unseen_done)
            return [self._jobs[i] for i in ids if i in self._jobs]

    def mark_seen(self, job_id: str) -> bool:
        """Dismiss a done-tab for ``job_id`` (called when the surface mounts it)."""
        with self._lock:
            if job_id not in self._unseen_done:
                return False
            self._unseen_done.remove(job_id)
        _fire_listener()
        return True

    def mark_all_seen(self) -> int:
        """Dismiss every done pill without dropping the jobs. Returns how many."""
        with self._lock:
            n = len(self._unseen_done)
            self._unseen_done.clear()
        if n:
            _fire_listener()
        return n

    # --- lifecycle ---------------------------------------------------------

    def cancel(self, job_id: str) -> bool:
        """Request cancellation of an active job. Returns False for an unknown or
        already-finished job. A pending job that the executor hasn't started is
        cancelled immediately; a running job is *marked* (cooperative) — its
        result is discarded when ``fn`` returns, but the thread is not killed."""
        job = self.get(job_id)
        if job is None or not job.active:
            return False
        job.cancel_requested = True
        future = job._future
        cancelled_now = bool(future is not None and future.cancel())
        if cancelled_now and job.status == PENDING:
            # The future never started — resolve the job here (its _run won't run).
            self._finalize(job, CANCELLED)
        else:
            _fire_listener()
        return True

    def wait(self, job_id: str, timeout: Optional[float] = None) -> Optional[BackgroundJob]:
        """Block until a job reaches a terminal state (or ``timeout``), then
        return it. Awaits the full ``_run`` — including the completion notice —
        so callers/tests see a fully-resolved job. Unknown id → None."""
        job = self.get(job_id)
        if job is None:
            return None
        future = job._future
        if future is not None:
            try:
                future.result(timeout=timeout)
            except Exception:
                pass  # the job captured its own outcome; we only await it here
        return job

    def clear_finished(self) -> int:
        """Drop terminal jobs from the registry; return how many were removed."""
        with self._lock:
            done = [i for i in list(self._order)
                    if (j := self._jobs.get(i)) is not None and not j.active]
            for i in done:
                self._jobs.pop(i, None)
                self._order.remove(i)
                if i in self._unseen_done:
                    self._unseen_done.remove(i)
        return len(done)

    def shutdown(self, *, wait: bool = False) -> None:
        """Tear the executor down (session exit). Cancels queued-but-unstarted
        jobs; ``wait`` blocks for in-flight ones."""
        ex = self._executor
        if ex is None:
            return
        self._executor = None
        ex.shutdown(wait=wait, cancel_futures=True)


# --------------------------------------------------------------------------- #
#  Registry access off REPLState (J's half of the two-pointer, lazily created)
# --------------------------------------------------------------------------- #

def _resolve_cap(state: Any) -> int:
    """The session's parallel-job ceiling, from ``cfg.max_parallel_workers``."""
    cfg = getattr(state, "cfg", None)
    cap = getattr(cfg, "max_parallel_workers", None)
    try:
        return max(1, int(cap))
    except (TypeError, ValueError):
        return 8


def _make_notifier(state: Any) -> Callable[[BackgroundJob], None]:
    """A completion sink that writes a one-line notice to the *live* console
    (re-read each fire, so a TUI console swap keeps the notice on the right
    surface). Notify-on-completion (the ``/jobs`` contract); best-effort."""

    def _notify(job: BackgroundJob) -> None:
        console = getattr(state, "console", None)
        if console is None:
            return
        label = job.name or job.kind
        secs = f"{job.elapsed():.0f}s"
        if job.status == DONE:
            msg = f"[green]✓ job {job.job_id} done[/green] [dim]· {job.kind} {label} · {secs}[/dim]"
        elif job.status == FAILED:
            msg = f"[red]✗ job {job.job_id} failed[/red] [dim]· {job.kind} {label}[/dim] — {job.error or ''}"
        elif job.status == CANCELLED:
            msg = f"[dim]⊘ job {job.job_id} cancelled · {job.kind} {label}[/dim]"
        else:
            return
        try:
            console.print(msg)
        except Exception:
            # A dropped notice does not change the job's state.
            pass

    return _notify


def get_registry(state: Any) -> Optional[JobRegistry]:
    """The job registry for ``state``, creating it on first use.

    Returns None for a stateless/fake context with no ``job_registry`` slot
    (e.g. a SimpleNamespace test ctx) so callers stay defensive — mirrors
    ``harness.session.get_registry``.
    """
    if state is None or not hasattr(state, "job_registry"):
        return None
    reg = state.job_registry
    if reg is None:
        reg = JobRegistry(max_workers=_resolve_cap(state), on_complete=_make_notifier(state))
        try:
            state.job_registry = reg
        except Exception:
            return reg
    return reg


def _peek_registry(state: Any) -> Optional[JobRegistry]:
    """Read the registry WITHOUT creating it — for the per-render status surface,
    which must stay a pure read (profile_bar calls it on every paint)."""
    reg = getattr(state, "job_registry", None)
    return reg if isinstance(reg, JobRegistry) else None


# --------------------------------------------------------------------------- #
#  Seam J4 — the profile-bar status segment (S places the call; J owns the fn)
# --------------------------------------------------------------------------- #

def _render_one(job: BackgroundJob) -> Text:
    """`fleet build · A1 ✓ A2 ⠹ B … · 3/6` for a composite job, or `task t1 ⠹`
    for a plain one."""
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(job.kind, style=_KIND_STYLE.get(job.kind, "cyan"))
    if job.name and job.name != job.kind:
        t.append(f" {job.name}", style="bold")
    if job.detail:
        t.append(" · ", style="dim")
        t.append(job.detail)
    if job.progress:
        done, total = job.progress
        t.append(" · ", style="dim")
        t.append(f"{done}/{total}", style="dim")
    elif not job.detail:
        # No sub-status to carry the state — show the job's own glyph.
        t.append(" ")
        t.append(job.glyph, style=_STATUS_STYLE.get(job.status, ""))
    return t


def _render_many(active: list[BackgroundJob]) -> Text:
    """`3 jobs · t1⠹ l2⠹ f3⠹` (capped) when several run at once."""
    t = Text(no_wrap=True, overflow="ellipsis")
    t.append(f"{len(active)} jobs", style="cyan")
    for job in active[:4]:
        t.append(" ")
        t.append(job.job_id, style="dim")
        t.append(job.glyph, style=_STATUS_STYLE.get(job.status, ""))
    if len(active) > 4:
        t.append(" …", style="dim")
    return t


def _pill_label(job: BackgroundJob) -> str:
    name = job.name or job.kind
    short = name if len(name) <= 18 else name[:17] + "…"
    return f"{job.glyph} {short}"


def chrome_pills(state: Any) -> list[dict[str, Any]]:
    """Face strip rows: in-flight jobs plus finished-unseen (done pills).

    Pure read. Clicking a pill opens the jobs board and marks that id seen.
    """
    try:
        reg = _peek_registry(state)
        if reg is None:
            return []
        out: list[dict[str, Any]] = []
        have: set[str] = set()
        for job in reg.active_jobs():
            have.add(job.job_id)
            out.append({
                "id": job.job_id,
                "kind": job.kind,
                "name": job.name or job.kind,
                "status": job.status,
                "glyph": job.glyph,
                "label": _pill_label(job),
                "unseen": False,
            })
        for job in reg.unseen_done():
            if job.job_id in have:
                continue
            out.append({
                "id": job.job_id,
                "kind": job.kind,
                "name": job.name or job.kind,
                "status": job.status,
                "glyph": job.glyph,
                "label": _pill_label(job),
                "unseen": True,
            })
        return out
    except Exception:
        return []


def summary_segment(state: Any) -> Optional[Text]:
    """The profile-bar job indicator — ``fleet · A1 ✓ A2 ⠹ B … · 3/6`` (J4).

    Returns a Rich ``Text`` while any job is in flight, else None so the bar stays
    lean (completion is announced by the notify-on-done line, not a lingering
    segment). A pure read: never creates the registry, never raises — it is
    called on every ``profile_bar`` paint. S places the one call line.
    """
    try:
        reg = _peek_registry(state)
        if reg is None:
            return None
        active = reg.active_jobs()
        if not active:
            return None
        return _render_one(active[0]) if len(active) == 1 else _render_many(active)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
#  Seam — frame-tab fallback (A1), so jobs are visible even before S lands (Q4)
# --------------------------------------------------------------------------- #

def job_frame_tab(state: Any) -> Optional[list[tuple[str, str, Any]]]:
    """Folder tabs for in-flight jobs + completed-unseen done-tabs, or None.

    Registered through A1's ``register_frame_tab``. Returns a list of
    ``(label, kind, payload)``:

    * kind ``jobs`` — aggregate in-flight chip (payload = the registry); click
      opens ``jobs://``.
    * kind ``job_done`` — one chip per completed-unseen job (payload = job id);
      click mounts ``jobs://<id>`` and dismisses the chip (Story #1 done-tab).

    Pure read; never creates the registry."""
    reg = _peek_registry(state)
    if reg is None:
        return None
    tabs: list[tuple[str, str, Any]] = []
    active = reg.active_jobs()
    if active:
        label = f"{len(active)} job{'' if len(active) == 1 else 's'}"
        tabs.append((label, "jobs", reg))
    for job in reg.unseen_done():
        # Done-tab: glyph + id (and name when short) so the user can answer or dismiss.
        name = job.name or job.kind
        short = name if len(name) <= 16 else name[:15] + "…"
        tabs.append((f"{job.glyph} {job.job_id} {short}", "job_done", job.job_id))
    return tabs or None


def register_job_seams() -> None:
    """Register the frame-tab fallback through A1's published seam, defensively.

    ``register_frame_tab`` is published by the status module; until/unless it is
    present this no-ops rather than raising, so J's command registration is safe
    on day 1 (the ``register_session_seams`` pattern). Idempotent (the seam
    de-dupes providers)."""
    try:
        from xlii.status import register_frame_tab  # type: ignore

        register_frame_tab(job_frame_tab)
    except Exception:
        # xlii.status may not expose the seam yet -- jobs work fine without the frame tab.
        pass


__all__ = [
    "KIND_TASK", "KIND_LOOP", "KIND_HARNESS", "KIND_FLEET", "KIND_JOURNAL",
    "PENDING", "RUNNING", "DONE", "FAILED", "CANCELLED",
    "BackgroundJob", "JobRegistry",
    "set_job_listener", "get_registry",
    "summary_segment", "job_frame_tab", "register_job_seams",
    "chrome_pills",
]
