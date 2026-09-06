"""Episode records — opt-in code-session resume (code-session-resume P0).

Two memory layers, not two modes: **place** (project turns, the default seed)
and **episode** (an opt-in session id for long/crashy/parallel runs). An
episode record stores what turns alone can't give back:

- the **same ``conversation_id``** — the chance to keep the prompt cache warm
  across a resume (``x-grok-conv-id`` prefix reuse);
- the FULL live history (user/assistant/tool trail), denser than the
  final-Q&A-only ``.xlii/turns/`` files;
- list/resume metadata (model, started_at, turn count).

Records live at ``.xlii/sessions/<id>.json``. Snapshots ride the kernel
spine's finalize tail (:func:`xlii.conversation.complete_turn_effects`) while
an episode is on — every surface, every turn shape, no extra plumbing.
Nothing here runs unless the user said ``/session on`` (the proposal's
"opt-in, never a boot wizard" contract).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

_SESSIONS_DIRNAME = "sessions"

# Sticky keep-session: once a launch passes --keep-session (or /session on
# runs), the preference is recorded per project and every later bare launch
# offers to restart where the last episode left off. /session off clears it.
_KEEP_FILENAME = "keep-session.json"

# Heuristic horizon for "the KV / prompt-cache prefix is plausibly still warm":
# episodes reattach the SAME conversation id, so a recently-updated record means
# the server-side prefix was in use minutes ago. Warmth can't be queried — this
# only shapes the offer's wording, never behavior.
KV_WARM_HORIZON_SECONDS = 3600

# P2 snapshot caps — keep full-replay usefulness (user/assistant + tool *structure*)
# while bounding rewrite size. Tool *bodies* truncate; message count caps the trail.
HISTORY_TIER = "tool_trim"
MAX_HISTORY_MESSAGES = 400
MAX_TOOL_BODY_CHARS = 8192
_TOOL_TRIM_MARK = "\n…[trimmed for episode snapshot]…"


def _sessions_dir(xli_dir: Path) -> Path:
    d = Path(xli_dir) / _SESSIONS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_trim_start(history: list, naive_start: int) -> int:
    """Walk a trailing-window cutoff *backward* to a boundary that keeps
    OpenAI-shaped tool pairing valid.

    A snapshot must never *begin* on a ``role=tool`` result — its ``role=assistant``
    parent (which carried the matching ``tool_calls``) would have been sliced off,
    leaving an orphaned tool message that ``resume_episode`` replays as a
    structurally invalid first turn. So when the naive ``history[-N:]`` cutoff
    lands inside a tool run, we move the start earlier (keeping a few more than
    ``N``) until the first kept entry is a user or a tool-call-carrying assistant
    message — i.e. never split an assistant's ``tool_calls`` from their results.

    Returns an index ``≤ naive_start`` (0 in the degenerate all-tool prefix case).
    """
    start = naive_start
    while start > 0:
        entry = history[start]
        if isinstance(entry, dict) and entry.get("role") == "tool":
            start -= 1  # orphaned result at the boundary — swallow it and its kin
            continue
        break
    return start


def prepare_history_for_snapshot(
    history: list,
    *,
    max_messages: int = MAX_HISTORY_MESSAGES,
    max_tool_chars: int = MAX_TOOL_BODY_CHARS,
) -> list:
    """Bound a history rewrite without dropping below full-replay usefulness.

    Keeps roughly the trailing ``max_messages`` entries (or all if shorter),
    but nudges the cutoff back to a valid tool-pairing boundary so the snapshot
    never starts on an orphaned ``role=tool`` result (see :func:`_safe_trim_start`)
    — the cap stays approximate; validity is the floor. Truncates oversized
    ``role=tool`` / tool-result string bodies; leaves user/assistant and
    tool-call *structure* intact.
    """
    if not history:
        return []
    if max_messages > 0 and len(history) > max_messages:
        start = _safe_trim_start(history, len(history) - max_messages)
        trail = list(history[start:])
    else:
        trail = list(history)
    out: list = []
    for entry in trail:
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        role = entry.get("role")
        content = entry.get("content")
        if role == "tool" and isinstance(content, str) and len(content) > max_tool_chars:
            trimmed = dict(entry)
            trimmed["content"] = content[:max_tool_chars] + _TOOL_TRIM_MARK
            out.append(trimmed)
        else:
            out.append(entry)
    return out


def capture_pointers(state: Any) -> dict:
    """Optional rail/loop/job hints for the episode record (hint-only on resume)."""
    pointers: dict[str, Any] = {}
    agent = getattr(state, "agent", None)
    rail = getattr(agent, "rail", None) if agent is not None else None
    if rail is not None:
        stage = getattr(rail, "stage", None) or getattr(rail, "current_stage", None)
        if stage is not None:
            pointers["rail_stage"] = str(stage)
        seeded = getattr(rail, "seeded_from_plan", None)
        if seeded is not None:
            pointers["rail_seeded_from_plan"] = bool(seeded)
    loop = getattr(state, "loop", None)
    if loop is not None and getattr(loop, "is_active", False):
        pointers["loop_active"] = True
        lid = getattr(loop, "id", None) or getattr(loop, "loop_id", None)
        if lid:
            pointers["loop_id"] = str(lid)
        status = getattr(loop, "status", None)
        if status:
            pointers["loop_status"] = str(status)
    reg = getattr(state, "job_registry", None)
    if reg is not None:
        # JobRegistry.jobs is a *method* returning jobs in dispatch order; the old
        # getattr(reg, "jobs", None) grabbed the bound method, list()'d it (raising
        # TypeError), and the blanket except silently dropped every pointer. Call
        # the accessor — tolerating a plain list attribute on a fake registry too.
        try:
            accessor = getattr(reg, "jobs", None)
            listed = accessor() if callable(accessor) else accessor
            jobs = list(listed or [])
        except Exception:
            jobs = []
        if jobs:
            last = jobs[-1]  # most recently dispatched
            pointers["last_job_kind"] = str(getattr(last, "kind", "") or "")
            pointers["last_job_name"] = str(
                getattr(last, "name", None)
                or getattr(last, "job_id", None)
                or getattr(last, "id", "")
                or ""
            )
    return pointers


def cache_stats_from_turn(stats: Any) -> Optional[dict]:
    """Extract last-turn cache/context counters for the episode record."""
    if stats is None:
        return None
    used = getattr(stats, "context_tokens", None)
    cached = getattr(stats, "cached_tokens", None)
    if used is None and cached is None:
        # TurnStats sometimes nests orch usage
        orch = getattr(stats, "orch", None)
        if orch is not None:
            used = getattr(orch, "context_tokens", None) or getattr(orch, "prompt_tokens", None)
            cached = getattr(orch, "cached_tokens", None)
    if used is None and cached is None:
        return None
    out: dict[str, Any] = {"updated_at": _now()}
    if used is not None:
        out["context_tokens"] = int(used)
    if cached is not None:
        out["cached_tokens"] = int(cached)
    return out


def format_cache_resume_line(record: dict) -> str:
    """Dim instrumentation line for resume (meter still recomputes next turn)."""
    cs = record.get("last_cache_stats") or {}
    if not isinstance(cs, dict) or not cs:
        return ""
    used = cs.get("context_tokens")
    cached = cs.get("cached_tokens")
    bits = []
    if used is not None:
        bits.append(f"{used:,} ctx" if isinstance(used, int) else f"{used} ctx")
    if cached is not None and used:
        try:
            pct = int(round(100 * int(cached) / int(used)))
            bits.append(f"{pct}% cached")
        except (TypeError, ValueError, ZeroDivisionError):
            bits.append(f"{cached} cached")
    elif cached is not None:
        bits.append(f"{cached} cached")
    if not bits:
        return ""
    return (
        f"last turn before exit: {' · '.join(bits)} — "
        "meter rebuilds on the first turn (warm-vs-cold is instrumentation only)"
    )


def format_pointers_line(record: dict) -> str:
    """One-line hint for optional rail/loop/job pointers (never auto-reattach)."""
    ptr = record.get("pointers") or {}
    if not isinstance(ptr, dict) or not ptr:
        return ""
    bits = []
    if ptr.get("rail_stage"):
        bits.append(f"rail={ptr['rail_stage']}")
    if ptr.get("loop_active"):
        bits.append("loop was active")
    if ptr.get("last_job_name") or ptr.get("last_job_kind"):
        bits.append(
            f"last job={ptr.get('last_job_kind') or '?'}:"
            f"{ptr.get('last_job_name') or '?'}"
        )
    if not bits:
        return ""
    return "pointers (hint only): " + " · ".join(bits)


def format_cache_delta_line(baseline: dict, stats: Any) -> str:
    """Compare pre-resume snapshot vs first post-resume turn stats."""
    now = cache_stats_from_turn(stats) or {}
    if not baseline or not now:
        return ""
    b_cached = baseline.get("cached_tokens")
    n_cached = now.get("cached_tokens")
    b_used = baseline.get("context_tokens")
    n_used = now.get("context_tokens")
    if b_cached is None or n_cached is None:
        return ""
    try:
        b_pct = (100 * int(b_cached) / int(b_used)) if b_used else None
        n_pct = (100 * int(n_cached) / int(n_used)) if n_used else None
    except (TypeError, ValueError, ZeroDivisionError):
        return ""
    label = "warm" if n_cached >= b_cached else "cold"
    if b_pct is not None and n_pct is not None:
        return (
            f"cache Δ after resume: {int(round(b_pct))}% → {int(round(n_pct))}% "
            f"({label}) — instrumentation only"
        )
    return f"cache Δ after resume: {b_cached} → {n_cached} ({label})"


def _record_path(xli_dir: Path, episode_id: str) -> Path:
    return _sessions_dir(xli_dir) / f"{episode_id}.json"


def new_episode(state: Any) -> Optional[str]:
    """Start tracking: mint a short id, snapshot now, return the id."""
    xli = getattr(getattr(state, "project", None), "xli_dir", None)
    if xli is None:
        return None
    for _ in range(8):                        # collision-proof the short id
        episode_id = uuid.uuid4().hex[:4]
        if not _record_path(xli, episode_id).exists():
            break
    state.episode_id = episode_id
    update_episode(state, started=True)
    return episode_id


def update_episode(state: Any, *, started: bool = False, stats: Any = None) -> None:
    """Snapshot the live episode (no-op unless ``/session on`` set an id).

    Called from the kernel spine at every turn's finalize; best-effort by
    contract — a failed snapshot never breaks a turn."""
    episode_id = getattr(state, "episode_id", None)
    if not episode_id:
        return
    try:
        xli = state.project.xli_dir
        path = _record_path(xli, episode_id)
        started_at = _now()
        prior: dict = {}
        if not started and path.exists():
            try:
                prior = json.loads(path.read_text())
                started_at = prior.get("started_at", started_at)
            except Exception:
                prior = {}
        history = prepare_history_for_snapshot(list(getattr(state.agent, "history", []) or []))
        record = {
            "id": episode_id,
            "conversation_id": getattr(state.project, "conversation_id", ""),
            "project_root": str(getattr(state.project, "project_root", "")),
            "model": str(getattr(getattr(state, "cfg", None), "orchestrator_model", "")
                         or getattr(getattr(state, "cfg", None), "model", "")),
            "started_at": started_at,
            "updated_at": _now(),
            "turns": sum(1 for e in history if isinstance(e, dict) and e.get("role") == "user"),
            # True while the episode runs; mark_clean flips it on a clean exit
            # or /session off. Crash residue therefore reads unclean=True — the
            # signal the launch-time soft offer keys on (P1).
            "unclean": True,
            "history_tier": HISTORY_TIER,
            "history": history,
        }
        pointers = capture_pointers(state)
        if pointers:
            record["pointers"] = pointers
        cs = cache_stats_from_turn(stats)
        if cs is None and isinstance(prior.get("last_cache_stats"), dict):
            cs = prior["last_cache_stats"]
        if cs is not None:
            record["last_cache_stats"] = cs
        from xlii.atomicio import write_text_atomic
        write_text_atomic(path, json.dumps(record, default=str, indent=1) + "\n")
    except Exception:
        # Best-effort by contract (see docstring): a failed snapshot costs
        # crash-recovery fidelity for this turn, never the turn itself.
        pass


def mark_clean(state: Any) -> None:
    """Flip the active episode's ``unclean`` flag off (clean exit / /session
    off). Call BEFORE clearing ``state.episode_id``. Best-effort."""
    episode_id = getattr(state, "episode_id", None)
    if not episode_id:
        return
    try:
        xli = state.project.xli_dir
        path = _record_path(xli, episode_id)
        if not path.exists():
            return
        record = json.loads(path.read_text())
        record["unclean"] = False
        record["updated_at"] = _now()
        from xlii.atomicio import write_text_atomic
        write_text_atomic(path, json.dumps(record, default=str, indent=1) + "\n")
    except Exception:
        # Best-effort: if the flag survives, the next launch only makes a
        # spurious crash-recovery offer.
        pass


def list_episodes(xli_dir: Path) -> list[dict]:
    """Episode summaries (no history payload), most recently updated first."""
    out: list[dict] = []
    d = Path(xli_dir) / _SESSIONS_DIRNAME
    if not d.is_dir():
        return out
    for p in d.glob("*.json"):
        if p.name == _KEEP_FILENAME:
            continue
        try:
            rec = json.loads(p.read_text())
            if not rec.get("id"):
                continue
            out.append({k: rec.get(k, "") for k in
                        ("id", "started_at", "updated_at", "turns", "model", "unclean")})
        except Exception:
            continue
    out.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
    return out


def latest_unclean(xli_dir: Path) -> Optional[dict]:
    """The most recent crash-residue episode (unclean=True), or None — the
    launch-time soft offer's one lookup. Never a wizard: one line, or silence."""
    for rec in list_episodes(xli_dir):
        if rec.get("unclean"):
            return rec
    return None


def load_episode(xli_dir: Path, episode_id: str) -> Optional[dict]:
    p = _record_path(Path(xli_dir), episode_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def resume_episode(state: Any, episode_id: str) -> Optional[dict]:
    """Load an episode into the live session: full history back into the
    agent, and the SAME conversation_id back onto the project — the prefix
    the prompt cache keys on. Returns the record, or None when absent."""
    xli = getattr(getattr(state, "project", None), "xli_dir", None)
    if xli is None:
        return None
    record = load_episode(xli, episode_id)
    if record is None:
        return None
    history = record.get("history") or []
    state.agent.history[:] = history
    conv_id = record.get("conversation_id") or ""
    if conv_id:
        try:
            state.project.conversation_id = conv_id
        except Exception:
            # A project that refuses the attribute just resumes without the recorded conversation id.
            pass
    state.episode_id = episode_id
    # P3 instrumentation baseline for the first post-resume turn's cache Δ line.
    baseline = record.get("last_cache_stats")
    if isinstance(baseline, dict) and baseline:
        try:
            state._episode_cache_baseline = dict(baseline)
        except Exception:
            # Without the baseline the next turn simply reports no cache delta.
            pass
    update_episode(state)          # stamp updated_at for `list` ordering
    return record


# --------------------------------------------------------------------------- #
#  sticky keep-session (the per-project preference behind --keep-session)
# --------------------------------------------------------------------------- #

def read_keep_session(xli_dir: Any) -> bool:
    """Whether this project was flagged --keep-session (or `/session on`)."""
    if xli_dir is None:
        return False
    try:
        data = json.loads((Path(xli_dir) / _SESSIONS_DIRNAME / _KEEP_FILENAME).read_text())
        return bool(isinstance(data, dict) and data.get("keep_session"))
    except (OSError, ValueError):
        return False


def write_keep_session(xli_dir: Any, on: bool) -> None:
    """Persist the sticky keep-session preference. Best-effort."""
    if xli_dir is None:
        return
    try:
        path = _sessions_dir(Path(xli_dir)) / _KEEP_FILENAME
        from xlii.atomicio import write_text_atomic
        write_text_atomic(path, json.dumps({"keep_session": bool(on)}) + "\n")
    except OSError:
        # The toggle applies to the live session; only persisting it across restarts is lost.
        pass


def _age_seconds(iso: str) -> Optional[float]:
    try:
        then = datetime.fromisoformat(iso)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - then).total_seconds())
    except (ValueError, TypeError):
        return None


def _format_age(seconds: float) -> str:
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def kept_offer_line(rec: dict) -> str:
    """The one launch-time question for a sticky project: what's waiting (turns,
    age, and — inside the horizon — that the KV prefix is likely still warm)
    plus the ask. Plain text; the caller decorates and appends [Y/n]."""
    bits = f"{rec.get('turns', 0)} turn(s)"
    age = _age_seconds(str(rec.get("updated_at", "")))
    if age is not None:
        bits += f", {_format_age(age)}"
        if age <= KV_WARM_HORIZON_SECONDS:
            bits += " — KV likely still warm"
    return (f"keep-session is on for this project · kept sess {rec.get('id')} "
            f"({bits}) · restart there?")


def continue_kept(state: Any, *, ask: Optional[Callable[[str], bool]] = None,
                  ) -> Optional[tuple[str, str, int]]:
    """The sticky keep-session arm of launch. None when the preference is off
    (caller falls through to the crash-residue soft offer); otherwise
    ``(action, episode_id, turns)`` with action ∈ {"resumed", "new"}.

    ``ask`` is the interactive seam: called with :func:`kept_offer_line`'s
    question, True means resume. None (non-interactive stdin — scripts, CI,
    the daemon) resumes WITHOUT asking: a prompt must never hang a pipe.
    Declining starts a fresh episode — the preference itself stays on
    (`/session off` is the off-ramp)."""
    xli = getattr(getattr(state, "project", None), "xli_dir", None)
    if xli is None or not read_keep_session(xli):
        return None
    records = list_episodes(xli)
    latest = records[0] if records else None
    if latest and latest.get("id"):
        if ask is None or ask(kept_offer_line(latest)):
            record = resume_episode(state, str(latest["id"]))
            if record is not None:
                return ("resumed", str(latest["id"]),
                        int(record.get("turns", 0) or 0))
    eid = new_episode(state)
    if not eid:
        return None
    return ("new", eid, 0)


def apply_episode_continuity(
    state: Any,
    project: Any,
    *,
    resume: Optional[str] = None,
    keep_session: bool = False,
    interactive: bool = False,
    ask: Optional[Callable[[str], bool]] = None,
    console: Any = None,
) -> None:
    """Episode continuity (code-session-resume P1) for session launch: CLI
    doors + the soft offer. One line or one question — never a boot wizard.

    Extracted verbatim from cmds/sessions/cmd_code (godzilla-mothra B4) so a
    headless body drives the same flow: ``resume``/``keep_session`` are typed
    (no argparse), and the one interactive question arrives as the ``ask``
    callback — a body passes ``interactive=False`` (or no ``ask``) and never
    blocks. ``ask`` fires only when ``interactive`` is true; non-interactive
    callers get continue_kept's silent auto-resume, preserving today's
    "never hang a script" policy.
    """
    if console is None:
        from xlii.ui import console as _shared_console

        console = _shared_console
    if keep_session:
        write_keep_session(project.xli_dir, True)
    if resume is not None:
        target = resume
        if not target:
            records = list_episodes(project.xli_dir)
            target = records[0]["id"] if records else ""
        record = resume_episode(state, target) if target else None
        if record is not None:
            console.print(
                f"[green]✓[/green] resumed [cyan]sess {target}[/cyan] — "
                f"{record.get('turns', 0)} turn(s) + conversation id restored"
            )
        else:
            console.print(
                f"[yellow]no episode{' ' + repr(resume) if resume else ''} to "
                "resume[/yellow] [dim]— /session list to see stored ones[/dim]"
            )
    elif keep_session:
        # Explicit flag: start keeping from HERE (fresh episode); the sticky
        # preference above makes the next bare launch offer the restart.
        eid = new_episode(state)
        if eid:
            console.print(f"[dim]episode[/dim] [cyan]sess {eid}[/cyan] "
                          f"[dim]— sticky for this project: the next launch offers to "
                          f"resume it (/session off to stop)[/dim]")
    else:
        kept = continue_kept(state, ask=ask if interactive else None)
        if kept is not None:
            action, eid, turns = kept
            if action == "resumed":
                console.print(
                    f"[green]✓[/green] resumed [cyan]sess {eid}[/cyan] — {turns} turn(s) "
                    f"+ conversation id restored [dim](keep-session; /session off to stop)[/dim]"
                )
            else:
                console.print(f"[dim]episode[/dim] [cyan]sess {eid}[/cyan] "
                              f"[dim]— fresh start; keep-session stays on[/dim]")
        else:
            residue = latest_unclean(project.xli_dir)
            if residue is not None:
                state.launch_hint = True
                console.print(
                    f"[dim]unclean episode [cyan]sess {residue['id']}[/cyan] from "
                    f"{residue.get('updated_at', '?')} — [/dim]"
                    f"[cyan]/session resume {residue['id']}[/cyan][dim] to pick it up[/dim]"
                )
