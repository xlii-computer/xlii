"""Graceful exit — an announced teardown so /exit never feels hung.

The slow parts of leaving a session used to run *silently* — in the TUI's
``on_unmount``, after the screen was already torn down, so a slower machine
showed a blank, non-blinking terminal that reads as "frozen." This runs the
same steps on the restored terminal with a line per step, so the user watches
progress instead of guessing. The journal step — once the genuinely slow one —
now *defers* its tail (raw entries are already durable; the next session's
catch-up job summarizes them), so every step here is near-instant.

One shared sequence for both surfaces (inline REPL + ``--tui``); idempotent, so
a double-call (e.g. on_unmount already saved) is a no-op. Every step is
best-effort — teardown must never raise.
"""

from __future__ import annotations

from typing import Any, Callable, Optional


def run_graceful_exit(
    state: Any,
    *,
    printer: Callable[[str], None],
    on_exit: Optional[Callable[[], None]] = None,
) -> None:
    """Run the announced teardown once. ``printer`` takes a Rich-markup string
    (both surfaces pass a real console's ``print``). ``on_exit`` is the inline
    caller's journal-flush hook (``_code_on_exit``); when absent the sequence
    flushes ``state.journal`` itself (the TUI path)."""
    if getattr(state, "_graceful_exit_done", False):
        if on_exit is not None and not getattr(state, "_graceful_exit_on_exit_done", False):
            try:
                state._graceful_exit_on_exit_done = True
            except Exception:
                # A state that refuses the flag risks running on_exit twice, which is preferable to blocking the
                # exit.
                pass
            try:
                on_exit()
            except Exception:
                # Exit hooks are best-effort: a failing one must not stop the process from exiting.
                pass
        return
    try:
        state._graceful_exit_done = True
    except Exception:
        # Some state objects may not allow ad-hoc attrs; exit is best-effort.
        pass

    def step(label: str, fn: Callable[[], None]) -> None:
        # Announce BEFORE the work so a slow step shows *what* is taking time.
        try:
            printer(f"[dim]· {label}…[/dim]")
        except Exception:
            # Best-effort teardown: output may be unavailable during shutdown.
            pass
        try:
            fn()
        except Exception:
            # Best-effort teardown: a failed step must not abort exit.
            pass

    # 1. Persist session state (attachments, flags). Usually instant; announced
    #    so a stalled disk still reads as progress, not a hang.
    if hasattr(state, "save"):
        step("saving session", state.save)

    # 2. The journal — now instant: the buffered tail stays on disk (raw entries
    #    are already durable) for the next session's catch-up job, so exit never
    #    pays the LLM summary. Inline routes through its on_exit hook; the TUI
    #    defers state.journal directly (flush stays the fallback for
    #    journal-likes without defer_flush).
    if on_exit is not None:
        try:
            state._graceful_exit_on_exit_done = True
        except Exception:
            # Same flag as above -- a state that refuses it must not block the exit.
            pass
        step("wrapping up the session journal", on_exit)
    else:
        jrnl = getattr(state, "journal", None)
        if jrnl is not None:
            fn = getattr(jrnl, "defer_flush", None) or getattr(jrnl, "flush", None)
            if fn is not None:
                step("wrapping up the session journal", fn)

    # 3. Shell-habit table compile (deterministic, quick) — the "or on session
    #    close" arm of the ghost-text distiller, so a short session isn't lost.
    def _compile() -> None:
        from xlii import shell_suggest
        shell_suggest.session_close_compile()

    step("saving shell habits", _compile)

    # 4. Background jobs — cancel anything still queued (a queued journal
    #    catch-up simply stays spooled for the next open) and say so when a
    #    running one will keep the process alive briefly after "bye" (the
    #    interpreter joins worker threads: work finishes, it isn't lost).
    registry = getattr(state, "job_registry", None)
    if registry is not None and hasattr(registry, "shutdown"):
        def _wind_down() -> None:
            try:
                running = [j for j in registry.jobs(active=True)
                           if getattr(j, "status", "") == "running"]
            except Exception:
                running = []
            registry.shutdown(wait=False)
            if running:
                printer(f"[dim]  {len(running)} background job(s) still finishing — "
                        "exit completes when they do[/dim]")

        step("winding down background jobs", _wind_down)

    try:
        printer("[dim]bye[/dim]")
    except Exception:
        # Best-effort teardown: final status output must never make exit fail.
        pass


def end_code_session(state: Any, *, console: Any = None) -> None:
    """The code session's exit tail (godzilla-mothra B4 — moved verbatim from
    cmds/sessions/code.py's ``_code_on_exit``).

    JRN-1 fast exit: the buffered tail is already durable on disk, so defer
    the LLM summary + uploads to the next session's catch-up job instead of
    paying them here (the "on session exit" half of the batched summarizer
    became "at next session open"). Best-effort. Then Episode continuity (P1):
    a normal exit is CLEAN — only crash residue (unclean=True) earns the next
    launch's soft resume offer.
    """
    if console is None:
        from xlii.ui import console as _shared_console

        console = _shared_console
    jrnl = getattr(state, "journal", None)
    if jrnl is not None:
        try:
            n = jrnl.defer_flush()
            if n:
                console.print(f"[dim]journal: {n} entr{'y' if n == 1 else 'ies'} "
                              "deferred — summarized at next open[/dim]")
        except Exception as e:
            console.print(f"[dim yellow]journal defer skipped on exit: {e}[/dim yellow]")
    from xlii.episode import mark_clean

    mark_clean(state)
