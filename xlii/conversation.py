"""Live Conversation — the kernel-owned single source of truth for turns.

Part of kernel convergence: one owner for the turn lifecycle (live + committed).
Surfaces (main transcript, TranscriptPane via conv:// or direct, future panes)
project from this.

Design:
- **Records, never writes (strangler phase).** Persistence stays exactly where it
  is today — the profile's :class:`~xlii.profile.TurnStore` policy (code: bounded
  ``persist_code_turn``; chat: ``write_turn`` + ``__rescan__``), invoked once by
  the existing turn pipelines. ``complete_turn`` only finalizes the in-memory
  view; a second ``write_turn`` here would double-persist every turn. Folding the
  TurnStore policy *behind* the Conversation is the Phase-3+ step.
- Tracks the in-flight turn for streaming / live projection.
- Committed turns are seeded from disk (bounded, like TurnStore) + appended on
  completion.
- One instance per session: :func:`ensure_conversation` is the get-or-create
  every surface must use, so panes observe the same object the turn pipeline
  drives.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from xlii.transcript import Turn, load_recent_turns

# Same shape transcript.write_turn stamps into turn files, so in-memory records
# and re-read files sort/display identically.
_TURN_TS_FMT = "%Y-%m-%d %H:%M:%S UTC"

# Bounded seed when the profile doesn't say (TurnStore always says).
DEFAULT_SEED_LIMIT = 50

# Synthetic VFS leaf for the live turn (ConvProvider); never a real file.
INFLIGHT_LEAF = "__inflight__.md"

# Throttled UI notify (matches agent Live refresh_per_second=10).
_CHUNK_NOTIFY_INTERVAL_S = 0.1
_CONV_LISTENER: Optional[Callable[[], None]] = None
_LAST_CONV_NOTIFY = 0.0


def set_conversation_listener(cb: Optional[Callable[[], None]]) -> Optional[Callable[[], None]]:
    """Install a repaint hook fired on conversation changes (chunk / start /
    complete / abort). Returns the previous hook — same contract as
    :func:`xlii.jobs.set_job_listener`. Kernel never imports the TUI; the
    surface installs ``call_from_thread`` here."""
    global _CONV_LISTENER
    prev = _CONV_LISTENER
    _CONV_LISTENER = cb
    return prev


def notify_conversation(*, force: bool = False) -> None:
    """Fire the conversation listener, throttled unless ``force`` (lifecycle)."""
    global _LAST_CONV_NOTIFY
    cb = _CONV_LISTENER
    if cb is None:
        return
    now = time.monotonic()
    if not force and (now - _LAST_CONV_NOTIFY) < _CHUNK_NOTIFY_INTERVAL_S:
        return
    _LAST_CONV_NOTIFY = now
    try:
        cb()
    except Exception:
        # A failing listener must not break the turn that notified it.
        pass


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime(_TURN_TS_FMT)


def _install_chunk_feed(state: Any, conv: Conversation) -> list[tuple[Any, Any]]:
    """Duck-type ``console.on_content_chunk`` so Agent streaming feeds
    ``append_chunk`` without the kernel importing the TUI. Restores prior
    values on teardown."""
    targets: list[Any] = []
    for obj in (
        getattr(state, "console", None),
        getattr(getattr(state, "agent", None), "console", None),
    ):
        if obj is not None and obj not in targets:
            targets.append(obj)

    def _feed(text: str, _c: Conversation = conv) -> None:
        if text:
            _c.append_chunk(text)

    restored: list[tuple[Any, Any]] = []
    for t in targets:
        prev = getattr(t, "on_content_chunk", None)
        restored.append((t, prev))
        try:
            t.on_content_chunk = _feed
        except Exception:
            # A target that refuses the attribute simply doesn't get the chunk feed.
            pass
    return restored


def _restore_chunk_feed(saved: list[tuple[Any, Any]]) -> None:
    for obj, prev in saved:
        try:
            if prev is None and hasattr(obj, "on_content_chunk"):
                try:
                    delattr(obj, "on_content_chunk")
                except Exception:
                    obj.on_content_chunk = None
            else:
                obj.on_content_chunk = prev
        except Exception:
            # Nothing left to restore onto -- the object is already gone or refuses the attribute.
            pass


@dataclass
class InFlight:
    """Transient state for the turn currently being generated.

    assistant_so_far accumulates streamed chunks.
    When complete, we finalize into a Turn (in memory).
    """

    user: str
    assistant_so_far: str = ""
    context: str = ""  # address or selection that provided context
    started_at: str = field(default_factory=_now_ts)


@dataclass
class Conversation:
    """Single owner of a session's conversation (episodic memory).

    - `turns`: committed turns, in order (bounded seed from disk + newly completed).
    - `in_flight`: the turn currently streaming (if any).
    - `complete_turn` finalizes in memory only — the TurnStore policy remains the
      one persistence path (see module docstring).
    """

    turns_dir: Path
    turns: list[Turn] = field(default_factory=list)
    in_flight: Optional[InFlight] = None
    seed_limit: int = DEFAULT_SEED_LIMIT

    def __post_init__(self) -> None:
        if not self.turns:
            self.turns = load_recent_turns(self.turns_dir, self.seed_limit)

    # --- lifecycle ---

    def start_turn(self, user: str, *, context: str = "") -> None:
        """Begin a new turn. Any previous in-flight is dropped (defensive)."""
        self.in_flight = InFlight(user=user.strip(), context=context)
        notify_conversation(force=True)

    def append_chunk(self, text: str) -> None:
        """Accumulate streamed assistant content for the live turn."""
        if self.in_flight is not None:
            self.in_flight.assistant_so_far += text
            notify_conversation(force=False)

    def complete_turn(self, assistant: Optional[str] = None) -> Optional[Turn]:
        """Finalize the in-flight turn **in memory** and return the new Turn.

        Does NOT write to disk — the caller's pipeline has already persisted the
        turn via the profile's TurnStore policy (writing here again was the
        double-persist bug). An empty reply clears the in-flight and records
        nothing (streamed turns land via the disk-backed refresh instead).
        """
        if self.in_flight is None:
            return None

        final_assistant = (assistant or self.in_flight.assistant_so_far).strip()
        user = self.in_flight.user
        started_at = self.in_flight.started_at
        self.in_flight = None
        notify_conversation(force=True)

        if not final_assistant:
            return None

        turn = Turn(timestamp=started_at or _now_ts(), user=user, assistant=final_assistant)
        self.turns.append(turn)
        return turn

    def abort_turn(self) -> None:
        """Drop the in-flight turn (error paths / cancelled submissions) so a
        phantom live row never lingers and a later completion can't mis-pair it."""
        self.in_flight = None
        notify_conversation(force=True)

    # --- projection / access for surfaces and agent history ---

    def recent(self, limit: int = 0) -> list[Turn]:
        """Return up to `limit` most recent committed turns (chronological).

        If limit <= 0 returns all.
        """
        if limit > 0:
            return self.turns[-limit:]
        return list(self.turns)

    def to_history(self, limit: int = 0) -> list[dict]:
        """Chat-completions style history prefix from committed turns."""
        from xlii.transcript import turns_to_history

        return turns_to_history(self.recent(limit))

    def has_in_flight(self) -> bool:
        return self.in_flight is not None

    def in_flight_summary(self) -> str:
        """Short human-readable status for UI / panes (user + partial assistant)."""
        if not self.in_flight:
            return ""
        partial = self.in_flight.assistant_so_far
        if len(partial) > 80:
            partial = partial[:77] + "…"
        return f"{self.in_flight.user} → {partial}"

    # --- integration helpers ---

    def refresh_from_disk(self) -> None:
        """Reload committed turns from disk (bounded, e.g. after an external write).
        Preserves current in-flight.
        """
        self.turns = load_recent_turns(self.turns_dir, self.seed_limit)

    @property
    def turns_dir_path(self) -> Path:
        return self.turns_dir


def conversation_for_profile(profile: Any) -> Optional[Conversation]:
    """Build a Conversation from a profile's memory store, or ``None`` when the
    profile carries no turns_dir. No path guessing — a wrong directory here would
    silently fork the transcript from what the TurnStore actually writes."""
    mem = getattr(profile, "memory", None)
    turns_dir = getattr(mem, "turns_dir", None) or getattr(profile, "turns_dir", None)
    if turns_dir is None:
        return None
    seed = getattr(mem, "seed_limit", None)
    return Conversation(
        turns_dir=Path(turns_dir),
        seed_limit=seed if isinstance(seed, int) and seed > 0 else DEFAULT_SEED_LIMIT,
    )


def ensure_conversation(state: Any) -> Optional[Conversation]:
    """The one get-or-create every surface must go through.

    Returns ``state.conversation`` if already attached (so the TUI, panes, and
    REPL all observe the SAME instance the turn pipeline drives); otherwise
    builds one from ``state.profile`` (falling back to the canonical
    :func:`xlii.active_session.turns_dir_of` derivation), attaches it, and
    returns it. ``None`` for a stateless context or one with no memory dir.
    """
    if state is None:
        return None
    conv = getattr(state, "conversation", None)
    if conv is not None:
        return conv
    conv = conversation_for_profile(getattr(state, "profile", None))
    if conv is None:
        from xlii.active_session import turns_dir_of

        td = turns_dir_of(state)
        if td is None:
            return None
        conv = Conversation(turns_dir=Path(td))
    try:
        state.conversation = conv
    except AttributeError:
        return conv  # frozen/slotted test doubles can still use the instance
    return conv


# --- Phase 2: minimal kernel turn executor / real sink owner -----------------


@dataclass
class TurnResult:
    """Result of a kernel-driven turn."""
    reply: str
    dirty: set[str]
    stats: Any


class KernelTurnExecutor:
    """The single owner of turn execution (strangler target — not yet wired).

    Responsibilities (minimal for convergence):
    - Own the Conversation lifecycle (start / complete, abort on error)
    - Call the provided run_turn (agent.run_turn)
    - Persist via the injected ``persist`` callable — the caller supplies the
      profile's TurnStore policy; the executor itself never picks a write path,
      so the one-pipeline invariant holds when this replaces the bridge.

    Higher level concerns (hooks, meters, checkpoints, loop continuations,
    journal) remain in the callers during the strangler phase.
    """

    def __init__(
        self,
        conversation: Conversation,
        run_turn: Callable[[str], "tuple[str, set[str], Any]"],
        *,
        persist: Optional[Callable[[str, str], None]] = None,
    ):
        self.conversation = conversation
        self._run_turn = run_turn
        self._persist = persist

    def execute(self, prompt: str, *, context: str = "",
                attachments: Optional[Sequence[Any]] = None) -> TurnResult:
        """Run one turn through the kernel owner.

        Starts live tracking in Conversation, runs the agent, completes the
        in-memory record, and invokes the caller-supplied persistence policy.
        """
        self.conversation.start_turn(prompt, context=context)

        try:
            if attachments:
                text, dirty, stats = self._run_turn(prompt, attachments=attachments)  # type: ignore[call-arg]
            else:
                text, dirty, stats = self._run_turn(prompt)
        except Exception:
            # On failure, clear in-flight cleanly (no phantom live row, no
            # mis-paired completion by a later turn).
            self.conversation.abort_turn()
            raise

        turn = self.conversation.complete_turn(text or "")
        # The reply is THIS turn's text — never a prior turn's. Streamed turns
        # can legitimately return "" (recovery from history is the caller's
        # policy, same as today's pipelines).
        reply = turn.assistant if turn is not None else (text or "")
        if self._persist is not None:
            self._persist(prompt, reply)
        return TurnResult(reply=reply, dirty=dirty, stats=stats)


def drive_turn(
    state: Any,
    prompt: str,
    run_turn: Callable[..., "tuple[str, set[str], Any]"],
    *,
    render: Callable[["TurnResult", str], None],
    on_error: Callable[[Exception], None],
    policy_hooks: bool = False,
    hook_extra: Optional[dict] = None,
    fold_attachments: bool = True,
    continue_loop: bool = True,
) -> Optional[TurnResult]:
    """THE turn owner (kernel convergence Phase 3; sole lane since Phase 5b).

    Owns the whole shared lifecycle every turn shares — main, loop
    continuation, and policy follow-up alike: loop-session sync, session
    meter, pre/post hooks, locker attachments (+ ``--once`` consumption),
    checkpoints, the live Conversation (start/abort/complete), the ONE
    persistence policy (profile TurnStore, else bounded ``persist_code_turn``)
    + end-of-turn sync, the journal, the turn receipt, loop builder-cost, and
    the loop continuation. Surfaces inject only delivery:

    - *render(result, prompt)* — render-ONLY answer delivery (blocks in the
      TUI, console prints inline). Must not persist: persistence lives here.
    - *on_error(exc)* — failure display. The turn returns ``None`` after it.
    - *policy_hooks* — the inline REPL's B1 post-turn policy hooks; off for
      the TUI (parity with pre-convergence behavior).
    - *hook_extra* — merged into the pre/post hook payloads (loop
      continuations pass ``{"loop": True}``, policy turns ``{"policy": True}``).
    - *fold_attachments* — loop/policy follow-ups pass False: staged locker
      files ride the ORIGINAL turn, not every autonomous cycle.
    - *continue_loop* — the loop driver passes False for the turns it spawns
      (it owns the advance loop; the tail here would double-advance).

    ``KeyboardInterrupt`` aborts the live turn and re-raises — interrupt
    recovery (history repair, loop mark) stays caller-side.
    """
    import sys as _sys

    from xlii.hooks import run_hooks
    from xlii.repl import (
        _checkpoint_begin,
        _checkpoint_end,
        _drive_loop_continuation,
        _session_meter_begin,
        _session_meter_end,
        _sync_loop_session,
    )

    _sync_loop_session(state)
    _session_meter_begin(state)
    try:
        payload = {"user_input": prompt, **(hook_extra or {})}
        run_hooks(state.project.xli_dir, "pre-turn", payload,
                  console=state.console)
    except Exception as exc:  # absent project/xli_dir on fakes — never break a turn
        print(f"[xlii.turn] pre-turn hook skipped: {exc}", file=_sys.stderr)

    # Stamp the answer-frame chip source before the run (inline parity; the TUI
    # render path re-reads live state at render time, so this is harmless there).
    try:
        from xlii.status import turn_record
        state.agent.turn_record = turn_record(state)
    except Exception:
        try:
            state.agent.turn_record = ("", "", "")
        except Exception:
            # Best-effort status metadata only; never fail turn execution on fallback stamp errors.
            pass

    conv = getattr(state, "conversation", None)
    chunk_feed: list[tuple[Any, Any]] = []
    if conv is not None:
        conv.start_turn(prompt)
        chunk_feed = _install_chunk_feed(state, conv)

    live_files: list = []
    if fold_attachments:
        try:
            live_files = state.live_attachment_paths()
        except Exception:
            live_files = []

    cp_tree = _checkpoint_begin(state)
    dirty: set = set()
    text = ""
    stats = None
    try:
        # Pass attachments positionally when staged so fakes/older callables
        # that don't declare an `attachments` keyword still work.
        if live_files:
            text, dirty, stats = run_turn(prompt, live_files)
        else:
            text, dirty, stats = run_turn(prompt)
    except KeyboardInterrupt:
        if conv is not None:
            conv.abort_turn()
        raise  # the inline loop owns interrupt recovery (history repair, loop mark)
    except Exception as e:
        if conv is not None:
            # A phantom "thinking…" row must not linger, and a LATER turn's
            # completion must never pair its reply with this prompt.
            conv.abort_turn()
        on_error(e)
        return None
    finally:
        _restore_chunk_feed(chunk_feed)
        _checkpoint_end(state, cp_tree, dirty)

    # `--once` entries have now ridden their turn — auto-disable them.
    if live_files:
        try:
            state.consume_once_attachments()
        except Exception as exc:
            try:
                state.console.print(
                    f"[yellow]warning: failed to consume one-time attachments: {exc}[/yellow]"
                )
            except Exception:
                # Best-effort warning path: if console output itself fails, suppress it
                # so attachment-consumption cleanup cannot break the turn flow.
                pass

    _session_meter_end(state, stats)

    ctrl = getattr(state, "loop", None)
    loop_active = ctrl is not None and getattr(ctrl, "is_active", False)
    if loop_active and stats is not None and getattr(stats, "total_cost", None) is not None:
        ctrl.record_builder_cost(stats.total_cost)

    result = TurnResult(reply=text or "", dirty=dirty, stats=stats)
    try:
        render(result, prompt)
    except Exception as exc:
        print(f"[xlii.turn] render failed: {exc}", file=_sys.stderr)
        try:
            on_error(exc)
        except Exception as on_error_exc:
            print(
                f"[xlii.turn] on_error handler failed after render failure: {on_error_exc}",
                file=_sys.stderr,
            )
    finally:
        complete_turn_effects(state, prompt, text, dirty, stats,
                              hook_extra=hook_extra)

    if continue_loop:
        _drive_loop_continuation(state, run_turn, render=render, on_error=on_error)

        if policy_hooks and not loop_active:
            from xlii.repl import _drive_policy_hooks
            _drive_policy_hooks(state, run_turn, render, prompt, dirty, stats)

    return result


def complete_turn_effects(
    state: Any,
    prompt: str,
    text: str,
    dirty: set,
    stats: Any,
    *,
    hook_extra: Optional[dict] = None,
) -> None:
    """The ONE post-render bookkeeping tail: persistence policy (profile
    TurnStore, else bounded ``persist_code_turn``) + end-of-turn sync →
    post-turn hook (payload merged with *hook_extra*) → journal → turn
    receipt → in-memory Conversation complete. Called only by
    :func:`drive_turn` since Phase 5b. All best-effort: a failed
    write/hook/journal never eats a rendered turn."""
    import sys as _sys

    from xlii.hooks import run_hooks

    try:
        prof = getattr(state, "profile", None)
        if prof is not None:
            dirty = prof.memory.persist(state.agent.history, prompt, dirty)
        else:
            from xlii.turn_store import final_reply_from_history, persist_turn
            reply = final_reply_from_history(state.agent.history)
            persist_turn(state.project.xli_dir / "turns", prompt, reply)
        from xlii.sync import end_of_turn_sync
        end_of_turn_sync(state, dirty)
    except Exception as exc:
        print(f"[xlii.turn] end-of-turn persist/sync failed: {exc}", file=_sys.stderr)

    payload: dict = {"user_input": prompt, "dirty": sorted(dirty),
                     "tool_calls": getattr(stats, "tool_calls", 0),
                     **(hook_extra or {})}
    try:
        run_hooks(state.project.xli_dir, "post-turn", payload, console=state.console)
    except Exception as exc:
        print(f"[xlii.turn] post-turn hook skipped: {exc}", file=_sys.stderr)

    jrnl = getattr(state, "journal", None)
    # D6 / memory_set: journal_mute must actually stop observe_turn so
    # bearings "memory: off" is truthful.
    if jrnl is not None and not bool(getattr(state, "journal_mute", False)):
        try:
            jrnl.observe_turn(prompt, dirty, stats,
                              cwd=str(getattr(state, "shell_cwd", "") or ""))
        except Exception:
            pass  # journaling never breaks a turn

    # Turn receipt (P1): evidence vs claims, harvested at the ONE finalize
    # point every surface now shares. Quiet on ok; loud only for the lanes the
    # P0 claim gate doesn't already warn about. Never breaks a turn.
    from xlii.turn_receipt import build_and_record_receipt
    build_and_record_receipt(state, prompt, text, dirty, stats)

    # Episode continuity (code-session-resume P0): while /session is on,
    # snapshot the full live history + conversation_id. No-op otherwise.
    try:
        from xlii.episode import update_episode
        update_episode(state, stats=stats)
    except Exception:
        # Episode snapshotting is continuity bookkeeping; the turn itself already completed.
        pass

    conv = getattr(state, "conversation", None)
    if conv is not None and conv.has_in_flight():
        conv.complete_turn(text)  # in-memory only; persistence happened above, once


class KernelTurnSink:
    """A TurnSink implementation that uses KernelTurnExecutor (not yet wired).

    Supports an optional ``result_callback`` for surfaces to render the outcome.
    Errors are NOT swallowed: they reach ``on_error`` when provided, else they
    propagate — a sink that silently eats a failed turn leaves the user staring
    at nothing.
    """

    def __init__(self, executor: KernelTurnExecutor, *,
                 result_callback: Optional[Callable[[TurnResult], None]] = None,
                 on_error: Optional[Callable[[Exception], None]] = None):
        self._executor = executor
        self._result_callback = result_callback
        self._on_error = on_error

    def submit(self, prompt: str, *, context: str = "") -> None:
        try:
            res = self._executor.execute(prompt, context=context)
        except Exception as e:
            if self._on_error is None:
                raise
            self._on_error(e)
            return
        if self._result_callback:
            self._result_callback(res)
