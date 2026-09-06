"""Persistent harness sessions + the harness-as-a-mode registry (Vector C).

`/delegate` (and its `/cursor` alias) are one-shot: spawn a harness, run one task,
tear it down. This module is the *orchestration* layer on top — Tier 1 keeps a
named ACP session alive across turns (`/cursor new build`, `/cursor @build <task>`,
`/cursor ls`), and Tier 1.5 makes a harness a first-class **mode** (`/cursor on` →
bare input drives the foreground session; `/cursor off` leaves).

The single owner is :class:`HarnessSessionRegistry`, which lives on
``REPLState.cursor_sessions`` (the one co-owned field, lazily created here). It is
the home of:
  • the named live sessions (each a :class:`PersistentHarnessSession`),
  • the **foreground** pointer (the harness name in mode — what A1's
    ``status.placeholder_key`` reads via ``REPLState.harness_foreground``), and
  • the per-mode active session bare input is routed to.

Consumes (defensively, so this vector ships before the others merge):
  • seam #3 — B's ``capture_output`` (last_output + capture-into-history);
  • seam #1 — A1's ``register_frame_tab`` (the live session as a context tab);
  • seam #2 — A2's ``register_preview`` (a captured exchange is previewable).
Each is wrapped in a best-effort import so a missing seam is a silent no-op.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from xlii.acp_client import AcpClient, AcpError
from xlii.harness.acp_session import acp_harness_names, acp_login_hint, resolve_acp_argv
from xlii.harness.detect import harness_label, harness_meta, list_harness_names

EventCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class SessionTurn:
    """Outcome of one turn against a persistent harness session."""

    text: str = ""
    files_touched: list[str] = field(default_factory=list)
    stop_reason: str = ""
    notes: list[str] = field(default_factory=list)
    error: Optional[str] = None


class PersistentHarnessSession:
    """A named harness session that survives across turns.

    For ACP harnesses (cursor / grok / claude) the underlying ``AcpClient`` is
    started lazily on the first turn and **kept alive** — every later ``ask`` is
    another ``session/prompt`` on the same session id, so the harness keeps its
    context. Headless harnesses (codex) have no live session, so each turn is a
    one-shot ``run_ask`` under the same name (the name still threads the mode +
    transcript bookkeeping).
    """

    def __init__(
        self,
        harness: str,
        name: str,
        *,
        project_root: Path,
        mode: str = "agent",
        model: str | None = None,
        permission: str = "allow",
        with_context: bool = False,
        argv: list[str] | None = None,
        timeout_s: float = 600.0,
    ) -> None:
        self.harness = harness.lower().strip()
        self.name = name
        self.project_root = Path(project_root)
        self.mode = mode
        self.model = model
        self.permission = permission
        # Tier 2 handoff: when set, the session's FIRST turn is seeded with the
        # live xlii tab manifest (role · docs · refs · conversation), so the
        # harness starts where xlii is. Later turns ride the live ACP session.
        self.with_context = with_context
        self._argv = argv
        self.timeout_s = timeout_s

        self.created_at = time.time()
        self.turns = 0
        self.last_text = ""
        self.last_error: Optional[str] = None
        self.files_touched: list[str] = []
        self.model_id: Optional[str] = model

        self._client: AcpClient | None = None
        self._turn_callback: EventCallback | None = None

    # --- introspection ------------------------------------------------------

    @property
    def is_acp(self) -> bool:
        return self.harness in acp_harness_names()

    @property
    def is_live(self) -> bool:
        """True once a backing ACP subprocess is running."""
        return self._client is not None

    @property
    def tier(self) -> str:
        try:
            return str(harness_meta(self.harness)["tier"])
        except ValueError:
            return ""

    def status_line(self) -> str:
        """Compact `name · harness · turns · state` line for `/cursor ls`."""
        state = "live" if self.is_live else ("idle" if self.is_acp else "headless")
        if self.last_error:
            state = "error"
        bits = [self.name, harness_label(self.harness), f"{self.turns} turn{'' if self.turns == 1 else 's'}", state]
        return " · ".join(bits)

    # --- driving ------------------------------------------------------------

    def _dispatch_event(self, kind: str, update: dict[str, Any]) -> None:
        cb = self._turn_callback
        if cb is not None:
            try:
                cb(kind, update)
            except Exception:
                pass  # a render callback must never kill the ACP reader

    def _ensure_client(self) -> AcpClient:
        if self._client is not None:
            return self._client
        argv = self._argv if self._argv is not None else resolve_acp_argv(self.harness)
        client = AcpClient(
            cwd=self.project_root,
            argv=argv,
            mode=self.mode,
            model=self.model,
            permission=self.permission,
            on_event=self._dispatch_event,
            request_timeout=self.timeout_s,
            login_hint=acp_login_hint(self.harness),
        )
        client.start()
        client.new_session()
        self._client = client
        self.model_id = client.current_model_id or self.model
        return client

    def ask(
        self,
        task: str,
        *,
        on_event: EventCallback | None = None,
        timeout_s: float | None = None,
    ) -> SessionTurn:
        """Run one turn against this session, reusing the live ACP session when
        there is one. Never raises — transport/login failures come back as
        ``SessionTurn.error``."""
        self._turn_callback = on_event
        timeout = timeout_s if timeout_s is not None else self.timeout_s
        try:
            if self.is_acp:
                client = self._ensure_client()
                res = client.prompt(task, timeout=timeout)
                turn = SessionTurn(
                    text=res.text,
                    files_touched=list(res.files_touched),
                    stop_reason=res.stop_reason or "",
                    notes=list(client.notes),
                )
            else:
                turn = self._ask_headless(task, timeout)
        except AcpError as e:
            turn = SessionTurn(error=str(e))
        except Exception as e:  # defensive: a session turn must never crash the REPL
            turn = SessionTurn(error=f"{type(e).__name__}: {e}")
        finally:
            self._turn_callback = None

        self.turns += 1
        self.last_text = turn.text
        self.last_error = turn.error
        for f in turn.files_touched:
            if f not in self.files_touched:
                self.files_touched.append(f)
        return turn

    def _ask_headless(self, task: str, timeout: float) -> SessionTurn:
        from xlii.harness.brief import HarnessBrief
        from xlii.harness.runner import run_ask

        result = run_ask(
            self.harness,
            HarnessBrief(kind="session", question=task, project_root=self.project_root),
            model=self.model,
            timeout_s=int(timeout),
            mode=self.mode,
            permission=self.permission,
        )
        return SessionTurn(text=result.text, error=result.error)

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None


class HarnessSessionRegistry:
    """The live registry of named harness sessions + the foreground-mode pointer.

    Lives on ``REPLState.cursor_sessions``. Names are global (one session per
    name); each session knows its harness, so `for_harness` filters and a mode
    drives the most-recently-touched session of its harness.
    """

    def __init__(self) -> None:
        self.sessions: dict[str, PersistentHarnessSession] = {}
        # The harness *name* whose mode is foreground (what A1's placeholder_key
        # reads via REPLState.harness_foreground). None = normal xlii routing.
        self.foreground: Optional[str] = None
        # The session name bare input is routed to while a mode is foreground.
        self.active_name: Optional[str] = None

    # --- lifecycle ----------------------------------------------------------

    def open(
        self,
        harness: str,
        name: str,
        *,
        project_root: Path,
        mode: str = "agent",
        model: str | None = None,
        permission: str = "allow",
        with_context: bool = False,
        argv: list[str] | None = None,
        timeout_s: float = 600.0,
    ) -> PersistentHarnessSession:
        """Create (or replace) a named session and make it the active one."""
        if name in self.sessions:
            self.sessions[name].close()
        session = PersistentHarnessSession(
            harness,
            name,
            project_root=project_root,
            mode=mode,
            model=model,
            permission=permission,
            with_context=with_context,
            argv=argv,
            timeout_s=timeout_s,
        )
        self.sessions[name] = session
        self.active_name = name
        return session

    def get(self, name: str) -> Optional[PersistentHarnessSession]:
        return self.sessions.get(name)

    def names(self) -> list[str]:
        return list(self.sessions.keys())

    def for_harness(self, harness: str) -> list[PersistentHarnessSession]:
        harness = harness.lower().strip()
        return [s for s in self.sessions.values() if s.harness == harness]

    def touch(self, name: str) -> None:
        """Mark a session as the most-recently-used (the mode's active target)."""
        if name in self.sessions:
            self.active_name = name

    def close(self, name: str) -> bool:
        session = self.sessions.pop(name, None)
        if session is None:
            return False
        session.close()
        if self.active_name == name:
            self.active_name = None
        # Leaving mode if the foreground harness has no sessions left.
        if self.foreground and not self.for_harness(self.foreground):
            self.foreground = None
        return True

    def close_all(self) -> None:
        for session in list(self.sessions.values()):
            session.close()
        self.sessions.clear()
        self.foreground = None
        self.active_name = None

    # --- harness-as-a-mode (Tier 1.5) --------------------------------------

    def enter_mode(
        self,
        harness: str,
        name: str | None = None,
        *,
        project_root: Path,
        **open_kw: Any,
    ) -> PersistentHarnessSession:
        """Make ``harness`` the foreground mode, selecting (or creating) a session.

        With ``name`` it targets that session (created if absent). Without one it
        reuses the harness's most-recent session, else opens a default named for
        the harness (`cursor` → session "cursor")."""
        harness = harness.lower().strip()
        target: Optional[PersistentHarnessSession] = None
        if name:
            target = self.sessions.get(name)
            if target is None or target.harness != harness:
                target = self.open(harness, name, project_root=project_root, **open_kw)
        else:
            existing = self.for_harness(harness)
            if self.active_name and self.active_name in self.sessions:
                act = self.sessions[self.active_name]
                if act.harness == harness:
                    target = act
            if target is None and existing:
                target = existing[-1]
            if target is None:
                target = self.open(harness, harness, project_root=project_root, **open_kw)
        self.foreground = harness
        self.active_name = target.name
        return target

    def leave_mode(self) -> Optional[str]:
        """Drop foreground mode (sessions stay alive). Returns the left harness."""
        left = self.foreground
        self.foreground = None
        return left

    def foreground_session(self) -> Optional[PersistentHarnessSession]:
        """The session bare input drives while a mode is foreground, or None."""
        if not self.foreground:
            return None
        if self.active_name and self.active_name in self.sessions:
            session = self.sessions[self.active_name]
            if session.harness == self.foreground:
                return session
        pool = self.for_harness(self.foreground)
        return pool[-1] if pool else None


# --------------------------------------------------------------------------- #
#  Registry access off REPLState (the one co-owned field, lazily created)
# --------------------------------------------------------------------------- #

def get_registry(state: Any) -> Optional[HarnessSessionRegistry]:
    """The session registry for ``state``, creating it on first use.

    Returns None for a stateless/fake context that has no ``cursor_sessions``
    slot (e.g. a SimpleNamespace test ctx) so callers stay defensive."""
    if state is None or not hasattr(state, "cursor_sessions"):
        return None
    reg = state.cursor_sessions
    if reg is None:
        reg = HarnessSessionRegistry()
        try:
            state.cursor_sessions = reg
        except Exception:
            return reg
    return reg


def foreground_harness(state: Any) -> Optional[str]:
    """The foreground harness name for ``state`` (seam #5 read), or None."""
    reg = getattr(state, "cursor_sessions", None)
    return reg.foreground if reg is not None else None


# --------------------------------------------------------------------------- #
#  Bare-input routing (Tier 1.5) — the chokepoint process_repl_input calls
# --------------------------------------------------------------------------- #

def drive_foreground_session(state: Any, text: str) -> bool:
    """Route one bare-input line to the foreground harness session.

    Returns True when it handled the line (a mode is foreground and a session
    took the turn), False otherwise (no mode active → normal xlii routing). This
    is the single hook ``repl.process_repl_input`` calls so both the inline REPL
    and the Textual TUI route the same way."""
    reg = getattr(state, "cursor_sessions", None)
    if reg is None or not reg.foreground:
        return False
    session = reg.foreground_session()
    console = getattr(state, "console", None)
    if session is None:
        if console is not None:
            console.print(f"[dim](no {reg.foreground} session — /{reg.foreground} new <name>)[/dim]")
        return True
    run_session_turn(state, session, text)
    return True


def run_session_turn(
    state: Any, session: PersistentHarnessSession, task: str, *, stream: bool = True
) -> SessionTurn:
    """Drive one turn, stream/print it, and capture the exchange (seam #3).

    Shared by the foreground-mode router and the `/cursor @name <task>` verb so
    both stream identically and fold into last_output + (optionally) history.

    ``stream=False`` suppresses the live token stream — for a background job
    (``/cursor @name --bg``), where interleaved chunks would garble the foreground
    REPL; the full reply is printed as one block on completion instead."""
    console = getattr(state, "console", None)
    from xlii.repl_cmds.delegate import HarnessStreamRelay, emit_harness_answer, _harness_badge

    relay: HarnessStreamRelay | None = None
    if console is not None and stream and session.is_acp:
        relay = HarnessStreamRelay(
            console,
            harness=session.harness,
            model=session.model or session.model_id,
            session=session,
        )

    def on_event(kind: str, update: dict[str, Any]) -> None:
        if relay is not None:
            relay.on_event(kind, update)

    prompt = task
    # Tier 2: seed the very first turn of a --context session with xlii's live
    # manifest, so the harness picks up where xlii is (later turns reuse the live
    # ACP session, so we don't re-send it each turn).
    if getattr(session, "with_context", False) and session.turns == 0:
        from xlii.harness.mcp_context import build_handoff_context

        handoff = build_handoff_context(state)
        if handoff:
            prompt = f"{handoff}\n\n---\n\n{task}"

    turn = session.ask(prompt, on_event=on_event if stream else None)
    if relay is not None:
        relay.finish()
    if console is not None:
        console.print()
        for note in turn.notes:
            if note.strip():
                console.print(f"[dim]note: {note}[/dim]")
        if turn.error:
            console.print(f"[yellow]{session.harness} session error:[/yellow] {turn.error}")
        elif turn.text and (not session.is_acp or not stream):
            badge = _harness_badge(
                session.harness, session.model or session.model_id, session
            )
            emit_harness_answer(console, turn.text, badge)
        if turn.files_touched:
            console.print("[bold]files changed (uncommitted):[/bold]")
            for f in turn.files_touched:
                console.print(f"  [green]{f}[/green]")

    if not turn.error:
        capture_session_output(
            state,
            session,
            task,
            turn.text,
            into_history=True,
        )
    return turn


# --------------------------------------------------------------------------- #
#  Seam #3 — capture (B), defensively. No-op until B's seam lands.
# --------------------------------------------------------------------------- #

def capture_harness_output(
    state: Any,
    source: str,
    text: str,
    *,
    into_history: bool = False,
) -> None:
    """Fold any harness exchange into B's capture seam (last_output + history).

    Best-effort: B owns ``capture_output`` (``shell_toolkit`` / ``repl_state``).
    Until that seam merges this is a silent no-op, so C ships before B — the
    FINDING's one buffer then serves harness output, `/cursor`, and `/delegate`."""
    if not text:
        return
    try:
        from xlii.shell_toolkit import capture_output  # type: ignore
    except Exception:
        return
    try:
        capture_output(state, text, source=source, into_history=into_history)
    except Exception:
        # Capturing harness output is bookkeeping; the harness result itself already returned.
        pass


def capture_session_output(
    state: Any,
    session: PersistentHarnessSession,
    task: str,
    text: str,
    *,
    into_history: bool = False,
) -> None:
    """Fold a persistent-session exchange into B's capture seam (seam #3)."""
    capture_harness_output(state, f"harness:{session.harness}", text, into_history=into_history)


# --------------------------------------------------------------------------- #
#  Seams #1 / #2 — session as a context tab + previewable (A1 / A2), defensively
# --------------------------------------------------------------------------- #

def session_tab(state: Any) -> Optional[tuple[str, str, Any]]:
    """`(label, kind, payload)` for the foreground session, or None.

    The payload is the live session so A2's preview can render the last
    exchange. Shape matches seam #1's `(label, kind, payload)` tab contract."""
    reg = getattr(state, "cursor_sessions", None)
    if reg is None or not reg.foreground:
        return None
    session = reg.foreground_session()
    if session is None:
        return None
    return (harness_label(session.harness), "session", session)


def _preview_session(payload: Any) -> Any:
    """A2 preview provider for a captured session exchange (seam #2)."""
    last = getattr(payload, "last_text", "") or "(no exchange yet)"
    name = getattr(payload, "name", "session")
    harness = getattr(payload, "harness", "")
    return f"# {name} ({harness})\n\n{last}"


def register_session_seams() -> None:
    """Register the session tab (#1) + preview (#2) providers, defensively.

    Both A1's ``register_frame_tab`` and A2's ``register_preview`` are published
    by *other* vectors; until they merge these imports fail and we no-op, so this
    is safe to call from C's command registration on day 1."""
    try:
        from xlii.status import register_frame_tab  # type: ignore

        register_frame_tab(session_tab)
    except Exception:
        # Per the docstring: the status seam may not exist yet, so registration no-ops.
        pass
    try:
        from xlii.tui.preview import register_preview  # type: ignore

        register_preview("session", _preview_session)
    except Exception:
        # Same -- the TUI preview seam may not exist yet.
        pass


__all__ = [
    "SessionTurn",
    "PersistentHarnessSession",
    "HarnessSessionRegistry",
    "get_registry",
    "foreground_harness",
    "drive_foreground_session",
    "run_session_turn",
    "capture_harness_output",
    "capture_session_output",
    "session_tab",
    "register_session_seams",
    "list_harness_names",
]
